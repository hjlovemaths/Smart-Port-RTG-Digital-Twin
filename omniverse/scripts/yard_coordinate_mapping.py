"""Business-coordinate mapping for the real-scale 1C-4C yard layout.

The yard engineering origin is the seaside start of block 1C bay 001.  Gantry
commands can be expressed either as metres from that origin or as block/bay
targets such as ("1C", 25).
"""

from __future__ import annotations

from dataclasses import dataclass


BAY_LENGTH_M = 2.475
BAY_WIDTH_M = 20.0
BAY_GAP_M = 0.20
BLOCK_GAP_M = 7.4
YARD_LAYOUT_START_Y_M = -0.50
# Shared with the PLC receiver: the operating truck centre is calibrated to
# the receiver's physical stop line, not the geometric centre of the painted lane.
TRUCK_OPERATING_LANE_CENTER_X_M = -3.75

# The primary moving RTG's painted yard datum is two metres left of the
# original logical origin. Keep that visual datum fixed while the surrounding
# yard columns are laid out around it. These values are shared by the ground
# layout, static RTG population, and inter-yard traffic layer; the live RTG
# controller continues to use its calibrated absolute gantry datum.
MOVING_RTG_YARD_CENTER_X_M = 0.0
MOVING_RTG_YARD_COLUMN_INDEX = 3
# The first container row is on the moving RTG's right side.  Move only the
# active RTG and its painted ground corridor toward that row; the neighbouring
# yard columns and static RTGs keep their existing datums.  The 2.00 m value
# moves the primary RTG farther left so its wheel axes and the first container
# row are centred on the same operating corridor.
PRIMARY_RTG_LATERAL_SHIFT_X_M = -2.0
RTG_LATERAL_ALIGNMENT_X_M = 1.2
PRIMARY_RTG_LATERAL_ALIGNMENT_X_M = (
    RTG_LATERAL_ALIGNMENT_X_M + PRIMARY_RTG_LATERAL_SHIFT_X_M
)
# The painted RTG corridor is 14.75 m wide. Measuring the composed RTG model
# gives a 19.97 m physical X span, so the 21 m centre spacing leaves about
# 1.03 m between adjacent RTG physical envelopes while making the visual yard
# gaps compact and uniform like the reference layout.
RTG_VISUAL_YARD_WIDTH_X_M = 14.75
RTG_PHYSICAL_WIDTH_X_M = 19.97
RTG_INTER_YARD_CLEARANCE_X_M = 1.03
# Exact X coordinates of the rendered RTG tire axes on the moving yard
# column.  These are taken from the model's black_tire_axis_X_rolls_Y prims,
# not from the old ground-reference tracks.  Keep the painted guides on the
# actual wheel centre lines so the live and static RTGs share one visible
# parking datum.
RTG_TIRE_TRACK_OFFSETS_X_M = (-4.416023299119, 10.333587785095)
YARD_COLUMN_SPACING_X_M = 21.0
VISUAL_YARD_LAYOUT_CENTER_X_M = (
    MOVING_RTG_YARD_CENTER_X_M + PRIMARY_RTG_LATERAL_SHIFT_X_M
)
INTER_YARD_GAP_X_M = YARD_COLUMN_SPACING_X_M - RTG_VISUAL_YARD_WIDTH_X_M
YARD_COLUMN_CENTERS_X = tuple(
    VISUAL_YARD_LAYOUT_CENTER_X_M
    + (index - MOVING_RTG_YARD_COLUMN_INDEX) * YARD_COLUMN_SPACING_X_M
    for index in range(8)
)
# Ground markings, static RTGs, and traffic gaps use the same evenly spaced
# visual column centers. The active column remains at -2 m, so the live RTG's
# calibrated -0.8 m lateral datum does not move.
PAINTED_YARD_COLUMN_CENTERS_X = tuple(
    YARD_COLUMN_CENTERS_X
)
# Bay numbers sit on the left truck-lane side of each painted yard corridor.
# For the active column this resolves to the calibrated lane centre at -3.75 m
# instead of the middle of the RTG span.
BAY_NUMBER_LABEL_OFFSET_X_M = (
    TRUCK_OPERATING_LANE_CENTER_X_M
    - PAINTED_YARD_COLUMN_CENTERS_X[MOVING_RTG_YARD_COLUMN_INDEX]
)
INTER_YARD_ROAD_WIDTH_X_M = INTER_YARD_GAP_X_M


@dataclass(frozen=True)
class YardBlock:
    name: str
    bay_count: int
    start_m: float

    @property
    def length_m(self) -> float:
        odd_slot_count = (self.bay_count + 1) // 2
        return odd_slot_count * BAY_LENGTH_M + (odd_slot_count - 1) * BAY_GAP_M

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
    bay = int(bay_number)
    if bay % 2 == 1:
        odd_slot_index = (bay - 1) // 2
        return yard_block.start_m + odd_slot_index * (BAY_LENGTH_M + BAY_GAP_M)
    previous_odd_bay = bay - 1
    return bay_start_m(block, previous_odd_bay)


def bay_end_m(block: str, bay_number: int) -> float:
    bay = int(bay_number)
    if bay % 2 == 1:
        return bay_start_m(block, bay) + BAY_LENGTH_M
    next_odd_bay = bay + 1
    return bay_end_m(block, next_odd_bay)


def bay_center_m(block: str, bay_number: int) -> float:
    return (bay_start_m(block, bay_number) + bay_end_m(block, bay_number)) * 0.5


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
