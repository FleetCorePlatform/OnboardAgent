#!/usr/bin/env python
import asyncio
from typing import Optional, Tuple

import gi
import loguru
import numpy as np
import time

from mypy.types import AnyType
from ultralytics import YOLO

from src.core.mqtt_manager import MqttManager
from src.models.credentials_model import CredentialsModel

gi.require_version("Gst", "1.0")
from gi.repository import Gst


class StreamHandler:
    def __init__(
        self,
        port: int,
        yolo_path: str,
        sample_rate: int,
        mqtt,
        alert_topic: str,
        presence_confirmation_frames: int,
        confidence_threshold: int,
    ) -> None:
        Gst.init(None)

        self.port = port
        self.model = YOLO(yolo_path)
        self.sample_rate = sample_rate
        self.mqtt_manager = mqtt
        self.alert_topic = alert_topic
        self.presence_confirmation_frames = presence_confirmation_frames
        self.confidence_threshold: float = confidence_threshold / 100

        self._running = False
        self._task = None

        self._frame_count = 0
        self._last_process_time = 0
        self._consecutive_detection_frames = 0
        self._min_interval = 0.2

        self._frame: Optional[np.ndarray] | None = None
        self._video_pipe = None
        self._video_sink = None
        self._handler: Optional[int] | None = None

        self._valve = None
        self._ws_sink = None

    async def start(self):
        if self._running:
            return

        self._running = True

        command = (
            f"udpsrc port={self.port} ! application/x-rtp, payload=96 ! rtph264depay ! h264parse ! "
            "tee name=t "

            # Branch 1: YOLO
            "t. ! queue ! avdec_h264 ! videoconvert ! video/x-raw,format=BGR ! "
            "appsink name=appsink emit-signals=true sync=false max-buffers=2 drop=true "

            # Branch 2: AWS Kinesis WebRTC
            "t. ! queue ! valve name=stream_valve drop=True ! "
            "x264enc bitrate=512 tune=zerolatency speed-preset=ultrafast ! "
            "video/x-h264,profile=baseline ! "
            "awskvswebrtcsink name=ws aws-region=eu-west-1 "
        )

        self._video_pipe = Gst.parse_launch(command)
        self._video_pipe.set_state(Gst.State.PLAYING)
        self._video_sink = self._video_pipe.get_by_name("appsink")

        self._valve = self._video_pipe.get_by_name("stream_valve")
        self._ws_sink = self._video_pipe.get_by_name("ws")

        self._handler = self._video_sink.connect("new-sample", self._decode_frame)
        self._task = asyncio.create_task(self._start_detection())

    def set_streaming_enabled(self, enabled: bool):
        if self._valve:
            self._valve.set_property("drop", not enabled)

    def update_aws_credentials(self, creds: CredentialsModel):
        if self._ws_sink:
            self._ws_sink.set_property("access-key", creds.access_key_id)
            self._ws_sink.set_property("secret-key", creds.secret_access_key)
            self._ws_sink.set_property("session-token", creds.session_token)

    async def stop(self):
        self._running = False

        if self._task:
            await self._task

        self._video_sink.disconnect(self._handler)
        self._video_pipe.set_state(Gst.State.NULL)

        self._video_pipe = None
        self._video_sink = None

        self._frame_count = 0
        self._last_process_time = 0
        self._consecutive_detection_frames = 0

    def _decode_frame(self, sink):
        if not self._running:
            return Gst.FlowReturn.OK

        sample = sink.emit("pull-sample")
        buf = sample.get_buffer()
        caps = sample.get_caps()

        self._frame = np.ndarray(
            (
                caps.get_structure(0).get_value("height"),
                caps.get_structure(0).get_value("width"),
                3,
            ),
            buffer=buf.extract_dup(0, buf.get_size()),
            dtype=np.uint8,
        )

        return Gst.FlowReturn.OK

    async def _start_detection(self):
        while self._running:
            if type(self._frame) == type(None):
                await asyncio.sleep(0.001)
                continue

            current_time = time.time()

            if self._frame_count % self.sample_rate != 0:
                await asyncio.sleep(0)
                continue

            if current_time - self._last_process_time < self._min_interval:
                await asyncio.sleep(0)
                continue

            detection, results = self._run_human_detection(self._frame)
            if detection:
                self._consecutive_detection_frames += 1

                if (
                    self._consecutive_detection_frames
                    == self.presence_confirmation_frames
                ):
                    self._send_detection_alert()
                    self._consecutive_detection_frames = 0
                    continue

            else:
                self._consecutive_detection_frames = 0

            self._last_process_time = current_time
            await asyncio.sleep(0)

    def _run_human_detection(self, frame) -> Tuple[bool, list[AnyType] | None]:
        results = self.model(frame, verbose=False)

        for result in results:
            boxes = result.boxes
            for box in boxes:
                class_id = int(box.cls[0])
                confidence = float(box.conf[0])
                # xyxy = box.xyxy[0].cpu().numpy()
                # x1, y1, x2, y2 = map(int, xyxy)

                if class_id == 0 and self.confidence_threshold <= confidence:
                    return True, results

        return False, None

    def _send_detection_alert(self):
        loguru.logger.debug("[W.I.P] Placeholder detection alert!")
