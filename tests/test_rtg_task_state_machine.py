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
from rtg_simulation_driver import WAITING_HOIST_M  # noqa: E402
from rtg_simulation_scenarios import _position  # noqa: E402
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
    parse_container_height_m,
    resolve_container_height_m,
)
from yard_coordinate_mapping import bay_position_m  # noqa: E402


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
    def test_scenario_positions_use_two_digit_bay_numbers(self) -> None:
        self.assertEqual(_position("3C", 62, 1, 3), "3C6213")
        self.assertEqual(len(_position("3C", 62, 1, 3)), 6)

    def test_container_height_parser_accepts_task_feed_formats(self) -> None:
        self.assertEqual(parse_container_height_m("2.591"), 2.591)
        self.assertEqual(parse_container_height_m("2591"), 2.591)
        self.assertEqual(parse_container_height_m("289.6cm"), 2.896)
        self.assertEqual(parse_container_height_m("9'6\""), 2.896)

    def test_explicit_height_overrides_container_type_fallback(self) -> None:
        self.assertEqual(resolve_container_height_m(None, "40GP"), 2.591)
        self.assertEqual(resolve_container_height_m(None, "40HC"), 2.896)
        self.assertEqual(resolve_container_height_m(2.6, "40HC"), 2.6)

    def test_inventory_heights_are_summed_for_safe_clearance(self) -> None:
        machine = RtgOperationStateMachine(
            OperationTask(
                "mixed-height-stack",
                WORK_TYPE_LOAD,
                TaskSlot("3C", 63),
                TaskSlot("3C", 63, 1, 3),
                bay_heights=(2, 0, 0, 0, 0, 0),
                container_height_m=2.6,
                bay_slot_heights_m=((2.591, 2.896), (), (), (), (), ()),
            )
        )

        self.assertAlmostEqual(machine.safe_hoist_m, 10.687)

    def test_publish_renders_20ft_spreader_preflight_before_update(self) -> None:
        class RenderDriver:
            def __init__(self) -> None:
                self.states = []

            def apply_state(self, state):
                self.states.append(state)
                return {
                    "spreaderSizeFt": 20.0,
                    "spreaderTargetSizeFt": 20,
                }

        simulator = RtgSimulationDriver()
        renderer = RenderDriver()
        simulator._driver = renderer

        simulator.publish(
            {
                "work_type": "UNLOAD",
                "start_pos": "3C6544",
                "bay_map": "123456",
                "container_size": 20,
                "task_id": "sim-20ft-preflight",
            }
        )

        self.assertEqual(len(renderer.states), 1)
        initial_state = renderer.states[0]
        self.assertEqual(initial_state["task"]["containerSize"], 20)
        self.assertEqual(initial_state["pose"]["tool20f"], 1)
        self.assertEqual(initial_state["pose"]["tool40f"], 0)
        self.assertEqual(initial_state["task"]["operationState"], "GANTRY_TO_SOURCE")
        self.assertEqual(initial_state["task"]["visualPhase"], "GANTRY_TO_SOURCE")

    def test_waiting_submission_starts_only_after_complete_high_wait(self) -> None:
        class RenderDriver:
            def apply_state(self, state):
                return None

        simulator = RtgSimulationDriver()
        simulator._driver = RenderDriver()
        simulator.submit(
            {
                "work_type": "UNLOAD",
                "start_pos": "3C6544",
                "bay_map": "123456",
                "container_size": 20,
                "task_id": "sim-first",
            },
            wait_for_previous=True,
        )
        queued = simulator.submit(
            {
                "work_type": "SWITCH",
                "start_pos": "3C6544",
                "end_pos": "3C6556",
                "bay_map": "123456",
                "container_size": 20,
                "task_id": "sim-second",
            },
            wait_for_previous=True,
        )
        self.assertEqual(queued["state"], "QUEUED")
        self.assertEqual(simulator._task.operation.task_id, "sim-first")  # type: ignore[union-attr]

        simulator._machine.state = OperationState.COMPLETE  # type: ignore[union-attr]
        simulator._current_hoist = WAITING_HOIST_M - 0.02
        self.assertFalse(simulator._start_next_queued_task_if_ready())
        self.assertEqual(simulator._task.operation.task_id, "sim-first")  # type: ignore[union-attr]

        simulator._current_hoist = WAITING_HOIST_M
        self.assertTrue(simulator._start_next_queued_task_if_ready())
        self.assertEqual(simulator._task.operation.task_id, "sim-second")  # type: ignore[union-attr]
        self.assertEqual(simulator._pending_tasks, [])

    def test_queued_sixth_tier_switch_starts_next_task_after_parking(self) -> None:
        """A sixth-tier placement must reach COMPLETE before queue handoff."""

        class RenderDriver:
            def apply_state(self, state):
                return None

        simulator = RtgSimulationDriver()
        simulator._driver = RenderDriver()
        simulator.submit(
            {
                "work_type": "SWITCH",
                "start_pos": "3C4344",
                "end_pos": "3C4356",
                "bay_map": "123456",
                "container_size": 20,
                "task_id": "sixth-tier-first",
            },
            wait_for_previous=True,
        )
        simulator.submit(
            {
                "work_type": "SWITCH",
                "start_pos": "3C6644",
                "end_pos": "3C6656",
                "bay_map": "123456",
                "container_size": 40,
                "task_id": "sixth-tier-second",
            },
            wait_for_previous=True,
        )

        now = time.monotonic()
        started_next_task = False
        for tick in range(1600):
            current = now + tick * 0.05
            simulator._make_pose(current)
            transition = simulator._machine.update(  # type: ignore[union-attr]
                PlcOperationSnapshot(
                    field=simulator._task.operation.source.field,  # type: ignore[union-attr]
                    bay=simulator._current_bay,
                    trolley_m=simulator._current_trolley,
                    hoist_m=simulator._current_hoist,
                    locked=simulator._lock,
                    vision_confirmed=True,
                ),
                current,
            )
            if transition is not None:
                simulator._state_entered_at = current
            if simulator._start_next_queued_task_if_ready():
                started_next_task = True
                break

        self.assertTrue(started_next_task)
        self.assertEqual(simulator.state, "GANTRY_TO_SOURCE")
        self.assertEqual(
            simulator._task.operation.task_id,  # type: ignore[union-attr]
            "sixth-tier-second",
        )
        self.assertEqual(simulator._pending_tasks, [])

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

    def test_simulation_uses_real_plc_truck_contact_height(self) -> None:
        simulator = RtgSimulationDriver()
        simulator.publish(
            {
                "work_type": "LOAD",
                "end_pos": "3C6321",
                "bay_map": "001000",
                "container_size": 20,
                "task_id": "sim-truck-contact",
            }
        )

        self.assertAlmostEqual(simulator._pick_hoist_target_m(), 4.32)

    def test_simulation_keeps_switch_locked_until_the_box_rests_on_target(self) -> None:
        simulator = RtgSimulationDriver()
        simulator.publish(
            {
                "work_type": "SWITCH",
                "start_pos": "3C6321",
                "end_pos": "3C6353",
                "bay_map": "441000",
                "container_size": 20,
                "task_id": "sim-place-lock",
            }
        )
        now = time.monotonic()
        simulator._machine.state = OperationState.HOIST_TO_PLACE  # type: ignore[union-attr]
        simulator._state_entered_at = now

        simulator._make_pose(now + 0.7)
        self.assertTrue(simulator._lock)
        self.assertGreater(simulator._current_hoist, 5.2)

        simulator._make_pose(now + 1.4)
        self.assertTrue(simulator._lock)
        self.assertAlmostEqual(simulator._current_hoist, 5.2)
        transition = simulator._machine.update(  # type: ignore[union-attr]
            PlcOperationSnapshot(
                field="3C",
                bay=63,
                trolley_m=simulator._current_trolley,
                hoist_m=simulator._current_hoist,
                locked=simulator._lock,
            ),
            now + 1.4,
        )
        self.assertEqual(transition.current, OperationState.UNLOCK)

    def test_completed_simulation_parks_high_and_keeps_its_pose_for_next_task(self) -> None:
        simulator = RtgSimulationDriver()
        simulator.publish(
            {
                "work_type": "SWITCH",
                "start_pos": "3C6544",
                "end_pos": "3C6556",
                "bay_map": "123456",
                "container_size": 20,
                "task_id": "sim-waiting-pose",
            }
        )
        now = time.monotonic()
        simulator._machine.state = OperationState.COMPLETE  # type: ignore[union-attr]
        simulator._state_entered_at = now
        simulator._current_gantry = 42.0
        simulator._current_gantry_bay = 65.0
        simulator._current_trolley = 15.19
        simulator._current_hoist = 10.4
        simulator._current_bay = 65

        parked_pose = simulator._make_pose(now + 1.8)
        self.assertAlmostEqual(parked_pose["hoistMeter"], 18.0)
        self.assertAlmostEqual(parked_pose["bigcarElecDistance"], 42.0)
        self.assertAlmostEqual(parked_pose["trolleyMeter"], 15.19)

        simulator.publish(
            {
                "work_type": "UNLOAD",
                "start_pos": "3C6321",
                "bay_map": "441000",
                "container_size": 20,
                "task_id": "sim-next-task",
            }
        )
        next_pose = simulator._make_pose(simulator._started_at)
        self.assertAlmostEqual(next_pose["hoistMeter"], 18.0)
        self.assertAlmostEqual(next_pose["bigcarElecDistance"], 42.0)
        self.assertAlmostEqual(next_pose["trolleyMeter"], 15.19)
        self.assertEqual(next_pose["currentBay"], 65)

        simulator._machine.state = OperationState.GANTRY_TO_SOURCE  # type: ignore[union-attr]
        simulator._state_entered_at = now
        target_gantry = bay_position_m("3C", 63)
        moving_pose = simulator._make_pose(now + 1.25)
        self.assertEqual(moving_pose["currentBay"], 64)
        self.assertGreater(
            abs(moving_pose["bigcarElecDistance"] - 42.0),
            0.001,
        )
        self.assertLess(
            abs(moving_pose["bigcarElecDistance"] - target_gantry),
            abs(42.0 - target_gantry),
        )

        arrived_pose = simulator._make_pose(now + 2.0)
        self.assertEqual(arrived_pose["currentBay"], 63)
        self.assertAlmostEqual(arrived_pose["bigcarElecDistance"], target_gantry, places=3)

    def test_gantry_travels_at_one_bay_per_second(self) -> None:
        simulator = RtgSimulationDriver()
        simulator.publish(
            {
                "work_type": "UNLOAD",
                "start_pos": "3C6644",
                "bay_map": "123456",
                "container_size": 40,
                "task_id": "sim-one-bay-per-second",
            }
        )
        now = time.monotonic()
        simulator._machine.state = OperationState.GANTRY_TO_SOURCE  # type: ignore[union-attr]
        simulator._state_entered_at = now
        simulator._current_gantry = bay_position_m("3C", 60)
        simulator._current_gantry_bay = 60.0
        simulator._gantry_motion_start_m = None
        simulator._gantry_motion_start_bay = None
        simulator._gantry_motion_target_m = None

        midpoint = simulator._make_pose(now + 3.0)
        self.assertEqual(midpoint["currentBay"], 63)
        self.assertAlmostEqual(simulator._current_gantry_bay, 63.0)

        arrived = simulator._make_pose(now + 6.0)
        self.assertEqual(arrived["currentBay"], 66)
        self.assertAlmostEqual(simulator._current_gantry_bay, 66.0)

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
        self.assertIsNone(
            machine.update(plc(trolley_m=15.19, locked=True, hoist_m=10.4), now + 0.9)
        )
        self.assertEqual(
            machine.update(plc(trolley_m=15.19, locked=True, hoist_m=5.2), now + 1.0).current,
            OperationState.UNLOCK,
        )
        self.assertEqual(machine.update(plc(trolley_m=15.19, locked=False), now + 1.1).current, OperationState.HOIST_SAFE)
        self.assertEqual(machine.safe_hoist_m, 10.4)
        self.assertIsNone(
            machine.update(plc(trolley_m=15.19, locked=False, hoist_m=8.0), now + 1.2)
        )
        self.assertEqual(
            machine.update(plc(trolley_m=15.19, locked=False, hoist_m=10.4), now + 1.3).current,
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
