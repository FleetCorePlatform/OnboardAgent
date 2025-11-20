from typing import Set

from src.enums.execution_state import ExecutionState


class IllegalStateSwitchException(Exception):
    def __init__(self, state: ExecutionState, event: str, valid_events: Set[str]):
        self.state: ExecutionState = state
        self.event: str = event
        self.valid_events: Set[str] = valid_events

        message: str = (
            f"Event '{event}' invalid from {state.name}. Valid: {sorted(valid_events)}"
        )
        super().__init__(message)
