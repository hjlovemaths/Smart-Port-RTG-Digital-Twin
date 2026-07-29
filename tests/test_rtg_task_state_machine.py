from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_ROOT / "omniverse" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from rtg_simulation_driver import _simulation_task_snapshot  # noqa: E402
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
    bay_heights_from_pattern,
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
        self.assertEqual(
            _task_from_payload(
                {
                    "work_type": "UNLOAD",
                    "start_pos": "3C6323",
                    "bay_map": "441000",
                }
            ).bay_heights,
            (4, 4, 1, 0, 0, 0),
        )

    def test_simulation_snapshot_exposes_authoritative_visual_phase(self) -> None:
        simulator = RtgSimulationDriver()
        simulator.publish(
            {
                "work_type": "UNLOAD",
                "start_pos": "3C6324",
                "bay_map": "441000",
                "container_size": 20,
                "task_id": "sim-visual-state",
            }
        )
        simulator._machine.state = OperationState.TROLLEY_TO_TARGET  # type: ignore[union-attr]

        snapshot = _simulation_task_snapshot(
            simulator._task,  # type: ignore[arg-type]
            simulator._machine,  # type: ignore[arg-type]
        )

        self.assertEqual(snapshot["stateSource"], "SIMULATION")
        self.assertEqual(snapshot["operationState"], "TROLLEY_TO_TARGET")
        self.assertEqual(snapshot["visualPhase"], "CARRYING")

    def test_simulation_reports_loaded_box_bottom_during_unload_lift(self) -> None:
        simulator = RtgSimulationDriver()
        simulator.publish(
            {
                "work_type": "UNLOAD",
                "start_pos": "3C6544",
                "bay_map": "123456",
                "container_size": 20,
                "task_id": "sim-bottom-height",
            }
        )
        now = time.monotonic()
        simulator._machine.state = OperationState.LOCK  # type: ignore[union-attr]
        simulator._state_entered_at = now

        simulator._make_pose(now)

        # R04/T04 rests from 7.8 m through 10.4 m.  Once locked, the PLC
        # reports the loaded box's bottom face, not the 10.4 m lock plane.
        self.assertTrue(simulator._lock)
        self.assertAlmostEqual(simulator._current_hoist, 7.8)

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
        self.assertEqual(
            machine.update(plc(), now + 0.1).current,
            OperationState.HOIST_TO_SOURCE_CLEARANCE,
        )
        self.assertEqual(
            machine.update(plc(hoist_m=8.0), now + 0.2).current,
            OperationState.TROLLEY_TO_SOURCE,
        )
        self.assertEqual(machine.update(plc(trolley_m=12.35), now + 0.3).current, OperationState.HOIST_TO_PICK)
        self.assertEqual(machine.update(plc(trolley_m=12.35, locked=True), now + 0.4).current, OperationState.LOCK)
        self.assertEqual(machine.visual_phase(), "CARRYING")
        self.assertEqual(machine.update(plc(trolley_m=12.35, locked=True), now + 0.5).current, OperationState.HOIST_UP)
        self.assertIsNone(
            machine.update(plc(trolley_m=12.35, locked=True, hoist_m=8.0), now + 0.6)
        )
        self.assertEqual(
            machine.update(plc(trolley_m=12.35, locked=True, hoist_m=10.4), now + 0.7).current,
            OperationState.TROLLEY_TO_TARGET,
        )
        self.assertEqual(machine.update(plc(trolley_m=15.19, locked=True), now + 0.8).current, OperationState.HOIST_TO_PLACE)
        self.assertEqual(machine.update(plc(trolley_m=15.19, locked=True), now + 0.9).current, OperationState.UNLOCK)
        self.assertEqual(machine.update(plc(trolley_m=15.19, locked=False), now + 1.0).current, OperationState.HOIST_SAFE)
        self.assertEqual(machine.safe_hoist_m, 10.4)
        self.assertIsNone(
            machine.update(plc(trolley_m=15.19, locked=False, hoist_m=8.0), now + 1.1)
        )
        self.assertEqual(
            machine.update(plc(trolley_m=15.19, locked=False, hoist_m=10.4), now + 1.2).current,
            OperationState.COMPLETE,
        )

    def test_source_approach_raises_empty_spreader_above_current_stack(self) -> None:
        machine = RtgOperationStateMachine(
            OperationTask(
                "source-clearance",
                WORK_TYPE_SWITCH,
                TaskSlot("3C", 63, 3, 3),
                TaskSlot("3C", 63, 2, 4),
                bay_heights=bay_heights_from_pattern("233400"),
            )
        )
        now = 10.0

        self.assertEqual(machine.update(plc(trolley_m=0.67, hoist_m=6.0), now).current, OperationState.GANTRY_TO_SOURCE)
        self.assertEqual(
            machine.update(plc(trolley_m=0.67, hoist_m=6.0), now + 0.1).current,
            OperationState.HOIST_TO_SOURCE_CLEARANCE,
        )
        self.assertEqual(machine.source_approach_hoist_m, 11.4)
        self.assertIsNone(
            machine.update(plc(trolley_m=0.67, hoist_m=11.3), now + 0.2)
        )
        self.assertEqual(
            machine.update(plc(trolley_m=0.67, hoist_m=11.4), now + 0.3).current,
            OperationState.TROLLEY_TO_SOURCE,
        )

    def test_preflight_faults_before_crossing_an_unclear_stack(self) -> None:
        machine = RtgOperationStateMachine(
            OperationTask(
                "source-blocked",
                WORK_TYPE_SWITCH,
                TaskSlot("3C", 63, 2, 2),
                TaskSlot("3C", 63, 3, 3),
                bay_heights=bay_heights_from_pattern("123456"),
            )
        )
        now = 10.0
        machine.MAX_HOIST_M = 13.0
        transition = machine.update(plc(trolley_m=0.67, hoist_m=6.0), now)
        self.assertEqual(transition.current, OperationState.FAULT)
        self.assertIn("requires=16.6m", transition.reason)

    def test_transfer_waits_for_bay_map_clearance_before_trolley_motion(self) -> None:
        machine = RtgOperationStateMachine(
            OperationTask(
                "unload-clearance",
                WORK_TYPE_UNLOAD,
                TaskSlot("3C", 63, 3, 3),
                TaskSlot("3C", 63),
                bay_heights=bay_heights_from_pattern("233400"),
            )
        )
        machine.state = OperationState.HOIST_UP
        machine.entered_at = 10.0

        # The bay's highest stack is R04/T04, so the task protocol requires
        # 11.4 m: 10.4 m of stack height plus one-metre clearance.
        self.assertEqual(machine.transfer_rows, (3, 4, 5, 6))
        self.assertEqual(machine.transfer_obstacle_tier, 4)
        self.assertEqual(machine.transfer_hoist_m, 11.4)
        self.assertIsNone(machine.update(plc(locked=True, hoist_m=11.3), 10.1))
        self.assertEqual(
            machine.update(plc(locked=True, hoist_m=11.4), 10.2).current,
            OperationState.TROLLEY_TO_TARGET,
        )

    def test_loaded_transfer_clears_highest_original_bay_stack(self) -> None:
        machine = RtgOperationStateMachine(
            OperationTask(
                "top-tier-unload",
                WORK_TYPE_UNLOAD,
                TaskSlot("3C", 65, 6, 6),
                TaskSlot("3C", 65),
                bay_heights=bay_heights_from_pattern("123456"),
            )
        )

        # Picking R06/T06 does not reduce the required lift to five tiers.
        # The carriage must still rise above the original six-tier bay height
        # before it can begin moving toward the truck.
        self.assertEqual(machine.transfer_obstacle_tier, 6)
        # The source is itself the sixth tier, so release clearance raises the
        # required value to the configured 18 m hard limit.
        self.assertEqual(machine.transfer_hoist_m, 18.0)

    def test_transfer_faults_when_bay_map_needs_more_than_max_hoist(self) -> None:
        machine = RtgOperationStateMachine(
            OperationTask(
                "load-blocked",
                WORK_TYPE_LOAD,
                TaskSlot("3C", 63),
                TaskSlot("3C", 63, 2, 3),
                bay_heights=bay_heights_from_pattern("123456"),
            )
        )
        machine.state = OperationState.HOIST_UP
        machine.entered_at = 10.0
        machine.MAX_HOIST_M = 16.0

        self.assertEqual(machine.transfer_obstacle_tier, 6)
        self.assertEqual(machine.transfer_hoist_m, 16.6)
        self.assertFalse(machine.transfer_clearance_possible)
        transition = machine.update(plc(locked=True, hoist_m=16.0), 10.1)
        self.assertEqual(transition.current, OperationState.FAULT)
        self.assertIn("requires=16.6m", transition.reason)

    def test_six_tier_transfer_passes_preflight_with_18m_limit(self) -> None:
        machine = RtgOperationStateMachine(
            OperationTask(
                "six-tier-unload",
                WORK_TYPE_UNLOAD,
                TaskSlot("3C", 65, 4, 4),
                TaskSlot("3C", 65),
                bay_heights=bay_heights_from_pattern("123456"),
            )
        )

        transition = machine.update(plc(field="3C", bay=65, hoist_m=6.0), 10.0)

        self.assertEqual(machine.transfer_hoist_m, 16.6)
        self.assertTrue(machine.transfer_clearance_possible)
        self.assertEqual(transition.current, OperationState.GANTRY_TO_SOURCE)

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
            plc(field="1C", bay=20, trolley_m=12.35),
            plc(field="1C", bay=20, trolley_m=12.35, locked=True, hoist_m=10.4),
            plc(field="1C", bay=20, trolley_m=12.35, locked=True),
            plc(field="1C", bay=20, trolley_m=12.35, locked=True, hoist_m=10.4),
            plc(field="1C", bay=20, trolley_m=0.67, locked=True),
            plc(field="1C", bay=20, trolley_m=0.67, locked=True),
            plc(field="1C", bay=20, trolley_m=0.67, locked=False),
            plc(field="1C", bay=20, trolley_m=0.67, locked=False, hoist_m=10.4),
        ]
        for offset, snapshot in enumerate(sequence):
            machine.update(snapshot, now + offset * 0.1)
        self.assertEqual(machine.safe_hoist_m, 10.4)
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
