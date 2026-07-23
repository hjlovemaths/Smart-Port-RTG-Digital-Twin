"""Apply the three-operation task sequence described by omniverse/task.txt.

Sequence:
    1. Inbound / unload truck at 1C/004, pattern 123456:
       RTG starts at 1C/017, moves to 1C/004.  A loaded 40 ft yard truck
       arrives from about ten bays away, the spreader picks the truck box and
       places it onto row 2 / tier 3.  The empty truck drives to 1C/001 and
       disappears, and the task-1 bay map is cleared.

    2. Outbound / load truck at 1C/020, pattern 103020:
       RTG moves to 1C/020.  An empty truck arrives from about ten bays away.
       The spreader picks row 3 / tier 3 and places it onto the truck.  The
       loaded truck drives to 1C/001 and disappears, and the task-2 bay map is
       cleared.

    3. Re-handle at 1C/010, pattern 123456:
       RTG moves to 1C/010 and moves row 1 / tier 1 to row 2 / tier 3.
"""

from __future__ import annotations

from pathlib import Path
import sys

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.append(str(SCRIPT_DIR))

from apply_outbound_truck_loading_demo import (  # noqa: E402
    EXTENDED_TRAILER_LENGTH_Y_M,
    HIDDEN_PRELOADED_CONTAINER_PREFIXES,
    REFERENCE_PICK_CONTAINER_CENTER_Z,
    REFERENCE_PICK_HOIST_Z,
    TRAILER_BOX_CLEARANCE_Z_M,
    TRAILER_DEFAULT_TOP_Z_M,
    TRUCK_OPERATING_LANE_CENTER_X_M,
    UNIFIED_TRUCK_ROOT,
    define_cube,
    make_preview_material,
    reset_and_set_samples,
)
from build_live_containers import (  # noqa: E402
    BAY_WIDTH_M,
    CONTAINER_COLORS,
    CONTAINER_VISUAL_HEIGHT_Z_M,
    CONTAINER_WIDTH_X_M,
    LEFT_INNER_SAFETY_X_M,
    LIVE_ROOT_PATH,
    PAD_TOP_Z_M,
    ROW_GAP_X_M,
    STACK_FLOOR_CLEARANCE_M,
    TIER_GAP_Z_M,
    TRUCK_LANE_WIDTH_X_M,
    create_container_box,
    create_container_details,
    create_truck_lane_markings,
    material,
    parse_pattern,
    reference_first_row_center_x_m,
)
from build_rtg_simready import rope_points  # noqa: E402
from rtg_live_control import (  # noqa: E402
    GANTRY_JOINT_PATH,
    GANTRY_PATH,
    HOIST_JOINT_PATH,
    HOIST_PATH,
    ROPE_SYSTEM_PATH,
    TROLLEY_JOINT_PATH,
    TROLLEY_PATH,
    gantry_bay_id_to_usd,
)
from yard_coordinate_mapping import bay_scene_y_m  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCENE_PATH = PROJECT_ROOT / "omniverse" / "scenes" / "smart_port.usda"
LIVE_CONTAINER_PATH = PROJECT_ROOT / "omniverse" / "scenes" / "live_containers.usda"
TRUCK_ROOT = "/World/PortAndRTG"
TRUCK_PART_PREFIX = "LIVE_internal_lane_truck_"

TRUCK_BED_CENTER_Z = (
    TRAILER_DEFAULT_TOP_Z_M
    + TRAILER_BOX_CLEARANCE_Z_M
    + CONTAINER_VISUAL_HEIGHT_Z_M * 0.5
)

GANTRY_HOME_USD_Y = 23.9
SPREADER_WORLD_X_AT_TROLLEY_ZERO = -3.7426
SPREADER_WORLD_Y_AT_GANTRY_HOME = 29.46
TROLLEY_USD_TO_WORLD_X = -1.4741

