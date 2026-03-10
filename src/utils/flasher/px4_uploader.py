############################################################################
#
#   Copyright (c) 2012-2024 PX4 Development Team. All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in
#    the documentation and/or other materials provided with the
#    distribution.
# 3. Neither the name PX4 nor the names of its contributors may be
#    used to endorse or promote products derived from this software
#    without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
# OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
# AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
############################################################################
#
# MODIFICATION NOTICE:
# This file was extracted and modified from the original PX4 Firmware Uploader
# (Tools/px4_uploader.py) in the PX4-Autopilot repository.
#
# Modifications include:
# - Refactoring of the core FirmwareFlasher orchestrator class (originally lines ~1060-1400).
# - Integration with the loguru logging framework.
# - Architectural restructuring, typing definition additions, and CLI removal
#   for the FleetCore OnboardAgent project.
#
############################################################################

import os
import socket
import sys
import time
from pathlib import Path
from typing import Optional
from loguru import logger

from src.exceptions.upload_exception import (
    FirmwareError,
    BoardMismatchError,
    UploadError,
    ProtocolError,
    SiliconErrataError,
)
from src.utils.flasher.bootloader import BootloaderProtocol
from src.utils.flasher.port_detector import PortDetector
from src.utils.flasher.serial_transport import SerialTransport
from src.utils.flasher.firmware import Firmware


