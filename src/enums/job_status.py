from enum import Enum


class JobStatus(Enum):
    QUEUED = (1,)
    IN_PROGRESS = (2,)
    SUCCEEDED = (3,)
    FAILED = (4,)
    REJECTED = (5,)