FRAMES = {
    # Task 1: truck -> yard at 1C/004.
    "t1_start": 1,
    "t1_rtg_arrive": 60,
    "t1_truck_appear": 70,
    "t1_truck_arrive": 110,
    "t1_lower_pick": 128,
    "t1_clamp": 145,
    "t1_lift": 170,
    "t1_travel_stack": 198,
    "t1_lower_place": 222,
    "t1_release": 238,
    "t1_raise": 252,
    "t1_truck_gone": 292,
    "t1_clear": 305,
    # Task 2: yard -> truck at 1C/020.
    "t2_start": 320,
    "t2_rtg_arrive": 370,
    "t2_truck_appear": 385,
    "t2_truck_arrive": 430,
    "t2_lower_pick": 462,
    "t2_clamp": 480,
    "t2_lift": 510,
    "t2_travel_truck": 542,
    "t2_lower_place": 575,
    "t2_release": 598,
    "t2_raise": 620,
    "t2_truck_gone": 665,
    "t2_clear": 680,
    # Task 3: re-handle at 1C/010.
    "t3_start": 700,
    "t3_rtg_arrive": 745,
    "t3_lower_pick": 770,
    "t3_clamp": 788,
    "t3_lift": 815,
    "t3_travel_target": 845,
    "t3_lower_place": 875,
    "t3_release": 895,
    "t3_raise": 925,
}


def parse_bay_id(bay_id: str) -> tuple[str, int]:
    block, bay = bay_id.split("/", 1)
    return block.upper(), int(bay)


def bay_center_y(bay_id: str) -> float:
    block, bay = parse_bay_id(bay_id)
    return bay_scene_y_m(block, bay, "center")


def lane_center_x() -> float:
    lane_left = -BAY_WIDTH_M * 0.5 + LEFT_INNER_SAFETY_X_M
    lane_right = lane_left + TRUCK_LANE_WIDTH_X_M
    return min(max(TRUCK_OPERATING_LANE_CENTER_X_M, lane_left), lane_right)


def container_center_y_40ft(bay_id: str) -> float:
    block, bay = parse_bay_id(bay_id)
    return (
        bay_scene_y_m(block, bay - 1, "center")
        + bay_scene_y_m(block, bay + 1, "center")
    ) * 0.5


def row_centers_x(row_count: int) -> list[float]:
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


def hoist_for_container_center_z(container_center_z: float) -> float:
    return REFERENCE_PICK_HOIST_Z + (
        float(container_center_z) - REFERENCE_PICK_CONTAINER_CENTER_Z
    )


def gantry_for_bay(bay_id: str) -> float:
    return GANTRY_HOME_USD_Y + (
        bay_center_y(bay_id) - SPREADER_WORLD_Y_AT_GANTRY_HOME
    )


def trolley_for_x(x: float) -> float:
    return (x - SPREADER_WORLD_X_AT_TROLLEY_ZERO) / TROLLEY_USD_TO_WORLD_X


def drive_attr(stage: Usd.Stage, joint_path: str):
    joint = UsdPhysics.PrismaticJoint.Get(stage, joint_path)
    if not joint:
        raise RuntimeError(f"Missing joint: {joint_path}")
    return UsdPhysics.DriveAPI.Get(
        joint.GetPrim(), UsdPhysics.Tokens.linear
    ).GetTargetPositionAttr()


def translate_attr(stage: Usd.Stage, prim_path: str):
    prim = stage.GetPrimAtPath(prim_path)
    if not prim:
        raise RuntimeError(f"Missing prim: {prim_path}")
    attr = prim.GetAttribute("xformOp:translate")
    if not attr:
        raise RuntimeError(f"Missing xformOp:translate on {prim_path}")
    return attr


def set_visibility_samples(prim, samples: list[tuple[int, str]]) -> None:
    attr = UsdGeom.Imageable(prim).CreateVisibilityAttr()
    attr.Clear()
    attr.Set(samples[0][1])
    for frame, value in samples:
        attr.Set(value, Usd.TimeCode(frame))


def get_translate(prim) -> Gf.Vec3d:
    attr = prim.GetAttribute("xformOp:translate")
    if not attr:
        raise RuntimeError(f"Missing xformOp:translate on {prim.GetPath()}")
    return Gf.Vec3d(attr.Get())


def animate_translate_subtree(
    stage: Usd.Stage,
    prefix: str,
    source_center: Gf.Vec3d,
    samples: list[tuple[int, Gf.Vec3d]],
) -> int:
    count = 0
    for prim in stage.Traverse():
        if not str(prim.GetPath()).startswith(prefix):
            continue
        attr = prim.GetAttribute("xformOp:translate")
        if not attr:
            continue
        original = Gf.Vec3d(attr.Get())
        rel = original - source_center
        reset_and_set_samples(attr, [(frame, center + rel) for frame, center in samples])
        count += 1
    return count