class FirmwareFlasher:
    def __init__(
        self,
        port: Optional[str] = None,
        baud_bootloader: int = 115200,
        baud_flightstack: Optional[list[int]] = None,
        force: bool = False,
        force_erase: bool = False,
        boot_delay: Optional[int] = None,
        use_protocol_splitter: bool = False,
        retry_count: int = 3,
        windowed: bool = False,
    ):
        self._port = port
        self._baud_bootloader = baud_bootloader
        self._baud_flightstack = (
            baud_flightstack if baud_flightstack is not None else [57600]
        )
        self._force = force
        self._force_erase = force_erase
        self._boot_delay = boot_delay
        self._use_protocol_splitter = use_protocol_splitter
        self._retry_count = retry_count
        self._windowed = windowed

        try:
            import serial
            import serial.tools.list_ports
        except ImportError as e:
            logger.error(f"Failed to import pyserial: {e}")
            logger.error("Install it with: python -m pip install pyserial")
            return
        self._port_detector = PortDetector()

    def flash_image(self, firmware_paths: list[Path]):
        if sys.platform.startswith("linux") and os.path.exists(
            "/usr/sbin/ModemManager"
        ):
            logger.warning(
                "ModemManager detected. It may interfere with PX4 devices. Consider: sudo systemctl disable ModemManager"
            )

        try:
            # Keep trying until we find a board or user interrupts
            while True:
                try:
                    if self._upload(firmware_paths):
                        return 0
                except BoardMismatchError:
                    # No suitable firmware for this board
                    return 2
                except (ConnectionError, TimeoutError):
                    # No device found yet, keep trying
                    time.sleep(0.05)
                except UploadError as e:
                    logger.error(f"Error: {e}")
                    return 1
        finally:
            logger.info("Firmware update was successful!")
            return 0

    def _upload(self, firmware_paths: list[Path]) -> bool:
        """Upload firmware to connected board."""
        # Load all firmware files
        firmwares = []
        for path in firmware_paths:
            try:
                fw = Firmware(path)
                firmwares.append(fw)
            except FirmwareError as e:
                logger.error(f"Failed to load {path}: {e}")
                if len(firmware_paths) == 1:
                    raise

        if not firmwares:
            raise FirmwareError("No valid firmware files")

        # Determine ports to try
        if self._port:
            patterns = self._port.split(",")
            ports = self._port_detector.expand_patterns(patterns)
        else:
            ports = self._port_detector.detect_ports()

        if not ports:
            raise ConnectionError("No serial ports found")

        logger.debug(f"Trying ports: {ports}")

        # Send MAVLink release command to GCS
        self._send_gcs_release()

        # Try each port
        last_error = None
        for port in ports:
            try:
                return self._upload_to_port(port, firmwares)
            except BoardMismatchError as e:
                logger.warning(f"Board mismatch on {port}: {e}")
                last_error = e
                continue
            except (ConnectionError, TimeoutError) as e:
                logger.debug(f"Connection failed on {port}: {e}")
                last_error = e
                continue
            except UploadError as e:
                logger.error(f"Upload failed on {port}: {e}")
                raise

        if last_error:
            raise last_error
        raise ConnectionError("No bootloader found on any port")

    def _upload_to_port(self, port: str, firmwares: list[Firmware]) -> bool:
        """Attempt upload on a specific port."""
        logger.debug(f"Trying port {port}")

        transport = SerialTransport(
            port,
            baudrate=self._baud_bootloader,
        )

        try:
            transport.open()
        except ConnectionError:
            return False

        protocol = BootloaderProtocol(
            transport,
            windowed=self._windowed,
        )

        try:
            # Try to identify bootloader
            if not self._try_identify(transport, protocol):
                return False

            # Find matching firmware
            firmware = self._select_firmware(firmwares, protocol)

            # Perform upload
            self._do_upload(protocol, firmware)
            return True

        finally:
            transport.close()

    def _try_identify(
        self, transport: SerialTransport, protocol: BootloaderProtocol
    ) -> bool:
        """Try to identify the bootloader, sending reboot if needed."""
        # First try to identify without reboot
        try:
            protocol.identify()
            logger.info(
                f"Found board {protocol.board_type},{protocol.board_rev} protocol v{protocol.bl_rev} on {transport.port_name}"
            )
            return True
        except (ProtocolError, TimeoutError):
            pass

        # Try rebooting at each baud rate
        for baud in self._baud_flightstack:
            logger.debug(
                f"Attempting reboot on {transport.port_name} at {baud} baud..."
            )

            try:
                transport.set_baudrate(baud)
            except Exception:
                continue

            # Send reboot commands multiple times to increase reliability
            for attempt in range(3):
                try:
                    transport.reset_buffers()
                    transport.send(protocol.MAVLINK_REBOOT_ID0)
                    transport.send(protocol.MAVLINK_REBOOT_ID1)
                    transport.flush()
                    time.sleep(0.1)

                    transport.send(protocol.NSH_INIT)
                    time.sleep(0.05)
                    transport.send(protocol.NSH_REBOOT_BL)
                    transport.flush()
                    time.sleep(0.2)
                except Exception:
                    pass

            # Wait for reboot
            time.sleep(0.5)
            transport.close()
            time.sleep(0.5)

            # Reopen at bootloader baud rate and try to identify
            try:
                transport.set_baudrate(self._baud_bootloader)
                transport.open()
            except Exception:
                continue

            # Try to identify multiple times
            for identify_attempt in range(5):
                try:
                    protocol.identify()
                    logger.info(
                        f"Found board {protocol.board_type},{protocol.board_rev} protocol v{protocol.bl_rev} on {transport.port_name}"
                    )
                    return True
                except (ProtocolError, TimeoutError):
                    time.sleep(0.3)

        return False

    def _select_firmware(
        self, firmwares: list[Firmware], protocol: BootloaderProtocol
    ) -> Firmware:
        """Select appropriate firmware for the board."""
        for fw in firmwares:
            if fw.board_id == protocol.board_type:
                if len(firmwares) > 1:
                    logger.info(f"Using firmware {fw.path}")
                return fw

        if self._force and len(firmwares) == 1:
            logger.warning(
                f"Firmware board_id={firmwares[0].board_id} does not match device board_id={protocol.board_type}. FORCED UPLOAD, FLASHING ANYWAY!"
            )
            return firmwares[0]

        raise BoardMismatchError(
            f"No suitable firmware for board {protocol.board_type}",
            details=f"available: {[fw.board_id for fw in firmwares]}",
        )

    def _do_upload(self, protocol: BootloaderProtocol, firmware: Firmware) -> None:
        """Perform the actual upload sequence."""
        logger.info(
            f"Firmware: board_id={firmware.board_id}, revision={firmware.board_revision}. "
            f"Size: {firmware.image_size} bytes ({firmware.usage_percent:.1f}%). "
            f"Bootloader version: {protocol.version}"
        )

        # Check for silicon errata (bootloader v4 on Pixhawk)
        if protocol.bl_rev == 4 and firmware.board_id == 9:
            if firmware.image_size > 1032192 and not self._force:
                raise SiliconErrataError(
                    "Board uses bootloader v4 and cannot safely flash >1MB. "
                    "Use px4_fmu-v2_default or update the bootloader. "
                    "Use force flag to override if you know the board is safe."
                )

        # Check flash size
        if protocol.fw_maxsize < firmware.image_size:
            raise FirmwareError(
                f"Firmware too large ({firmware.image_size} bytes) for flash ({protocol.fw_maxsize} bytes)"
            )

        # Check for undersized config
        if (
            protocol.bl_rev >= 5
            and protocol.fw_maxsize > firmware.image_maxsize
            and not self._force
        ):
            logger.warning(
                f"Board flash ({protocol.fw_maxsize} bytes) larger than firmware config ({firmware.image_maxsize} bytes)"
            )

        self._print_board_info(protocol)

        protocol.erase(force_full=self._force_erase)
        protocol.program(firmware)
        protocol.verify(firmware)

        if self._boot_delay is not None:
            protocol.set_boot_delay(self._boot_delay)

        protocol.reboot()

    def _print_board_info(self, protocol: BootloaderProtocol) -> None:
        """Log board OTP and chip info."""
        if protocol.sn:
            logger.debug(f"Serial: {protocol.sn.hex()}")
        if protocol.chip_id:
            logger.debug(f"Chip: 0x{protocol.chip_id:08X}")
        if protocol.chip_family:
            logger.debug(f"Family: {protocol.chip_family}")
        if protocol.chip_revision:
            logger.debug(f"Revision: {protocol.chip_revision}")
        logger.debug(
            f"Flash: {protocol.fw_maxsize} bytes. Windowed mode: {'yes' if protocol.windowed_mode else 'no'}"
        )

    def _send_gcs_release(self) -> None:
        """Send UDP message to release serial port from GCS."""
        try:
            heartbeat = bytes.fromhex("fe097001010000000100020c5103033c8a")
            command = bytes.fromhex(
                "fe210101014c0000000000000000000000000000000000"
                "00000000000000803f00000000f6000000008459"
            )

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(heartbeat, ("127.0.0.1", 14550))
            sock.sendto(command, ("127.0.0.1", 14550))
            sock.close()
        except Exception:
            pass
