**main.py** - Parse CLI arguments, instantiate DroneJobCoordinator, start event loop, handle graceful shutdown on SIGINT/SIGTERM

**coordinator.py** - DroneJobCoordinator class owns all subsystems (MQTT, drone, state machine, telemetry). Receives job notifications, delegates to processor, manages execution lifecycle, coordinates shutdown

**config.py** - Load .config.env, validate required fields, expose typed configuration object (thing_name, drone_address, cert paths, etc.)

**core/drone_controller.py** - Async wrapper around MavsdkController. Handles connect, upload_mission, arm, start_mission, mission completion subscription. Manages connection failures and retry logic

**core/mqtt_manager.py** - Unified async MQTT client. Wraps IoTJobsClient and IoTBaseClient. Exposes subscribe/publish methods, manages connection lifecycle, converts callbacks to async/await interface

**core/state_machine.py** - ExecutionState enum (IDLE, DOWNLOADING, UPLOADING, ARMED, IN_FLIGHT, COMPLETING, ERROR). StateManager validates transitions, prevents invalid state changes, emits state change events

**enums/connection_types.py** - Existing enum for drone connection types (serial, UDP, etc.)

**enums/execution_state.py** - States for mission execution flow: IDLE → DOWNLOADING → UPLOADING → ARMED → IN_FLIGHT → COMPLETING → IDLE | ERROR

**enums/job_status.py** - AWS IoT job statuses: QUEUED, IN_PROGRESS, SUCCEEDED, FAILED, REJECTED

**jobs/processor.py** - JobProcessor class receives job documents, validates structure, extracts job type, instantiates appropriate command class, passes to coordinator for execution

**jobs/status_reporter.py** - Updates job execution status to AWS IoT. Handles IN_PROGRESS, SUCCEEDED, FAILED, REJECTED updates. Manages retry logic for network failures

**jobs/commands/base.py** - JobCommand abstract base class. Defines interface: async execute(context), async rollback(), timeout_seconds property. All job commands inherit from this

**jobs/commands/download_mission.py** - DownloadMissionCommand handles file download from S3/HTTP, validates integrity, extracts ZIP contents, returns mission file path. Updates state to DOWNLOADING

**jobs/commands/execute_mission.py** - ExecuteMissionCommand uploads mission to drone, arms, starts mission, subscribes to completion events. Updates state through UPLOADING → ARMED → IN_FLIGHT → COMPLETING

**models/execution_context.py** - ExecutionContext dataclass holds current execution state: job_id, mission_file_path, start_time, watchdog_task reference. Passed between coordinator and commands

**models/job_document.py** - Existing dataclass definitions for AWS IoT job document structure

**models/telemetry_data.py** - Existing dataclasses for TelemetryData, Battery, Position, Health

**telemetry/collector.py** - Async task subscribes to drone telemetry streams (battery, GPS, health). Formats into TelemetryData objects, pushes to queue for publishing

**telemetry/publisher.py** - Consumes telemetry queue, batches messages, publishes to MQTT telemetry topic at fixed intervals (e.g., 1Hz). Handles publish failures

**utils/download_handler.py** - Existing function handles HTTP/S3 file downloads

**utils/watchdog.py** - Async timeout enforcer. Wraps command execution with asyncio.wait_for. On timeout: cancels task, forces state reset to IDLE, updates job to FAILED

**utils/zip_manager.py** - Existing function extracts mission files from ZIP archives
