import unittest

from src.core.state_machine import StateMachine
from src.enums.execution_state import ExecutionState
from src.exceptions.state_exceptions import IllegalStateSwitchException


class StateMachineTest(unittest.TestCase):
    def test_full_valid_transitions(self):
        sm = StateMachine()

        sm.trigger("download")  # IDLE -> DOWNLOADING
        self.assertEqual(sm.get_state(), ExecutionState.DOWNLOADING)

        sm.trigger("upload")  # DOWNLOADING -> UPLOADING
        self.assertEqual(sm.get_state(), ExecutionState.UPLOADING)

        sm.trigger("arm")  # UPLOADING -> ARMED
        self.assertEqual(sm.get_state(), ExecutionState.ARMED)

        sm.trigger("fly")  # ARMED -> IN_FLIGHT
        self.assertEqual(sm.get_state(), ExecutionState.IN_FLIGHT)

        sm.trigger("complete")  # IN_FLIGHT -> COMPLETING
        self.assertEqual(sm.get_state(), ExecutionState.COMPLETING)

        sm.trigger("idle")  # COMPLETING -> IDLE
        self.assertEqual(sm.get_state(), ExecutionState.IDLE)

    def test_error_transition(self):
        sm = StateMachine()
        sm.trigger("download")
        sm.trigger("error")
        self.assertEqual(sm.get_state(), ExecutionState.ERROR)

    def test_reset_from_error(self):
        sm = StateMachine()
        sm.trigger("download")
        sm.trigger("error")
        sm.trigger("reset")
        self.assertEqual(sm.get_state(), ExecutionState.IDLE)

    def test_rejected_transition(self):
        sm = StateMachine()
        sm.trigger("reject")
        self.assertEqual(sm.get_state(), ExecutionState.REJECTED)

        sm.trigger("idle")
        self.assertEqual(sm.get_state(), ExecutionState.IDLE)

    def test_cancelling_transitions(self):
        sm = StateMachine()
        sm.trigger("download")
        sm.trigger("cancel")
        self.assertEqual(sm.get_state(), ExecutionState.CANCELLING)

        sm.trigger("idle")
        self.assertEqual(sm.get_state(), ExecutionState.IDLE)

    def test_idle_to_valid_state(self):
        sm: StateMachine = StateMachine()
        success: bool = sm.trigger("download")

        self.assertEqual(success, True)

    def test_idle_to_invalid_state(self):
        sm: StateMachine = StateMachine()

        self.assertRaises(IllegalStateSwitchException, lambda: sm.trigger("upload"))

    def test_force_reset_state(self):
        sm: StateMachine = StateMachine()
        success: bool = sm.trigger("download")

        sm.force_reset()
        state: ExecutionState = sm.get_state()

        self.assertEqual(success, True)
        self.assertEqual(state, ExecutionState.IDLE)


if __name__ == "__main__":
    unittest.main()
