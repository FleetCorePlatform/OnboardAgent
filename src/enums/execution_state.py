from enum import Enum


class ExecutionState(Enum):
    IDLE = (0,)
    DOWNLOADING = (1,)
    UPLOADING = (2,)
    ARMED = (3,)
    IN_FLIGHT = (4,)
    COMPLETING = (5,)
    ERROR = (6,)
    CANCELLING = (7,)
    REJECTED = (8,)
