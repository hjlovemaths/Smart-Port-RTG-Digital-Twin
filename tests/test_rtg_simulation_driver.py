from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_ROOT / "omniverse" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import rtg_simulation_driver as simulation  # noqa: E402
import yard_inventory  # noqa: E402


class LightweightSimulationInventoryTests(unittest.TestCase):
    def test_startup_inventory_contains_three_adjacent_test_bays(self) -> None:
        inventory = simulation.RtgSimulationDriver._lightweight_test_inventory(
            yard_inventory
        )

        stats = inventory.to_dict()["stats"]
        self.assertEqual(stats["occupiedStackCount"], 3)
        self.assertEqual(stats["containerCount"], 18)
        for bay_number in (19, 21, 23):
            self.assertEqual(
                inventory.stack_pattern(inventory.stack("1C", bay_number, 20)),
                "111111",
            )


if __name__ == "__main__":
    unittest.main()
