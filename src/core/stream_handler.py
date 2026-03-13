import asyncio
import io
import json
import time
from datetime import datetime, UTC
from typing import Optional, Tuple, Any, List, Callable, AsyncIterator

import cv2
import gi
import numpy as np
from ultralytics import YOLO

from src.core.kinesis_video_manager import KinesisVideoClient
from src.core.mqtt_manager import MqttManager
from src.core.credential_provider import CredentialProvider
from src.core.upload_manager import UploadManager
from src.enums.detection_object import DetectionObjects
from src.models.drone_coordinates import DroneCoordinates
from src.models.job_document import Metadata
from src.utils.gst_video_track import GstVideoTrack
from loguru import logger

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
        kvs_client_factory: Callable[..., KinesisVideoClient],
        credential_provider: CredentialProvider,
        upload_manager: UploadManager,
        coordinate_stream: Callable[[], AsyncIterator[DroneCoordinates]],
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
        self._kvs_client_factory = kvs_client_factory
        self._credential_provider = credential_provider
        self._upload_manager = upload_manager
        self._data_channel_callback = None
        self._data_channel_open_callback = None
        self._data_channel_close_callback = None

        self.__coordinate_stream = coordinate_stream

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
        self._state_lock = asyncio.Lock()

        self._streaming_enabled = False
        self._streaming_transition_in_progress = False
        self._last_streaming_command = (None, 0.0)
        self._command_dedup_window = 0.05

    def set_data_channel_callback(self, cb: Callable):
        self._data_channel_callback = cb

    def set_data_channel_open_callback(self, cb: Callable):
        self._data_channel_open_callback = cb

    def set_data_channel_close_callback(self, cb: Callable):
        self._data_channel_close_callback = cb

    def send_data_message(self, message: bytes):
        if self._kvs_client:
            self._kvs_client.send_data_message(message)

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
            "decodebin ! videoconvert ! video/x-raw,format=BGR ! "
            "appsink name=appsink emit-signals=true sync=false max-buffers=2 drop=true "
        )

        logger.info(f"Launching Pipeline: {command}")
        self._video_pipe = Gst.parse_launch(command)

        import threading
        from gi.repository import GLib

        self._loop = GLib.MainLoop()
        self._loop_thread = threading.Thread(target=self._loop.run, daemon=True)
        self._loop_thread.start()

        bus = self._video_pipe.get_bus()
        bus.add_signal_watch()

        def on_message(bus, message):
            t = message.type
            if t == Gst.MessageType.ERROR:
                err, debug = message.parse_error()
                logger.error(f"GStreamer Pipeline Error: {err}, {debug}")
            elif t == Gst.MessageType.WARNING:
                err, debug = message.parse_warning()
                logger.warning(f"GStreamer Pipeline Warning: {err}, {debug}")

        bus.connect("message", on_message)

        self._video_pipe.set_state(Gst.State.PLAYING)

        self._video_sink = self._video_pipe.get_by_name("appsink")
        self._handler = self._video_sink.connect("new-sample", self._decode_frame)

        self._task = asyncio.create_task(self._start_detection())

    async def set_streaming_state(self, enabled: bool):
        async with self._state_lock:
            current_time = time.time()
            last_enabled, last_time = self._last_streaming_command

            if (
                last_enabled == enabled
                and (current_time - last_time) < self._command_dedup_window
            ):
                logger.debug(
                    f"Ignoring duplicate streaming command: enabled={enabled} "
                    f"(last command {(current_time - last_time) * 1000:.1f}ms ago)"
                )
                return

            self._last_streaming_command = (enabled, current_time)

            if self._streaming_transition_in_progress:
                logger.warning(
                    f"Streaming state transition already in progress, "
                    f"ignoring request to set enabled={enabled}"
                )
                return

            self._streaming_transition_in_progress = True

            try:
                if enabled:
                    await self._enable_streaming()
                else:
                    await self._disable_streaming()

            except Exception as e:
                logger.exception(f"Error during streaming state transition: {e}")
            finally:
                self._streaming_transition_in_progress = False

    async def _enable_streaming(self):
        """Enable streaming and start WebRTC."""
        logger.debug("Enabling streaming...")

        if self._webrtc_task and not self._webrtc_task.done():
            logger.warning("WebRTC task already running")
            return

        if not self._running:
            logger.info("GStreamer pipeline not running. Starting it now...")
            await self.start()

        if not self._gst_track:
            logger.info("Initializing GstVideoTrack for streaming...")
            self._gst_track = GstVideoTrack()

        logger.debug("Requesting credentials for WebRTC...")
        try:
            creds = await asyncio.to_thread(self._credential_provider.get_credentials)
            logger.debug("Credentials obtained successfully.")
        except Exception as e:
            logger.error(f"Failed to obtain credentials: {e}")
            raise

        try:
            self._kvs_client = self._kvs_client_factory(
                credentials=creds,
                video_track=self._gst_track,
                data_channel_callback=self._data_channel_callback,
                data_channel_open_callback=self._data_channel_open_callback,
                data_channel_close_callback=self._data_channel_close_callback,
            )
        except Exception as e:
            logger.error(
                f"Failed to initialize KVS client (is the signaling channel created?): {e}"
            )
            self._webrtc_task = None
            raise

        self._webrtc_task = asyncio.create_task(self._kvs_client.run())
        logger.info("Streaming started")

    async def _disable_streaming(self):
        """Disable streaming and stop WebRTC."""
        logger.debug("Disabling streaming...")

        if not self._webrtc_task or self._webrtc_task.done():
            logger.debug("WebRTC task not running")
            return

        if self._kvs_client:
            try:
                logger.debug("Gracefully stopping KVS client...")
                await asyncio.wait_for(self._kvs_client.stop(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("KVS client stop timeout")
            except Exception as e:
                logger.error(f"Error stopping KVS client: {e}")

        try:
            await asyncio.wait_for(self._webrtc_task, timeout=2.0)
        except asyncio.TimeoutError:
            logger.warning("WebRTC task still running, cancelling...")
            self._webrtc_task.cancel()
            try:
                await self._webrtc_task
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error waiting for webrtc task: {e}")

        self._kvs_client = None
        logger.info("Streaming stopped")

    async def stop(self):
        logger.debug("Stopping stream_handler: setting running to False")
        self._running = False

        try:
            await asyncio.wait_for(self.set_streaming_state(False), timeout=3.0)
        except asyncio.TimeoutError:
            logger.error("Streaming state change timeout during stop")
        except Exception as e:
            logger.error(f"Error disabling streaming: {e}")

        logger.debug("Stopping stream_handler: stopping GStreamer pipeline")
        if self._video_sink and self._handler:
            self._video_sink.disconnect(self._handler)

        if self._video_pipe:
            self._video_pipe.set_state(Gst.State.NULL)

        logger.debug("Stopping stream_handler: quitting GLib loop")
        if hasattr(self, "_loop") and self._loop.is_running():
            self._loop.quit()

        if self._gst_track:
            self._gst_track.stop()

        if self._task:
            if not self._task.done():
                logger.debug("Cancelling detection pipeline")
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    logger.info(
                        "Stopping stream_handler task was successfully cancelled"
                    )
                except Exception as e:
                    logger.error(f"Stopping stream_handler task raised {e}")

        self._video_pipe = None
        self._video_sink = None
        logger.info("Stopping stream_handler: done")

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
            if self._frame_count % 30 == 0:
                logger.trace(f"Frame {self._frame_count} received from GStreamer")

            if self._gst_track:
                self._gst_track.update_frame(array)

        except Exception as e:
            logger.warning(f"Frame decode error: {e}")

        return Gst.FlowReturn.OK

    async def _start_detection(self):
        try:
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

                detection, detected_object, confidence, _ = await asyncio.to_thread(
                    self._run_human_detection, self._frame.copy()
                )
                if detection:
                    self._consecutive_detection_frames += 1
                    if (
                        self._consecutive_detection_frames
                        == self._presence_confirmation_frames
                    ):
                        if self._current_mission_uuid:
                            asyncio.create_task(
                                self._send_detection_alert(
                                    self._frame.copy(),
                                    self._current_mission_uuid,
                                    detected_object,
                                    confidence,
                                )
                            )
                        self._consecutive_detection_frames = 0
                else:
                    self._consecutive_detection_frames = 0

                self._last_process_time = current_time
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            logger.info("_start_detection task cancelled.")
            raise

    def _run_human_detection(
        self, frame
    ) -> Tuple[bool, str, float, Optional[List[Any]]]:
        try:
            results = self._model(frame, verbose=False)
            for result in results:
                for box in result.boxes:
                    cls = int(box.cls[0])
                    conf = float(box.conf[0])
                    if (
                        cls in DetectionObjects.values()
                        and conf >= self._confidence_threshold
                    ):
                        return True, DetectionObjects.get_name(cls), conf, results
        except Exception as e:
            logger.error(f"Inference error: {e}")

        return False, "none", 0.0, None

    async def _send_detection_alert(
        self,
        frame: np.ndarray,
        mission_uuid: str,
        detected_type: str,
        confidence: float,
    ) -> None:
        timestamp = int(time.time())
        file_name = f"{timestamp}_detection.jpg"
        s3_key = f"detections/{self._current_mission_metadata.outpost}/{self._current_mission_metadata.group}/mission/{mission_uuid}/{self._device_name}/{file_name}"

        try:
            coordinates = await self.__coordinate_stream().__anext__()
            location = {
                "lat": coordinates.latitude_deg,
                "lng": coordinates.longitude_deg,
            }
        except StopAsyncIteration:
            location = None

        self._mqtt_manager.publish(
            topic=self._alert_topic,
            message=json.dumps(
                {
                    "mission_uuid": mission_uuid,
                    "detected_by_drone_uuid": self._device_name,
                    "object": detected_type,
                    "confidence": confidence,
                    "detected_at": datetime.now(UTC).isoformat(
                        sep=" ", timespec="microseconds"
                    ),
                    "location": {
                        "lat": location.latitude_deg,
                        "lng": location.longitude_deg,
                    },
                    "image_key": s3_key,
                }
            ),
        )
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
            logger.info(f"Frame uploaded to {s3_key}")
        except Exception as e:
            logger.error(f"Upload failed: {e}")
