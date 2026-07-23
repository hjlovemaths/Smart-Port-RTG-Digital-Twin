"""Generate lightweight live container stacks from bay-map strings.

Example:
    blender --background --python omniverse/scripts/build_live_containers.py -- \
        --bay 1C/004 --pattern 123456 --container-size-ft 40
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.append(str(SCRIPT_DIR))

from yard_coordinate_mapping import (
    BAY_GAP_M,
    BAY_LENGTH_M,
    BAY_WIDTH_M,
    YARD_LAYOUT_START_Y_M,
    YARD_TOTAL_LENGTH_M,
    bay_scene_y_m,
    bay_id_scene_y_m,
    parse_bay_id,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = PROJECT_ROOT / "omniverse" / "scenes" / "live_containers.usda"

LIVE_ROOT_PATH = "/World/LiveContainers"
PAD_TOP_Z_M = 0.10

CONTAINER_WIDTH_X_M = 1.306287
CONTAINER_LENGTH_20FT_Y_M = BAY_LENGTH_M
CONTAINER_LENGTH_40FT_Y_M = BAY_LENGTH_M * 2.0 + BAY_GAP_M
CONTAINER_LENGTH_Y_M = CONTAINER_LENGTH_20FT_Y_M
CONTAINER_VISUAL_HEIGHT_Z_M = 0.904353
CONTAINER_ACTUAL_HEIGHT_M = 2.50
MAX_STACK_TIERS = 6
ROW_COUNT = 6
MAX_STACK_HEIGHT_Z_M = 15.0
ROW_GAP_X_M = 0.20
LEFT_INNER_SAFETY_X_M = 1.20
TRUCK_LANE_WIDTH_X_M = 5.00
RIGHT_INNER_SAFETY_X_M = 1.50
REFERENCE_LEFT_INNER_SAFETY_X_M = 2.00
REFERENCE_TRUCK_LANE_WIDTH_X_M = 3.20
REFERENCE_RIGHT_INNER_SAFETY_X_M = 2.60
REFERENCE_ROW_GAP_X_M = 0.12
STACK_FLOOR_CLEARANCE_M = 0.02
TIER_GAP_Z_M = 0.035
GROUND_MARKING_Z_M = PAD_TOP_Z_M + 0.045

CONTAINER_COLORS = (
    ("weathered_blue", Gf.Vec3f(0.04, 0.21, 0.48)),
    ("oxide_red", Gf.Vec3f(0.50, 0.11, 0.055)),
    ("warm_gray", Gf.Vec3f(0.47, 0.50, 0.46)),
    ("faded_teal", Gf.Vec3f(0.07, 0.36, 0.34)),
    ("sand_yellow", Gf.Vec3f(0.66, 0.52, 0.20)),
    ("dark_navy", Gf.Vec3f(0.03, 0.06, 0.14)),
)


def reference_first_row_center_x_m(row_count: int) -> float:
    """Keep row 1 at the pre-tightening visual position.

    The operator reads rows from right to left.  Earlier visual checks approved
    the rightmost row position, so the tightened layout compresses rows 2..N
    toward the truck lane while row 1 stays anchored.
    """

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


def parse_pattern(pattern: str) -> list[int]:
    values = []
    for char in pattern.strip():
        if not char.isdigit():
            raise ValueError(f"Container pattern may only contain digits: {pattern!r}")
        value = int(char)
        if value > MAX_STACK_TIERS:
            raise ValueError(
                f"Container stack tier {value} is higher than the {MAX_STACK_TIERS}-tier limit"
            )
        values.append(value)
    if not values:
        raise ValueError("Container pattern cannot be empty")
    if len(values) != ROW_COUNT:
        raise ValueError(f"Container pattern must contain exactly {ROW_COUNT} rows")
    return values


def material(stage: Usd.Stage, name: str, color: Gf.Vec3f) -> UsdShade.Material:
    material_path = f"{LIVE_ROOT_PATH}/Looks/{name}"
    mat = UsdShade.Material.Define(stage, material_path)
    shader = UsdShade.Shader.Define(stage, f"{material_path}/PreviewSurface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(color)
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.62)
    mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return mat


def set_cube_transform(cube: UsdGeom.Cube, center: Gf.Vec3d, scale: Gf.Vec3f) -> None:
    cube.CreateSizeAttr(1.0)
    xform = UsdGeom.Xformable(cube.GetPrim())
    xform.AddTranslateOp().Set(center)
    xform.AddScaleOp().Set(scale)


def create_container_box(
    stage: Usd.Stage,
    path: str,
    center: Gf.Vec3d,
    scale: Gf.Vec3f,
    mat: UsdShade.Material,
    *,
    bay_id: str,
    row: int,
    tier: int,
    tiers: int,
) -> None:
    cube = UsdGeom.Cube.Define(stage, path)
    set_cube_transform(cube, center, scale)
    prim = cube.GetPrim()
    UsdShade.MaterialBindingAPI.Apply(prim).Bind(mat)
    prim.CreateAttribute("live:bayId", Sdf.ValueTypeNames.String).Set(bay_id)
    prim.CreateAttribute("live:row", Sdf.ValueTypeNames.Int).Set(row)
    prim.CreateAttribute("live:tier", Sdf.ValueTypeNames.Int).Set(tier)
    prim.CreateAttribute("live:tiers", Sdf.ValueTypeNames.Int).Set(tiers)
    prim.CreateAttribute("live:source", Sdf.ValueTypeNames.Token).Set("bay_map")


def create_colored_cube(
    stage: Usd.Stage,
    path: str,
    center: Gf.Vec3d,
    scale: Gf.Vec3f,
    color: Gf.Vec3f,
) -> None:
    cube = UsdGeom.Cube.Define(stage, path)
    set_cube_transform(cube, center, scale)
    cube.CreateDisplayColorAttr([color])


def create_container_details(
    stage: Usd.Stage,
    path: str,
    center: Gf.Vec3d,
    scale: Gf.Vec3f,
) -> None:
    dark = Gf.Vec3f(0.018, 0.018, 0.016)
    weather = Gf.Vec3f(0.70, 0.68, 0.60)
    top_z = center[2] + scale[2] * 0.5 + 0.012
    create_colored_cube(
        stage,
        f"{path}_WeatheredTop",
        Gf.Vec3d(center[0], center[1], top_z),
        Gf.Vec3f(scale[0] * 0.88, scale[1] * 0.82, 0.018),
        weather,
    )
    for side_name, side_y in (
        ("Front", center[1] - scale[1] * 0.5 - 0.012),
        ("Back", center[1] + scale[1] * 0.5 + 0.012),
    ):
        for rib_index in range(1, 5):
            rib_x = center[0] - scale[0] * 0.5 + rib_index * scale[0] / 5.0
            create_colored_cube(
                stage,
                f"{path}_{side_name}_Rib_{rib_index:02d}",
                Gf.Vec3d(rib_x, side_y, center[2]),
                Gf.Vec3f(0.018, 0.018, scale[2] * 0.82),
                dark,
            )
        create_colored_cube(
            stage,
            f"{path}_{side_name}_BottomLine",
            Gf.Vec3d(center[0], side_y, center[2] - scale[2] * 0.5 + 0.06),
            Gf.Vec3f(scale[0] * 0.92, 0.020, 0.035),
            dark,
        )


def create_layout_outline(
    stage: Usd.Stage, path: str, x_min: float, x_max: float, y_min: float, y_max: float
) -> None:
    points = [
        Gf.Vec3f(x_min, y_min, PAD_TOP_Z_M + 0.08),
        Gf.Vec3f(x_max, y_min, PAD_TOP_Z_M + 0.08),
        Gf.Vec3f(x_max, y_max, PAD_TOP_Z_M + 0.08),
        Gf.Vec3f(x_min, y_max, PAD_TOP_Z_M + 0.08),
        Gf.Vec3f(x_min, y_min, PAD_TOP_Z_M + 0.08),
    ]
    curves = UsdGeom.BasisCurves.Define(stage, path)
    curves.CreateTypeAttr(UsdGeom.Tokens.linear)
    curves.CreateBasisAttr(UsdGeom.Tokens.bezier)
    curves.CreateWrapAttr(UsdGeom.Tokens.nonperiodic)
    curves.CreateCurveVertexCountsAttr([len(points)])
    curves.CreatePointsAttr(points)
    curves.CreateWidthsAttr([0.08])
    curves.SetWidthsInterpolation(UsdGeom.Tokens.constant)
    curves.CreateDisplayColorAttr([Gf.Vec3f(0.94, 0.90, 0.72)])


def create_ground_polyline(
    stage: Usd.Stage,
    path: str,
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
    color: Gf.Vec3f,
    width: float = 0.08,
) -> None:
    points: list[Gf.Vec3f] = []
    counts: list[int] = []
    z = GROUND_MARKING_Z_M + 0.035
    for start, end in segments:
        points.append(Gf.Vec3f(start[0], start[1], z))
        points.append(Gf.Vec3f(end[0], end[1], z))
        counts.append(2)
    curves = UsdGeom.BasisCurves.Define(stage, path)
    curves.CreateTypeAttr(UsdGeom.Tokens.linear)
    curves.CreateBasisAttr(UsdGeom.Tokens.bezier)
    curves.CreateWrapAttr(UsdGeom.Tokens.nonperiodic)
    curves.CreateCurveVertexCountsAttr(counts)
    curves.CreatePointsAttr(points)
    curves.CreateWidthsAttr([width])
    curves.SetWidthsInterpolation(UsdGeom.Tokens.constant)
    curves.CreateDisplayColorAttr([color])


def create_truck_lane_arrow(
    stage: Usd.Stage,
    path: str,
    center_x: float,
    center_y: float,
    size: float,
) -> None:
    white = Gf.Vec3f(0.98, 0.98, 0.90)
    create_ground_polyline(
        stage,
        path,
        [
            ((center_x, center_y - size * 0.46), (center_x, center_y + size * 0.38)),
            ((center_x, center_y + size * 0.38), (center_x - size * 0.26, center_y + size * 0.10)),
            ((center_x, center_y + size * 0.38), (center_x + size * 0.26, center_y + size * 0.10)),
        ],
        white,
        width=0.10,
    )


def create_ground_bar(
    stage: Usd.Stage,
    path: str,
    center: Gf.Vec3d,
    scale: Gf.Vec3f,
    color: Gf.Vec3f,
) -> None:
    create_colored_cube(stage, path, center, scale, color)


SEGMENT_DEFS = {
    "A": (0.0, 0.5, 0.55, 0.075),
    "B": (0.275, 0.25, 0.075, 0.50),
    "C": (0.275, -0.25, 0.075, 0.50),
    "D": (0.0, -0.5, 0.55, 0.075),
    "E": (-0.275, -0.25, 0.075, 0.50),
    "F": (-0.275, 0.25, 0.075, 0.50),
    "G": (0.0, 0.0, 0.55, 0.075),
}

SEGMENT_GLYPHS = {
    "0": "ABCDEF",
    "1": "BC",
    "2": "ABGED",
    "3": "ABGCD",
    "4": "FBGC",
    "5": "AFGCD",
    "6": "AFGECD",
    "7": "ABC",
    "8": "ABCDEFG",
    "9": "ABFGCD",
    "C": "AFED",
}


def create_segment_label(
    stage: Usd.Stage,
    path: str,
    text: str,
    origin: Gf.Vec3d,
    size: float,
    color: Gf.Vec3f,
) -> None:
    char_advance = 0.72 * size
    x_cursor = origin[0]
    for char_index, char in enumerate(text.upper()):
        if char == " ":
            x_cursor += char_advance * 0.65
            continue
        segments = SEGMENT_GLYPHS.get(char)
        if not segments:
            x_cursor += char_advance
            continue
        for segment in segments:
            local_x, local_y, local_w, local_h = SEGMENT_DEFS[segment]
            create_ground_bar(
                stage,
                f"{path}_Char_{char_index:02d}_{char}_Seg_{segment}",
                Gf.Vec3d(
                    x_cursor + local_x * size,
                    origin[1] + local_y * size,
                    origin[2],
                ),
                Gf.Vec3f(local_w * size, local_h * size, 0.018),
                color,
            )
        x_cursor += char_advance


def create_yard_ground_markings(
    stage: Usd.Stage,
    path: str,
    block: str,
    bay_number: int,
    bay_center_y: float,
    stack_x_min: float,
    stack_x_max: float,
    column_centers: list[float],
) -> None:
    white = Gf.Vec3f(0.92, 0.92, 0.86)
    yellow = Gf.Vec3f(1.0, 0.74, 0.06)
    dark = Gf.Vec3f(0.03, 0.035, 0.032)
    y_length = BAY_LENGTH_M + 1.15
    y_front = bay_center_y - y_length * 0.5
    y_back = bay_center_y + y_length * 0.5
    yard_lane_y_min = YARD_LAYOUT_START_Y_M
    yard_lane_y_max = YARD_LAYOUT_START_Y_M + YARD_TOTAL_LENGTH_M
    yard_lane_center_y = (yard_lane_y_min + yard_lane_y_max) * 0.5
    yard_lane_length = yard_lane_y_max - yard_lane_y_min
    lane_left_x = -BAY_WIDTH_M * 0.5 + LEFT_INNER_SAFETY_X_M
    lane_right_x = lane_left_x + TRUCK_LANE_WIDTH_X_M
    lane_mid_x = (lane_left_x + lane_right_x) * 0.5

    truck_lane_width = lane_right_x - lane_left_x
    create_ground_bar(
        stage,
        f"{path}/TruckLaneAsphaltSurface_1C4C_Through",
        Gf.Vec3d(lane_mid_x, yard_lane_center_y, GROUND_MARKING_Z_M - 0.012),
        Gf.Vec3f(truck_lane_width + 0.20, yard_lane_length, 0.012),
        Gf.Vec3f(0.085, 0.095, 0.090),
    )

    for name, x, width, color in (
        ("TruckLaneLeftWhite", lane_left_x, 0.065, white),
        ("TruckLaneRightWhite", lane_right_x, 0.065, white),
        ("TruckLaneCenterYellow", lane_mid_x, 0.075, yellow),
    ):
        create_ground_bar(
            stage,
            f"{path}/{name}_1C4C_Through",
            Gf.Vec3d(x, yard_lane_center_y, GROUND_MARKING_Z_M),
            Gf.Vec3f(width, yard_lane_length, 0.014),
            color,
        )

    for name, x, width, color in (
        ("StackLeftBoundary", stack_x_min, 0.060, white),
        ("StackRightBoundary", stack_x_max, 0.060, white),
    ):
        create_ground_bar(
            stage,
            f"{path}/{name}",
            Gf.Vec3d(x, bay_center_y, GROUND_MARKING_Z_M),
            Gf.Vec3f(width, y_length, 0.014),
            color,
        )

    arrow_spacing_m = 90.0
    arrow_count = max(2, int(yard_lane_length / arrow_spacing_m))
    for arrow_index in range(arrow_count):
        arrow_y = yard_lane_y_min + (arrow_index + 0.5) * yard_lane_length / arrow_count
        create_truck_lane_arrow(
            stage,
            f"{path}/TruckLaneForwardArrow_1C4C_{arrow_index + 1:02d}",
            lane_mid_x,
            arrow_y,
            1.05,
        )

    for name, y in (
        ("TruckLane1CStartStopLine", yard_lane_y_min),
        ("TruckLane4CEndStopLine", yard_lane_y_max),
    ):
        create_ground_bar(
            stage,
            f"{path}/{name}",
            Gf.Vec3d(lane_mid_x, y, GROUND_MARKING_Z_M),
            Gf.Vec3f(truck_lane_width, 0.060, 0.014),
            white,
        )

    sorted_columns = sorted(column_centers)
    for index in range(len(sorted_columns) - 1):
        separator_x = (sorted_columns[index] + sorted_columns[index + 1]) * 0.5
        create_ground_bar(
            stage,
            f"{path}/ContainerColumnSeparator_{index + 1:02d}",
            Gf.Vec3d(separator_x, bay_center_y, GROUND_MARKING_Z_M - 0.002),
            Gf.Vec3f(0.040, y_length, 0.010),
            Gf.Vec3f(0.72, 0.72, 0.66),
        )

    for name, y in (("BayFrontLine", y_front), ("BayBackLine", y_back)):
        create_ground_bar(
            stage,
            f"{path}/{name}",
            Gf.Vec3d((stack_x_min + stack_x_max) * 0.5, y, GROUND_MARKING_Z_M),
            Gf.Vec3f(stack_x_max - stack_x_min, 0.060, 0.014),
            white,
        )

    # Small painted bay id, styled like CCTV-visible ground markings rather
    # than a floating UI label.
    label_pad_x = lane_right_x + 0.42
    label_y = y_front + 0.72
    create_ground_bar(
        stage,
        f"{path}/BayIdDarkPaintPatch",
        Gf.Vec3d(label_pad_x + 0.34, label_y, GROUND_MARKING_Z_M - 0.004),
        Gf.Vec3f(1.55, 0.72, 0.012),
        dark,
    )
    create_segment_label(
        stage,
        f"{path}/BlockLabel_{block}",
        block,
        Gf.Vec3d(label_pad_x - 0.32, label_y + 0.08, GROUND_MARKING_Z_M + 0.014),
        0.46,
        white,
    )
    create_segment_label(
        stage,
        f"{path}/BayNumberLabel_{bay_number:03d}",
        f"{bay_number:02d}",
        Gf.Vec3d(label_pad_x + 0.26, label_y - 0.12, GROUND_MARKING_Z_M + 0.014),
        0.40,
        white,
    )


def container_length_for_size(container_size_ft: int) -> float:
    if container_size_ft == 20:
        return CONTAINER_LENGTH_20FT_Y_M
    if container_size_ft == 40:
        return CONTAINER_LENGTH_40FT_Y_M
    raise ValueError("Container size must be 20 or 40 ft")


def validate_container_bay_number(block: str, bay_number: int, container_size_ft: int) -> None:
    if container_size_ft == 20 and bay_number % 2 == 0:
        raise ValueError(
            f"20 ft containers should be placed on odd bay numbers, got {block}/{bay_number:03d}"
        )
    if container_size_ft == 40 and bay_number % 2 != 0:
        raise ValueError(
            f"40 ft containers should be placed on even bay numbers, got {block}/{bay_number:03d}"
        )


def container_center_y_for_bay(block: str, bay_number: int, container_size_ft: int) -> float:
    if container_size_ft == 20:
        return bay_scene_y_m(block, bay_number, "center")
    front_odd_bay = bay_number - 1
    rear_odd_bay = bay_number + 1
    return (
        bay_scene_y_m(block, front_odd_bay, "center")
        + bay_scene_y_m(block, rear_odd_bay, "center")
    ) * 0.5


def container_length_for_bay(block: str, bay_number: int, container_size_ft: int) -> float:
    return container_length_for_size(container_size_ft)


def occupied_bay_numbers(bay_number: int, container_size_ft: int) -> tuple[int, ...]:
    if container_size_ft == 20:
        return (bay_number,)
    return (bay_number - 1, bay_number + 1)


def build_live_container_layer(
    bay_id: str,
    pattern: str,
    *,
    container_size_ft: int = 20,
    show_ground_markings: bool = False,
) -> Path:
    if OUTPUT_PATH.exists():
        OUTPUT_PATH.unlink()

    block, bay_number = parse_bay_id(bay_id)
    normalized_bay_id = f"{block}/{bay_number:03d}"
    validate_container_bay_number(block, bay_number, container_size_ft)
    heights = parse_pattern(pattern)
    container_length_y = container_length_for_bay(block, bay_number, container_size_ft)

    stage = Usd.Stage.CreateNew(str(OUTPUT_PATH))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)

    world = stage.OverridePrim("/World")
    stage.SetDefaultPrim(world)
    root = UsdGeom.Xform.Define(stage, LIVE_ROOT_PATH).GetPrim()
    root.CreateAttribute("live:bayId", Sdf.ValueTypeNames.String).Set(normalized_bay_id)
    root.CreateAttribute("live:bayMapPattern", Sdf.ValueTypeNames.String).Set(pattern)
    root.CreateAttribute("live:layoutMode", Sdf.ValueTypeNames.Token).Set(
        "six_container_columns_plus_one_truck_lane"
    )
    root.CreateAttribute("live:containerSizeFt", Sdf.ValueTypeNames.Int).Set(
        container_size_ft
    )
    root.CreateAttribute("live:physicsMode", Sdf.ValueTypeNames.Token).Set(
        "visual_only_no_collision"
    )
    root.CreateAttribute("live:maxStackHeightM", Sdf.ValueTypeNames.Float).Set(
        MAX_STACK_HEIGHT_Z_M
    )
    root.CreateAttribute("live:containerVisualHeightM", Sdf.ValueTypeNames.Float).Set(
        CONTAINER_VISUAL_HEIGHT_Z_M
    )
    root.CreateAttribute("live:containerActualHeightM", Sdf.ValueTypeNames.Float).Set(
        CONTAINER_ACTUAL_HEIGHT_M
    )

    materials = [
        material(stage, f"Container_{name}", color) for name, color in CONTAINER_COLORS
    ]

    bay_center_y = container_center_y_for_bay(block, bay_number, container_size_ft)
    row_width = CONTAINER_WIDTH_X_M
    total_width = len(heights) * row_width + (len(heights) - 1) * ROW_GAP_X_M
    required_width = (
        LEFT_INNER_SAFETY_X_M
        + TRUCK_LANE_WIDTH_X_M
        + total_width
        + RIGHT_INNER_SAFETY_X_M
    )
    if required_width > BAY_WIDTH_M:
        raise ValueError(
            f"Live container layout needs {required_width:.2f} m, "
            f"but the bay is only {BAY_WIDTH_M:.2f} m wide"
        )
    actual_stack_height = CONTAINER_ACTUAL_HEIGHT_M * max(heights)
    if actual_stack_height > MAX_STACK_HEIGHT_Z_M:
        raise ValueError(
            f"Stack height {actual_stack_height:.2f} m exceeds "
            f"{MAX_STACK_HEIGHT_Z_M:.2f} m"
        )
    row_pitch = row_width + ROW_GAP_X_M
    first_row_center_x = reference_first_row_center_x_m(len(heights))
    leftmost_stack_x = first_row_center_x - (len(heights) - 1) * row_pitch
    column_centers = [
        leftmost_stack_x + (len(heights) - row_index) * row_pitch
        for row_index in range(1, len(heights) + 1)
    ]
    container_width = CONTAINER_WIDTH_X_M

    stack_path = (
        f"{LIVE_ROOT_PATH}/Bay_{block}_{bay_number:03d}_"
        f"{container_size_ft}FT_Pattern_{pattern}"
    )
    stack = UsdGeom.Xform.Define(stage, stack_path).GetPrim()
    stack.CreateAttribute("live:block", Sdf.ValueTypeNames.String).Set(block)
    stack.CreateAttribute("live:bayNumber", Sdf.ValueTypeNames.Int).Set(bay_number)
    stack.CreateAttribute("live:rowCount", Sdf.ValueTypeNames.Int).Set(len(heights))
    stack.CreateAttribute("live:containerColumnCount", Sdf.ValueTypeNames.Int).Set(
        len(heights)
    )
    stack.CreateAttribute("live:truckLaneCount", Sdf.ValueTypeNames.Int).Set(1)
    stack.CreateAttribute("live:rowOrder", Sdf.ValueTypeNames.Token).Set(
        "right_to_left"
    )
    stack.CreateAttribute("live:firstRowCenterXM", Sdf.ValueTypeNames.Float).Set(
        first_row_center_x
    )
    stack.CreateAttribute("live:firstRowAnchorPolicy", Sdf.ValueTypeNames.Token).Set(
        "keep_reference_rightmost_row"
    )
    stack.CreateAttribute("live:rowWidthM", Sdf.ValueTypeNames.Float).Set(row_width)
    stack.CreateAttribute("live:totalWidthM", Sdf.ValueTypeNames.Float).Set(total_width)
    stack.CreateAttribute("live:leftInnerSafetyM", Sdf.ValueTypeNames.Float).Set(
        LEFT_INNER_SAFETY_X_M
    )
    stack.CreateAttribute("live:truckLaneWidthM", Sdf.ValueTypeNames.Float).Set(
        TRUCK_LANE_WIDTH_X_M
    )
    stack.CreateAttribute("live:rightInnerSafetyM", Sdf.ValueTypeNames.Float).Set(
        RIGHT_INNER_SAFETY_X_M
    )
    stack.CreateAttribute("live:containerLengthM", Sdf.ValueTypeNames.Float).Set(
        container_length_y
    )
    stack.CreateAttribute("live:containerWidthVisualM", Sdf.ValueTypeNames.Float).Set(
        container_width
    )
    stack.CreateAttribute("live:tierGapVisualM", Sdf.ValueTypeNames.Float).Set(TIER_GAP_Z_M)

    for row_index, height in enumerate(heights, start=1):
        x = column_centers[row_index - 1]
        for tier in range(1, height + 1):
            mat = materials[(row_index * 3 + tier * 5) % len(materials)]
            z = (
                PAD_TOP_Z_M
                + STACK_FLOOR_CLEARANCE_M
                + (tier - 0.5) * CONTAINER_VISUAL_HEIGHT_Z_M
                + (tier - 1) * TIER_GAP_Z_M
            )
            container_path = f"{stack_path}/R{row_index:02d}_T{tier:02d}_Container"
            create_container_box(
                stage,
                container_path,
                Gf.Vec3d(x, bay_center_y, z),
                Gf.Vec3f(container_width, container_length_y, CONTAINER_VISUAL_HEIGHT_Z_M),
                mat,
                bay_id=normalized_bay_id,
                row=row_index,
                tier=tier,
                tiers=height,
            )
            create_container_details(
                stage,
                container_path,
                Gf.Vec3d(x, bay_center_y, z),
                Gf.Vec3f(container_width, container_length_y, CONTAINER_VISUAL_HEIGHT_Z_M),
            )

    outline_pad = 0.35
    stack_x_min = leftmost_stack_x - row_width * 0.5 - outline_pad
    stack_x_max = leftmost_stack_x + (len(heights) - 1) * row_pitch + row_width * 0.5 + outline_pad
    if show_ground_markings:
        create_layout_outline(
            stage,
            f"{stack_path}/BayMap_Outline",
            stack_x_min,
            stack_x_max,
            bay_center_y - container_length_y * 0.5 - outline_pad,
            bay_center_y + container_length_y * 0.5 + outline_pad,
        )
        create_yard_ground_markings(
            stage,
            f"{stack_path}/GroundMarkings",
            block,
            bay_number,
            bay_center_y,
            stack_x_min,
            stack_x_max,
            column_centers,
        )

    layer = stage.GetRootLayer()
    layer.customLayerData = {
        "description": "Runtime container instances generated from live bay-map data",
        "bayId": normalized_bay_id,
        "bayMapPattern": pattern,
        "layoutMode": "six_container_columns_plus_one_truck_lane",
        "physicsMode": "visual_only_no_collision",
        "containerCount": sum(heights),
        "containerColumnCount": len(heights),
        "truckLaneCount": 1,
        "containerActualHeightMeters": CONTAINER_ACTUAL_HEIGHT_M,
        "containerVisualHeightMeters": CONTAINER_VISUAL_HEIGHT_Z_M,
        "containerLengthMeters": container_length_y,
        "containerSizeFeet": container_size_ft,
        "leftInnerSafetyMeters": LEFT_INNER_SAFETY_X_M,
        "truckLaneWidthMeters": TRUCK_LANE_WIDTH_X_M,
        "rightInnerSafetyMeters": RIGHT_INNER_SAFETY_X_M,
        "rowOrder": "right_to_left",
        "rowHeights": ",".join(str(value) for value in heights),
    }
    layer.Save()
    return OUTPUT_PATH


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bay", default="1C/041")
    parser.add_argument("--pattern", default="413413")
    parser.add_argument("--container-size-ft", type=int, choices=(20, 40), default=20)
    parser.add_argument("--show-ground-markings", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else None)
    print(
        "Built live containers layer:",
        build_live_container_layer(
            args.bay,
            args.pattern,
            container_size_ft=args.container_size_ft,
            show_ground_markings=args.show_ground_markings,
        ),
    )


if __name__ == "__main__":
    main()
