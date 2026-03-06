from enum import IntEnum


class BootloaderCommand(IntEnum):
    """Bootloader protocol commands."""

    NOP = 0x00  # Guaranteed to be discarded by the bootloader
    GET_SYNC = 0x21
    GET_DEVICE = 0x22
    CHIP_ERASE = 0x23
    CHIP_VERIFY = 0x24  # rev2 only
    PROG_MULTI = 0x27
    READ_MULTI = 0x28  # rev2 only
    GET_CRC = 0x29  # rev3+
    GET_OTP = 0x2A  # rev4+, get a word from OTP area
    GET_SN = 0x2B  # rev4+, get a word from SN area
    GET_CHIP = 0x2C  # rev5+, get chip version
    SET_BOOT_DELAY = 0x2D  # rev5+, set boot delay
    GET_CHIP_DES = 0x2E  # rev5+, get chip description in ASCII
    GET_VERSION = 0x2F  # rev5+, get bootloader version in ASCII
    REBOOT = 0x30
    CHIP_FULL_ERASE = 0x40  # Full erase of flash, rev6+


class BootloaderResponse(IntEnum):
    """Bootloader response codes."""

    INSYNC = 0x12
    EOC = 0x20
    OK = 0x10
    FAILED = 0x11
    INVALID = 0x13  # rev3+
    BAD_SILICON_REV = 0x14  # rev5+


class DeviceInfo(IntEnum):
    """Device information parameter codes."""

    BL_REV = 0x01  # Bootloader protocol revision
    BOARD_ID = 0x02  # Board type
    BOARD_REV = 0x03  # Board revision
    FLASH_SIZE = 0x04  # Max firmware size in bytes
