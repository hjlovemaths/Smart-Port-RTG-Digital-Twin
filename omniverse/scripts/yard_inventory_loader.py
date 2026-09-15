"""Read live yard inventory JSON and expose stable slot coordinates.

This module intentionally avoids importing ``pxr`` so it can run in normal
Python service/tests as well as inside Isaac Sim.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.append(str(SCRIPT_DIR))

from yard_coordinate_mapping import bay_scene_y_m, parse_bay_id


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY_PATH = PROJECT_ROOT / "omniverse" / "data" / "yard_inventory.live.json"

BAY_GAP_M = 0.20
BAY_LENGTH_M = 2.475
CONTAINER_WIDTH_X_M = 1.306287
CONTAINER_LENGTH_20FT_Y_M = BAY_LENGTH_M
CONTAINER_LENGTH_40FT_Y_M = BAY_LENGTH_M * 2.0 + BAY_GAP_M
CONTAINER_VISUAL_HEIGHT_Z_M = 0.904353
ROW_COUNT = 6
ROW_GAP_X_M = 0.20
PAD_TOP_Z_M = 0.10
STACK_FLOOR_CLEARANCE_M = 0.02
TIER_GAP_Z_M = 0.035

REFERENCE_LEFT_INNER_SAFETY_X_M = 2.00
REFERENCE_TRUCK_LANE_WIDTH_X_M = 3.20
REFERENCE_RIGHT_INNER_SAFETY_X_M = 2.60
REFERENCE_ROW_GAP_X_M = 0.12
BAY_WIDTH_M = 20.0


@dataclass(frozen=True)
class YardContainer:
    container_id: str
    container_no: str
    size_ft: int
    type: str
    color_index: int = 0


@dataclass(frozen=True)
class YardSlot:
    bay_id: str
    block: str
    bay_number: int
    row: int
    tier: int
    row_height: int
    container_size_ft: int
    center_x: float
    center_y: float
    center_z: float
    length_y: float
    width_x: float
    height_z: float
    container: YardContainer

    @property
    def prim_suffix(self) -> str:
        return f"Bay_{self.block}_{self.bay_number:03d}/R{self.row:02d}_T{self.tier:02d}_Container"


@dataclass(frozen=True)
class YardStack:
    bay_id: str
    block: str
    bay_number: int
    container_size_ft: int
    row_heights: tuple[int, ...]
    slots: tuple[YardSlot, ...]

    @property
    def pattern(self) -> str:
        return "".join(str(value) for value in self.row_heights)


def load_inventory(path: Path | str = DEFAULT_INVENTORY_PATH) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def reference_first_row_center_x_m(row_count: int) -> float:
    reference_usable_width = (
        BAY_WIDTH_M
        - REFERENCE_LEFT_INNER_SAFETY_X_M
        - REFERENCE_TRUCK_LANE_WIDTH_X_M
        - REFERENCE_RIGHT_INNER_SAFETY_X_M
    )
    reference_row_width = (
        reference_usable_width - (row_count - 1) * REFERENCE_ROW_GAP_X_M
    ) / row_count
    reference_row_pitch = reference_row_width + REFERENCE_ROW_GAP_X_M
    reference_leftmost_stack_x = (
        -BAY_WIDTH_M * 0.5
        + REFERENCE_LEFT_INNER_SAFETY_X_M
        + REFERENCE_TRUCK_LANE_WIDTH_X_M
        + reference_row_width * 0.5
    )
    return reference_leftmost_stack_x + (row_count - 1) * reference_row_pitch


def row_centers_x(row_count: int = ROW_COUNT) -> list[float]:
    row_pitch = CONTAINER_WIDTH_X_M + ROW_GAP_X_M
    first_row_center_x = reference_first_row_center_x_m(row_count)
    leftmost_stack_x = first_row_center_x - (row_count - 1) * row_pitch
    return [
        leftmost_stack_x + (row_count - row_index) * row_pitch
        for row_index in range(1, row_count + 1)
    ]


def tier_center_z(tier: int) -> float:
    return (
        PAD_TOP_Z_M
        + STACK_FLOOR_CLEARANCE_M
        + (tier - 0.5) * CONTAINER_VISUAL_HEIGHT_Z_M
        + (tier - 1) * TIER_GAP_Z_M
    )


def tier_top_z(tier: int) -> float:
    return tier_center_z(tier) + CONTAINER_VISUAL_HEIGHT_Z_M * 0.5


def container_length_for_size(size_ft: int) -> float:
    if int(size_ft) == 20:
        return CONTAINER_LENGTH_20FT_Y_M
    if int(size_ft) == 40:
        return CONTAINER_LENGTH_40FT_Y_M
    raise ValueError(f"Unsupported container size: {size_ft!r}")


def container_center_y_for_bay(block: str, bay_number: int, size_ft: int) -> float:
    if int(size_ft) == 20:
        return bay_scene_y_m(block, bay_number, "center")
    return (
        bay_scene_y_m(block, bay_number - 1, "center")
        + bay_scene_y_m(block, bay_number + 1, "center")
    ) * 0.5


def iter_stacks(data: dict[str, Any]) -> Iterable[YardStack]:
    stacks = data.get("stacks", {})
    if isinstance(stacks, list):
        stack_items = ((item.get("bayId", ""), item) for item in stacks)
    else:
        stack_items = stacks.items()
    for _key, stack in stack_items:
        bay_id = str(stack["bayId"])
        block = str(stack.get("block") or parse_bay_id(bay_id)[0]).upper()
        bay_number = int(stack.get("bayNumber") or parse_bay_id(bay_id)[1])
        size_ft = int(stack.get("containerSizeFt", 20))
        rows = list(stack.get("rows", []))
        centers_x = row_centers_x(len(rows) or ROW_COUNT)
        center_y = container_center_y_for_bay(block, bay_number, size_ft)
        length_y = container_length_for_size(size_ft)
        slots: list[YardSlot] = []
        row_heights: list[int] = []
        for row_index, row_items in enumerate(rows, start=1):
            row_height = len(row_items)
            row_heights.append(row_height)
            center_x = centers_x[row_index - 1]
            for tier, item in enumerate(row_items, start=1):
                container = YardContainer(
                    container_id=str(item.get("containerId", "")),
                    container_no=str(item.get("containerNo", "")),
                    size_ft=int(item.get("sizeFt", size_ft)),
                    type=str(item.get("type", f"{size_ft}GP")),
                    color_index=int(item.get("colorIndex", 0)),
                )
                slots.append(
                    YardSlot(
                        bay_id=bay_id,
                        block=block,
                        bay_number=bay_number,
                        row=row_index,
                        tier=tier,
                        row_height=row_height,
                        container_size_ft=size_ft,
                        center_x=center_x,
                        center_y=center_y,
                        center_z=tier_center_z(tier),
                        length_y=length_y,
                        width_x=CONTAINER_WIDTH_X_M,
                        height_z=CONTAINER_VISUAL_HEIGHT_Z_M,
                        container=container,
                    )
                )
        yield YardStack(
            bay_id=bay_id,
            block=block,
            bay_number=bay_number,
            container_size_ft=size_ft,
            row_heights=tuple(row_heights),
            slots=tuple(slots),
        )


def stack_by_bay(data: dict[str, Any], bay_id: str) -> YardStack:
    normalized = bay_id.strip().upper().replace("-", "/")
    for stack in iter_stacks(data):
        if stack.bay_id.upper() == normalized:
            return stack
    raise KeyError(f"No occupied stack for bay {bay_id!r}")


def find_slot(
    data: dict[str, Any],
    *,
    bay_id: str,
    row: int,
    tier: Optional[int] = None,
) -> YardSlot:
    stack = stack_by_bay(data, bay_id)
    row_number = int(row)
    if tier is None:
        matching = [slot for slot in stack.slots if slot.row == row_number]
        if not matching:
            raise KeyError(f"No containers in {bay_id} row {row_number}")
        return max(matching, key=lambda slot: slot.tier)
    tier_number = int(tier)
    for slot in stack.slots:
        if slot.row == row_number and slot.tier == tier_number:
            return slot
    raise KeyError(f"No container at {bay_id} row {row_number} tier {tier_number}")


def inventory_summary(data: dict[str, Any]) -> dict[str, int]:
    stacks = list(iter_stacks(data))
    return {
        "stack_count": len(stacks),
        "container_count": sum(len(stack.slots) for stack in stacks),
        "anchor_count": len(data.get("anchors", [])),
    }

