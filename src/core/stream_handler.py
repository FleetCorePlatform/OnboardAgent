#!/usr/bin/env python
import asyncio
import io
import time
from typing import Optional, Tuple, Any, List, Callable

import cv2
import gi
import loguru
import numpy as np
from ultralytics import YOLO

from src.core.kinesis_video_manager import KinesisVideoClient
from src.core.mqtt_manager import MqttManager
from src.core.credential_provider import CredentialProvider
from src.core.upload_manager import UploadManager
from src.models.job_document import Metadata
from src.utils.gst_video_track import GstVideoTrack

gi.require_version("Gst", "1.0")
from gi.repository import Gst


class StreamHandler:
    def __init__(
        self,
        device_name: str,
        port: int,
        yolo_path: str,
        sample_rate: int,
        mqtt: MqttManager,
        alert_topic: str,
        presence_confirmation_frames: int,
        confidence_threshold: int,
        channel_arn: str,
        kvs_client_factory: Callable[..., KinesisVideoClient],
        credential_provider: CredentialProvider,
        upload_manager: UploadManager,
    ) -> None:
        Gst.init(None)

        self._device_name = device_name
        self._port = port
        self._model = YOLO(yolo_path)
        self._sample_rate = sample_rate
        self._mqtt_manager = mqtt
        self._alert_topic = alert_topic
        self._presence_confirmation_frames = presence_confirmation_frames
        self._confidence_threshold: float = confidence_threshold / 100
        self._channel_arn = channel_arn
        self._kvs_client_factory = kvs_client_factory
        self._credential_provider = credential_provider
        self._upload_manager = upload_manager

        try:
            self._aws_region = self._channel_arn.split(":")[3]
        except IndexError:
            self._aws_region = "eu-west-1"
            loguru.logger.warning(
                f"Could not parse region from ARN, defaulting to {self._aws_region}"
            )

        self._running = False
        self._task = None
        self._webrtc_task = None
        self._frame = None
        self._gst_track = None
        self._kvs_client = None
        self._video_pipe = None
        self._video_sink = None
        self._handler = None

        self._frame_count = 0
        self._last_process_time = 0
        self._consecutive_detection_frames = 0
        self._min_interval = 0.2
        self._current_mission_uuid: Optional[str] = None
        self._current_mission_metadata: Optional[Metadata] = None

    def set_active_mission_info(
        self, mission_uuid: Optional[str], metadata: Optional[Metadata]
    ):
        self._current_mission_uuid = mission_uuid
        self._current_mission_metadata = metadata

    async def start(self):
        if self._running:
            return

        self._running = True

        self._gst_track = GstVideoTrack()

        self._kvs_client = None

        command = (
            f"udpsrc port={self._port} ! application/x-rtp, payload=96 ! rtph264depay ! h264parse ! "
            "avdec_h264 ! videoconvert ! video/x-raw,format=BGR ! "
            "appsink name=appsink emit-signals=true sync=false max-buffers=2 drop=true "
        )

        loguru.logger.info(f"Launching Pipeline: {command}")
        self._video_pipe = Gst.parse_launch(command)
        self._video_pipe.set_state(Gst.State.PLAYING)

        self._video_sink = self._video_pipe.get_by_name("appsink")
        self._handler = self._video_sink.connect("new-sample", self._decode_frame)

        self._task = asyncio.create_task(self._start_detection())

    async def set_streaming_state(self, enabled: bool):
        loguru.logger.info(f"Streaming enabled set to {enabled}")

        if enabled:
            if self._webrtc_task and not self._webrtc_task.done():
                loguru.logger.warning("Streaming already enabled")
                return

            creds = await asyncio.to_thread(self._credential_provider.get_credentials)

            self._kvs_client = self._kvs_client_factory(
                region=self._aws_region,
                credentials=creds,
                video_track=self._gst_track,
            )

            self._webrtc_task = asyncio.create_task(self._kvs_client.run())
            loguru.logger.info("Streaming started")

        else:
            if not self._webrtc_task or self._webrtc_task.done():
                loguru.logger.warning("Streaming already disabled")
                return

            self._webrtc_task.cancel()
            if self._kvs_client:
                await self._kvs_client.stop()
            self._kvs_client = None
            loguru.logger.info("Streaming stopped")

    async def stop(self):
        self._running = False

        await self.set_streaming_state(False)

        if self._gst_track:
            self._gst_track.stop()

        if self._task:
            await self._task

        if self._video_sink and self._handler:
            self._video_sink.disconnect(self._handler)

        if self._video_pipe:
            self._video_pipe.set_state(Gst.State.NULL)

        self._video_pipe = None
        self._video_sink = None

    def _decode_frame(self, sink):
        if not self._running:
            return Gst.FlowReturn.OK

        sample = sink.emit("pull-sample")
        if not sample:
            return Gst.FlowReturn.OK

        buf = sample.get_buffer()
        caps = sample.get_caps()

        try:
            h = caps.get_structure(0).get_value("height")
            w = caps.get_structure(0).get_value("width")

            array = np.ndarray(
                (h, w, 3),
                buffer=buf.extract_dup(0, buf.get_size()),
                dtype=np.uint8,
            )

            self._frame = array
            if self._gst_track:
                self._gst_track.update_frame(array)

        except Exception as e:
            loguru.logger.warning(f"Frame decode error: {e}")

        return Gst.FlowReturn.OK

    async def _start_detection(self):
        while self._running:
            if self._frame is None:
                await asyncio.sleep(0.001)
                continue

            current_time = time.time()
            self._frame_count += 1

            if self._frame_count % self._sample_rate != 0:
                await asyncio.sleep(0)
                continue

            if current_time - self._last_process_time < self._min_interval:
                await asyncio.sleep(0)
                continue

            detection, _ = self._run_human_detection(self._frame)
            if detection:
                self._consecutive_detection_frames += 1
                if (
                    self._consecutive_detection_frames
                    == self._presence_confirmation_frames
                ):
                    if self._current_mission_uuid:
                        asyncio.create_task(
                            asyncio.to_thread(
                                self._send_detection_alert,
                                self._frame.copy(),
                                self._current_mission_uuid,
                            )
                        )
                    self._consecutive_detection_frames = 0
            else:
                self._consecutive_detection_frames = 0

            self._last_process_time = current_time
            await asyncio.sleep(0)

    def _run_human_detection(self, frame) -> Tuple[bool, Optional[List[Any]]]:
        try:
            results = self._model(frame, verbose=False)
            for result in results:
                for box in result.boxes:
                    cls = int(box.cls[0])
                    conf = float(box.conf[0])
                    if cls == 0 and conf >= self._confidence_threshold:
                        return True, results
        except Exception as e:
            loguru.logger.error(f"Inference error: {e}")

        return False, None

    def _send_detection_alert(self, frame: np.ndarray, mission_uuid: str):
        timestamp = int(time.time())
        file_name = f"{timestamp}_detection.jpg"
        s3_key = f"detections/{self._current_mission_metadata.outpost}/{self._current_mission_metadata.group}/mission/{mission_uuid}/{self._device_name}/{file_name}"

        asyncio.create_task(self._async_upload(frame, s3_key))

    async def _async_upload(self, frame, s3_key):
        try:
            _, buffer = cv2.imencode(".jpg", frame)
            io_buf = io.BytesIO(buffer)

            await asyncio.to_thread(
                self._upload_manager.upload_bytes,
                io_buf,
                self._current_mission_metadata.bucket,
                s3_key,
            )
            loguru.logger.info(f"Frame uploaded to {s3_key}")
        except Exception as e:
            loguru.logger.error(f"Upload failed: {e}")
