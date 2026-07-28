from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_ROOT / "omniverse" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from rtg_simulation_driver import _task_from_payload  # noqa: E402
from rtg_simulation_driver import RtgSimulationDriver  # noqa: E402
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
    def test_simulation_reaches_complete_for_all_work_types(self) -> None:
        scenarios = (
            ("LOAD", None, "3C6321"),
            ("UNLOAD", "3C6321", None),
            ("SWITCH", "3C6321", "3C6351"),
        )
        for work_type, start_pos, end_pos in scenarios:
            simulator = RtgSimulationDriver()
            simulator.publish(
                {
                    "work_type": work_type,
                    "start_pos": start_pos,
                    "end_pos": end_pos,
                    "bay_map": "441000",
                    "container_size": 20,
                    "task_id": f"sim-{work_type.lower()}",
                }
            )
            now = time.monotonic()
            for tick in range(1600):
                current = now + tick * 0.05
                pose = simulator._make_pose(current)
                transition = simulator._machine.update(  # type: ignore[union-attr]
                    PlcOperationSnapshot(
                        field=simulator._task.operation.source.field,  # type: ignore[union-attr]
                        bay=simulator._current_bay,
                        trolley_m=simulator._current_trolley,
                        hoist_m=simulator._current_hoist,
                        locked=simulator._lock,
                    ),
                    current,
                )
                if transition is not None:
                    simulator._state_entered_at = current
                if simulator.state in ("COMPLETE", "FAULT"):
                    break
            self.assertEqual(simulator.state, "COMPLETE", work_type)

    def test_simulation_payload_builds_all_three_work_types(self) -> None:
        load = _task_from_payload(
            {"work_type": "LOAD", "end_pos": "3C6321", "task_id": "l1"}
        )
        unload = _task_from_payload(
            {"work_type": "UNLOAD", "start_pos": "3C6321", "task_id": "u1"}
        )
        switch = _task_from_payload(
            {
                "work_type": "SWITCH",
                "start_pos": "3C6321",
                "end_pos": "3C6351",
                "task_id": "s1",
            }
        )
        self.assertEqual(load.work_type, "LOAD")
        self.assertEqual(unload.source.row, 2)
        self.assertEqual(switch.target.row, 5)

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
