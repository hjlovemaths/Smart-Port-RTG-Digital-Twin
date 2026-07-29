from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_ROOT / "omniverse" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from yard_inventory import (  # noqa: E402
    BLOCK_BAY_COUNTS,
    SCHEMA,
    YardInventory,
    physical_footprint,
)


class YardInventoryTests(unittest.TestCase):
    def test_random_snapshot_covers_all_314_anchors_without_overlap(self) -> None:
        inventory = YardInventory.random(seed=17)
        snapshot = inventory.to_dict()

        self.assertEqual(snapshot["schema"], SCHEMA)
        self.assertEqual(snapshot["stats"]["anchorCount"], sum(BLOCK_BAY_COUNTS.values()))
        occupied = {}
        for stack in inventory.stacks.values():
            for anchor in physical_footprint(
                stack["block"],
                stack["bayNumber"],
                stack["containerSizeFt"],
            ):
                self.assertNotIn(anchor, occupied)
                occupied[anchor] = stack["bayId"]

    def test_random_generation_is_reproducible(self) -> None:
        first = YardInventory.random(seed=20260729).to_dict()
        second = YardInventory.random(seed=20260729).to_dict()

        for snapshot in (first, second):
            snapshot.pop("createdAtUtc")
            snapshot.pop("updatedAtUtc")
            for stack in snapshot["stacks"].values():
                stack.pop("updatedAtUtc")
        self.assertEqual(first, second)

    def test_atomic_save_and_reload_preserve_inventory(self) -> None:
        inventory = YardInventory.random(seed=3)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "yard_inventory.json"
            inventory.save(path)
            loaded = YardInventory.load(path)
            raw = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(raw["schema"], SCHEMA)
        self.assertEqual(loaded.to_dict()["stacks"], inventory.to_dict()["stacks"])

    def test_load_adds_container_and_duplicate_completion_is_idempotent(self) -> None:
        inventory = YardInventory.empty()
        task = {
            "taskId": "load-001",
            "workType": "LOAD",
            "endPos": "3C6321",
            "containerSize": 20,
            "containerHeight": 2.896,
            "containerType": "20HC",
            "containerNo": "MSCU1234567",
            "bayMap": "000000",
        }
        inventory.reconcile_task_snapshot(task)
        first = inventory.settle_task(task)
        duplicate = inventory.settle_task(task)
        stack = inventory.stack("3C", 63, 20)

        self.assertTrue(first["applied"])
        self.assertFalse(duplicate["applied"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(inventory.stack_pattern(stack), "010000")
        self.assertEqual(stack["rows"][1][0]["containerNo"], "MSCU1234567")
        self.assertEqual(stack["rows"][1][0]["heightM"], 2.896)

    def test_unload_removes_source_and_switch_moves_container(self) -> None:
        inventory = YardInventory.empty()
        unload = {
            "taskId": "unload-001",
            "workType": "UNLOAD",
            "startPos": "1C4112",
            "containerSize": 20,
            "containerNo": "UNLOAD-BOX",
            "bayMap": "200000",
        }
        inventory.reconcile_task_snapshot(unload)
        inventory.settle_task(unload)
        self.assertEqual(
            inventory.stack_pattern(inventory.stack("1C", 41, 20)),
            "100000",
        )

        switch = {
            "taskId": "switch-001",
            "workType": "SWITCH",
            "startPos": "1C4111",
            "endPos": "1C4151",
            "containerSize": 20,
            "containerNo": "SWITCH-BOX",
            "bayMap": "100000",
        }
        inventory.reconcile_task_snapshot(switch)
        inventory.settle_task(switch)

        target = inventory.stack("1C", 41, 20)
        self.assertEqual(inventory.stack_pattern(target), "000010")
        self.assertEqual(target["rows"][4][0]["containerNo"], "SWITCH-BOX")

    def test_cross_bay_switch_updates_source_and_target(self) -> None:
        inventory = YardInventory.empty()
        task = {
            "taskId": "switch-cross",
            "workType": "SWITCH",
            "startPos": "2C1111",
            "endPos": "2C3551",
            "containerSize": 20,
            "containerNo": "CROSS-BAY",
            "bayMap": "100000",
        }
        inventory.reconcile_task_snapshot(task)
        transaction = inventory.settle_task(task)

        self.assertEqual(set(transaction["changedBays"]), {"2C/011", "2C/035"})
        self.assertEqual(
            inventory.stack_pattern(inventory.stack("2C", 11, 20)),
            "000000",
        )
        self.assertEqual(
            inventory.stack_pattern(inventory.stack("2C", 35, 20)),
            "000010",
        )


if __name__ == "__main__":
    unittest.main()
