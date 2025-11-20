from typing import Dict

from src.enums.execution_state import ExecutionState as State, ExecutionState
from src.exceptions.state_exceptions import IllegalStateSwitchException


class StateMachine:
    def __init__(self):
        self.__state: State = State.IDLE
        self.__transitions: Dict[State, Dict[str, State]] = {
            State.IDLE: {
                "download": State.DOWNLOADING,
                "reject": State.REJECTED,
                "error": State.ERROR,
            },
            State.DOWNLOADING: {
                "upload": State.UPLOADING,
                "cancel": State.CANCELLING,
                "error": State.ERROR,
            },
            State.UPLOADING: {
                "arm": State.ARMED,
                "cancel": State.CANCELLING,
                "error": State.ERROR,
            },
            State.ARMED: {
                "fly": State.IN_FLIGHT,
                "cancel": State.CANCELLING,
                "error": State.ERROR,
            },
            State.IN_FLIGHT: {
                "complete": State.COMPLETING,
                "emergency": State.CANCELLING,
                "error": State.ERROR,
            },
            State.COMPLETING: {"idle": State.IDLE, "error": State.ERROR},
            State.CANCELLING: {"idle": State.IDLE, "error": State.ERROR},
            State.ERROR: {"reset": State.IDLE},
            State.REJECTED: {"idle": State.IDLE},
        }

    def trigger(self, event: str) -> bool:
        if event in self.__transitions[self.__state]:
            self.__state = self.__transitions[self.__state][event]
            return True
        else:
            raise IllegalStateSwitchException(
                self.__state, event, set(self.__transitions[self.__state].keys())
            )

    def get_state(self) -> ExecutionState:
        return self.__state

    def force_reset(self):
        self.__state = State.IDLE
