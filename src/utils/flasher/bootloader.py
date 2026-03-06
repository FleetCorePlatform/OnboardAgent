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
# - Extraction of the BootloaderProtocol class (originally lines ~400-920).
# - Integration with the loguru logging framework.
# - Architectural restructuring and typing definition additions for the
#   FleetCore OnboardAgent project.
#
############################################################################

import struct
import time
from typing import Optional

from dns import serial
from loguru import logger

from src.enums.uploader_enums import BootloaderResponse, BootloaderCommand, DeviceInfo
from src.exceptions.upload_exception import ProtocolError, SiliconErrataError
from src.models.uploader_models import ProtocolConfig
from src.utils.flasher.serial_transport import SerialTransport
from src.utils.flasher.firmware import Firmware


class BootloaderProtocol:
    """Implements the PX4 bootloader protocol.

    Handles all communication with the bootloader including sync,
    identification, programming, and verification.
    """

    # Reboot command sequences
    NSH_INIT: bytes = b"\r\r\r"
    NSH_REBOOT_BL: bytes = b"reboot -b\n"
    NSH_REBOOT: bytes = b"reboot\n"

    # MAVLink reboot commands (MAVLink v1 COMMAND_LONG with MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN)
    MAVLINK_REBOOT_ID1 = bytes.fromhex(
        "fe2172ff004c00004040000000000000000000000000"
        "000000000000000000000000f600010000536b"
    )
    MAVLINK_REBOOT_ID0 = bytes.fromhex(
        "fe2145ff004c00004040000000000000000000000000"
        "000000000000000000000000f600000000cc37"
    )

    def __init__(
        self,
        transport: SerialTransport,
        sync_timeout: float = 0.5,
        erase_timeout: float = 30.0,
        windowed: bool = False,
    ):
        """Initialize bootloader protocol handler.

        Args:
            transport: Serial transport instance
            sync_timeout: Timeout for sync operations
            erase_timeout: Timeout for chip erase
            windowed: Use windowed mode for faster uploads on real serial ports
        """
        self.transport = transport
        self.sync_timeout = sync_timeout
        self.erase_timeout = erase_timeout

        # Board info (populated by identify())
        self.bl_rev: int = 0
        self.board_type: int = 0
        self.board_rev: int = 0
        self.fw_maxsize: int = 0
        self.version: str = "unknown"
        self.otp: bytes = b""
        self.sn: bytes = b""
        self.chip_id: int = 0
        self.chip_family: str = ""
        self.chip_revision: str = ""

        # Windowed mode for faster uploads on some interfaces
        self.windowed_mode = windowed
        self._window_size = 0
        self._window_max = 256
        self._window_per = 2  # SYNC + result per block

    def _send_command(self, cmd: int, *args: bytes) -> None:
        """Send a command to the bootloader.

        Args:
            cmd: Command byte
            *args: Additional data bytes
        """
        data = bytes([cmd]) + b"".join(args) + bytes([BootloaderResponse.EOC])
        self.transport.send(data)

    def _recv_int(self) -> int:
        """Receive a 32-bit little-endian integer."""
        raw = self.transport.recv(4)
        return struct.unpack("<I", raw)[0]

    def _get_sync(self, flush: bool = True) -> None:
        """Wait for and validate sync response.

        Args:
            flush: Whether to flush output buffer first

        Raises:
            ProtocolError: If response is not valid INSYNC + OK
        """
        if flush:
            self.transport.flush()

        insync = self.transport.recv(1)
        if insync[0] != BootloaderResponse.INSYNC:
            raise ProtocolError(
                f"Expected INSYNC (0x{BootloaderResponse.INSYNC:02X}), "
                f"got 0x{insync[0]:02X}",
                port=self.transport.port_name,
                operation="sync",
            )

        result = self.transport.recv(1)
        if result[0] == BootloaderResponse.INVALID:
            raise ProtocolError(
                "Bootloader reports INVALID OPERATION", port=self.transport.port_name
            )
        if result[0] == BootloaderResponse.FAILED:
            raise ProtocolError(
                "Bootloader reports OPERATION FAILED", port=self.transport.port_name
            )
        if result[0] == BootloaderResponse.BAD_SILICON_REV:
            raise SiliconErrataError(
                "Chip has silicon errata, programming not supported.\n"
                "See https://docs.px4.io/main/en/flight_controller/silicon_errata.html",
                port=self.transport.port_name,
            )
        if result[0] != BootloaderResponse.OK:
            raise ProtocolError(
                f"Expected OK (0x{BootloaderResponse.OK:02X}), got 0x{result[0]:02X}",
                port=self.transport.port_name,
            )

    def _try_sync(self) -> bool:
        """Attempt to get sync without raising exceptions.

        Returns:
            True if sync successful, False otherwise
        """
        try:
            self.transport.flush()
            insync = self.transport.recv(1, timeout=0.1)
            if insync[0] != BootloaderResponse.INSYNC:
                return False
            result = self.transport.recv(1, timeout=0.1)
            if result[0] == BootloaderResponse.BAD_SILICON_REV:
                raise SiliconErrataError(
                    "Chip has silicon errata, programming not supported",
                    port=self.transport.port_name,
                )
            return result[0] == BootloaderResponse.OK
        except TimeoutError:
            return False
        except Exception as e:
            logger.debug(f"Sync attempt failed: {e}")
            return False

    def _validate_sync_window(self, count: int) -> None:
        """Validate multiple sync responses for windowed mode.

        Args:
            count: Number of sync responses to validate (each is 2 bytes)
        """
        if count <= 0:
            return

        data = self.transport.recv(count)
        if len(data) != count:
            raise ProtocolError(
                f"Expected {count} bytes, got {len(data)}",
                port=self.transport.port_name,
                operation="ack_window",
            )

        for i in range(0, len(data), 2):
            if data[i] != BootloaderResponse.INSYNC:
                raise ProtocolError(
                    f"Expected INSYNC at byte {i}, got 0x{data[i]:02X}",
                    port=self.transport.port_name,
                )
            if data[i + 1] == BootloaderResponse.INVALID:
                raise ProtocolError(
                    "Bootloader reports INVALID OPERATION",
                    port=self.transport.port_name,
                )
            if data[i + 1] == BootloaderResponse.FAILED:
                raise ProtocolError(
                    "Bootloader reports OPERATION FAILED", port=self.transport.port_name
                )
            if data[i + 1] != BootloaderResponse.OK:
                raise ProtocolError(
                    f"Expected OK, got 0x{data[i + 1]:02X}",
                    port=self.transport.port_name,
                )

    def _detect_interface_type(self) -> None:
        """Detect if connected via USB CDC or real serial port.

        Currently just resets buffers. Windowed mode can be enabled manually
        with --windowed for real serial ports (FTDI, etc.).
        """
        self.transport.reset_buffers()

    def sync(self) -> None:
        """Synchronize with bootloader.

        Sends sync command and waits for valid response.

        Raises:
            ProtocolError: If sync fails
        """
        logger.debug("Syncing with bootloader")
        self.transport.reset_buffers()
        self._send_command(BootloaderCommand.GET_SYNC)
        self._get_sync()
        logger.debug("Sync successful")

    def _get_device_info(self, param: int) -> int:
        """Get device information parameter.

        Args:
            param: DeviceInfo parameter code

        Returns:
            Parameter value
        """
        self._send_command(BootloaderCommand.GET_DEVICE, bytes([param]))
        value = self._recv_int()
        self._get_sync()
        return value

    def _get_otp(self, address: int) -> bytes:
        """Read 4 bytes from OTP area.

        Args:
            address: OTP address (byte offset)

        Returns:
            4 bytes of OTP data
        """
        self._send_command(BootloaderCommand.GET_OTP, struct.pack("<I", address))
        value = self.transport.recv(4)
        self._get_sync()
        return value

    def _get_sn(self, address: int) -> bytes:
        """Read 4 bytes from serial number area.

        Args:
            address: SN address (byte offset)

        Returns:
            4 bytes of SN data
        """
        self._send_command(BootloaderCommand.GET_SN, struct.pack("<I", address))
        value = self.transport.recv(4)
        self._get_sync()
        return value

    def _get_chip(self) -> int:
        """Get chip ID.

        Returns:
            Chip ID value
        """
        self._send_command(BootloaderCommand.GET_CHIP)
        value = self._recv_int()
        self._get_sync()
        return value

    def _get_chip_description(self) -> tuple[str, str]:
        """Get chip family and revision.

        Returns:
            Tuple of (family, revision) strings
        """
        self._send_command(BootloaderCommand.GET_CHIP_DES)
        length = self._recv_int()
        value = self.transport.recv(length)
        self._get_sync()

        pieces = value.split(b",")
        if len(pieces) >= 2:
            return pieces[0].decode("latin-1"), pieces[1].decode("latin-1")
        return "unknown", "unknown"

    def _get_version(self) -> str:
        """Get bootloader version string.

        Returns:
            Version string or "unknown" if not supported
        """
        self._send_command(BootloaderCommand.GET_VERSION)
        try:
            length = self._recv_int()
            value = self.transport.recv(length)
            self._get_sync()
            return value.decode("utf-8", errors="replace")
        except (TimeoutError, ProtocolError):
            # Older bootloaders don't support this
            return "unknown"

    def identify(self) -> None:
        """Identify the connected board.

        Queries bootloader for board information and stores in instance
        attributes.

        Raises:
            ProtocolError: If identification fails or protocol version unsupported
        """
        logger.info("Identifying board...")

        self._detect_interface_type()
        self.sync()

        # Get bootloader protocol revision
        self.bl_rev = self._get_device_info(DeviceInfo.BL_REV)
        logger.info(f"Bootloader protocol: v{self.bl_rev}")

        if self.bl_rev < ProtocolConfig.BL_REV_MIN:
            raise ProtocolError(
                f"Bootloader protocol {self.bl_rev} too old "
                f"(minimum {ProtocolConfig.BL_REV_MIN})",
                port=self.transport.port_name,
            )
        if self.bl_rev > ProtocolConfig.BL_REV_MAX:
            logger.warning(
                f"Bootloader protocol {self.bl_rev} newer than supported "
                f"({ProtocolConfig.BL_REV_MAX}), proceeding with caution"
            )

        # Get board info
        self.board_type = self._get_device_info(DeviceInfo.BOARD_ID)
        self.board_rev = self._get_device_info(DeviceInfo.BOARD_REV)
        self.fw_maxsize = self._get_device_info(DeviceInfo.FLASH_SIZE)

        logger.info(f"Board type: {self.board_type}, revision: {self.board_rev}")
        logger.info(f"Flash size: {self.fw_maxsize} bytes")

        # Get version string (v5+)
        if self.bl_rev >= 5:
            self.version = self._get_version()
            logger.info(f"Bootloader version: {self.version}")

        # Get OTP and serial number (v4+)
        if self.bl_rev >= 4:
            self._read_otp_and_sn()

        # Get chip info (v5+)
        if self.bl_rev >= 5:
            self._read_chip_info()

    def _read_otp_and_sn(self) -> None:
        """Read OTP and serial number data."""
        # Read OTP (32*6 = 192 bytes)
        otp_data = bytearray()
        for addr in range(0, 32 * 6, 4):
            otp_data.extend(self._get_otp(addr))
        self.otp = bytes(otp_data)

        # Read serial number (12 bytes)
        sn_data = bytearray()
        for addr in range(0, 12, 4):
            sn_bytes = self._get_sn(addr)
            sn_data.extend(sn_bytes[::-1])  # Reverse byte order
        self.sn = bytes(sn_data)

        logger.debug(f"Serial number: {self.sn.hex()}")

        # Try to get chip ID
        try:
            self.chip_id = self._get_chip()
            logger.debug(f"Chip ID: 0x{self.chip_id:08X}")
        except (TimeoutError, ProtocolError) as e:
            logger.debug(f"Could not read chip ID: {e}")

    def _read_chip_info(self) -> None:
        """Read chip family and revision (v5+)."""
        try:
            self.chip_family, self.chip_revision = self._get_chip_description()
            logger.info(f"Chip: {self.chip_family} rev {self.chip_revision}")
        except (TimeoutError, ProtocolError) as e:
            logger.debug(f"Could not read chip description: {e}")

    def erase(
        self, force_full: bool = False, progress_callback: Optional[callable] = None
    ) -> None:
        """Erase the flash memory.

        Args:
            force_full: Force full chip erase (v6+)
            progress_callback: Optional callback(progress, total) for progress

        Raises:
            TimeoutError: If erase times out
            ProtocolError: If erase fails
        """
        logger.debug("Erasing flash")

        if force_full and self.bl_rev >= 6:
            logger.debug("Using full chip erase")
            self._send_command(BootloaderCommand.CHIP_FULL_ERASE)
        else:
            self._send_command(BootloaderCommand.CHIP_ERASE)

        # Erase can take a long time, poll for completion
        deadline = time.monotonic() + self.erase_timeout
        usual_duration = 15.0

        while time.monotonic() < deadline:
            elapsed = time.monotonic() - (deadline - self.erase_timeout)
            remaining = deadline - time.monotonic()

            if progress_callback:
                if remaining >= usual_duration:
                    progress_callback(elapsed, usual_duration)
                else:
                    progress_callback(usual_duration, usual_duration)

            if self._try_sync():
                logger.debug("Erase complete")
                if progress_callback:
                    progress_callback(1.0, 1.0)
                return

        raise TimeoutError(
            f"Erase timed out after {self.erase_timeout}s, port={self.transport.port_name}, action=erase"
        )

    def program(
        self, firmware: Firmware) -> None:
        """Program firmware to flash.

        Args:
            firmware: Firmware instance to program

        Raises:
            ProtocolError: If programming fails
        """
        image = firmware.image
        total = len(image)
        written = 0

        logger.debug(f"Programming {total} bytes")

        # Split image into chunks
        chunk_size = ProtocolConfig.PROG_MULTI_MAX
        chunks = [image[i : i + chunk_size] for i in range(0, total, chunk_size)]

        for i, chunk in enumerate(chunks):
            self._program_multi(chunk)

            if self.windowed_mode:
                self._window_size += self._window_per

                # Periodically validate window
                if (i + 1) % 256 == 0:
                    self._validate_sync_window(self._window_size)
                    self._window_size = 0
            else:
                self._get_sync(flush=False)

            written += len(chunk)

        # Validate any remaining window
        if self.windowed_mode and self._window_size > 0:
            self._validate_sync_window(self._window_size)
            self._window_size = 0

        logger.debug("Programming complete")

    def _program_multi(self, data: bytes) -> None:
        """Program a chunk of data.

        Args:
            data: Bytes to program (max PROG_MULTI_MAX)
        """
        length = len(data)
        cmd = bytes([BootloaderCommand.PROG_MULTI, length]) + data
        cmd += bytes([BootloaderResponse.EOC])
        self.transport.send(cmd)

        if self.windowed_mode:
            # Delay based on transmission time plus flash programming time
            time.sleep(length * self.transport.chartime + 0.001)

    def verify_crc(
        self, firmware: Firmware) -> None:
        """Verify programmed firmware using CRC (v3+).

        Args:
            firmware: Firmware instance to verify against

        Raises:
            ProtocolError: If verification fails
        """
        if self.bl_rev < 3:
            raise ProtocolError(
                "CRC verification requires bootloader v3+",
                port=self.transport.port_name,
            )

        logger.debug("Verifying CRC")

        expected_crc = firmware.crc(self.fw_maxsize)
        logger.debug(f"Expected CRC: 0x{expected_crc:08X}")

        self._send_command(BootloaderCommand.GET_CRC)

        # CRC calculation takes time, especially on larger flash
        time.sleep(0.5)

        reported_crc = self._recv_int()
        self._get_sync()

        logger.debug(f"Reported CRC: 0x{reported_crc:08X}")

        if reported_crc != expected_crc:
            raise ProtocolError(
                f"CRC mismatch: expected 0x{expected_crc:08X}, "
                f"got 0x{reported_crc:08X}",
                port=self.transport.port_name,
                operation="verify",
            )

        logger.debug("CRC verification passed")

    def verify_read(
        self, firmware: Firmware) -> None:
        """Verify programmed firmware by reading back (v2).

        Args:
            firmware: Firmware instance to verify against

        Raises:
            ProtocolError: If verification fails
        """
        logger.debug("Verifying by read-back")

        self._send_command(BootloaderCommand.CHIP_VERIFY)
        self._get_sync()

        image = firmware.image
        total = len(image)
        verified = 0

        chunk_size = ProtocolConfig.READ_MULTI_MAX
        chunks = [image[i : i + chunk_size] for i in range(0, total, chunk_size)]

        for chunk in chunks:
            length = len(chunk)
            cmd = bytes([BootloaderCommand.READ_MULTI, length])
            cmd += bytes([BootloaderResponse.EOC])
            self.transport.send(cmd)
            self.transport.flush()

            readback = self.transport.recv(length)
            self._get_sync()

            if readback != chunk:
                logger.error(f"Verify failed at offset {verified}")
                logger.debug(f"Expected: {chunk.hex()}")
                logger.debug(f"Got:      {readback.hex()}")
                raise ProtocolError(
                    "Verification failed",
                    port=self.transport.port_name,
                    operation="verify",
                )

            verified += length

        logger.debug("Read-back verification passed")

    def verify(
        self, firmware: Firmware) -> None:
        """Verify programmed firmware using appropriate method.

        Uses CRC for v3+ bootloaders, read-back for v2.

        Args:
            firmware: Firmware to verify against
        """
        if self.bl_rev >= 3:
            self.verify_crc(firmware)
        else:
            self.verify_read(firmware)

    def set_boot_delay(self, delay_ms: int) -> None:
        """Set boot delay in flash (v5+).

        Args:
            delay_ms: Boot delay in milliseconds
        """
        if self.bl_rev < 5:
            logger.warning("Boot delay requires bootloader v5+")
            return

        self._send_command(BootloaderCommand.SET_BOOT_DELAY, struct.pack("b", delay_ms))
        self._get_sync()
        logger.info(f"Boot delay set to {delay_ms}ms")

    def reboot(self) -> None:
        """Reboot into the application.

        Raises:
            ProtocolError: If reboot fails (v3+ validates first flash word)
        """
        logger.info("Rebooting to application")
        self._send_command(BootloaderCommand.REBOOT)
        self.transport.flush()

        # v3+ can report failure if first flash word is invalid
        if self.bl_rev >= 3:
            try:
                self._get_sync()
            except TimeoutError:
                # Timeout is expected - board is rebooting
                pass

    def send_reboot_commands(
        self, baudrates: list[int], use_protocol_splitter: bool = False
    ) -> bool:
        """Send reboot commands to try to enter bootloader.

        Tries MAVLink and NSH reboot commands at various baud rates.

        Args:
            baudrates: List of baud rates to try
            use_protocol_splitter: Use protocol splitter framing

        Returns:
            True if commands were sent, False if no more baud rates to try
        """
        for baudrate in baudrates:
            try:
                self.transport.set_baudrate(baudrate)
            except (serial.SerialException, NotImplementedError):
                continue

            logger.info(f"Sending reboot command at {baudrate} baud")

            def send(data: bytes) -> None:
                if use_protocol_splitter:
                    self._send_protocol_splitter_frame(data)
                else:
                    self.transport.send(data)

            try:
                self.transport.flush()
                send(self.MAVLINK_REBOOT_ID0)
                send(self.MAVLINK_REBOOT_ID1)
                send(self.NSH_INIT)
                send(self.NSH_REBOOT_BL)
                send(self.NSH_INIT)
                send(self.NSH_REBOOT)
                self.transport.flush()
            except Exception as e:
                logger.debug(f"Error sending reboot: {e}")
                continue

            return True

        return False

    def _send_protocol_splitter_frame(self, data: bytes) -> None:
        """Send data with protocol splitter framing.

        Header format:
        - Byte 0: Magic ('S' = 0x53)
        - Byte 1: Type (0) | Length high bits (7 bits)
        - Byte 2: Length low bits
        - Byte 3: Checksum (XOR of bytes 0-2)
        """
        magic = 0x53
        len_h = (len(data) >> 8) & 0x7F
        len_l = len(data) & 0xFF
        checksum = magic ^ len_h ^ len_l

        header = bytes([magic, len_h, len_l, checksum])
        self.transport.send(header + data)