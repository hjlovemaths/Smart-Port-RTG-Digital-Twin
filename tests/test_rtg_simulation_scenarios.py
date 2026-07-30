import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "omniverse" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import rtg_simulation_scenarios as scenarios  # noqa: E402
from yard_inventory import YardInventory  # noqa: E402


class FakeSimulator:
    def __init__(self) -> None:
        self._subscription = object()
        self.submitted = []

    def submit(self, task, *, wait_for_previous):
        self.submitted.append((task, wait_for_previous))


class DeterministicScenarioReuseTests(unittest.TestCase):
    def tearDown(self) -> None:
        scenarios._scenario_runner = None

    def test_continue_appends_to_existing_simulator_and_inventory_plan(self) -> None:
        initial = YardInventory.random(
            seed=20260730,
            occupancy_ratio=1.0,
            forty_ft_ratio=0.0,
        )
        first_tasks, first_expected = scenarios.plan_deterministic_scenarios(
            initial,
            task_count=1,
            seed=1,
            work_types=("UNLOAD",),
        )
        simulator = FakeSimulator()
        scenarios._scenario_runner = scenarios.DeterministicScenarioRunner(
            seed=1,
            inventory_seed=20260729,
            tasks=list(first_tasks),
            expected_projection=scenarios._inventory_projection(first_expected),
            expected_inventory=first_expected,
            simulator=simulator,
            reset_inventory=True,
        )

        status = scenarios.continue_deterministic_scenarios(
            task_count=1,
            seed=2,
            work_types=("UNLOAD",),
        )

        self.assertEqual(len(simulator.submitted), 1)
        self.assertTrue(simulator.submitted[0][1])
        self.assertEqual(status["taskCount"], 2)
        self.assertEqual(len(scenarios._scenario_runner.tasks), 2)
        self.assertIs(
            scenarios._scenario_runner.simulator,
            simulator,
        )


if __name__ == "__main__":
    unittest.main()
