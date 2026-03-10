from dataclasses import dataclass


@dataclass
class ProtocolConfig:
    """Protocol configuration constants."""

    BL_REV_MIN: int = 2  # Minimum supported bootloader protocol
    BL_REV_MAX: int = 6  # Maximum supported bootloader protocol
    PROG_MULTI_MAX: int = (
        252  # Max bytes per PROG_MULTI (protocol max 255, must be multiple of 4)
    )
    READ_MULTI_MAX: int = 252  # Max bytes per READ_MULTI
    MAX_DES_LENGTH: int = 20  # Max chip description length
