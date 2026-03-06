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
# - Extraction of the Firmware class (originally lines ~150-260).
# - Integration with the loguru logging framework.
# - Architectural restructuring and typing definition additions for the
#   FleetCore OnboardAgent project.
#
############################################################################

import base64
import binascii
import json
import zlib
from dataclasses import field, dataclass
from pathlib import Path

from loguru import logger

from src.exceptions.upload_exception import FirmwareError


@dataclass
class Firmware:
    """Loads and validates a PX4 firmware file.

    The firmware file is JSON containing metadata and a zlib-compressed,
    base64-encoded firmware image.

    Attributes:
        path: Path to the firmware file
        board_id: Target board ID from firmware metadata
        board_revision: Board revision from metadata
        image: Decompressed firmware binary (padded to 4-byte alignment)
        image_size: Original image size before padding
        image_maxsize: Maximum image size the firmware was built for
        description: Full firmware metadata dictionary
    """

    path: Path
    board_id: int = field(init=False)
    board_revision: int = field(init=False)
    image: bytes = field(init=False)
    image_size: int = field(init=False)
    image_maxsize: int = field(init=False)
    description: dict = field(init=False)

    def __post_init__(self):
        """Load and validate the firmware file."""
        self.path = Path(self.path)
        self._load()

    def _load(self) -> None:
        """Load firmware from JSON file."""
        logger.info(f"Loading firmware from {self.path}")

        if not self.path.exists():
            raise FirmwareError(f"Firmware file not found: {self.path}")

        try:
            with open(self.path, "r") as f:
                self.description = json.load(f)
        except json.JSONDecodeError as e:
            raise FirmwareError(f"Invalid firmware JSON: {e}", details=str(self.path))
        except IOError as e:
            raise FirmwareError(
                f"Cannot read firmware file: {e}", details=str(self.path)
            )

        # Extract required fields
        required_fields = ["image", "board_id", "image_size", "image_maxsize"]
        for field_name in required_fields:
            if field_name not in self.description:
                raise FirmwareError(
                    f"Firmware missing required field: {field_name}",
                    details=str(self.path),
                )

        self.board_id = self.description["board_id"]
        self.board_revision = self.description.get("board_revision", 0)
        self.image_size = self.description["image_size"]
        self.image_maxsize = self.description["image_maxsize"]

        # Decompress image
        try:
            compressed = base64.b64decode(self.description["image"])
            image_data = bytearray(zlib.decompress(compressed))
        except (binascii.Error, zlib.error) as e:
            raise FirmwareError(
                f"Cannot decompress firmware image: {e}", details=str(self.path)
            )

        # Pad to 4-byte alignment
        while len(image_data) % 4 != 0:
            image_data.append(0xFF)

        self.image = bytes(image_data)

        logger.info(
            f"Loaded firmware: board_id={self.board_id}, "
            f"size={self.image_size} bytes ({self.usage_percent:.1f}%)"
        )

    @property
    def usage_percent(self) -> float:
        """Percentage of maximum flash used."""
        return (self.image_size / self.image_maxsize) * 100.0

    def crc(self, padlen: int) -> int:
        """Calculate CRC32 of firmware image with padding.

        Args:
            padlen: Total length to pad image to (typically flash size)

        Returns:
            CRC32 value matching bootloader's calculation
        """
        state = 0xFFFFFFFF
        state = zlib.crc32(self.image, state)

        padding_length = padlen - len(self.image)
        if padding_length > 0:
            padding = b"\xff" * padding_length
            state = zlib.crc32(padding, state)

        return (state ^ 0xFFFFFFFF) & 0xFFFFFFFF
