from pydantic.dataclasses import dataclass


@dataclass
class MissionProgressData:
    current: int
    total: int

    @property
    def is_complete(self) -> bool:
        return self.current == self.total
