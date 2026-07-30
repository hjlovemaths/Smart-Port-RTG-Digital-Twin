"""Deterministic, inventory-aware RTG simulation scenario runner.

Typical Omniverse Kit Script Editor use:

    exec(open(
        r"D:/dnt/project/Smart Port RTG Digital Twin/"
        r"omniverse/scripts/rtg_simulation_scenarios.py",
        encoding="utf-8",
    ).read())
    start_deterministic_scenarios(task_count=6, seed=20260729)
    continue_deterministic_scenarios(task_count=2, seed=20260730)

The default run resets only the simulation inventory, plans legal tasks from a
copy of that snapshot, and submits the complete queue to rtg_simulation_driver.
No live inventory or PLC connection is used.
"""

from __future__ import annotations

import copy
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence


def _resolve_script_dir() -> Path:
    candidates = (
        Path(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts"),
        Path.cwd() / "omniverse" / "scripts",
        Path(__file__).resolve().parent,
    )
    for candidate in candidates:
        if (candidate / "rtg_simulation_driver.py").is_file():
            return candidate
    return Path(__file__).resolve().parent


SCRIPT_DIR = _resolve_script_dir()
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


_previous_stop_scenarios = globals().get("stop_deterministic_scenarios")
_previous_scenario_runner = globals().get("_scenario_runner")
_previous_simulation = globals().get("_simulation")
if callable(_previous_stop_scenarios) and _previous_scenario_runner is None:
    try:
        _previous_stop_scenarios()
    except Exception as exc:  # noqa: BLE001 - Script Editor reload cleanup
        print(f"[RTG Scenarios] previous runner stop failed: {exc}")


# Script Editor keeps globals and imported modules alive between executions.
# Always reload the simulator so scenario reruns pick up the latest receiver,
# inventory-visual, and safety fixes without requiring a Kit restart.
simulation_path = SCRIPT_DIR / "rtg_simulation_driver.py"
exec(
    compile(
        simulation_path.read_text(encoding="utf-8"),
        str(simulation_path),
        "exec",
    ),
    globals(),
)
# Re-executing this file is how the Kit Script Editor picks up new helper
# functions.  Keep the active simulator object alive so a code reload does not
# clear the current yard model or task queue.
if _previous_scenario_runner is not None and _previous_simulation is not None:
    globals()["_simulation"] = _previous_simulation


from yard_inventory import (  # noqa: E402
    DEFAULT_SIM_INVENTORY_PATH,
    MAX_TIERS,
    YardInventory,
    generate_random_inventory,
)


DEFAULT_SCENARIO_SEED = 20260729
DEFAULT_INVENTORY_SEED = 20260729
DEFAULT_WORK_TYPES = ("SWITCH", "UNLOAD", "LOAD")
DEFAULT_BLOCK = "3C"
DEFAULT_BAY_MIN = 55
DEFAULT_BAY_MAX = 63
# Sixth-tier mixed-height placements can exceed the visual engineering hoist
# envelope. The deterministic regression runner stays at five tiers.
SCENARIO_MAX_TIERS = min(5, MAX_TIERS)


def _position(block: str, bay_number: int, row: int, tier: int) -> str:
    bay = int(bay_number)
    if not 0 <= bay <= 99:
        raise ValueError("six-character task positions support bay numbers 00-99")
    return f"{block}{bay:02d}{int(row)}{int(tier)}"


def _normalize_work_types(values: Iterable[str]) -> tuple[str, ...]:
    result = tuple(str(value).strip().upper() for value in values if str(value).strip())
    unsupported = set(result) - {"LOAD", "UNLOAD", "SWITCH"}
    if unsupported:
        raise ValueError(f"Unsupported scenario work types: {sorted(unsupported)}")
    if not result:
        raise ValueError("At least one scenario work type is required")
    return result


def _stack_candidates(
    inventory: YardInventory,
    *,
    block: Optional[str],
    bay_min: Optional[int],
    bay_max: Optional[int],
) -> list[Dict[str, Any]]:
    candidates = []
    for key in sorted(inventory.stacks):
        stack = inventory.stacks[key]
        if block and stack["block"] != str(block).strip().upper():
            continue
        bay = int(stack["bayNumber"])
        if bay_min is not None and bay < int(bay_min):
            continue
        if bay_max is not None and bay > int(bay_max):
            continue
        candidates.append(stack)
    return candidates


def _source_candidates(
    inventory: YardInventory,
    *,
    block: Optional[str],
    bay_min: Optional[int],
    bay_max: Optional[int],
) -> list[tuple[Dict[str, Any], int]]:
    result = []
    for stack in _stack_candidates(
        inventory,
        block=block,
        bay_min=bay_min,
        bay_max=bay_max,
    ):
        for row_index, row in enumerate(stack["rows"], start=1):
            if row:
                result.append((stack, row_index))
    return result


def _load_candidates(
    inventory: YardInventory,
    *,
    block: Optional[str],
    bay_min: Optional[int],
    bay_max: Optional[int],
) -> list[tuple[Dict[str, Any], int]]:
    result = []
    for stack in _stack_candidates(
        inventory,
        block=block,
        bay_min=bay_min,
        bay_max=bay_max,
    ):
        for row_index, row in enumerate(stack["rows"], start=1):
            if len(row) < SCENARIO_MAX_TIERS:
                result.append((stack, row_index))
    return result


def _switch_candidates(
    inventory: YardInventory,
    *,
    block: Optional[str],
    bay_min: Optional[int],
    bay_max: Optional[int],
) -> list[tuple[Dict[str, Any], int, int]]:
    result = []
    for stack in _stack_candidates(
        inventory,
        block=block,
        bay_min=bay_min,
        bay_max=bay_max,
    ):
        occupied_rows = [
            index
            for index, row in enumerate(stack["rows"], start=1)
            if row
        ]
        target_rows = [
            index
            for index, row in enumerate(stack["rows"], start=1)
            if len(row) < SCENARIO_MAX_TIERS
        ]
        for source_row in occupied_rows:
            for target_row in target_rows:
                if source_row != target_row:
                    result.append((stack, source_row, target_row))
    return result


def _task_container_fields(container: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "containerNo": str(container.get("containerNo") or container.get("containerId")),
        "containerHeight": float(container.get("heightM") or 2.6),
        "containerType": str(container.get("type") or ""),
    }


def _build_unload_task(
    inventory: YardInventory,
    rng: random.Random,
    task_id: str,
    *,
    block: Optional[str],
    bay_min: Optional[int],
    bay_max: Optional[int],
) -> Dict[str, Any]:
    candidates = _source_candidates(
        inventory, block=block, bay_min=bay_min, bay_max=bay_max
    )
    if not candidates:
        raise ValueError("No occupied source slot is available for UNLOAD")
    stack, row_index = rng.choice(candidates)
    tier = len(stack["rows"][row_index - 1])
    container = stack["rows"][row_index - 1][-1]
    return {
        "taskId": task_id,
        "workType": "UNLOAD",
        "startPos": _position(
            stack["block"], stack["bayNumber"], row_index, tier
        ),
        "endPos": None,
        "containerSize": int(stack["containerSizeFt"]),
        "bayMap": inventory.stack_pattern(stack),
        **_task_container_fields(container),
    }


def _build_load_task(
    inventory: YardInventory,
    rng: random.Random,
    task_id: str,
    *,
    block: Optional[str],
    bay_min: Optional[int],
    bay_max: Optional[int],
) -> Dict[str, Any]:
    candidates = _load_candidates(
        inventory, block=block, bay_min=bay_min, bay_max=bay_max
    )
    if not candidates:
        raise ValueError("No target slot below the scenario tier limit is available")
    stack, row_index = rng.choice(candidates)
    target_tier = len(stack["rows"][row_index - 1]) + 1
    high_cube = rng.random() < 0.25
    height_m = 2.896 if high_cube else 2.591
    container_no = task_id.replace("SCN-", "SCNBOX-")
    return {
        "taskId": task_id,
        "workType": "LOAD",
        "startPos": None,
        "endPos": _position(
            stack["block"], stack["bayNumber"], row_index, target_tier
        ),
        "containerSize": int(stack["containerSizeFt"]),
        "containerHeight": height_m,
        "containerType": (
            f"{int(stack['containerSizeFt'])}{'HC' if high_cube else 'GP'}"
        ),
        "containerNo": container_no,
        "bayMap": inventory.stack_pattern(stack),
    }


def _build_switch_task(
    inventory: YardInventory,
    rng: random.Random,
    task_id: str,
    *,
    block: Optional[str],
    bay_min: Optional[int],
    bay_max: Optional[int],
) -> Dict[str, Any]:
    candidates = _switch_candidates(
        inventory, block=block, bay_min=bay_min, bay_max=bay_max
    )
    if not candidates:
        raise ValueError("No legal same-bay source/target pair is available for SWITCH")
    stack, source_row, target_row = rng.choice(candidates)
    source_tier = len(stack["rows"][source_row - 1])
    target_tier = len(stack["rows"][target_row - 1]) + 1
    container = stack["rows"][source_row - 1][-1]
    return {
        "taskId": task_id,
        "workType": "SWITCH",
        "startPos": _position(
            stack["block"], stack["bayNumber"], source_row, source_tier
        ),
        "endPos": _position(
            stack["block"], stack["bayNumber"], target_row, target_tier
        ),
        "containerSize": int(stack["containerSizeFt"]),
        "bayMap": inventory.stack_pattern(stack),
        **_task_container_fields(container),
    }


_TASK_BUILDERS = {
    "LOAD": _build_load_task,
    "UNLOAD": _build_unload_task,
    "SWITCH": _build_switch_task,
}


def _inventory_projection(inventory: YardInventory) -> Dict[str, Any]:
    """Return the stable business state used for end-of-run validation."""

    return {
        key: [
            [str(item.get("containerNo") or item.get("containerId")) for item in row]
            for row in stack["rows"]
        ]
        for key, stack in sorted(inventory.stacks.items())
    }


def plan_deterministic_scenarios(
    inventory: YardInventory,
    *,
    task_count: int = 6,
    seed: int = DEFAULT_SCENARIO_SEED,
    work_types: Sequence[str] = DEFAULT_WORK_TYPES,
    block: Optional[str] = DEFAULT_BLOCK,
    bay_min: Optional[int] = DEFAULT_BAY_MIN,
    bay_max: Optional[int] = DEFAULT_BAY_MAX,
) -> tuple[list[Dict[str, Any]], YardInventory]:
    """Plan legal tasks and return the expected post-run inventory."""

    count = int(task_count)
    if count < 1:
        raise ValueError("task_count must be at least one")
    normalized_types = _normalize_work_types(work_types)
    planning_inventory = copy.deepcopy(inventory)
    rng = random.Random(int(seed))
    run_sequence = len(planning_inventory.completed_tasks)
    tasks: list[Dict[str, Any]] = []

    for index in range(1, count + 1):
        preferred = normalized_types[(index - 1) % len(normalized_types)]
        fallback_order = (preferred,) + tuple(
            value for value in normalized_types if value != preferred
        )
        task = None
        errors = []
        for work_type in fallback_order:
            task_id = (
                f"SCN-{int(seed)}-{run_sequence:04d}-{index:03d}-{work_type}"
            )
            try:
                task = _TASK_BUILDERS[work_type](
                    planning_inventory,
                    rng,
                    task_id,
                    block=block,
                    bay_min=bay_min,
                    bay_max=bay_max,
                )
                break
            except ValueError as exc:
                errors.append(str(exc))
        if task is None:
            raise RuntimeError(
                f"Unable to plan scenario task {index}: {'; '.join(errors)}"
            )
        planning_inventory.settle_task(task)
        tasks.append(task)

    return tasks, planning_inventory


@dataclass
class DeterministicScenarioRunner:
    seed: int
    inventory_seed: int
    tasks: list[Dict[str, Any]]
    expected_projection: Dict[str, Any]
    expected_inventory: YardInventory
    simulator: Any
    reset_inventory: bool

    def status(self) -> Dict[str, Any]:
        driver = getattr(self.simulator, "_driver", None)
        inventory = getattr(driver, "_inventory", None)
        completed_ledger = (
            inventory.completed_tasks if inventory is not None else {}
        )
        task_ids = [str(task["taskId"]) for task in self.tasks]
        completed_ids = [task_id for task_id in task_ids if task_id in completed_ledger]
        simulation_status = get_simulation_status()
        all_completed = len(completed_ids) == len(task_ids)
        inventory_matches = (
            _inventory_projection(inventory) == self.expected_projection
            if all_completed and inventory is not None
            else None
        )
        return {
            "running": bool(simulation_status.get("running")),
            "seed": self.seed,
            "inventorySeed": self.inventory_seed,
            "resetInventory": self.reset_inventory,
            "taskCount": len(task_ids),
            "completedTaskCount": len(completed_ids),
            "queuedTaskCount": simulation_status.get("queuedTaskCount", 0),
            "activeState": simulation_status.get("state"),
            "activeTask": simulation_status.get("task"),
            "allCompleted": all_completed,
            "inventoryMatchesPlan": inventory_matches,
            "taskIds": task_ids,
            "completedTaskIds": completed_ids,
        }

    def stop(self) -> None:
        stop_simulation()


_scenario_runner: Optional[DeterministicScenarioRunner] = _previous_scenario_runner


def preview_deterministic_scenarios(
    *,
    task_count: int = 6,
    seed: int = DEFAULT_SCENARIO_SEED,
    work_types: Sequence[str] = DEFAULT_WORK_TYPES,
    block: Optional[str] = DEFAULT_BLOCK,
    bay_min: Optional[int] = DEFAULT_BAY_MIN,
    bay_max: Optional[int] = DEFAULT_BAY_MAX,
) -> list[Dict[str, Any]]:
    """Return a deterministic plan without modifying files or starting Kit."""

    inventory = YardInventory.load(DEFAULT_SIM_INVENTORY_PATH)
    tasks, _expected = plan_deterministic_scenarios(
        inventory,
        task_count=task_count,
        seed=seed,
        work_types=work_types,
        block=block,
        bay_min=bay_min,
        bay_max=bay_max,
    )
    return tasks


def start_deterministic_scenarios(
    *,
    task_count: int = 6,
    seed: int = DEFAULT_SCENARIO_SEED,
    inventory_seed: int = DEFAULT_INVENTORY_SEED,
    work_types: Sequence[str] = DEFAULT_WORK_TYPES,
    block: Optional[str] = DEFAULT_BLOCK,
    bay_min: Optional[int] = DEFAULT_BAY_MIN,
    bay_max: Optional[int] = DEFAULT_BAY_MAX,
    reset_inventory: bool = True,
) -> Dict[str, Any]:
    """Reset (optionally), plan, start, and queue a deterministic scenario."""

    global _scenario_runner
    if _scenario_runner is not None:
        _scenario_runner.stop()
        _scenario_runner = None
    else:
        stop_simulation()

    if reset_inventory:
        generate_random_inventory(
            DEFAULT_SIM_INVENTORY_PATH,
            seed=int(inventory_seed),
        )

    initial_inventory = YardInventory.load(DEFAULT_SIM_INVENTORY_PATH)
    tasks, expected_inventory = plan_deterministic_scenarios(
        initial_inventory,
        task_count=task_count,
        seed=seed,
        work_types=work_types,
        block=block,
        bay_min=bay_min,
        bay_max=bay_max,
    )

    simulator = start_simulation()
    for task in tasks:
        simulator.submit(task, wait_for_previous=True)

    _scenario_runner = DeterministicScenarioRunner(
        seed=int(seed),
        inventory_seed=int(inventory_seed),
        tasks=tasks,
        expected_projection=_inventory_projection(expected_inventory),
        expected_inventory=expected_inventory,
        simulator=simulator,
        reset_inventory=bool(reset_inventory),
    )
    print(
        "[RTG Scenarios] queued deterministic run "
        f"tasks={len(tasks)} seed={seed} inventorySeed={inventory_seed} "
        f"area={block or '*'} {bay_min or '*'}-{bay_max or '*'}"
    )
    for index, task in enumerate(tasks, start=1):
        print(
            f"[RTG Scenarios] {index:02d} {task['workType']} "
            f"{task.get('startPos') or 'TRUCK'} -> "
            f"{task.get('endPos') or 'TRUCK'} "
            f"bayMap={task['bayMap']} task={task['taskId']}"
        )
    return _scenario_runner.status()


def continue_deterministic_scenarios(
    *,
    task_count: int = 1,
    seed: int = DEFAULT_SCENARIO_SEED,
    work_types: Sequence[str] = DEFAULT_WORK_TYPES,
    block: Optional[str] = DEFAULT_BLOCK,
    bay_min: Optional[int] = DEFAULT_BAY_MIN,
    bay_max: Optional[int] = DEFAULT_BAY_MAX,
) -> Dict[str, Any]:
    """Append tasks while reusing the current simulator and yard model.

    Call ``start_deterministic_scenarios`` once for the initial run, then use
    this function for additional work.  Planning starts from the previous
    run's expected post-task inventory, so tasks appended while earlier work
    is still queued remain consistent with the eventual inventory.
    """

    global _scenario_runner
    if _scenario_runner is None:
        raise RuntimeError(
            "No deterministic scenario is active; call "
            "start_deterministic_scenarios() once first"
        )

    simulator = _scenario_runner.simulator
    if getattr(simulator, "_subscription", None) is None:
        raise RuntimeError(
            "The deterministic simulator is stopped; call "
            "start_deterministic_scenarios() to create a new run"
        )

    planning_inventory = copy.deepcopy(_scenario_runner.expected_inventory)
    tasks, expected_inventory = plan_deterministic_scenarios(
        planning_inventory,
        task_count=task_count,
        seed=seed,
        work_types=work_types,
        block=block,
        bay_min=bay_min,
        bay_max=bay_max,
    )
    for task in tasks:
        simulator.submit(task, wait_for_previous=True)

    _scenario_runner.tasks.extend(tasks)
    _scenario_runner.expected_inventory = expected_inventory
    _scenario_runner.expected_projection = _inventory_projection(expected_inventory)
    print(
        "[RTG Scenarios] appended deterministic run "
        f"tasks={len(tasks)} seed={seed} "
        f"area={block or '*'} {bay_min or '*'}-{bay_max or '*'} "
        "(reused current yard model)"
    )
    for index, task in enumerate(tasks, start=1):
        print(
            f"[RTG Scenarios] appended {index:02d} {task['workType']} "
            f"{task.get('startPos') or 'TRUCK'} -> "
            f"{task.get('endPos') or 'TRUCK'} "
            f"bayMap={task['bayMap']} task={task['taskId']}"
        )
    return _scenario_runner.status()


def get_deterministic_scenario_status() -> Dict[str, Any]:
    if _scenario_runner is None:
        return {
            "running": False,
            "taskCount": 0,
            "completedTaskCount": 0,
            "allCompleted": False,
            "inventoryMatchesPlan": None,
        }
    return _scenario_runner.status()


def stop_deterministic_scenarios() -> None:
    global _scenario_runner
    if _scenario_runner is not None:
        _scenario_runner.stop()
        _scenario_runner = None


def print_deterministic_scenario_plan() -> None:
    tasks = (
        _scenario_runner.tasks
        if _scenario_runner is not None
        else preview_deterministic_scenarios()
    )
    print(json.dumps(tasks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    print(
        "Use start_deterministic_scenarios(...) in Omniverse Kit Script Editor."
    )
