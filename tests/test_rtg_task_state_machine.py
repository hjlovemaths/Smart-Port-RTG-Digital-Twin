from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_ROOT / "omniverse" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from rtg_task_state_machine import (  # noqa: E402
    OperationState,
    OperationTask,
    PlcOperationSnapshot,
    RtgOperationStateMachine,
    TaskSlot,
    WORK_TYPE_LOAD,
    WORK_TYPE_SWITCH,
    WORK_TYPE_UNLOAD,
)


def plc(**overrides):
    values = {
        "field": "3C",
        "bay": 63,
        "trolley_m": 12.35,
        "hoist_m": 8.0,
        "locked": False,
        "alarmed": False,
        "vision_confirmed": True,
    }
    values.update(overrides)
    return PlcOperationSnapshot(**values)


class OperationStateMachineTests(unittest.TestCase):
    def test_unload_waits_for_gantry_bay_then_starts_truck(self) -> None:
        machine = RtgOperationStateMachine(
            OperationTask(
                "unload-gantry",
                WORK_TYPE_UNLOAD,
                TaskSlot("4C", 7, 6, 2),
                TaskSlot("4C", 7),
            )
        )

        machine.update(
            plc(field="3C", bay=90, trolley_m=3.70, hoist_m=14.70),
            100.0,
        )

        self.assertEqual(machine.state, OperationState.GANTRY_TO_SOURCE)
        self.assertEqual(machine.visual_phase(), "GANTRY_TO_SOURCE")

        transition = machine.update(
            plc(field="3C", bay=7, trolley_m=3.70, hoist_m=14.70),
            100.1,
        )

        self.assertEqual(transition.current, OperationState.TRUCK_APPROACH)
        self.assertEqual(machine.visual_phase(), "TRUCK_APPROACH")

    def test_switch_uses_source_then_target_row_and_completes(self) -> None:
        machine = RtgOperationStateMachine(
            OperationTask(
                "switch-1",
                WORK_TYPE_SWITCH,
                TaskSlot("3C", 63, 3, 2),
                TaskSlot("3C", 63, 2, 3),
            )
        )
        now = 100.0

        # Validation and gantry arrival are separate PLC-driven transitions.
        self.assertEqual(machine.update(plc(), now).current, OperationState.GANTRY_TO_SOURCE)
        self.assertEqual(machine.update(plc(), now + 0.1).current, OperationState.TROLLEY_TO_SOURCE)
        self.assertEqual(machine.update(plc(trolley_m=12.35), now + 0.2).current, OperationState.HOIST_TO_PICK)
        self.assertEqual(machine.update(plc(trolley_m=12.35, locked=True), now + 0.3).current, OperationState.LOCK)
        self.assertEqual(machine.visual_phase(), "CARRYING")
        self.assertEqual(machine.update(plc(trolley_m=12.35, locked=True), now + 0.4).current, OperationState.HOIST_UP)
        self.assertEqual(machine.update(plc(trolley_m=12.35, locked=True), now + 0.5).current, OperationState.TROLLEY_TO_TARGET)
        self.assertEqual(machine.update(plc(trolley_m=15.19, locked=True), now + 0.6).current, OperationState.HOIST_TO_PLACE)
        self.assertEqual(machine.update(plc(trolley_m=15.19, locked=True), now + 0.7).current, OperationState.UNLOCK)
        self.assertEqual(machine.update(plc(trolley_m=15.19, locked=False), now + 0.8).current, OperationState.HOIST_SAFE)
        self.assertEqual(machine.update(plc(trolley_m=15.19, locked=False, hoist_m=8.0), now + 0.9).current, OperationState.COMPLETE)

    def test_unload_requires_truck_exit_after_release(self) -> None:
        machine = RtgOperationStateMachine(
            OperationTask(
                "unload-1",
                WORK_TYPE_UNLOAD,
                TaskSlot("1C", 20, 3, 3),
                TaskSlot("1C", 20),
            ),
            truck_approach_seconds=0.0,
            truck_settle_seconds=0.0,
            truck_exit_seconds=1.0,
        )
        now = 10.0
        sequence = [
            plc(field="1C", bay=20, trolley_m=12.35),
            plc(field="1C", bay=20, trolley_m=12.35),
            plc(field="1C", bay=20, trolley_m=12.35),
            plc(field="1C", bay=20, trolley_m=12.35),
            plc(field="1C", bay=20, trolley_m=12.35),
            plc(field="1C", bay=20, trolley_m=12.35, locked=True),
            plc(field="1C", bay=20, trolley_m=12.35, locked=True),
            plc(field="1C", bay=20, trolley_m=12.35, locked=True),
            plc(field="1C", bay=20, trolley_m=0.67, locked=True),
            plc(field="1C", bay=20, trolley_m=0.67, locked=True),
            plc(field="1C", bay=20, trolley_m=0.67, locked=False),
            plc(field="1C", bay=20, trolley_m=0.67, locked=False, hoist_m=8.0),
        ]
        for offset, snapshot in enumerate(sequence):
            machine.update(snapshot, now + offset * 0.1)
        self.assertEqual(machine.state, OperationState.TRUCK_EXITING)
        self.assertEqual(machine.update(plc(field="1C", bay=20), now + 2.2).current, OperationState.COMPLETE)

    def test_alarm_transitions_any_active_operation_to_fault(self) -> None:
        machine = RtgOperationStateMachine(
            OperationTask(
                "load-1",
                WORK_TYPE_LOAD,
                TaskSlot("1C", 4),
                TaskSlot("1C", 4, 2, 3),
            )
        )
        machine.update(plc(field="1C", bay=4), 1.0)
        transition = machine.update(plc(field="1C", bay=4, alarmed=True), 1.1)
        self.assertEqual(transition.current, OperationState.FAULT)


if __name__ == "__main__":
    unittest.main()
