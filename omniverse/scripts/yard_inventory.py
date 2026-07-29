"""Persistent whole-yard container inventory for the Smart Port RTG twin.

The inventory JSON is the business source of truth.  USD prims are only a
rendered projection and may be rebuilt at any time.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import random
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


SCHEMA = "smart-port-yard-inventory/v1"
BLOCK_BAY_COUNTS = {"1C": 91, "2C": 91, "3C": 91, "4C": 41}
ROW_COUNT = 6
MAX_TIERS = 6
DEFAULT_CONTAINER_HEIGHT_M = 2.60
INVENTORY_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DEFAULT_SIM_INVENTORY_PATH = INVENTORY_DATA_DIR / "yard_inventory.sim.json"
DEFAULT_LIVE_INVENTORY_PATH = INVENTORY_DATA_DIR / "yard_inventory.live.json"
# Backward-compatible import name. Runtime callers should explicitly choose
# live or simulation mode instead of sharing one file.
DEFAULT_INVENTORY_PATH = DEFAULT_LIVE_INVENTORY_PATH


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def normalize_work_type(value: object) -> str:
    text = str(value or "").strip().upper()
    if text in {"LOAD", "INBOUND", "IMPORT"}:
        return "LOAD"
    if text in {"UNLOAD", "OUTBOUND", "EXPORT"}:
        return "UNLOAD"
    if text in {"SWITCH", "REHANDLE"}:
        return "SWITCH"
    return text


def parse_task_position(
    value: object,
) -> Optional[tuple[str, int, Optional[int], Optional[int]]]:
    if value is None:
        return None
    text = str(value).strip().upper().replace("-", "").replace("/", "")
    match = re.match(r"^([1-4]C)(\d+)", text)
    if not match:
        return None
    block = match.group(1)
    digits = match.group(2)
    if len(digits) >= 4:
        bay_text = digits[:-2]
        row = int(digits[-2])
        tier = int(digits[-1])
    elif (
        len(digits) == 3
        and digits[0] != "0"
        and 1 <= int(digits[1]) <= ROW_COUNT
        and 1 <= int(digits[2]) <= MAX_TIERS
    ):
        bay_text = digits[0]
        row = int(digits[1])
        tier = int(digits[2])
    else:
        bay_text = digits
        row = None
        tier = None
    bay = int(bay_text)
    if block not in BLOCK_BAY_COUNTS or not 1 <= bay <= BLOCK_BAY_COUNTS[block]:
        return None
    return block, bay, row, tier


def normalize_bay_number(block: str, bay_number: int, size_ft: int) -> int:
    block_name = str(block).strip().upper()
    if block_name not in BLOCK_BAY_COUNTS:
        raise ValueError(f"Unknown yard block: {block!r}")
    bay = int(bay_number)
    size = 20 if int(size_ft) == 20 else 40
    if size == 20 and bay % 2 == 0:
        bay = max(1, bay - 1)
    elif size == 40 and bay % 2 == 1:
        bay += 1
    maximum = BLOCK_BAY_COUNTS[block_name]
    if not 1 <= bay <= maximum:
        raise ValueError(
            f"Bay {block_name}/{bay:03d} is outside 001-{maximum:03d}"
        )
    if size == 40 and (bay - 1 < 1 or bay + 1 > maximum):
        raise ValueError(f"40 ft bay {block_name}/{bay:03d} has no full footprint")
    return bay


def bay_id(block: str, bay_number: int) -> str:
    return f"{str(block).strip().upper()}/{int(bay_number):03d}"


def physical_footprint(block: str, bay_number: int, size_ft: int) -> set[str]:
    size = 20 if int(size_ft) == 20 else 40
    bay = normalize_bay_number(block, bay_number, size)
    if size == 20:
        return {bay_id(block, bay)}
    return {bay_id(block, bay - 1), bay_id(block, bay + 1)}


def _container_height_m(task: Dict[str, Any]) -> float:
    value = task.get("containerHeight")
    try:
        height = float(value)
    except (TypeError, ValueError):
        height = 0.0
    if 2.2 <= height <= 3.2:
        return round(height, 3)
    type_text = str(task.get("containerType") or "").upper()
    return 2.896 if "HC" in type_text or "HQ" in type_text else 2.591


def _task_size_ft(task: Dict[str, Any], bay_number: int) -> int:
    try:
        value = int(task.get("containerSize") or 0)
    except (TypeError, ValueError):
        value = 0
    if value in (20, 1):
        return 20
    if value in (40, 45, 2, 3):
        return 40
    return 40 if int(bay_number) % 2 == 0 else 20


def _task_identity(task: Dict[str, Any]) -> str:
    task_id = str(task.get("taskId") or "").strip()
    if task_id:
        return task_id
    material = "|".join(
        str(task.get(name) or "")
        for name in (
            "workType",
            "startPos",
            "endPos",
            "containerNo",
            "bayMap",
        )
    )
    return "anonymous-" + hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]


def _placeholder_container(
    block: str,
    bay_number: int,
    size_ft: int,
    row: int,
    tier: int,
    *,
    source: str,
    height_m: float = DEFAULT_CONTAINER_HEIGHT_M,
) -> Dict[str, Any]:
    token = f"{block}-{bay_number:03d}-{size_ft}-{row:02d}-{tier:02d}"
    digest = hashlib.sha1(token.encode("utf-8")).hexdigest()
    high_cube = float(height_m) >= 2.8
    return {
        "containerId": f"INV-{digest[:12].upper()}",
        "containerNo": f"SP{digest[:9].upper()}",
        "sizeFt": int(size_ft),
        "heightM": round(float(height_m), 3),
        "type": f"{size_ft}{'HC' if high_cube else 'GP'}",
        "colorIndex": int(digest[12:14], 16) % 6,
        "source": source,
    }


def _task_container(task: Dict[str, Any], size_ft: int) -> Dict[str, Any]:
    identity = _task_identity(task)
    number = str(task.get("containerNo") or "").strip() or f"TASK-{identity}"
    height_m = _container_height_m(task)
    container_type = str(task.get("containerType") or "").strip()
    if not container_type:
        container_type = f"{size_ft}{'HC' if height_m >= 2.8 else 'GP'}"
    digest = hashlib.sha1(number.encode("utf-8")).hexdigest()
    return {
        "containerId": number,
        "containerNo": number,
        "sizeFt": int(size_ft),
        "heightM": height_m,
        "type": container_type,
        "colorIndex": int(digest[:2], 16) % 6,
        "source": "task",
        "lastTaskId": identity,
    }


class YardInventory:
    """Mutable, serializable whole-yard inventory with idempotent settlement."""

    def __init__(
        self,
        *,
        stacks: Optional[Dict[str, Dict[str, Any]]] = None,
        completed_tasks: Optional[Dict[str, Dict[str, Any]]] = None,
        seed: Optional[int] = None,
        created_at_utc: Optional[str] = None,
        updated_at_utc: Optional[str] = None,
    ) -> None:
        self.stacks: Dict[str, Dict[str, Any]] = copy.deepcopy(stacks or {})
        self.completed_tasks: Dict[str, Dict[str, Any]] = copy.deepcopy(
            completed_tasks or {}
        )
        self.seed = seed
        self.created_at_utc = created_at_utc or utc_now_iso()
        self.updated_at_utc = updated_at_utc or self.created_at_utc
        self._validate_all()

    @classmethod
    def empty(cls) -> "YardInventory":
        return cls()

    @classmethod
    def load(cls, path: Path | str = DEFAULT_INVENTORY_PATH) -> "YardInventory":
        inventory_path = Path(path)
        if not inventory_path.is_file():
            return cls.empty()
        raw = json.loads(inventory_path.read_text(encoding="utf-8"))
        if raw.get("schema") != SCHEMA:
            raise ValueError(
                f"Unsupported yard inventory schema: {raw.get('schema')!r}"
            )
        return cls(
            stacks=raw.get("stacks") or {},
            completed_tasks=raw.get("completedTasks") or {},
            seed=raw.get("seed"),
            created_at_utc=raw.get("createdAtUtc"),
            updated_at_utc=raw.get("updatedAtUtc"),
        )

    @classmethod
    def random(
        cls,
        *,
        seed: int = 20260729,
        occupancy_ratio: float = 0.42,
        forty_ft_ratio: float = 0.38,
        max_tiers: int = 5,
    ) -> "YardInventory":
        rng = random.Random(int(seed))
        inventory = cls(seed=int(seed))
        serial = 1
        for block, maximum in BLOCK_BAY_COUNTS.items():
            odd_bays = list(range(1, maximum + 1, 2))
            index = 0
            while index < len(odd_bays):
                if rng.random() > occupancy_ratio:
                    index += 1
                    continue
                can_use_40 = index + 1 < len(odd_bays)
                use_40 = can_use_40 and rng.random() < forty_ft_ratio
                if use_40:
                    logical_bay = odd_bays[index] + 1
                    size_ft = 40
                    index += 2
                else:
                    logical_bay = odd_bays[index]
                    size_ft = 20
                    index += 1

                rows: list[list[Dict[str, Any]]] = []
                for row in range(1, ROW_COUNT + 1):
                    if rng.random() < 0.32:
                        tier_count = 0
                    else:
                        tier_count = rng.choices(
                            range(1, max(2, int(max_tiers)) + 1),
                            weights=range(max(2, int(max_tiers)), 0, -1),
                            k=1,
                        )[0]
                    row_items = []
                    for tier in range(1, tier_count + 1):
                        height_m = 2.896 if rng.random() < 0.24 else 2.591
                        item = _placeholder_container(
                            block,
                            logical_bay,
                            size_ft,
                            row,
                            tier,
                            source="random-initial-snapshot",
                            height_m=height_m,
                        )
                        item["containerId"] = f"SP-{serial:07d}"
                        item["containerNo"] = f"SP{serial:09d}"
                        item["colorIndex"] = rng.randrange(6)
                        serial += 1
                        row_items.append(item)
                    rows.append(row_items)
                if not any(rows):
                    rows[rng.randrange(ROW_COUNT)].append(
                        _placeholder_container(
                            block,
                            logical_bay,
                            size_ft,
                            1,
                            1,
                            source="random-initial-snapshot",
                        )
                    )
                inventory._put_stack(
                    block,
                    logical_bay,
                    size_ft,
                    rows,
                    source="random-initial-snapshot",
                    remove_conflicts=False,
                )
        inventory.updated_at_utc = inventory.created_at_utc
        return inventory

    def save(self, path: Path | str = DEFAULT_INVENTORY_PATH) -> Path:
        inventory_path = Path(path)
        inventory_path.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at_utc = utc_now_iso()
        content = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=inventory_path.name + ".",
            suffix=".tmp",
            dir=inventory_path.parent,
            delete=False,
        )
        temp_path = Path(handle.name)
        try:
            with handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, inventory_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()
        return inventory_path

    def to_dict(self) -> Dict[str, Any]:
        anchors = self._anchors()
        stats = {
            "anchorCount": len(anchors),
            "initializedStackCount": len(self.stacks),
            "occupiedStackCount": sum(
                1 for stack in self.stacks.values() if any(stack["rows"])
            ),
            "containerCount": sum(
                len(row)
                for stack in self.stacks.values()
                for row in stack["rows"]
            ),
            "completedTaskCount": len(self.completed_tasks),
        }
        return {
            "schema": SCHEMA,
            "createdAtUtc": self.created_at_utc,
            "updatedAtUtc": self.updated_at_utc,
            "seed": self.seed,
            "blocks": BLOCK_BAY_COUNTS,
            "stats": stats,
            "anchors": anchors,
            "stacks": copy.deepcopy(self.stacks),
            "completedTasks": copy.deepcopy(self.completed_tasks),
        }

    def stack(
        self, block: str, bay_number: int, size_ft: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        block_name = str(block).strip().upper()
        bay = int(bay_number)
        if size_ft is not None:
            bay = normalize_bay_number(block_name, bay, int(size_ft))
        return self.stacks.get(bay_id(block_name, bay))

    def stack_pattern(self, stack: Dict[str, Any]) -> str:
        return "".join(str(len(row)) for row in stack["rows"])

    def stack_slot_heights(self, stack: Dict[str, Any]) -> list[list[float]]:
        return [
            [round(float(item.get("heightM") or DEFAULT_CONTAINER_HEIGHT_M), 3)
             for item in row]
            for row in stack["rows"]
        ]

    def active_stacks(self) -> Iterable[Dict[str, Any]]:
        return (
            self.stacks[key]
            for key in sorted(
                self.stacks,
                key=lambda value: (
                    list(BLOCK_BAY_COUNTS).index(value.split("/", 1)[0]),
                    int(value.split("/", 1)[1]),
                ),
            )
            if any(self.stacks[key]["rows"])
        )

    def reconcile_task_snapshot(self, task: Dict[str, Any]) -> set[str]:
        """Merge the task's current bayMap into the addressed inventory bay."""

        work_type = normalize_work_type(task.get("workType"))
        position = (
            task.get("endPos") or task.get("targetPos")
            if work_type == "LOAD"
            else task.get("startPos") or task.get("sourcePos")
        )
        details = parse_task_position(position)
        pattern = str(task.get("bayMap") or "").strip()
        if details is None or not re.fullmatch(r"\d{6}", pattern):
            return set()
        block, raw_bay, source_row, source_tier = details
        size_ft = _task_size_ft(task, raw_bay)
        bay = normalize_bay_number(block, raw_bay, size_ft)
        key = bay_id(block, bay)
        existing = self.stacks.get(key)
        if existing is not None and self.stack_pattern(existing) == pattern:
            if (
                work_type in {"UNLOAD", "SWITCH"}
                and source_row is not None
                and source_tier is not None
                and source_tier <= len(existing["rows"][source_row - 1])
            ):
                current = existing["rows"][source_row - 1][source_tier - 1]
                current.update(_task_container(task, size_ft))
            return {key}

        slot_heights = task.get("baySlotHeights")
        rows: list[list[Dict[str, Any]]] = []
        for row in range(1, ROW_COUNT + 1):
            items = []
            for tier in range(1, int(pattern[row - 1]) + 1):
                height_m = DEFAULT_CONTAINER_HEIGHT_M
                if (
                    isinstance(slot_heights, (list, tuple))
                    and len(slot_heights) >= row
                    and isinstance(slot_heights[row - 1], (list, tuple))
                    and len(slot_heights[row - 1]) >= tier
                ):
                    raw_height = slot_heights[row - 1][tier - 1]
                    if isinstance(raw_height, dict):
                        raw_height = (
                            raw_height.get("height")
                            or raw_height.get("containerHeight")
                        )
                    try:
                        parsed_height = float(raw_height)
                    except (TypeError, ValueError):
                        parsed_height = 0.0
                    if 2.2 <= parsed_height <= 3.2:
                        height_m = parsed_height
                item = _placeholder_container(
                    block,
                    bay,
                    size_ft,
                    row,
                    tier,
                    source="task-bay-map",
                    height_m=height_m,
                )
                if (
                    work_type in {"UNLOAD", "SWITCH"}
                    and row == source_row
                    and tier == source_tier
                ):
                    item.update(_task_container(task, size_ft))
                items.append(item)
            rows.append(items)
        removed = self._put_stack(
            block,
            bay,
            size_ft,
            rows,
            source="task-bay-map",
            remove_conflicts=True,
        )
        return {key, *removed}

    def enrich_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Return a task view whose bay map and heights come from inventory."""

        result = dict(task)
        work_type = normalize_work_type(result.get("workType"))
        position = (
            result.get("endPos") or result.get("targetPos")
            if work_type == "LOAD"
            else result.get("startPos") or result.get("sourcePos")
        )
        details = parse_task_position(position)
        if details is None:
            return result
        block, raw_bay, _row, _tier = details
        size_ft = _task_size_ft(result, raw_bay)
        stack = self.stack(block, raw_bay, size_ft)
        if stack is None:
            return result
        result["bayMap"] = self.stack_pattern(stack)
        result["baySlotHeights"] = self.stack_slot_heights(stack)
        result["inventoryBayId"] = stack["bayId"]
        result["inventoryRevision"] = stack["revision"]
        return result

    def settle_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Apply one successful task exactly once and return its transaction."""

        identity = _task_identity(task)
        if identity in self.completed_tasks:
            previous = copy.deepcopy(self.completed_tasks[identity])
            previous["applied"] = False
            previous["duplicate"] = True
            return previous

        work_type = normalize_work_type(task.get("workType"))
        if work_type not in {"LOAD", "UNLOAD", "SWITCH"}:
            raise ValueError(f"Unsupported work type: {work_type!r}")
        before = copy.deepcopy(self.stacks)
        changed: set[str] = set()
        try:
            source_details = parse_task_position(
                task.get("startPos") or task.get("sourcePos")
            )
            target_details = parse_task_position(
                task.get("endPos") or task.get("targetPos")
            )
            reference = source_details or target_details
            if reference is None:
                raise ValueError("Task has no parseable source or target position")
            size_ft = _task_size_ft(task, reference[1])

            if work_type == "LOAD":
                container = _task_container(task, size_ft)
                changed.update(
                    self._insert_at(target_details, size_ft, container, identity)
                )
            elif work_type == "UNLOAD":
                _container, removed = self._remove_at(source_details, size_ft)
                changed.update(removed)
            else:
                container, removed = self._remove_at(source_details, size_ft)
                changed.update(removed)
                container["lastTaskId"] = identity
                container["source"] = "switch"
                changed.update(
                    self._insert_at(target_details, size_ft, container, identity)
                )
        except Exception:
            self.stacks = before
            raise

        record = {
            "taskId": identity,
            "workType": work_type,
            "completedAtUtc": utc_now_iso(),
            "changedBays": sorted(changed),
            "applied": True,
            "duplicate": False,
        }
        self.completed_tasks[identity] = copy.deepcopy(record)
        self.updated_at_utc = record["completedAtUtc"]
        return record

    def _remove_at(
        self,
        details: Optional[tuple[str, int, Optional[int], Optional[int]]],
        size_ft: int,
    ) -> tuple[Dict[str, Any], set[str]]:
        if details is None:
            raise ValueError("Task source position is missing")
        block, raw_bay, row, tier = details
        if row is None or tier is None or not 1 <= row <= ROW_COUNT:
            raise ValueError("Task source row/tier is missing or invalid")
        bay = normalize_bay_number(block, raw_bay, size_ft)
        key = bay_id(block, bay)
        stack = self.stacks.get(key)
        if stack is None or tier > len(stack["rows"][row - 1]):
            raise ValueError(f"Inventory source is empty: {key} R{row:02d} T{tier:02d}")
        container = stack["rows"][row - 1].pop(tier - 1)
        self._touch_stack(stack, "task-settlement")
        return container, {key}

    def _insert_at(
        self,
        details: Optional[tuple[str, int, Optional[int], Optional[int]]],
        size_ft: int,
        container: Dict[str, Any],
        task_id: str,
    ) -> set[str]:
        if details is None:
            raise ValueError("Task target position is missing")
        block, raw_bay, row, tier = details
        if row is None or not 1 <= row <= ROW_COUNT:
            raise ValueError("Task target row is missing or invalid")
        bay = normalize_bay_number(block, raw_bay, size_ft)
        key = bay_id(block, bay)
        removed: set[str] = set()
        stack = self.stacks.get(key)
        if stack is None:
            removed = self._put_stack(
                block,
                bay,
                size_ft,
                [[] for _ in range(ROW_COUNT)],
                source="task-settlement",
                remove_conflicts=True,
            )
            stack = self.stacks[key]
        target_row = stack["rows"][row - 1]
        target_tier = int(tier or (len(target_row) + 1))
        if not 1 <= target_tier <= MAX_TIERS:
            raise ValueError(f"Target tier is outside 1-{MAX_TIERS}: {target_tier}")
        if len(target_row) >= MAX_TIERS:
            raise ValueError(f"Target stack is full: {key} R{row:02d}")
        insert_index = min(target_tier - 1, len(target_row))
        placed = copy.deepcopy(container)
        placed["sizeFt"] = int(size_ft)
        placed["lastTaskId"] = task_id
        target_row.insert(insert_index, placed)
        self._touch_stack(stack, "task-settlement")
        return {key, *removed}

    def _put_stack(
        self,
        block: str,
        bay_number: int,
        size_ft: int,
        rows: list[list[Dict[str, Any]]],
        *,
        source: str,
        remove_conflicts: bool,
    ) -> set[str]:
        block_name = str(block).strip().upper()
        size = 20 if int(size_ft) == 20 else 40
        bay = normalize_bay_number(block_name, bay_number, size)
        key = bay_id(block_name, bay)
        removed: set[str] = set()
        if remove_conflicts:
            footprint = physical_footprint(block_name, bay, size)
            for other_key, other in list(self.stacks.items()):
                if other_key == key:
                    continue
                if footprint & physical_footprint(
                    other["block"], other["bayNumber"], other["containerSizeFt"]
                ):
                    removed.add(other_key)
                    del self.stacks[other_key]
        normalized_rows = [copy.deepcopy(row) for row in rows[:ROW_COUNT]]
        normalized_rows.extend([[] for _ in range(ROW_COUNT - len(normalized_rows))])
        self.stacks[key] = {
            "bayId": key,
            "block": block_name,
            "bayNumber": bay,
            "containerSizeFt": size,
            "rows": normalized_rows,
            "revision": int(self.stacks.get(key, {}).get("revision", 0)) + 1,
            "source": source,
            "updatedAtUtc": utc_now_iso(),
        }
        return removed

    @staticmethod
    def _touch_stack(stack: Dict[str, Any], source: str) -> None:
        stack["revision"] = int(stack.get("revision") or 0) + 1
        stack["source"] = source
        stack["updatedAtUtc"] = utc_now_iso()

    def _anchors(self) -> list[Dict[str, Any]]:
        active_by_anchor: Dict[str, str] = {}
        for key, stack in self.stacks.items():
            for physical_id in physical_footprint(
                stack["block"],
                stack["bayNumber"],
                stack["containerSizeFt"],
            ):
                active_by_anchor[physical_id] = key
        anchors = []
        for block, maximum in BLOCK_BAY_COUNTS.items():
            for bay in range(1, maximum + 1):
                key = bay_id(block, bay)
                stack = self.stacks.get(key)
                occupied_by = active_by_anchor.get(key)
                anchors.append(
                    {
                        "bayId": key,
                        "block": block,
                        "bayNumber": bay,
                        "logicalSizeFt": 20 if bay % 2 else 40,
                        "status": (
                            "stack"
                            if stack is not None
                            else "covered"
                            if occupied_by
                            else "empty"
                        ),
                        "occupiedBy": occupied_by,
                    }
                )
        return anchors

    def _validate_all(self) -> None:
        occupied: Dict[str, str] = {}
        for key, stack in self.stacks.items():
            if key != stack.get("bayId"):
                raise ValueError(f"Inventory stack key mismatch: {key}")
            rows = stack.get("rows")
            if not isinstance(rows, list) or len(rows) != ROW_COUNT:
                raise ValueError(f"Inventory stack must have six rows: {key}")
            if any(not isinstance(row, list) or len(row) > MAX_TIERS for row in rows):
                raise ValueError(f"Inventory stack has invalid tiers: {key}")
            for physical_id in physical_footprint(
                stack["block"],
                stack["bayNumber"],
                stack["containerSizeFt"],
            ):
                if physical_id in occupied and occupied[physical_id] != key:
                    raise ValueError(
                        f"Inventory stacks overlap at {physical_id}: "
                        f"{occupied[physical_id]} and {key}"
                    )
                occupied[physical_id] = key


def generate_random_inventory(
    path: Path | str = DEFAULT_SIM_INVENTORY_PATH,
    *,
    seed: int = 20260729,
    occupancy_ratio: float = 0.42,
    forty_ft_ratio: float = 0.38,
    max_tiers: int = 5,
) -> YardInventory:
    inventory = YardInventory.random(
        seed=seed,
        occupancy_ratio=occupancy_ratio,
        forty_ft_ratio=forty_ft_ratio,
        max_tiers=max_tiers,
    )
    inventory.save(path)
    return inventory
