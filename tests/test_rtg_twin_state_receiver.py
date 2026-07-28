from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_ROOT / "omniverse" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import rtg_twin_state_receiver as receiver  # noqa: E402


class TaskPositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.driver = receiver.OmniverseRtgTwinDriver()

    def test_four_c_position_uses_two_digit_bay_then_row_and_tier(self) -> None:
        self.assertEqual(
            self.driver._parse_task_position_details("4C0762"),
            ("4C", 7, 6, 2),
        )

    def test_live_outbound_task_uses_start_bay_row_and_tier(self) -> None:
        task = {
            "taskId": "26885357",
            "workType": "UNLOAD",
            "startPos": "3C7741",
            "endPos": None,
            "containerSize": 0,
            "bayMap": "413122",
        }

        self.assertEqual(
            self.driver._task_stack_key(task),
            ("3C", 77, "413122", 20),
        )
        self.assertEqual(
            self.driver._task_source_container_path(task),
            f"{receiver.LIVE_TASK_STACK_PATH}/R04_T01_Container",
        )

    def test_outbound_prefers_source_over_destination_position(self) -> None:
        task = {
            "workType": "UNLOAD",
            "startPos": "1C02033",
            "endPos": "1C001",
            "containerSize": 40,
            "bayMap": "103020",
        }

        self.assertEqual(
            self.driver._task_stack_key(task),
            ("1C", 20, "103020", 40),
        )
        self.assertEqual(
            self.driver._task_source_container_path(task),
            f"{receiver.LIVE_TASK_STACK_PATH}/R03_T03_Container",
        )

    def test_reported_second_row_first_tier_task_selects_r02_t01(self) -> None:
        task = {
            "taskId": "26885371",
            "workType": "UNLOAD",
            "startPos": "3C7721",
            "containerSize": 0,
            "bayMap": "413012",
        }

        self.assertEqual(
            self.driver._task_source_container_path(task),
            f"{receiver.LIVE_TASK_STACK_PATH}/R02_T01_Container",
        )

    def test_source_outside_bay_map_is_rejected(self) -> None:
        task = {
            "workType": "UNLOAD",
            "startPos": "3C7749",
            "bayMap": "413122",
        }

        self.assertIsNone(self.driver._task_source_container_path(task))

    def test_rehandle_uses_source_bay_row_and_tier(self) -> None:
        task = {
            "taskId": "26945501",
            "workType": "SWITCH",
            "startPos": "3C6323",
            "endPos": "3C6351",
            "containerSize": 20,
            "bayMap": "133456",
        }

        self.assertEqual(
            self.driver._task_stack_key(task),
            ("3C", 63, "133456", 20),
        )
        self.assertEqual(
            self.driver._task_source_container_path(task),
            f"{receiver.LIVE_TASK_STACK_PATH}/R02_T03_Container",
        )


