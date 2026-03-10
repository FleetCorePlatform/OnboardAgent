from enum import Enum


class PacketType(Enum):
    HANDSHAKE_REQ = "HANDSHAKE_REQ"
    HANDSHAKE_ACK = "HANDSHAKE_ACK"
    CONTROL = "CONTROL"
    CMD_REQ = "CMD_REQ"
    CMD_ACK = "CMD_ACK"


class ControlStatus(Enum):
    START_MANUAL_CONTROL = "START_MANUAL_CONTROL"
    STOP_MANUAL_CONTROL = "STOP_MANUAL_CONTROL"


class ManualControlAckStatus(Enum):
    ACCEPTED = "ACCEPTED"
    DENIED = "DENIED"
    STOPPED = "STOPPED"


class ControlActions(Enum):
    TAKEOFF = "TAKEOFF"
    LAND = "LAND"