def create_sequence_stack(
    stage: Usd.Stage,
    stack_path: str,
    bay_id: str,
    pattern: str,
    materials,
) -> dict[tuple[int, int], Gf.Vec3d]:
    block, bay_number = parse_bay_id(bay_id)
    heights = parse_pattern(pattern)
    centers_x = row_centers_x(len(heights))
    bay_y = container_center_y_40ft(bay_id)
    row_tier_centers: dict[tuple[int, int], Gf.Vec3d] = {}

    stack = UsdGeom.Xform.Define(stage, stack_path).GetPrim()
    stack.CreateAttribute("live:bayId", Sdf.ValueTypeNames.String).Set(bay_id)
    stack.CreateAttribute("live:bayNumber", Sdf.ValueTypeNames.Int).Set(bay_number)
    stack.CreateAttribute("live:bayMapPattern", Sdf.ValueTypeNames.String).Set(pattern)
    stack.CreateAttribute("live:rowOrder", Sdf.ValueTypeNames.Token).Set("right_to_left")
    stack.CreateAttribute("live:containerSizeFt", Sdf.ValueTypeNames.Int).Set(40)

    for row_index, height in enumerate(heights, start=1):
        x = centers_x[row_index - 1]
        for tier in range(1, height + 1):
            center = Gf.Vec3d(x, bay_y, tier_center_z(tier))
            row_tier_centers[(row_index, tier)] = center
            container_path = f"{stack_path}/R{row_index:02d}_T{tier:02d}_Container"
            mat = materials[(row_index * 3 + tier * 5) % len(materials)]
            create_container_box(
                stage,
                container_path,
                center,
                Gf.Vec3f(
                    CONTAINER_WIDTH_X_M,
                    5.194,
                    CONTAINER_VISUAL_HEIGHT_Z_M,
                ),
                mat,
                bay_id=bay_id,
                row=row_index,
                tier=tier,
                tiers=height,
            )
            create_container_details(
                stage,
                container_path,
                center,
                Gf.Vec3f(CONTAINER_WIDTH_X_M, 5.194, CONTAINER_VISUAL_HEIGHT_Z_M),
            )
    return row_tier_centers


def create_task_truck_box(
    stage: Usd.Stage,
    path: str,
    center: Gf.Vec3d,
    mat,
    *,
    bay_id: str,
) -> None:
    create_container_box(
        stage,
        path,
        center,
        Gf.Vec3f(CONTAINER_WIDTH_X_M, 5.194, CONTAINER_VISUAL_HEIGHT_Z_M),
        mat,
        bay_id=bay_id,
        row=0,
        tier=0,
        tiers=1,
    )
    create_container_details(
        stage,
        path,
        center,
        Gf.Vec3f(CONTAINER_WIDTH_X_M, 5.194, CONTAINER_VISUAL_HEIGHT_Z_M),
    )


