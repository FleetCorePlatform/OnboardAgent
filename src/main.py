import argparse
import asyncio
import sys
from typing import Optional

from loguru import logger
import warnings

from src.exceptions.aioice_exception_patch import global_exception_handler

from src.containers import ApplicationContainer
from src.exceptions.config_exceptions import ConfigException

warnings.filterwarnings("ignore", category=RuntimeWarning)


def main(config_path: Optional[str] = None):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_exception_handler(global_exception_handler)

    container = ApplicationContainer(
        event_loop=loop,
        config_path=config_path,
    )

    try:
        config = container.config()
        if config.verbose:
            logger.remove()
            logger.add(sys.stdout, level="DEBUG")
    except ConfigException as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    stream_handler = container.stream_handler()
    coordinator = container.coordinator()

    try:
        loop.run_until_complete(coordinator.start())
        loop.run_until_complete(coordinator.run())

        loop.run_forever()
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception:
        logger.exception("Fatal error occurred during execution")
        sys.exit(1)
    finally:
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
