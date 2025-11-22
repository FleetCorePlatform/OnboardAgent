import asyncio
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
        recognition: StreamHandler,
        loop: asyncio.AbstractEventLoop,
    ):
        self.config = config
        self.mqtt = mqtt
        self.drone = drone
        self.state = state
        self.telemetry_collector = collector
        self.telemetry_publisher = publisher
        self.recognition = recognition
        self.loop = loop
        self.current_job_id: Optional[str] = None
        self.job_document: Optional[Job] = None

    async def start(self):
        try:
            await self.mqtt.connect()
            logger.info("MQTT connected")
        except MqttConnectionException as e:
            logger.error(e)
            raise

        self.mqtt.subscribe(self.config.internal_topic, self._job_notification_handler)

        asyncio.run_coroutine_threadsafe(self._process_next_job(), self.loop)

    async def run(self):
        while True:
            await asyncio.sleep(1)

    def _job_notification_handler(self, topic, payload, **kwargs):
        """Handle job notification from MQTT."""
        if self.state.get_state() != ExecutionState.IDLE:
            logger.warning("Job notification received but system not idle, ignoring")
            return

        asyncio.run_coroutine_threadsafe(self._process_next_job(), self.loop)

    async def _process_next_job(self):
        """Fetch and process next queued job."""
        try:
            next_job = self.mqtt.get_next_queued_job()
            if not next_job:
                logger.warning("No queued jobs")
                return

            await self._execute_job(next_job.job_id)

        except Exception as e:
            logger.error(f"Job processing failed: {e}")
            if self.current_job_id:
                self.mqtt.update_job_status(self.current_job_id, JobStatus.FAILED)
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

        if document.steps[0].action.name != "Download-File":
            logger.warning(f"Unsupported action: {document.steps[0].action.name}")
            self.mqtt.update_job_status(job_id, JobStatus.REJECTED)
            return

        self.mqtt.update_job_status(job_id, JobStatus.IN_PROGRESS)

        try:
            await self._download_mission(document)

            await self._execute_mission()

            self.mqtt.update_job_status(job_id, JobStatus.SUCCEEDED)
            logger.info(f"Job {job_id} completed successfully")

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
        """Download and extract mission file."""
        self.state.trigger("download")

        url = document.steps[0].action.input.args[0]
        filename = document.steps[0].action.input.args[1]

        logger.info(f"Downloading mission from {url}")
        try:
            path = handle_download(url, filename)
        except DownloadNotAllowedFolderException:
            raise Exception("Cannot download to a directory other than /tmp")
        except DownloadException as e:
            raise Exception(f"Download failed {e}")

        logger.info("Download succeeded, extracting mission")
        self.mission_file = extract_mission(path, self.config.thing_name, filename)
        logger.debug(f"Mission file ready: {self.mission_file}")

    async def _execute_mission(self):
        """Connect to drone and execute mission."""
        if not self.mission_file:
            raise Exception("No mission file available")

        try:
            await self.drone.connect()
            logger.info("Connected to mavsdk system")
        except DroneConnectException as e:
            raise Exception(f"Mavsdk system connection failed: {e}")

        self.state.trigger("upload")
        try:
            await self.drone.upload_mission(self.mission_file, return_to_launch=True)
            logger.info("Mission uploaded")
        except DroneUploadException as e:
            raise Exception(f"Mission upload failed: {e}")

        self.state.trigger("arm")
        try:
            await self.drone.arm()
            logger.info("Mavsdk system armed")
        except DroneArmException as e:
            raise Exception(f"Arm failed: {e}")

        self.state.trigger("fly")
        try:
            await self.drone.start_mission()
            logger.info("Mission started")
        except DroneStartMissionException as e:
            raise Exception(f"Mission start failed: {e}")

        logger.debug(f"Starting detection, and telemetry systems..")
        await asyncio.gather(
            self.recognition.start(),
            self.telemetry_publisher.start(),
            self.telemetry_collector.start(),
        )

        try:
            await self._monitor_mission()
        finally:
            await self.recognition.stop()
            await self.telemetry_publisher.stop()
            await self.telemetry_collector.stop()

            logger.debug(
                f"Telemetry samples collected: {self.telemetry_collector.queue.qsize()}"
            )

            logger.debug(f"Telemetry errors: {self.telemetry_collector.error_count}")
            logger.debug(f"Publisher errors: {self.telemetry_publisher.error_count}")
            if self.telemetry_collector.last_error:
                logger.error(
                    f"Last telemetry error: {self.telemetry_collector.last_error}"
                )
            if self.telemetry_publisher.last_error:
                logger.error(
                    f"Last publisher error: {self.telemetry_publisher.last_error}"
                )

    async def _monitor_mission(self):
        """Monitor mission progress until completion."""
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
        """Wait for drone to land after mission."""
        async for in_air in self.drone.stream_in_air():
            if not in_air:
                logger.info("Drone landed")
                self.state.trigger("idle")
                break

    async def stop(self):
        """Shutdown coordinator and cleanup."""
        logger.info("Shutting down coordinator")
        await self.mqtt.disconnect()
        if self.state.get_state() == ExecutionState.IN_FLIGHT:
            await self.drone.cancel_mission()
        logger.info("Coordinator stopped")