def create_live_container_sequence_layer() -> dict[str, object]:
    if LIVE_CONTAINER_PATH.exists():
        LIVE_CONTAINER_PATH.unlink()

    stage = Usd.Stage.CreateNew(str(LIVE_CONTAINER_PATH))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)

    world = stage.OverridePrim("/World")
    stage.SetDefaultPrim(world)
    root = UsdGeom.Xform.Define(stage, LIVE_ROOT_PATH).GetPrim()
    root.CreateAttribute("live:sequenceDemo", Sdf.ValueTypeNames.Bool).Set(True)
    root.CreateAttribute("live:sourceTaskFile", Sdf.ValueTypeNames.String).Set(
        "omniverse/task.txt"
    )

    materials = [
        material(stage, f"SequenceContainer_{name}", color)
        for name, color in CONTAINER_COLORS
    ]

    create_truck_lane_markings(stage, f"{LIVE_ROOT_PATH}/SequenceTruckLane")

    t1_stack = f"{LIVE_ROOT_PATH}/Task01_1C004_40FT_Pattern_123456"
    t2_stack = f"{LIVE_ROOT_PATH}/Task02_1C020_40FT_Pattern_103020"
    t3_stack = f"{LIVE_ROOT_PATH}/Task03_1C010_40FT_Pattern_123456"
    t1_centers = create_sequence_stack(stage, t1_stack, "1C/004", "123456", materials)
    t2_centers = create_sequence_stack(stage, t2_stack, "1C/020", "103020", materials)
    t3_centers = create_sequence_stack(stage, t3_stack, "1C/010", "123456", materials)

    set_visibility_samples(
        stage.GetPrimAtPath(t1_stack),
        [
            (FRAMES["t1_start"], UsdGeom.Tokens.inherited),
            (FRAMES["t1_clear"], UsdGeom.Tokens.invisible),
        ],
    )
    set_visibility_samples(
        stage.GetPrimAtPath(t2_stack),
        [
            (FRAMES["t1_start"], UsdGeom.Tokens.invisible),
            (FRAMES["t2_start"], UsdGeom.Tokens.inherited),
            (FRAMES["t2_clear"], UsdGeom.Tokens.invisible),
        ],
    )
    set_visibility_samples(
        stage.GetPrimAtPath(t3_stack),
        [
            (FRAMES["t1_start"], UsdGeom.Tokens.invisible),
            (FRAMES["t3_start"], UsdGeom.Tokens.inherited),
            (FRAMES["t3_raise"], UsdGeom.Tokens.inherited),
        ],
    )

    truck_x = lane_center_x()
    t1_truck_y_start = bay_center_y("1C/014")
    t1_truck_y_stop = container_center_y_40ft("1C/004")
    t2_truck_y_start = bay_center_y("1C/030")
    t2_truck_y_stop = container_center_y_40ft("1C/020")
    truck_y_exit = bay_center_y("1C/001")
    truck_box_z = TRUCK_BED_CENTER_Z

    t1_box_path = f"{LIVE_ROOT_PATH}/Task01_TruckLoadedBox_To_R02_T03"
    create_task_truck_box(
        stage,
        t1_box_path,
        Gf.Vec3d(truck_x, t1_truck_y_start, truck_box_z),
        materials[1],
        bay_id="1C/004",
    )
    set_visibility_samples(
        stage.GetPrimAtPath(t1_box_path),
        [
            (FRAMES["t1_start"], UsdGeom.Tokens.invisible),
            (FRAMES["t1_truck_appear"], UsdGeom.Tokens.inherited),
            (FRAMES["t1_clear"], UsdGeom.Tokens.invisible),
        ],
    )
    t1_target = Gf.Vec3d(t1_centers[(2, 2)][0], t1_centers[(2, 2)][1], tier_center_z(3))
    t1_pick_hoist = hoist_for_container_center_z(truck_box_z)
    t1_place_hoist = hoist_for_container_center_z(t1_target[2])
    t1_high = max(3.40, t1_pick_hoist + 1.35, t1_place_hoist + 1.35)
    animate_translate_subtree(
        stage,
        t1_box_path,
        Gf.Vec3d(truck_x, t1_truck_y_start, truck_box_z),
        [
            (FRAMES["t1_truck_appear"], Gf.Vec3d(truck_x, t1_truck_y_start, truck_box_z)),
            (FRAMES["t1_truck_arrive"], Gf.Vec3d(truck_x, t1_truck_y_stop, truck_box_z)),
            (FRAMES["t1_clamp"], Gf.Vec3d(truck_x, t1_truck_y_stop, truck_box_z)),
            (
                FRAMES["t1_lift"],
                Gf.Vec3d(truck_x, t1_truck_y_stop, truck_box_z + (t1_high - t1_pick_hoist)),
            ),
            (
                FRAMES["t1_travel_stack"],
                Gf.Vec3d(t1_target[0], t1_target[1], t1_target[2] + (t1_high - t1_place_hoist)),
            ),
            (FRAMES["t1_lower_place"], t1_target),
            (FRAMES["t1_raise"], t1_target),
        ],
    )

    # Task 2 source R03/T03 moves out to truck and leaves with it.
    t2_source_path = f"{t2_stack}/R03_T03_Container"
    t2_source = t2_centers[(3, 3)]
    t2_truck_target = Gf.Vec3d(truck_x, t2_truck_y_stop, truck_box_z)
    t2_pick_hoist = hoist_for_container_center_z(t2_source[2])
    t2_place_hoist = hoist_for_container_center_z(t2_truck_target[2])
    t2_high = max(3.70, t2_pick_hoist + 1.35, t2_place_hoist + 1.35)
    animate_translate_subtree(
        stage,
        t2_source_path,
        t2_source,
        [
            (FRAMES["t2_start"], t2_source),
            (FRAMES["t2_clamp"], t2_source),
            (
                FRAMES["t2_lift"],
                Gf.Vec3d(t2_source[0], t2_source[1], t2_source[2] + (t2_high - t2_pick_hoist)),
            ),
            (
                FRAMES["t2_travel_truck"],
                Gf.Vec3d(
                    t2_truck_target[0],
                    t2_truck_target[1],
                    t2_truck_target[2] + (t2_high - t2_place_hoist),
                ),
            ),
            (FRAMES["t2_lower_place"], t2_truck_target),
            (FRAMES["t2_release"], t2_truck_target),
            (FRAMES["t2_truck_gone"], Gf.Vec3d(truck_x, truck_y_exit, truck_box_z)),
        ],
    )

    # Task 3 source R01/T01 moves to R02/T03.
    t3_source_path = f"{t3_stack}/R01_T01_Container"
    t3_source = t3_centers[(1, 1)]
    t3_target = Gf.Vec3d(t3_centers[(2, 2)][0], t3_centers[(2, 2)][1], tier_center_z(3))
    t3_pick_hoist = hoist_for_container_center_z(t3_source[2])
    t3_place_hoist = hoist_for_container_center_z(t3_target[2])
    t3_high = max(3.30, t3_pick_hoist + 1.35, t3_place_hoist + 1.35)
    animate_translate_subtree(
        stage,
        t3_source_path,
        t3_source,
        [
            (FRAMES["t3_start"], t3_source),
            (FRAMES["t3_clamp"], t3_source),
            (
                FRAMES["t3_lift"],
                Gf.Vec3d(t3_source[0], t3_source[1], t3_source[2] + (t3_high - t3_pick_hoist)),
            ),
            (
                FRAMES["t3_travel_target"],
                Gf.Vec3d(t3_target[0], t3_target[1], t3_target[2] + (t3_high - t3_place_hoist)),
            ),
            (FRAMES["t3_lower_place"], t3_target),
            (FRAMES["t3_raise"], t3_target),
        ],
    )

    stage.SetStartTimeCode(FRAMES["t1_start"])
    stage.SetEndTimeCode(FRAMES["t3_raise"])
    stage.SetFramesPerSecond(24)
    stage.SetTimeCodesPerSecond(24)
    stage.GetRootLayer().customLayerData = {
        "description": "Three-task RTG operation sequence generated from omniverse/task.txt",
        "sequenceTasks": "inbound 1C/004; outbound 1C/020; rehandle 1C/010",
        "containerSizeFeet": 40,
        "taskCount": 3,
    }
    stage.GetRootLayer().Save()

    return {
        "t1": {
            "truckBoxPath": t1_box_path,
            "target": tuple(round(v, 4) for v in t1_target),
            "hoistPick": t1_pick_hoist,
            "hoistPlace": t1_place_hoist,
            "hoistHigh": t1_high,
        },
        "t2": {
            "sourcePath": t2_source_path,
            "target": tuple(round(v, 4) for v in t2_truck_target),
            "hoistPick": t2_pick_hoist,
            "hoistPlace": t2_place_hoist,
            "hoistHigh": t2_high,
        },
        "t3": {
            "sourcePath": t3_source_path,
            "target": tuple(round(v, 4) for v in t3_target),
            "hoistPick": t3_pick_hoist,
            "hoistPlace": t3_place_hoist,
            "hoistHigh": t3_high,
        },
    }


