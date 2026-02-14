import asyncio
import json
from typing import Optional

from loguru import logger

from src.config import Config
from src.core.drone_controller import MavsdkController
from src.core.mqtt_manager import MqttManager
from src.core.state_machine import StateMachine
from src.core.stream_handler import StreamHandler
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
from src.telemetry.collector import TelemetryCollector
from src.telemetry.publisher import TelemetryPublisher
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

    async def start(self):
        try:
            await self.mqtt.connect()
            logger.info("MQTT connected")
        except MqttConnectionException as e:
            logger.error(e)
            raise

        self.mqtt.subscribe(self.config.internal_topic, self._job_notification_handler)
        self.mqtt.subscribe(
            self.config.streaming_topic, self._streaming_command_handler
        )

        asyncio.run_coroutine_threadsafe(self._process_next_job(), self.loop)

    async def run(self):
        while True:
            await asyncio.sleep(1)

    def _job_notification_handler(self, topic, payload, **kwargs):
        asyncio.run_coroutine_threadsafe(self._evaluate_incoming_job(), self.loop)

    async def _evaluate_incoming_job(self):
        """Peeks at the next queued job to decide if it's a high-priority CANCEL command."""
        try:
            next_job_summary = self.mqtt.get_next_queued_job()
            if not next_job_summary:
                return

            job_desc = self.mqtt.describe_job(next_job_summary.job_id)
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
        """
        Supports payloads: "true", "false", "on", "off", or JSON {"enabled": true}
        """
        try:
            payload_str = (
                payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)
            )
            should_stream = False

            if payload_str.strip().startswith("{"):
                try:
                    data = json.loads(payload_str)
                    should_stream = bool(data.get("enabled", False))
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON in streaming command: {payload_str}")
                    return
            else:
                should_stream = payload_str.lower() in ["true", "1", "on", "enable"]

            logger.info(
                f"Received streaming command. Setting state to: {should_stream}"
            )

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
            self.mqtt.update_job_status(self.current_job_id, JobStatus.CANCELED)

        self.mqtt.update_job_status(cancel_job_id, JobStatus.SUCCEEDED)

        self.state.force_reset()
        self.current_job_id = None
        self.current_task = None

    async def _process_next_job(self):
        """Fetch and process next queued job."""
        if self.current_task and not self.current_task.done():
            logger.debug("Job already in progress, skipping..")
            return

        try:
            next_job = self.mqtt.get_next_queued_job()
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

        job_response = self.mqtt.describe_job(job_id)
        document = self.mqtt.get_job_document(job_response)

        if not document:
            logger.warning(f"Invalid job document for {job_id}")
            self.mqtt.update_job_status(job_id, JobStatus.REJECTED)
            return

        self.job_document = document

        match document.operation:
            case "DOWNLOAD":
                await self._execute_download_job(job_id)
            case "CANCEL":
                logger.info("Processed Cancel job while IDLE.")
                self.mqtt.update_job_status(job_id, JobStatus.SUCCEEDED)
            case _:
                logger.warning(f"Unsupported action: {document.operation}")
                self.mqtt.update_job_status(job_id, JobStatus.REJECTED)
                return

    async def _execute_download_job(self, job_id: str):
        self.mqtt.update_job_status(job_id, JobStatus.IN_PROGRESS)

        try:
            await self._download_mission(self.job_document)
            await self._execute_mission()
            self.mqtt.update_job_status(job_id, JobStatus.SUCCEEDED)
            logger.info(f"Job {job_id} completed successfully")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}")
            self.mqtt.update_job_status(job_id, JobStatus.FAILED)
            self.state.trigger("error")
        finally:
            self.current_job_id = None
            self.mission_file = None
            if self.state.get_state() != ExecutionState.IDLE:
                self.state.force_reset()

    async def _download_mission(self, document: Job):
        self.state.trigger("download")
        url = document.download_url
        download_path = document.download_path

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

        try:
            await self.drone.connect()
        except DroneConnectException as e:
            raise Exception(f"Mavsdk system connection failed: {e}")

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

        mission_uuid = self.job_document.mission_uuid
        mission_metadata = self.job_document.mission_metadata
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
        if self.current_task:
            self.current_task.cancel()
        await self.mqtt.disconnect()
        if self.state.get_state() == ExecutionState.IN_FLIGHT:
            await self.drone.cancel_mission()
        logger.info("Coordinator stopped")