class TaskVisualPolicyTests(unittest.TestCase):
    def test_spreader_size_prefers_plc_limit_signal(self) -> None:
        self.assertEqual(
            receiver._requested_spreader_size_ft(
                {"tool20f": 1, "tool40f": 0},
                {"containerSize": 40},
            ),
            20,
        )
        self.assertEqual(
            receiver._requested_spreader_size_ft(
                {"tool20f": 0, "tool40f": 1},
                {"containerSize": 20},
            ),
            40,
        )

    def test_spreader_size_uses_task_while_between_limit_switches(self) -> None:
        self.assertEqual(
            receiver._requested_spreader_size_ft(
                {"tool20f": 0, "tool40f": 0},
                {"containerSize": 20},
            ),
            20,
        )
        self.assertEqual(
            receiver._requested_spreader_size_ft(
                {"tool20f": 0, "tool40f": 0},
                {"containerSize": 0},
                40,
            ),
            40,
        )

    def test_conflicting_spreader_limit_signals_hold_current_size(self) -> None:
        self.assertIsNone(
            receiver._requested_spreader_size_ft(
                {"tool20f": 1, "tool40f": 1},
                {"containerSize": 20},
            )
        )

    def test_dual_lock_signals_latch_through_switching_gap(self) -> None:
        driver = receiver.OmniverseRtgTwinDriver()

        self.assertFalse(
            driver._resolve_spreader_locked(
                {"lockState": 0, "unlockState": 1}
            )
        )
        self.assertTrue(
            driver._resolve_spreader_locked(
                {"lockState": 1, "unlockState": 0}
            )
        )
        self.assertTrue(
            driver._resolve_spreader_locked(
                {"lockState": 0, "unlockState": 0}
            )
        )
        self.assertFalse(
            driver._resolve_spreader_locked(
                {"lockState": 0, "unlockState": 1}
            )
        )

    def test_legacy_lock_only_signal_does_not_latch(self) -> None:
        driver = receiver.OmniverseRtgTwinDriver()

        self.assertTrue(driver._resolve_spreader_locked({"lockState": 1}))
        self.assertFalse(driver._resolve_spreader_locked({"lockState": 0}))

    def test_truck_center_has_no_visual_camera_offset(self) -> None:
        self.assertEqual(receiver.TASK_TRUCK_VISUAL_X_OFFSET_M, 0.0)
        self.assertEqual(
            receiver.TRUCK_OPERATING_LANE_CENTER_X_M
            + receiver.TASK_TRUCK_VISUAL_X_OFFSET_M,
            -4.65,
        )

    def test_hoisted_box_top_meets_spreader_contact_plane(self) -> None:
        height = 0.904353
        center_z = receiver._hoisted_container_local_center_z(height)

        self.assertAlmostEqual(center_z, 2.0978235)
        self.assertAlmostEqual(
            center_z + height * 0.5,
            receiver.SPREADER_CONTAINER_CONTACT_LOCAL_Z,
        )

    def test_hoisted_box_is_world_scoped_to_avoid_rtg_x_scale(self) -> None:
        self.assertTrue(
            receiver.LIVE_HOISTED_LOAD_PATH.startswith("/World/LiveContainers/")
        )
        self.assertNotIn("RTG_PRIMARY_DYNAMIC", receiver.LIVE_HOISTED_LOAD_PATH)

    def test_load_truck_arrives_loaded_and_leaves_empty(self) -> None:
        self.assertTrue(
            receiver._truck_box_visible(
                receiver.WORK_TYPE_LOAD, receiver.TASK_STATE_TRUCK_APPROACH
            )
        )
        self.assertFalse(
            receiver._truck_box_visible(
                receiver.WORK_TYPE_LOAD, receiver.TASK_STATE_PLACED
            )
        )

    def test_unload_truck_arrives_empty_and_leaves_loaded(self) -> None:
        self.assertFalse(
            receiver._truck_box_visible(
                receiver.WORK_TYPE_UNLOAD, receiver.TASK_STATE_READY_TO_PICK
            )
        )
        self.assertTrue(
            receiver._truck_box_visible(
                receiver.WORK_TYPE_UNLOAD, receiver.TASK_STATE_PLACED
            )
        )

    def test_unload_source_hides_after_lock(self) -> None:
        self.assertTrue(
            receiver._outbound_source_visible(
                receiver.WORK_TYPE_UNLOAD, receiver.TASK_STATE_READY_TO_PICK
            )
        )
        self.assertFalse(
            receiver._outbound_source_visible(
                receiver.WORK_TYPE_UNLOAD, receiver.TASK_STATE_CARRYING
            )
        )

    def test_rehandle_source_hides_while_carried(self) -> None:
        self.assertTrue(
            receiver._outbound_source_visible(
                receiver.WORK_TYPE_SWITCH, receiver.TASK_STATE_READY_TO_PICK
            )
        )
        self.assertFalse(
            receiver._outbound_source_visible(
                receiver.WORK_TYPE_SWITCH, receiver.TASK_STATE_CARRYING
            )
        )

    def test_source_visibility_includes_sibling_container_details(self) -> None:
        class Prim:
            def __init__(self, name: str) -> None:
                self.name = name
                self.parent = None
                self.children = []

            def __bool__(self) -> bool:
                return True

            def GetName(self) -> str:
                return self.name

            def GetParent(self):
                return self.parent

            def GetChildren(self):
                return self.children

        parent = Prim("LiveTaskStack")
        source = Prim("R04_T01_Container")
        rib = Prim("R04_T01_Container_Front_Rib_01")
        top = Prim("R04_T01_Container_WeatheredTop")
        other = Prim("R05_T01_Container")
        parent.children = [source, rib, top, other]
        for child in parent.children:
            child.parent = parent

        class Stage:
            @staticmethod
            def GetPrimAtPath(_path):
                return source

        calls = []
        driver = receiver.OmniverseRtgTwinDriver()
        driver._set_visibility = lambda prim, visible: calls.append(
            (prim.GetName(), visible)
        )
        driver._set_container_visual_visibility(Stage(), "/source", False)

        self.assertEqual(
            calls,
            [
                ("R04_T01_Container", False),
                ("R04_T01_Container_Front_Rib_01", False),
                ("R04_T01_Container_WeatheredTop", False),
            ],
        )