def ensure_sequence_truck(stage: Usd.Stage, root_samples, visibility_samples) -> None:
    # Hide older segmented/generated trucks so only the unified truck is visible.
    for prim in stage.Traverse():
        if (
            str(prim.GetParent().GetPath()) == TRUCK_ROOT
            and prim.GetName().startswith(TRUCK_PART_PREFIX)
        ):
            UsdGeom.Imageable(prim).CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)
        if str(prim.GetPath()).startswith(f"{TRUCK_ROOT}/LIVE_internal_lane_truck_extended_40ft_trailer"):
            UsdGeom.Imageable(prim).CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)

    stage.RemovePrim(UNIFIED_TRUCK_ROOT)
    truck = UsdGeom.Xform.Define(stage, UNIFIED_TRUCK_ROOT)
    attr = truck.AddTranslateOp().GetAttr()
    reset_and_set_samples(
        attr,
        [(frame, Gf.Vec3d(x, y, 0.0)) for frame, x, y in root_samples],
    )
    set_visibility_samples(truck.GetPrim(), visibility_samples)

    white = make_preview_material(
        stage, f"{TRUCK_ROOT}/Looks/SequenceTruckCabWhite", Gf.Vec3f(0.90, 0.88, 0.80)
    )
    black = make_preview_material(
        stage, f"{TRUCK_ROOT}/Looks/SequenceTruckBlack", Gf.Vec3f(0.015, 0.015, 0.014)
    )
    glass = make_preview_material(
        stage, f"{TRUCK_ROOT}/Looks/SequenceTruckGlass", Gf.Vec3f(0.04, 0.08, 0.11)
    )
    steel = make_preview_material(
        stage, f"{TRUCK_ROOT}/Looks/SequenceTruckSteel", Gf.Vec3f(0.075, 0.070, 0.065)
    )
    deck = make_preview_material(
        stage,
        f"{TRUCK_ROOT}/Looks/SequenceTruckWoodDeck",
        Gf.Vec3f(0.58, 0.40, 0.22),
        roughness=0.78,
    )

    define_cube(
        stage,
        f"{UNIFIED_TRUCK_ROOT}/TrailerMainFrame",
        Gf.Vec3d(0.0, 0.0, 0.46),
        Gf.Vec3f(0.72, EXTENDED_TRAILER_LENGTH_Y_M - 0.05, 0.12),
        steel,
    )
    define_cube(
        stage,
        f"{UNIFIED_TRUCK_ROOT}/TrailerDeck",
        Gf.Vec3d(0.0, 0.0, 0.68),
        Gf.Vec3f(1.04, EXTENDED_TRAILER_LENGTH_Y_M, 0.04),
        deck,
    )
    for side_name, x in (("LeftRail", -0.48), ("RightRail", 0.48)):
        define_cube(
            stage,
            f"{UNIFIED_TRUCK_ROOT}/Trailer{side_name}",
            Gf.Vec3d(x, 0.0, 0.64),
            Gf.Vec3f(0.07, EXTENDED_TRAILER_LENGTH_Y_M, 0.08),
            steel,
        )
    for index, rel_y in enumerate((-2.45, -1.225, 0.0, 1.225, 2.45), start=1):
        define_cube(
            stage,
            f"{UNIFIED_TRUCK_ROOT}/TrailerCrossbar_{index:02d}",
            Gf.Vec3d(0.0, rel_y, 0.62),
            Gf.Vec3f(1.05, 0.08, 0.08),
            steel,
        )
    define_cube(
        stage,
        f"{UNIFIED_TRUCK_ROOT}/CabBody",
        Gf.Vec3d(0.0, -3.35, 0.70),
        Gf.Vec3f(1.02, 1.00, 0.72),
        white,
    )
    define_cube(
        stage,
        f"{UNIFIED_TRUCK_ROOT}/CabWindshield",
        Gf.Vec3d(0.0, -3.86, 0.88),
        Gf.Vec3f(0.78, 0.035, 0.30),
        glass,
    )
    define_cube(
        stage,
        f"{UNIFIED_TRUCK_ROOT}/CabBumper",
        Gf.Vec3d(0.0, -3.89, 0.38),
        Gf.Vec3f(1.05, 0.07, 0.12),
        black,
    )
    for axle_index, y in enumerate((-3.35, -1.65, 1.75, 2.42), start=1):
        for side, x in (("L", -0.67), ("R", 0.67)):
            define_cube(
                stage,
                f"{UNIFIED_TRUCK_ROOT}/Wheel_{axle_index}_{side}",
                Gf.Vec3d(x, y, 0.36),
                Gf.Vec3f(0.18, 0.44, 0.44),
                black,
            )


