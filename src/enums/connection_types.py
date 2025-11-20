from enum import Enum


class ConnectionTypes(Enum):
    SERIAL = "serial"
    UDPIN = "udpin"
    TCPIN = "tcpin"