class TaskFlowTests(unittest.TestCase):
    def test_truck_approach_timer_starts_when_gantry_reaches_task_bay(self) -> None:
        class Machine:
            state = receiver.OperationState.GANTRY_TO_SOURCE

            @staticmethod
            def update(_plc, _now):
                return None

            @staticmethod
            def visual_phase():
                return receiver.TASK_STATE_TRUCK_APPROACH

        driver = receiver.OmniverseRtgTwinDriver()
        key = ("UNLOAD", "4C", 7, "123456", 20, "26947440")
        driver._operation_task_key = key
        driver._operation_state_machine = Machine()
        driver._task_flow_state = receiver.TASK_STATE_GANTRY_TO_SOURCE
        driver._task_vehicle_started_at = 10.0
        driver._set_operation_state_attr = lambda *_args: None
        driver._set_task_flow_attr = lambda *_args: None

        with patch.object(receiver.time, "time", return_value=200.0):
            state = driver._advance_operation_flow(
                object(),
                key,
                {"visionConfirmed": True},
                {
                    "currentField": "3C",
                    "currentBay": 7,
                    "trolleyMeter": 3.70,
                    "hoistMeter": 14.70,
                    "lockState": 0,
                    "alarmStatus": 0,
                },
            )

        self.assertEqual(state, receiver.TASK_STATE_TRUCK_APPROACH)
        self.assertEqual(driver._task_vehicle_started_at, 200.0)

    def test_unload_visual_sync_does_not_override_plc_trolley_position(self) -> None:
        task = {
            "taskId": "26945521",
            "workType": "UNLOAD",
            "startPos": "3C7741",
            "containerSize": 20,
            "bayMap": "413122",
        }
        driver = receiver.OmniverseRtgTwinDriver()
        driver._last_task_vehicle_key = driver._task_vehicle_key(task)
        calls = []
        driver._advance_operation_flow = (
            lambda *_args: receiver.TASK_STATE_READY_TO_PICK
        )
        driver._update_task_vehicle_pose = (
            lambda *_args: calls.append("vehicle") or 1.0
        )
        driver._sync_task_source_visual = lambda *_args: calls.append("source")
        driver._sync_task_visuals = lambda *_args: calls.append("visuals")
        driver._align_spreader_to_task_source = (
            lambda *_args: calls.append("trolley_override")
        )

        driver._sync_task_vehicle(object(), task, {"trolleyPosition": 9.47})

        self.assertEqual(calls, ["vehicle", "source", "visuals"])

    def test_lock_and_unlock_advance_outbound_flow(self) -> None:
        driver = receiver.OmniverseRtgTwinDriver()
        driver._task_flow_state = receiver.TASK_STATE_TRUCK_APPROACH
        driver._task_flow_state_started_at = 100.0
        driver._task_vehicle_started_at = 100.0
        driver._set_task_flow_attr = lambda _stage, _state: None
        key = ("UNLOAD", "3C", 77, "413122", 20, "26885357")

        with patch.object(receiver.time, "time", return_value=105.0):
            self.assertEqual(
                driver._advance_task_flow(object(), key, {"lockState": 0}),
                receiver.TASK_STATE_TRUCK_WAITING,
            )
        with patch.object(receiver.time, "time", return_value=106.5):
            self.assertEqual(
                driver._advance_task_flow(object(), key, {"lockState": 0}),
                receiver.TASK_STATE_READY_TO_PICK,
            )
        with patch.object(receiver.time, "time", return_value=106.6):
            self.assertEqual(
                driver._advance_task_flow(object(), key, {"lockState": 1}),
                receiver.TASK_STATE_CARRYING,
            )
        with patch.object(receiver.time, "time", return_value=107.2):
            self.assertEqual(
                driver._advance_task_flow(object(), key, {"lockState": 0}),
                receiver.TASK_STATE_PLACED,
            )
        with patch.object(receiver.time, "time", return_value=108.2):
            self.assertEqual(
                driver._advance_task_flow(object(), key, {"lockState": 0}),
                receiver.TASK_STATE_TRUCK_EXITING,
            )

    def test_lock_and_unlock_advance_rehandle_flow(self) -> None:
        driver = receiver.OmniverseRtgTwinDriver()
        driver._task_flow_state = receiver.TASK_STATE_READY_TO_PICK
        driver._task_flow_state_started_at = 100.0
        driver._set_task_flow_attr = lambda _stage, _state: None
        key = ("SWITCH", "3C", 63, "133456", 20, "26945501")

        with patch.object(receiver.time, "time", return_value=100.1):
            self.assertEqual(
                driver._advance_switch_flow(object(), key, {"lockState": 1}),
                receiver.TASK_STATE_CARRYING,
            )
        with patch.object(receiver.time, "time", return_value=100.7):
            self.assertEqual(
                driver._advance_switch_flow(object(), key, {"lockState": 0}),
                receiver.TASK_STATE_PLACED,
            )


if __name__ == "__main__":
    unittest.main()
