import argparse
import asyncio
import sys
from typing import Optional

from loguru import logger

from src.config import Config
from src.coordinator import JobCoordinator
from src.core.credential_provider import CredentialProvider
from src.core.drone_controller import MavsdkController
from src.core.mqtt_manager import MqttManager
from src.core.state_machine import StateMachine
from src.core.stream_handler import StreamHandler
from src.exceptions.config_exceptions import ConfigException
from src.telemetry.collector import TelemetryCollector
from src.telemetry.publisher import TelemetryPublisher


def main(config_path: Optional[str] = None):
    try:
        config: Config = Config(config_path)
        if config.verbose:
            logger.remove()
            logger.add(sys.stdout, level="DEBUG")
    except ConfigException as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    mqtt = MqttManager(
        cert_path=config.cert_filepath,
        private_key_path=config.pri_key_filepath,
        ca_file_path=config.ca_filepath,
        endpoint=config.endpoint,
        thing_name=config.thing_name,
        timeout=30,
    )

    drone = MavsdkController(
        address=config.drone_address,
        port=config.drone_port,
        protocol=config.drone_connection_type,
    )

    state = StateMachine()

    telemetry_collector = TelemetryCollector(
        device_name=config.thing_name,
        drone=drone.system,
        interval_hz=config.telemetry_sample_interval,
    )

    telemetry_publisher = TelemetryPublisher(
        collector=telemetry_collector,
        mqtt=mqtt,
        topic=config.telemetry_topic,
        batch_size=config.telemetry_sample_count,
    )

    stream_handler = StreamHandler(
        port=config.stream_port,
        yolo_path=config.yolo_model_path,
        sample_rate=config.stream_sample_rate,
        mqtt=mqtt,
        alert_topic=config.alert_topic,
        presence_confirmation_frames=config.presence_confirmation_frames,
        confidence_threshold=config.detection_confidence_threshold,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    coordinator = JobCoordinator(
        config=config,
        mqtt=mqtt,
        drone=drone,
        state=state,
        collector=telemetry_collector,
        publisher=telemetry_publisher,
        recognition=stream_handler,
        loop=loop,
    )

    c = CredentialProvider(
        config.cert_filepath,
        config.pri_key_filepath,
        config.ca_filepath,
        config.role_alias,
        config.thing_name,
    )
    print(c.get_credentials())

    # try:
    #     loop.run_until_complete(coordinator.start())
    #     loop.run_until_complete(coordinator.run())  # Blocks until shutdown
    # except KeyboardInterrupt:
    #     logger.info("Shutdown requested")
    # except Exception as e:
    #     logger.error(f"Fatal error: {e}")
    #     sys.exit(1)
    # finally:
    #     loop.run_until_complete(coordinator.stop())
    #     loop.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "config",
        help="The .config.env configuration file",
        type=str,
        default=None,
        nargs="?",
    )
    args = parser.parse_args()

    env_file: Optional[str] | None = args.config

    main(env_file)
    sys.exit(0)
