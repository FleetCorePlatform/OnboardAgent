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
# - Extraction of the SerialTransport class (originally lines ~260-400).
# - Integration with the loguru logging framework.
# - Architectural restructuring and typing definition additions for the
#   FleetCore OnboardAgent project.
#
############################################################################

from typing import Optional

import serial
from loguru import logger
from src.exceptions.upload_exception import ConnectionError, TimeoutError

class SerialTransport:
    """Handles serial port communication with proper resource management.

    Provides context manager support for automatic cleanup and configurable
    timeouts.
    """

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        timeout: float = 0.5,
        write_timeout: float = 2.0,
    ):
        """Initialize serial transport.

        Args:
            port: Serial port path (e.g., /dev/ttyUSB0, COM3)
            baudrate: Baud rate for communication
            timeout: Read timeout in seconds
            write_timeout: Write timeout in seconds (0 = no timeout)
        """
        self._port = None
        self.port_name = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.write_timeout = write_timeout
        self._chartime = 10.0 / baudrate  # 8N1 = 10 bits per byte

    def __enter__(self) -> "SerialTransport":
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def open(self) -> None:
        """Open the serial port."""
        if self._port is not None and self._port.is_open:
            return

        logger.debug(f"Opening serial port {self.port_name} at {self.baudrate} baud")

        try:
            self._port = serial.Serial(
                self.port_name,
                self.baudrate,
                timeout=self.timeout,
                write_timeout=self.write_timeout,
            )
        except serial.SerialException as e:
            raise ConnectionError(
                f"Cannot open serial port: {e}", port=self.port_name, operation="open"
            )

    def close(self) -> None:
        """Close the serial port."""
        if self._port is not None:
            logger.debug(f"Closing serial port {self.port_name}")
            try:
                self._port.close()
            except Exception as e:
                logger.warning(f"Error closing port {self.port_name}: {e}")
            self._port = None

    @property
    def is_open(self) -> bool:
        """Check if port is open."""
        return self._port is not None and self._port.is_open

    def send(self, data: bytes) -> None:
        """Send data over serial port.

        Args:
            data: Bytes to send

        Raises:
            ConnectionError: If send fails
        """
        if not self.is_open:
            raise ConnectionError(
                "Port not open", port=self.port_name, operation="send"
            )

        logger.debug(f"TX: {data.hex()}")

        try:
            self._port.write(data)
        except serial.SerialException as e:
            raise ConnectionError(
                f"Write failed: {e}", port=self.port_name, operation="send"
            )

    def recv(self, count: int = 1, timeout: Optional[float] = None) -> bytes:
        """Receive data from serial port.

        Args:
            count: Number of bytes to receive
            timeout: Override default timeout

        Returns:
            Received bytes

        Raises:
            TimeoutError: If timeout expires before all bytes received
            ConnectionError: If read fails
        """
        if not self.is_open:
            raise ConnectionError(
                "Port not open", port=self.port_name, operation="recv"
            )

        old_timeout = self._port.timeout
        if timeout is not None:
            self._port.timeout = timeout

        try:
            data = self._port.read(count)
        except serial.SerialException as e:
            raise ConnectionError(
                f"Read failed: {e}", port=self.port_name, operation="recv"
            )
        finally:
            if timeout is not None:
                self._port.timeout = old_timeout

        if len(data) < count:
            raise TimeoutError(
                f"Timeout waiting for {count} bytes, got {len(data)}",
                port=self.port_name,
                operation="recv",
            )

        logger.debug(f"RX: {data.hex()}")
        return data

    def flush(self) -> None:
        """Flush output buffer."""
        if self._port is not None:
            self._port.flush()

    def reset_buffers(self) -> None:
        """Reset input and output buffers."""
        if self._port is not None:
            self._port.reset_input_buffer()
            self._port.reset_output_buffer()

    def set_baudrate(self, baudrate: int) -> None:
        """Change baud rate.

        Args:
            baudrate: New baud rate
        """
        logger.debug(f"Changing baudrate to {baudrate}")
        self.baudrate = baudrate
        self._chartime = 10.0 / baudrate

        if self._port is not None:
            try:
                self._port.baudrate = baudrate
            except (serial.SerialException, NotImplementedError) as e:
                logger.debug(f"Cannot change baudrate: {e}")
                raise

    @property
    def chartime(self) -> float:
        """Time to transmit one character."""
        return self._chartime