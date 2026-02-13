import argparse
import asyncio
import sys
from typing import Optional

from loguru import logger

from src.containers import ApplicationContainer
from src.exceptions.config_exceptions import ConfigException


def main(config_path: Optional[str] = None):
    # 1. Setup Loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # 2. Initialize Container
    container = ApplicationContainer(
        event_loop=loop,
        config_path=config_path,
    )

    # 3. Setup Logging
    try:
        config = container.config()
        if config.verbose:
            logger.remove()
            logger.add(sys.stdout, level="DEBUG")
    except ConfigException as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    # 4. Wire & Run
    stream_handler = container.stream_handler()
    # coordinator = container.coordinator()

    try:
        # Start core services
        loop.run_until_complete(stream_handler.start())

        # Enable streaming (will fetch creds automatically)
        loop.run_until_complete(stream_handler.set_streaming_state(True))

        # Start coordinator (when ready)
        # loop.run_until_complete(coordinator.start())
        # loop.run_until_complete(coordinator.run())

        loop.run_forever()
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        # Graceful shutdown
        if "stream_handler" in locals():
            loop.run_until_complete(stream_handler.stop())

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
