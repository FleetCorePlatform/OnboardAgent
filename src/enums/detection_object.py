from enum import Enum
from typing import List


class DetectionObjects(Enum):
    PERSON = 0
    BICYCLE = 1
    CAR = 2
    MOTORCYCLE = 3
    BUS = 5
    TRUCK = 7
    DOG = 16
    CAT = 17

    @classmethod
    def get_name(cls, class_id: int) -> str:
        for obj in cls:
            if obj.value == class_id:
                return obj.name.lower()
        return "unknown"

    @classmethod
    def values(cls) -> List[int]:
        return [obj.value for obj in cls]