def apply_rtg_and_truck_sequence(live_info: dict[str, object]) -> dict[str, object]:
    stage = Usd.Stage.Open(str(SCENE_PATH))
    if not stage:
        raise RuntimeError(f"Cannot open {SCENE_PATH}")

    for prim in stage.Traverse():
        if prim.GetName().startswith(HIDDEN_PRELOADED_CONTAINER_PREFIXES):
            UsdGeom.Imageable(prim).CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)

    truck_x = lane_center_x()
    truck_samples = [
        (FRAMES["t1_start"], truck_x, bay_center_y("1C/014")),
        (FRAMES["t1_truck_appear"], truck_x, bay_center_y("1C/014")),
        (FRAMES["t1_truck_arrive"], truck_x, container_center_y_40ft("1C/004")),
        (FRAMES["t1_raise"], truck_x, container_center_y_40ft("1C/004")),
        (FRAMES["t1_truck_gone"], truck_x, bay_center_y("1C/001")),
        (FRAMES["t2_truck_appear"], truck_x, bay_center_y("1C/030")),
        (FRAMES["t2_truck_arrive"], truck_x, container_center_y_40ft("1C/020")),
        (FRAMES["t2_raise"], truck_x, container_center_y_40ft("1C/020")),
        (FRAMES["t2_truck_gone"], truck_x, bay_center_y("1C/001")),
    ]
    ensure_sequence_truck(
        stage,
        truck_samples,
        [
            (FRAMES["t1_start"], UsdGeom.Tokens.invisible),
            (FRAMES["t1_truck_appear"], UsdGeom.Tokens.inherited),
            (FRAMES["t1_truck_gone"], UsdGeom.Tokens.inherited),
            (FRAMES["t1_truck_gone"] + 1, UsdGeom.Tokens.invisible),
            (FRAMES["t2_truck_appear"], UsdGeom.Tokens.inherited),
            (FRAMES["t2_truck_gone"], UsdGeom.Tokens.inherited),
            (FRAMES["t2_truck_gone"] + 1, UsdGeom.Tokens.invisible),
        ],
    )

    row_x = row_centers_x(6)
    t1_target_x = row_x[1]
    t2_source_x = row_x[2]
    t3_source_x = row_x[0]
    t3_target_x = row_x[1]
    t1_y = container_center_y_40ft("1C/004")
    t2_y = container_center_y_40ft("1C/020")
    t3_y = container_center_y_40ft("1C/010")

    t1_pick = live_info["t1"]["hoistPick"]
    t1_place = live_info["t1"]["hoistPlace"]
    t1_high = live_info["t1"]["hoistHigh"]
    t2_pick = live_info["t2"]["hoistPick"]
    t2_place = live_info["t2"]["hoistPlace"]
    t2_high = live_info["t2"]["hoistHigh"]
    t3_pick = live_info["t3"]["hoistPick"]
    t3_place = live_info["t3"]["hoistPlace"]
    t3_high = live_info["t3"]["hoistHigh"]

    gantry_samples = [
        (FRAMES["t1_start"], gantry_for_bay("1C/017")),
        (FRAMES["t1_rtg_arrive"], gantry_for_bay("1C/004")),
        (FRAMES["t1_clear"], gantry_for_bay("1C/004")),
        (FRAMES["t2_start"], gantry_for_bay("1C/004")),
        (FRAMES["t2_rtg_arrive"], gantry_for_bay("1C/020")),
        (FRAMES["t2_clear"], gantry_for_bay("1C/020")),
        (FRAMES["t3_start"], gantry_for_bay("1C/020")),
        (FRAMES["t3_rtg_arrive"], gantry_for_bay("1C/010")),
        (FRAMES["t3_raise"], gantry_for_bay("1C/010")),
    ]
    trolley_samples = [
        (FRAMES["t1_start"], trolley_for_x(truck_x)),
        (FRAMES["t1_lift"], trolley_for_x(truck_x)),
        (FRAMES["t1_travel_stack"], trolley_for_x(t1_target_x)),
        (FRAMES["t1_raise"], trolley_for_x(t1_target_x)),
        (FRAMES["t2_start"], trolley_for_x(t2_source_x)),
        (FRAMES["t2_lift"], trolley_for_x(t2_source_x)),
        (FRAMES["t2_travel_truck"], trolley_for_x(truck_x)),
        (FRAMES["t2_raise"], trolley_for_x(truck_x)),
        (FRAMES["t3_start"], trolley_for_x(t3_source_x)),
        (FRAMES["t3_lift"], trolley_for_x(t3_source_x)),
        (FRAMES["t3_travel_target"], trolley_for_x(t3_target_x)),
        (FRAMES["t3_raise"], trolley_for_x(t3_target_x)),
    ]
    hoist_samples = [
        (FRAMES["t1_start"], t1_high),
        (FRAMES["t1_truck_arrive"], t1_high),
        (FRAMES["t1_lower_pick"], t1_pick),
        (FRAMES["t1_clamp"], t1_pick),
        (FRAMES["t1_lift"], t1_high),
        (FRAMES["t1_travel_stack"], t1_high),
        (FRAMES["t1_lower_place"], t1_place),
        (FRAMES["t1_release"], t1_place),
        (FRAMES["t1_raise"], t1_high),
        (FRAMES["t2_start"], t2_high),
        (FRAMES["t2_truck_arrive"], t2_high),
        (FRAMES["t2_lower_pick"], t2_pick),
        (FRAMES["t2_clamp"], t2_pick),
        (FRAMES["t2_lift"], t2_high),
        (FRAMES["t2_travel_truck"], t2_high),
        (FRAMES["t2_lower_place"], t2_place),
        (FRAMES["t2_release"], t2_place),
        (FRAMES["t2_raise"], t2_high),
        (FRAMES["t3_start"], t3_high),
        (FRAMES["t3_rtg_arrive"], t3_high),
        (FRAMES["t3_lower_pick"], t3_pick),
        (FRAMES["t3_clamp"], t3_pick),
        (FRAMES["t3_lift"], t3_high),
        (FRAMES["t3_travel_target"], t3_high),
        (FRAMES["t3_lower_place"], t3_place),
        (FRAMES["t3_release"], t3_place),
        (FRAMES["t3_raise"], t3_high),
    ]

    reset_and_set_samples(
        translate_attr(stage, GANTRY_PATH),
        [(frame, Gf.Vec3d(0.0, value, 0.0)) for frame, value in gantry_samples],
    )
    reset_and_set_samples(drive_attr(stage, GANTRY_JOINT_PATH), gantry_samples)
    reset_and_set_samples(
        translate_attr(stage, TROLLEY_PATH),
        [(frame, Gf.Vec3d(value, 0.0, 0.0)) for frame, value in trolley_samples],
    )
    reset_and_set_samples(drive_attr(stage, TROLLEY_JOINT_PATH), trolley_samples)
    reset_and_set_samples(
        translate_attr(stage, HOIST_PATH),
        [(frame, Gf.Vec3d(0.0, 0.0, value)) for frame, value in hoist_samples],
    )
    reset_and_set_samples(drive_attr(stage, HOIST_JOINT_PATH), hoist_samples)

    ropes = UsdGeom.BasisCurves.Get(stage, ROPE_SYSTEM_PATH)
    if not ropes:
        raise RuntimeError(f"Missing dynamic ropes: {ROPE_SYSTEM_PATH}")
    points_attr = ropes.GetPointsAttr()
    points_attr.Clear()
    points_attr.Set(rope_points(t1_high))
    for frame, value in hoist_samples:
        points_attr.Set(rope_points(value), Usd.TimeCode(frame))

    stage.SetStartTimeCode(FRAMES["t1_start"])
    stage.SetEndTimeCode(FRAMES["t3_raise"])
    stage.SetFramesPerSecond(24)
    stage.SetTimeCodesPerSecond(24)
    stage.GetRootLayer().customLayerData.update(
        {
            "taskSequenceDemoEnabled": True,
            "taskSequenceSource": "omniverse/task.txt",
            "taskSequenceFramePlan": "1-305 inbound, 320-680 outbound, 700-925 rehandle",
            "taskSequenceTruck": UNIFIED_TRUCK_ROOT,
        }
    )
    stage.GetRootLayer().Save()

    return {
        "gantryStart": round(gantry_for_bay("1C/017"), 4),
        "gantryTask1": round(gantry_for_bay("1C/004"), 4),
        "gantryTask2": round(gantry_for_bay("1C/020"), 4),
        "gantryTask3": round(gantry_for_bay("1C/010"), 4),
        "taskYs": {"1C/004": round(t1_y, 4), "1C/020": round(t2_y, 4), "1C/010": round(t3_y, 4)},
    }


def apply_task_sequence_demo() -> dict[str, object]:
    live_info = create_live_container_sequence_layer()
    scene_info = apply_rtg_and_truck_sequence(live_info)
    result = {"live": live_info, "scene": scene_info}
    print("Applied task sequence demo:", result)
    return result


if __name__ == "__main__":
    apply_task_sequence_demo()
