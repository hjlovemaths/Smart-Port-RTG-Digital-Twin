"""Business-coordinate mapping for the real-scale 1C-4C yard layout.

The yard engineering origin is the seaside start of block 1C bay 001.  Gantry
commands can be expressed either as metres from that origin or as block/bay
targets such as ("1C", 25).
"""

from __future__ import annotations

from dataclasses import dataclass


BAY_LENGTH_M = 6.0
BAY_WIDTH_M = 20.0
BAY_GAP_M = 0.20
BLOCK_GAP_M = 5.0
YARD_LAYOUT_START_Y_M = -0.50


@dataclass(frozen=True)
class YardBlock:
    name: str
    bay_count: int
    start_m: float

    @property
    def length_m(self) -> float:
        return self.bay_count * BAY_LENGTH_M + (self.bay_count - 1) * BAY_GAP_M

    @property
    def end_m(self) -> float:
        return self.start_m + self.length_m


def _build_blocks() -> tuple[YardBlock, ...]:
    blocks: list[YardBlock] = []
    current_m = 0.0
    for name, bay_count in (("1C", 91), ("2C", 91), ("3C", 91), ("4C", 41)):
        block = YardBlock(name=name, bay_count=bay_count, start_m=current_m)
        blocks.append(block)
        current_m = block.end_m + BLOCK_GAP_M
    return tuple(blocks)


YARD_BLOCKS = _build_blocks()
YARD_TOTAL_LENGTH_M = YARD_BLOCKS[-1].end_m
GANTRY_HARD_LIMITS_M = (0.0, YARD_TOTAL_LENGTH_M)
GANTRY_SOFT_LIMITS_M = (1.0, YARD_TOTAL_LENGTH_M - 1.0)


def normalize_block_name(block: str) -> str:
    return block.strip().upper()


def get_block(block: str) -> YardBlock:
    name = normalize_block_name(block)
    for yard_block in YARD_BLOCKS:
        if yard_block.name == name:
            return yard_block
    valid = ", ".join(yard_block.name for yard_block in YARD_BLOCKS)
    raise ValueError(f"Unknown yard block {block!r}; expected one of: {valid}")


def validate_bay(block: str, bay_number: int) -> YardBlock:
    yard_block = get_block(block)
    bay = int(bay_number)
    if bay < 1 or bay > yard_block.bay_count:
        raise ValueError(
            f"Bay {bay} is outside {yard_block.name}/001-"
            f"{yard_block.bay_count:03d}"
        )
    return yard_block


def bay_start_m(block: str, bay_number: int) -> float:
    yard_block = validate_bay(block, bay_number)
    return yard_block.start_m + (int(bay_number) - 1) * (BAY_LENGTH_M + BAY_GAP_M)


def bay_end_m(block: str, bay_number: int) -> float:
    return bay_start_m(block, bay_number) + BAY_LENGTH_M


def bay_center_m(block: str, bay_number: int) -> float:
    return bay_start_m(block, bay_number) + BAY_LENGTH_M * 0.5


def bay_position_m(block: str, bay_number: int, anchor: str = "center") -> float:
    anchor_name = anchor.strip().lower()
    if anchor_name == "start":
        return bay_start_m(block, bay_number)
    if anchor_name == "center":
        return bay_center_m(block, bay_number)
    if anchor_name == "end":
        return bay_end_m(block, bay_number)
    raise ValueError("Bay anchor must be 'start', 'center', or 'end'")


def parse_bay_id(bay_id: str) -> tuple[str, int]:
    normalized = bay_id.strip().upper().replace("-", "/")
    parts = [part for part in normalized.split("/") if part]
    if len(parts) != 2:
        raise ValueError("Bay id must look like '1C/025' or '1C-25'")
    return parts[0], int(parts[1])


def bay_id_position_m(bay_id: str, anchor: str = "center") -> float:
    block, bay_number = parse_bay_id(bay_id)
    return bay_position_m(block, bay_number, anchor)


def bay_scene_y_m(block: str, bay_number: int, anchor: str = "center") -> float:
    return YARD_LAYOUT_START_Y_M + bay_position_m(block, bay_number, anchor)


def bay_id_scene_y_m(bay_id: str, anchor: str = "center") -> float:
    block, bay_number = parse_bay_id(bay_id)
    return bay_scene_y_m(block, bay_number, anchor)
