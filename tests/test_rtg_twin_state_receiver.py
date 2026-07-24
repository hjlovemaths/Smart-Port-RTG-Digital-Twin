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


class TaskVisualPolicyTests(unittest.TestCase):
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


class TaskFlowTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
