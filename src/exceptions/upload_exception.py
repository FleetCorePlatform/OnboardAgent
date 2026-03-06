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
# - Extraction of the UploadError and derived exception classes (originally lines ~50-100).
# - Architectural restructuring for the FleetCore OnboardAgent project.
#
############################################################################

from typing import Optional


class UploadError(Exception):
    def __init__(
        self,
        message: str,
        port: Optional[str] = None,
        operation: Optional[str] = None,
        details: Optional[str] = None,
    ):
        self.port = port
        self.operation = operation
        self.details = details

        parts = [message]
        if port:
            parts.append(f"port={port}")
        if operation:
            parts.append(f"during {operation}")
        if details:
            parts.append(f"({details})")

        super().__init__(" ".join(parts))


class ProtocolError(UploadError):
    """Error in bootloader protocol communication."""

    pass


class ConnectionError(UploadError):
    """Error establishing or maintaining serial connection."""

    pass


class FirmwareError(UploadError):
    """Error loading or validating firmware file."""

    pass


class BoardMismatchError(UploadError):
    """Firmware not suitable for the connected board."""

    pass


class TimeoutError(UploadError):
    """Operation timed out."""

    pass


class SiliconErrataError(UploadError):
    """Board has silicon errata that prevents safe operation."""

    pass
