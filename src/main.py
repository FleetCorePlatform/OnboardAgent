import argparse
import asyncio
import sys
from typing import Optional

from loguru import logger

from src.config import Config
from src.coordinator import JobCoordinator
from src.core.drone_controller import MavsdkController
from src.core.mqtt_manager import MqttManager
from src.core.state_machine import StateMachine
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
        config.cert_filepath,
        config.pri_key_filepath,
        config.ca_filepath,
        config.endpoint,
        config.thing_name,
        30,
    )

    drone = MavsdkController(
        config.drone_address,
        config.drone_port,
        config.drone_connection_type,
    )

    state = StateMachine()

    telemetry_collector = TelemetryCollector(
        drone.system, interval_hz=config.telemetry_sample_interval
    )

    telemetry_publisher = TelemetryPublisher(
        telemetry_collector,
        mqtt,
        config.telemetry_topic,
        batch_size=config.telemetry_sample_count,
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
        loop=loop,
    )

    try:
        loop.run_until_complete(coordinator.start())
        loop.run_until_complete(coordinator.run())  # Blocks until shutdown
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        loop.run_until_complete(coordinator.stop())
        loop.close()


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
