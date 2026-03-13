import asyncio
import json
import time
from typing import Optional

from loguru import logger

from src.config import Config
from src.core.drone_controller import MavsdkController
from src.core.mqtt_manager import MqttManager
from src.core.state_machine import StateMachine
from src.core.stream_handler import StreamHandler
from src.core.manual_controller import ManualController
from src.enums.execution_state import ExecutionState
from src.enums.job_status import JobStatus
from src.exceptions.download_exceptions import (
    DownloadNotAllowedFolderException,
    DownloadException,
)
from src.exceptions.drone_excetions import (
    DroneUploadException,
    DroneArmException,
    DroneStartMissionException,
    DroneConnectException,
    DroneStreamMissionProgressException,
)
from src.models.job_document import Job
from src.exceptions.mqtt_exceptions import MqttConnectionException
from src.utils.telemetry.collector import TelemetryCollector
from src.utils.telemetry.publisher import TelemetryPublisher
from src.utils.download_handler import handle_download
from src.utils.zip_manager import extract_mission


class JobCoordinator:
    def __init__(
        self,
        config: Config,
        mqtt: MqttManager,
        drone: MavsdkController,
        state: StateMachine,
        collector: TelemetryCollector,
        publisher: TelemetryPublisher,
        streamer: StreamHandler,
        loop: asyncio.AbstractEventLoop,
    ):
        self.config = config
        self.mqtt = mqtt
        self.drone = drone
        self.state = state
        self.telemetry_collector = collector
        self.telemetry_publisher = publisher
        self.streamer = streamer
        self.loop = loop

        self.current_job_id: Optional[str] = None
        self.job_document: Optional[Job] = None
        self.current_task: Optional[asyncio.Task] = None
        self.mission_file: Optional[str] = None

        self._processing = True

        self.manual_controller = ManualController(
            drone=self.drone,
            try_take_control_cb=self._try_take_manual_control,
            release_control_cb=self._release_manual_control,
            send_data_msg=self.streamer.send_data_message,
        )
        self.streamer.set_data_channel_callback(self._on_data_channel_message)
        self.streamer.set_data_channel_open_callback(self._on_data_channel_open)
        self.streamer.set_data_channel_close_callback(self._on_data_channel_close)

        self._last_streaming_command = (None, 0.0)
        self._streaming_command_dedup_window = 0.1

    def _on_data_channel_open(self):
        self.manual_controller.on_datachannel_open()

    def _on_data_channel_close(self):
        self.manual_controller.on_datachannel_close()

    def _try_take_manual_control(self) -> bool:
        try:
            return self.state.trigger("manual")
        except Exception as e:
            logger.warning(f"Manual control transition denied: {e}")
            return False

    def _release_manual_control(self) -> None:
        try:
            self.state.trigger("idle")
        except Exception as e:
            logger.warning(f"Failed to return to idle from manual control: {e}")

    def _on_data_channel_message(self, message: bytes):
        asyncio.run_coroutine_threadsafe(
            self._process_data_channel_message(message), self.loop
        )

    async def _process_data_channel_message(self, message: bytes):
        response = await self.manual_controller.handle_packet(message)
        if response:
            self.streamer.send_data_message(response)

    async def start(self):
        try:
            await self.mqtt.connect()
            logger.info("MQTT connected")
        except MqttConnectionException as e:
            logger.error(e)
            raise

        try:
            logger.debug(f"Subscribing to {self.config.internal_topic}")
            await self.mqtt.subscribe(
                self.config.internal_topic, self._job_notification_handler
            )
        except Exception as e:
            logger.error(
                f"Critical subscription failed for {self.config.internal_topic}: {e}"
            )

        try:
            logger.debug(f"Subscribing to {self.config.streaming_topic}")
            await self.mqtt.subscribe(
                self.config.streaming_topic, self._streaming_command_handler
            )
        except Exception as e:
            logger.error(f"Subscription failed for {self.config.streaming_topic}: {e}")

        logger.info("Startup sequence complete, coordinator running.")

        try:
            await self.drone.connect()
        except DroneConnectException as e:
            raise Exception(f"Mavsdk system connection failed: {e}")

        asyncio.run_coroutine_threadsafe(self._process_next_job(), self.loop)

    async def run(self):
        while True:
            await asyncio.sleep(1)

    def _job_notification_handler(self, topic, payload, **kwargs):
        asyncio.run_coroutine_threadsafe(self._evaluate_incoming_job(), self.loop)

    async def _evaluate_incoming_job(self):
        try:
            next_job_summary = await self.mqtt.get_next_queued_job()
            if not next_job_summary:
                return

            job_desc = await self.mqtt.describe_job(next_job_summary.job_id)
            document = self.mqtt.get_job_document(job_desc)

            if not document:
                return

            is_busy = self.state.get_state() != ExecutionState.IDLE
            is_cancel = document.operation == "CANCEL"

            if is_cancel and is_busy:
                logger.warning(
                    f"INTERRUPT: Cancel job {next_job_summary.job_id} received during execution."
                )
                await self._process_cancel_immediate(next_job_summary.job_id)

            elif not is_busy:
                if self.current_job_id != next_job_summary.job_id:
                    await self._process_next_job()

            else:
                logger.info(
                    f"System busy. Ignoring standard job {next_job_summary.job_id} for now."
                )

        except Exception as e:
            logger.error(f"Failed to evaluate incoming job: {e}")

    def _streaming_command_handler(self, topic, payload, **kwargs):
        try:
            if hasattr(payload, "tobytes"):
                payload_str = payload.tobytes().decode("utf-8")
            elif isinstance(payload, (bytes, bytearray)):
                payload_str = payload.decode("utf-8")
            else:
                payload_str = str(payload)

            payload_str = payload_str.strip()
            logger.debug(f"Raw streaming payload: '{payload_str}'")

            should_stream = False
            if payload_str.startswith("{"):
                try:
                    data = json.loads(payload_str)

                    if "message" in data:
                        msg_val = data["message"]
                        if isinstance(msg_val, str) and msg_val.strip().startswith("{"):
                            data = json.loads(msg_val)
                        elif isinstance(msg_val, dict):
                            data = msg_val

                    should_stream = bool(data.get("enabled", False))
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON in streaming command: {payload_str}")
                    return
            else:
                should_stream = payload_str.lower() in ["true", "1", "on", "enable"]

            logger.info(
                f"Processed streaming command. Setting state to: {should_stream}"
            )

            current_time = time.time()
            last_enabled, last_time = self._last_streaming_command

            if (
                last_enabled == should_stream
                and (current_time - last_time) < self._streaming_command_dedup_window
            ):
                logger.debug(
                    f"Ignoring duplicate streaming command: enabled={should_stream} "
                    f"(last command {(current_time - last_time) * 1000:.1f}ms ago)"
                )
                return

            self._last_streaming_command = (should_stream, current_time)

            asyncio.run_coroutine_threadsafe(
                self.streamer.set_streaming_state(should_stream), self.loop
            )

        except Exception as e:
            logger.error(f"Error processing streaming command: {e}")

    async def _process_cancel_immediate(self, cancel_job_id: str):
        """Interrupts the current task and aborts the drone."""
        logger.info(f"Executing immediate cancellation via job {cancel_job_id}")

        if self.current_task and not self.current_task.done():
            self.current_task.cancel()
            try:
                await self.current_task
            except asyncio.CancelledError:
                logger.info("Current task execution cancelled internally")

        await self._trigger_drone_abort()

        if self.current_job_id:
            await self.mqtt.update_job_status(self.current_job_id, JobStatus.CANCELED)

        await self.mqtt.update_job_status(cancel_job_id, JobStatus.SUCCEEDED)

        self.state.force_reset()
        self.current_job_id = None
        self.current_task = None

    async def _process_next_job(self):
        if not self._processing:
            return

        if self.current_task and not self.current_task.done():
            logger.debug("Job already in progress, skipping..")
            return

        try:
            next_job = await self.mqtt.get_next_queued_job()
            if not next_job:
                logger.debug("No next job, skipping..")
                return

            logger.info(f"Starting job {next_job.job_id}")
            self.current_task = asyncio.create_task(self._execute_job(next_job.job_id))

            try:
                await self.current_task
            except asyncio.CancelledError:
                logger.info("Job execution task was cancelled during operation")

        except Exception as e:
            logger.error(f"Job processing setup failed: {e}")
            self.state.force_reset()

    async def _execute_job(self, job_id: str):
        """Execute a job from start to finish."""
        self.current_job_id = job_id

        job_response = await self.mqtt.describe_job(job_id)
        document = self.mqtt.get_job_document(job_response)

        if not document:
            logger.warning(f"Invalid job document for {job_id}")
            await self.mqtt.update_job_status(job_id, JobStatus.REJECTED)
            return

        self.job_document = document

        match document.operation:
            case "DOWNLOAD":
                await self._execute_download_job(job_id)
            case "CANCEL":
                logger.info("Processed Cancel job while IDLE.")
                await self.mqtt.update_job_status(job_id, JobStatus.SUCCEEDED)
            case _:
                logger.warning(f"Unsupported action: {document.operation}")
                await self.mqtt.update_job_status(job_id, JobStatus.REJECTED)
                return

    async def _execute_download_job(self, job_id: str):
        await self.mqtt.update_job_status(job_id, JobStatus.IN_PROGRESS)

        try:
            await self._download_mission(self.job_document)
            await self._execute_mission()
            await self.mqtt.update_job_status(job_id, JobStatus.SUCCEEDED)
            logger.info(f"Job {job_id} completed successfully")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}")
            await self.mqtt.update_job_status(job_id, JobStatus.FAILED)
            self.state.trigger("error")
        finally:
            self.current_job_id = None
            self.mission_file = None
            if self.state.get_state() != ExecutionState.IDLE:
                self.state.force_reset()

    async def _download_mission(self, document: Job):
        self.state.trigger("download")
        url = document.data.download_url
        download_path = document.data.download_path

        logger.info(f"Downloading mission from {url}")
        try:
            path = await self.loop.run_in_executor(
                None, handle_download, url, download_path
            )
        except DownloadNotAllowedFolderException:
            raise Exception("Cannot download to a directory other than /tmp")
        except DownloadException as e:
            raise Exception(f"Download failed {e}")

        logger.info("Download succeeded, extracting mission")
        self.mission_file = extract_mission(path, self.config.thing_name, download_path)

    async def _execute_mission(self):
        if not self.mission_file:
            raise Exception("No mission file available")

        self.state.trigger("upload")
        try:
            await self.drone.upload_mission(self.mission_file, return_to_launch=True)
        except DroneUploadException as e:
            raise Exception(f"Mission upload failed: {e}")

        self.state.trigger("arm")
        try:
            await self.drone.arm()
        except DroneArmException as e:
            raise Exception(f"Arm failed: {e}")

        self.state.trigger("fly")
        try:
            await self.drone.start_mission()
        except DroneStartMissionException as e:
            raise Exception(f"Mission start failed: {e}")

        if self.job_document and self.job_document.data:
            mission_uuid = self.job_document.data.mission_uuid
            mission_metadata = self.job_document.data.metadata
            self.streamer.set_active_mission_info(mission_uuid, mission_metadata)

        logger.debug(f"Starting detection, and telemetry systems..")
        await asyncio.gather(
            self.streamer.start(),
            self.telemetry_publisher.start(),
            self.telemetry_collector.start(),
        )

        try:
            logger.debug("Waiting for mission to finish")
            await self._monitor_mission()
        finally:
            self.streamer.set_active_mission_info(None, None)
            await self.streamer.stop()
            await self.telemetry_publisher.stop()
            await self.telemetry_collector.stop()

    async def _trigger_drone_abort(self):
        """Sends immediate RTL command to drone."""
        try:
            logger.warning("Sending Mission Cancel / Return to Launch command")
            await self.drone.cancel_mission()
        except Exception as e:
            logger.error(f"Failed to send drone cancel command: {e}")

    async def _monitor_mission(self):
        try:
            async for progress in self.drone.stream_mission_progress():
                logger.debug(f"Mission progress: {progress.current}/{progress.total}")

                if progress.is_complete:
                    self.state.trigger("complete")
                    logger.info("Mission complete, waiting for landing")
                    await self._wait_for_landing()
                    break

        except DroneStreamMissionProgressException as e:
            raise Exception(f"Mission monitoring failed: {e}")

    async def _wait_for_landing(self):
        async for in_air in self.drone.stream_in_air():
            if not in_air:
                logger.info("Drone landed")
                self.state.trigger("idle")
                break

    async def stop(self):
        logger.info("Shutting down coordinator")

        self._processing = False

        if self.current_task and not self.current_task.done():
            self.current_task.cancel()
            try:
                await asyncio.wait_for(self.current_task, timeout=3.0)
            except asyncio.TimeoutError:
                logger.error("Job execution task didn't stop within timeout")
            except asyncio.CancelledError:
                logger.info("Job execution task cancelled")

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    self.streamer.stop(),
                    self.telemetry_publisher.stop(),
                    self.telemetry_collector.stop(),
                    return_exceptions=True,
                ),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            logger.error("Telemetry/streaming shutdown timeout")

        if self.state.get_state() == ExecutionState.IN_FLIGHT:
            try:
                await asyncio.wait_for(self.drone.cancel_mission(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.error("Drone cancel timeout")
            except Exception as e:
                logger.error(f"Failed to cancel drone mission: {e}")

        try:
            await asyncio.wait_for(self.mqtt.disconnect(), timeout=2.0)
        except asyncio.TimeoutError:
            logger.error("MQTT disconnect timeout")
        except Exception as e:
            logger.error(f"MQTT disconnect error: {e}")

        logger.info("Coordinator stopped")
