from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICES_DIR = PROJECT_ROOT / "services"
if str(SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICES_DIR))

from rtg_twin_service import TwinStateCache  # noqa: E402


class TwinStateCacheTests(unittest.TestCase):
    def test_work_mode_from_rcms_heartbeat_is_forwarded_to_twin_state(self) -> None:
        cache = TwinStateCache("RTG_220", 3.0, 1800.0)
        cache.update_plc(
            {
                "current_field_num": "3C",
                "current_bay_num": 12,
            }
        )
        cache.update_heartbeat({"work_mode": 3})

        state = cache.build_state()
        self.assertEqual(state["workMode"], 3)
        self.assertEqual(state["pose"]["workMode"], 3)

    def test_heartbeat_only_frame_keeps_latest_pose(self) -> None:
        cache = TwinStateCache("RTG_220", 3.0, 1800.0)
        cache.update_plc(
            {
                "current_field_num": "4C",
                "current_bay_num": 7,
                "smallcar_pos": 15.0,
            }
        )
        cache.update_heartbeat({"work_mode": 4})

        pose = cache.build_state()["pose"]
        self.assertEqual(pose["currentField"], "4C")
        self.assertEqual(pose["currentBay"], 7)
        self.assertEqual(pose["workMode"], 4)

    def test_rcms_heartbeat_work_mode_overrides_plc_default(self) -> None:
        cache = TwinStateCache("RTG_220", 3.0, 1800.0)
        cache.update_plc({"current_field_num": "4C", "current_bay_num": 27, "work_mode": 0})
        cache.update_heartbeat({"work_mode": 1})

        self.assertEqual(cache.build_state()["pose"]["workMode"], 1)

    def test_rcms_heartbeat_service_flags_are_forwarded(self) -> None:
        cache = TwinStateCache("RTG_220", 3.0, 1800.0)
        cache.update_heartbeat(
            {
                "rtg_state": 1,
                "comm_state": 1,
                "iecs_server": 1,
                "ctrl_server": 1,
                "plc_server": 1,
                "gd_server": 1,
                "vi_server": 1,
                "sf_server": 1,
            }
        )

        heartbeat = cache.build_state()["heartbeat"]
        self.assertEqual(heartbeat["iecsServer"], 1)
        self.assertEqual(heartbeat["ctrlServer"], 1)
        self.assertEqual(heartbeat["plcServer"], 1)
        self.assertEqual(heartbeat["gdServer"], 1)
        self.assertEqual(heartbeat["viServer"], 1)
        self.assertEqual(heartbeat["sfServer"], 1)

    def test_malformed_seven_digit_bay_map_is_rejected(self) -> None:
        cache = TwinStateCache("RTG_220", 3.0, 1800.0)
        accepted = cache.update_task(
            {
                "task_id": "bad-map",
                "task_state": 1,
                "task_type": 1,
                "task_work_type": "LOAD",
                "task_endpos": "3C6223",
                "cntr_size": 40,
                "bay_map": "5300025",
            }
        )

        self.assertFalse(accepted)
        self.assertIsNone(cache.build_state()["task"])

    def test_invalid_task_does_not_replace_last_valid_task(self) -> None:
        cache = TwinStateCache("RTG_220", 3.0, 1800.0)
        cache.update_task(
            {
                "task_id": "good-map",
                "task_state": 1,
                "task_type": 1,
                "task_work_type": "LOAD",
                "task_endpos": "3C6223",
                "cntr_size": 40,
                "bay_map": "530025",
            }
        )

        accepted = cache.update_task(
            {
                "task_id": "bad-map",
                "task_state": 1,
                "task_type": 1,
                "task_work_type": "LOAD",
                "task_endpos": "3C6223",
                "cntr_size": 40,
                "bay_map": "5300025",
            }
        )

        self.assertFalse(accepted)
        self.assertEqual(cache.build_state()["task"]["taskId"], "good-map")

    def test_completion_keeps_terminal_task_for_inventory_settlement(self) -> None:
        cache = TwinStateCache("RTG_220", 3.0, 1800.0)
        cache.update_task(
            {
                "task_id": "task-001",
                "task_state": 1,
                "task_type": 1,
                "task_work_type": "LOAD",
                "task_endpos": "3C6321",
                "task_cntrno": "MSCU1234567",
                "cntr_size": 20,
                "bay_map": "000000",
            }
        )

        self.assertTrue(cache.complete_task())
        task = cache.build_state()["task"]

        self.assertEqual(task["taskId"], "task-001")
        self.assertEqual(task["operationState"], "COMPLETED")
        self.assertIsNotNone(task["completedAtUtc"])

    def test_completion_without_active_task_is_safe(self) -> None:
        cache = TwinStateCache("RTG_220", 3.0, 1800.0)
        self.assertFalse(cache.complete_task())

    def test_cross_bay_target_map_is_forwarded_to_receiver(self) -> None:
        cache = TwinStateCache("RTG_220", 3.0, 1800.0)
        cache.update_task(
            {
                "task_id": "switch-cross",
                "task_state": 1,
                "task_type": 1,
                "task_work_type": "SWITCH",
                "task_startpos": "2C1111",
                "task_endpos": "2C3551",
                "cntr_size": 20,
                "bay_map": "100000",
                "target_bay_map": "000000",
            }
        )

        self.assertEqual(
            cache.build_state()["task"]["targetBayMap"],
            "000000",
        )


if __name__ == "__main__":
    unittest.main()
