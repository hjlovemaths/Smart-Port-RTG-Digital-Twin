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
