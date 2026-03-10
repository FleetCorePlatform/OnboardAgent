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
# - Extraction of the PortDetector class (originally lines ~920-1060).
# - Integration with the loguru logging framework.
# - Architectural restructuring and typing definition additions for the
#   FleetCore OnboardAgent project.
#
############################################################################

import glob
import sys

from dns import serial
from loguru import logger


class PortDetector:
    def __init__(self):
        self.platform = sys.platform
        self._PX4_USB_IDS: list[tuple[int, int, str]] = [
            # (Vendor ID, Product ID, Description)
            (0x26AC, 0x0010, "3D Robotics PX4 FMU"),
            (0x26AC, 0x0011, "3D Robotics PX4 BL"),
            (0x26AC, 0x0012, "3D Robotics PX4IO"),
            (0x26AC, 0x0032, "3D Robotics PX4 FMU v5"),
            (0x3185, 0x0035, "Holybro Durandal"),
            (0x3185, 0x0036, "Holybro Kakute"),
            (0x3162, 0x004B, "Holybro Pixhawk 4"),
            (0x1FC9, 0x001C, "NXP FMUK66"),
            (0x2DAE, 0x1058, "Cube Orange"),
            (0x2DAE, 0x1016, "Cube Black"),
            (0x2DAE, 0x1011, "Cube Yellow"),
            (
                0x0483,
                0x5740,
                "STMicroelectronics Virtual COM Port",
            ),  # Generic ST bootloader
            (0x1209, 0x5740, "Generic STM32"),
            (0x1209, 0x5741, "ArduPilot"),
            (0x3185, 0x0039, "ARK FMU v6x"),
            (0x3185, 0x003A, "ARK Pi6x"),
            (0x3185, 0x003B, "ARK FPV"),
            (0x2341, 0x8036, "Arduino Leonardo"),  # Some PX4 boards use this
        ]

    def detect_ports(self) -> list[str]:
        """Detect available PX4-compatible serial ports.

        Returns:
            List of port paths, prioritized by likelihood of being PX4
        """
        ports = set()

        vid_pid_ports = self._detect_by_vid_pid()
        ports.update(vid_pid_ports)
        ports.update(self._detect_by_patterns())

        result = []
        for port in vid_pid_ports:
            if port in ports:
                result.append(port)
                ports.discard(port)

        result.extend(sorted(ports))

        logger.info(f"Detected {len(result)} potential ports: {result}")
        return result

    def _detect_by_vid_pid(self) -> list[str]:
        """Detect ports by USB Vendor/Product ID.

        Returns:
            List of ports matching known PX4 VID/PIDs
        """
        ports = []
        known_ids = {(vid, pid) for vid, pid, _ in self._PX4_USB_IDS}

        try:
            for port_info in serial.tools.list_ports.comports():
                if (port_info.vid, port_info.pid) in known_ids:
                    logger.debug(
                        f"Found PX4 device: {port_info.device} "
                        f"(VID=0x{port_info.vid:04X}, PID=0x{port_info.pid:04X})"
                    )
                    ports.append(port_info.device)
        except Exception as e:
            logger.debug(f"VID/PID detection failed: {e}")

        return ports

    def _detect_by_patterns(self) -> list[str]:
        """Detect ports by Linux-specific glob patterns."""
        linux_patterns = [
            "/dev/serial/by-id/*PX4*",
            "/dev/serial/by-id/*px4*",
            "/dev/serial/by-id/*3D_Robotics*",
            "/dev/serial/by-id/*Autopilot*",
            "/dev/serial/by-id/*Bitcraze*",
            "/dev/serial/by-id/*Gumstix*",
            "/dev/serial/by-id/*Hex*",
            "/dev/serial/by-id/*Holybro*",
            "/dev/serial/by-id/*Cube*",
            "/dev/serial/by-id/*ArduPilot*",
            "/dev/serial/by-id/*BL_FMU*",
            "/dev/serial/by-id/*_BL*",
            "/dev/ttyACM*",
            "/dev/ttyUSB*",
        ]

        return list({port for pattern in linux_patterns for port in glob.glob(pattern)})

    def expand_patterns(self, patterns: list[str]) -> list[str]:
        """Expand glob patterns to actual port paths.

        Args:
            patterns: List of port paths or glob patterns

        Returns:
            List of expanded port paths
        """
        ports = []
        for pattern in patterns:
            if "*" in pattern or "?" in pattern:
                matches = glob.glob(pattern)
                if matches:
                    ports.extend(matches)
                else:
                    logger.debug(f"Pattern matched no ports: {pattern}")
            else:
                ports.append(pattern)

        return list(set(ports))
