"""Animate an outbound truck-loading task for the live 1C/004 bay map.

Scenario:
    An empty yard truck drives from 1C/010 toward 1C/003 and stops with its
    trailer centred at 1C/004.  The RTG then picks the top container from
    row 6 / tier 6 and lowers it onto the empty trailer.

This script is intentionally separate from apply_container_pick_place_demo.py,
so flip-box and outbound-loading demos can be applied independently.
"""

from __future__ import annotations

from pathlib import Path
import sys

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.append(str(SCRIPT_DIR))

from build_live_containers import (  # noqa: E402
    BAY_WIDTH_M,
    CONTAINER_VISUAL_HEIGHT_Z_M,
    LEFT_INNER_SAFETY_X_M,
    TRUCK_LANE_WIDTH_X_M,
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

BAY_ID = "1C/004"
TRUCK_START_BAY_ID = "1C/010"
STACK_PATH = "/World/LiveContainers/Bay_1C_004_40FT_Pattern_123456"
SOURCE_PREFIX = f"{STACK_PATH}/R06_T06_Container"
SOURCE_MAIN_PATH = f"{STACK_PATH}/R06_T06_Container"

TRUCK_ROOT = "/World/PortAndRTG"
TRUCK_PART_PREFIX = "LIVE_internal_lane_truck_"
TRUCK_TRAILER_KEYWORDS = ("trailer", "crossbar", "rail")
TRUCK_ORIGINAL_TRAILER_HIDE_KEYWORDS = (
    "skeletal_trailer_main_frame",
    "trailer_crossbar",
    "trailer_side_rail",
)
HIDDEN_PRELOADED_CONTAINER_PREFIXES = ("YLOAD", "Container_Box")

TIER_GAP_Z_M = 0.035
TRAILER_DEFAULT_TOP_Z_M = 0.68
TRAILER_BOX_CLEARANCE_Z_M = 0.04
TRUCK_OPERATING_LANE_CENTER_X_M = -4.65
ORIGINAL_TRAILER_LENGTH_Y_M = 4.25
EXTENDED_TRAILER_LENGTH_Y_M = 5.40
TRUCK_CAB_FORWARD_OFFSET_Y_M = -0.55
EXTENDED_TRAILER_ROOT = f"{TRUCK_ROOT}/LIVE_internal_lane_truck_extended_40ft_trailer"
UNIFIED_TRUCK_ROOT = f"{TRUCK_ROOT}/LIVE_internal_yard_truck_unified_40ft"

# Same visual calibration as apply_container_pick_place_demo.py.
GANTRY_HOME_USD_Y = 23.9
SPREADER_WORLD_X_AT_TROLLEY_ZERO = -3.7426
SPREADER_WORLD_Y_AT_GANTRY_HOME = 29.46
TROLLEY_USD_TO_WORLD_X = -1.4741
REFERENCE_PICK_CONTAINER_CENTER_Z = 3.3902355
REFERENCE_PICK_HOIST_Z = 0.90

FRAMES = {
    "start": 1,
    "truck_arrive": 48,
    "align_source": 72,
    "lower_pick": 105,
    "clamp": 122,
    "lift_source": 152,
    "travel_truck": 185,
    "lower_to_truck": 220,
    "release": 238,
    "raise_clear": 260,
}


def lane_center_x() -> float:
    """Visual truck operating centre inside the marked lane.

    The geometric lane centre is farther left and is partly hidden by the RTG
    leg from the default operator view.  The truck should wait on the inside
    side of the lane, close to the stack but still inside the 5 m drive lane.
    """

    lane_left_x = -BAY_WIDTH_M * 0.5 + LEFT_INNER_SAFETY_X_M
    lane_right_x = lane_left_x + TRUCK_LANE_WIDTH_X_M
    return min(max(TRUCK_OPERATING_LANE_CENTER_X_M, lane_left_x), lane_right_x)


def parse_bay_id(bay_id: str) -> tuple[str, int]:
    block, bay = bay_id.split("/", 1)
    return block.upper(), int(bay)


def bay_center_y(bay_id: str) -> float:
    block, bay = parse_bay_id(bay_id)
    return bay_scene_y_m(block, bay, "center")


def reset_and_set_samples(attr, samples) -> None:
    attr.Clear()
    attr.Set(samples[0][1])
    for frame, value in samples:
        attr.Set(value, Usd.TimeCode(frame))


def make_preview_material(
    stage: Usd.Stage, path: str, color: Gf.Vec3f, roughness: float = 0.65
):
    material = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, f"{path}/PreviewSurface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(color)
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness)
    material.CreateSurfaceOutput().ConnectToSource(
        shader.ConnectableAPI(), "surface"
    )
    return material


def get_translate(prim) -> Gf.Vec3d:
    attr = prim.GetAttribute("xformOp:translate")
    if not attr:
        raise RuntimeError(f"Missing xformOp:translate on {prim.GetPath()}")
    return Gf.Vec3d(attr.Get())


def translate_attr(stage: Usd.Stage, prim_path: str):
    prim = stage.GetPrimAtPath(prim_path)
    if not prim:
        raise RuntimeError(f"Missing prim: {prim_path}")
    attr = prim.GetAttribute("xformOp:translate")
    if not attr:
        raise RuntimeError(f"Missing xformOp:translate on {prim_path}")
    return attr


def drive_attr(stage: Usd.Stage, joint_path: str):
    joint = UsdPhysics.PrismaticJoint.Get(stage, joint_path)
    if not joint:
        raise RuntimeError(f"Missing joint: {joint_path}")
    return UsdPhysics.DriveAPI.Get(
        joint.GetPrim(), UsdPhysics.Tokens.linear
    ).GetTargetPositionAttr()


def hoist_for_container_center_z(container_center_z: float) -> float:
    return REFERENCE_PICK_HOIST_Z + (
        float(container_center_z) - REFERENCE_PICK_CONTAINER_CENTER_Z
    )


def clear_live_container_time_samples(stage: Usd.Stage) -> int:
    cleared = 0
    for prim in stage.Traverse():
        if not str(prim.GetPath()).startswith(STACK_PATH):
            continue
        attr = prim.GetAttribute("xformOp:translate")
        if not attr:
            continue
        value = attr.Get()
        attr.Clear()
        attr.Set(value)
        cleared += 1
    return cleared


def set_translate_samples(prim, samples: list[tuple[int, Gf.Vec3d]]) -> None:
    attr = prim.GetAttribute("xformOp:translate")
    if not attr:
        raise RuntimeError(f"Missing xformOp:translate on {prim.GetPath()}")
    reset_and_set_samples(attr, samples)


def apply_container_to_truck_animation() -> dict[str, object]:
    stage = Usd.Stage.Open(str(LIVE_CONTAINER_PATH))
    if not stage:
        raise RuntimeError(f"Cannot open {LIVE_CONTAINER_PATH}")

    cleared = clear_live_container_time_samples(stage)
    source_main = stage.GetPrimAtPath(SOURCE_MAIN_PATH)
    if not source_main:
        raise RuntimeError(f"Missing source container: {SOURCE_MAIN_PATH}")

    source_center = get_translate(source_main)
    truck_target_center = Gf.Vec3d(
        lane_center_x(),
        bay_center_y(BAY_ID),
        TRAILER_DEFAULT_TOP_Z_M
        + TRAILER_BOX_CLEARANCE_Z_M
        + CONTAINER_VISUAL_HEIGHT_Z_M * 0.5,
    )

    hoist_pick = hoist_for_container_center_z(source_center[2])
    hoist_place = hoist_for_container_center_z(truck_target_center[2])
    hoist_high = max(3.75, hoist_pick + 1.25, hoist_place + 1.25)

    source_lifted = Gf.Vec3d(
        source_center[0],
        source_center[1],
        source_center[2] + (hoist_high - hoist_pick),
    )
    truck_lifted = Gf.Vec3d(
        truck_target_center[0],
        truck_target_center[1],
        truck_target_center[2] + (hoist_high - hoist_place),
    )

    source_prims = [
        prim
        for prim in stage.Traverse()
        if str(prim.GetPath()).startswith(SOURCE_PREFIX)
        and prim.GetAttribute("xformOp:translate")
    ]
    for prim in source_prims:
        original = get_translate(prim)
        rel = original - source_center
        set_translate_samples(
            prim,
            [
                (FRAMES["start"], original),
                (FRAMES["lower_pick"], original),
                (FRAMES["clamp"], original),
                (FRAMES["lift_source"], source_lifted + rel),
                (FRAMES["travel_truck"], truck_lifted + rel),
                (FRAMES["lower_to_truck"], truck_target_center + rel),
                (FRAMES["release"], truck_target_center + rel),
                (FRAMES["raise_clear"], truck_target_center + rel),
            ],
        )
        prim.CreateAttribute("live:outboundDemoRole", Sdf.ValueTypeNames.String).Set(
            "R06_T06_loaded_to_empty_truck"
        )

    stage.GetRootLayer().customLayerData.update(
        {
            "outboundTruckLoadingDemoEnabled": True,
            "outboundTruckLoadingSource": "R06/T06",
            "outboundTruckLoadingTarget": "empty yard truck trailer at 1C/004",
            "previousLiveContainerSamplesCleared": cleared,
        }
    )
    stage.GetRootLayer().Save()
    return {
        "sourceCenter": tuple(round(v, 4) for v in source_center),
        "truckTargetCenter": tuple(round(v, 4) for v in truck_target_center),
        "animatedPrims": len(source_prims),
        "clearedTranslateAttrs": cleared,
        "hoistPick": round(hoist_pick, 4),
        "hoistPlace": round(hoist_place, 4),
        "hoistHigh": round(hoist_high, 4),
    }


def top_level_truck_prims(stage: Usd.Stage):
    return [
        prim
        for prim in stage.Traverse()
        if str(prim.GetParent().GetPath()) == TRUCK_ROOT
        and prim.GetName().startswith(TRUCK_PART_PREFIX)
        and prim.GetAttribute("xformOp:translate")
    ]


def bbox_center_for_prims(stage: Usd.Stage, prims) -> Gf.Vec3d:
    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
        useExtentsHint=False,
    )
    box = Gf.BBox3d()
    for prim in prims:
        box = Gf.BBox3d.Combine(box, cache.ComputeWorldBound(prim))
    aligned = box.ComputeAlignedRange()
    return (aligned.GetMin() + aligned.GetMax()) * 0.5


def bbox_center_for_prim(stage: Usd.Stage, prim, frame=Usd.TimeCode.Default()) -> Gf.Vec3d:
    cache = UsdGeom.BBoxCache(
        frame,
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
        useExtentsHint=False,
    )
    aligned = cache.ComputeWorldBound(prim).ComputeAlignedRange()
    return (aligned.GetMin() + aligned.GetMax()) * 0.5


def truck_part_visual_adjustment_y(
    stage: Usd.Stage, prim, trailer_center: Gf.Vec3d
) -> float:
    """Return a per-part visual Y offset for the extended 40 ft trailer.

    The source truck was drawn with a short trailer.  We keep wheels and cab
    unscaled, hide the old short frame, and only redistribute visible wheel
    groups so the new longer trailer reads correctly.
    """

    name = prim.GetName()
    if any(token in name for token in ("wheel_0", "wheel_hub_0")):
        return TRUCK_CAB_FORWARD_OFFSET_Y_M
    if any(
        token in name
        for token in ("white_cab", "windshield", "side_window", "headlight")
    ):
        return TRUCK_CAB_FORWARD_OFFSET_Y_M
    if any(token in name for token in ("wheel_1", "wheel_hub_1", "wheel_2", "wheel_hub_2", "wheel_3", "wheel_hub_3")):
        center = bbox_center_for_prim(stage, prim)
        factor = EXTENDED_TRAILER_LENGTH_Y_M / ORIGINAL_TRAILER_LENGTH_Y_M
        return (center[1] - trailer_center[1]) * (factor - 1.0)
    return 0.0


def create_extended_trailer_cube(
    stage: Usd.Stage,
    path: str,
    start_center: Gf.Vec3d,
    stop_center: Gf.Vec3d,
    relative_center: Gf.Vec3d,
    size: Gf.Vec3f,
    material,
) -> None:
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1.0)
    prim = cube.GetPrim()
    xform = UsdGeom.Xformable(prim)
    for op in xform.GetOrderedXformOps():
        op.GetAttr().Clear()
    xform.ClearXformOpOrder()
    translate_op = xform.AddTranslateOp()
    scale_op = xform.AddScaleOp()
    translate_attr = translate_op.GetAttr()
    scale_op.GetAttr().Set(size)
    reset_and_set_samples(
        translate_attr,
        [
            (FRAMES["start"], start_center + relative_center),
            (FRAMES["truck_arrive"], stop_center + relative_center),
            (FRAMES["raise_clear"], stop_center + relative_center),
        ],
    )
    UsdShade.MaterialBindingAPI(prim).Bind(material)
    prim.CreateAttribute("live:truckTrailerExtension", Sdf.ValueTypeNames.Bool).Set(True)


def create_extended_trailer_visual(
    stage: Usd.Stage, start_center: Gf.Vec3d, stop_center: Gf.Vec3d
) -> None:
    rail_mat = make_preview_material(
        stage,
        f"{TRUCK_ROOT}/Looks/ExtendedTruckTrailerDarkSteel",
        Gf.Vec3f(0.075, 0.070, 0.065),
    )
    deck_mat = make_preview_material(
        stage,
        f"{TRUCK_ROOT}/Looks/ExtendedTruckTrailerDeck",
        Gf.Vec3f(0.58, 0.40, 0.22),
        roughness=0.78,
    )
    y_len = EXTENDED_TRAILER_LENGTH_Y_M
    create_extended_trailer_cube(
        stage,
        f"{EXTENDED_TRAILER_ROOT}/MainFrame",
        start_center,
        stop_center,
        Gf.Vec3d(0.0, 0.0, -0.08),
        Gf.Vec3f(0.72, y_len - 0.05, 0.12),
        rail_mat,
    )
    create_extended_trailer_cube(
        stage,
        f"{EXTENDED_TRAILER_ROOT}/DeckSurface",
        start_center,
        stop_center,
        Gf.Vec3d(0.0, 0.0, 0.135),
        Gf.Vec3f(1.04, y_len, 0.035),
        deck_mat,
    )
    for side_name, x in (("LeftRail", -0.48), ("RightRail", 0.48)):
        create_extended_trailer_cube(
            stage,
            f"{EXTENDED_TRAILER_ROOT}/{side_name}",
            start_center,
            stop_center,
            Gf.Vec3d(x, 0.0, 0.10),
            Gf.Vec3f(0.07, y_len, 0.08),
            rail_mat,
        )


def define_cube(
    stage: Usd.Stage,
    path: str,
    center: Gf.Vec3d,
    size: Gf.Vec3f,
    material,
) -> None:
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1.0)
    prim = cube.GetPrim()
    xform = UsdGeom.Xformable(prim)
    xform.ClearXformOpOrder()
    xform.AddTranslateOp().GetAttr().Set(center)
    xform.AddScaleOp().GetAttr().Set(size)
    UsdShade.MaterialBindingAPI(prim).Bind(material)


def create_unified_truck_visual(
    stage: Usd.Stage, start_center: Gf.Vec3d, stop_center: Gf.Vec3d
) -> None:
    """Create one animated truck root so cab, wheels, and trailer stay together."""

    root = UsdGeom.Xform.Define(stage, UNIFIED_TRUCK_ROOT)
    root_attr = root.AddTranslateOp().GetAttr()
    reset_and_set_samples(
        root_attr,
        [
            (FRAMES["start"], Gf.Vec3d(start_center[0], start_center[1], 0.0)),
            (
                FRAMES["truck_arrive"],
                Gf.Vec3d(stop_center[0], stop_center[1], 0.0),
            ),
            (FRAMES["raise_clear"], Gf.Vec3d(stop_center[0], stop_center[1], 0.0)),
        ],
    )
    root.GetPrim().CreateAttribute("live:truckRole", Sdf.ValueTypeNames.String).Set(
        "unified_empty_40ft_yard_truck"
    )

    white = make_preview_material(
        stage, f"{TRUCK_ROOT}/Looks/UnifiedTruckCabWhite", Gf.Vec3f(0.90, 0.88, 0.80)
    )
    black = make_preview_material(
        stage, f"{TRUCK_ROOT}/Looks/UnifiedTruckBlack", Gf.Vec3f(0.015, 0.015, 0.014)
    )
    glass = make_preview_material(
        stage, f"{TRUCK_ROOT}/Looks/UnifiedTruckGlass", Gf.Vec3f(0.04, 0.08, 0.11)
    )
    steel = make_preview_material(
        stage, f"{TRUCK_ROOT}/Looks/UnifiedTruckSteel", Gf.Vec3f(0.075, 0.070, 0.065)
    )
    deck = make_preview_material(
        stage,
        f"{TRUCK_ROOT}/Looks/UnifiedTruckWoodDeck",
        Gf.Vec3f(0.58, 0.40, 0.22),
        roughness=0.78,
    )

    # Trailer local coordinates: root XY is trailer centre.
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

    # Cab is in front of the trailer and is part of the same root.
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


def apply_truck_and_rtg_animation(
    source_x: float,
    source_y: float,
    target_x: float,
    target_y: float,
    hoist_pick: float,
    hoist_place: float,
    hoist_high: float,
) -> dict[str, object]:
    stage = Usd.Stage.Open(str(SCENE_PATH))
    if not stage:
        raise RuntimeError(f"Cannot open {SCENE_PATH}")

    truck_prims = top_level_truck_prims(stage)
    if not truck_prims:
        raise RuntimeError(f"Missing truck prims with prefix {TRUCK_PART_PREFIX}")
    trailer_prims = [
        prim
        for prim in truck_prims
        if any(keyword in prim.GetName() for keyword in TRUCK_TRAILER_KEYWORDS)
    ]
    trailer_center = bbox_center_for_prims(stage, trailer_prims or truck_prims)
    start_center = Gf.Vec3d(lane_center_x(), bay_center_y(TRUCK_START_BAY_ID), trailer_center[2])
    stop_center = Gf.Vec3d(lane_center_x(), target_y, trailer_center[2])
    start_delta = start_center - trailer_center
    stop_delta = stop_center - trailer_center

    for prim in truck_prims:
        UsdGeom.Imageable(prim).CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)
    extended_trailer = stage.GetPrimAtPath(EXTENDED_TRAILER_ROOT)
    if extended_trailer:
        UsdGeom.Imageable(extended_trailer).CreateVisibilityAttr().Set(
            UsdGeom.Tokens.invisible
        )

    create_unified_truck_visual(stage, start_center, stop_center)

    for prim in stage.Traverse():
        if prim.GetName().startswith(HIDDEN_PRELOADED_CONTAINER_PREFIXES):
            UsdGeom.Imageable(prim).CreateVisibilityAttr().Set(
                UsdGeom.Tokens.invisible
            )

    nominal_gantry_y = gantry_bay_id_to_usd(BAY_ID)
    gantry_y = GANTRY_HOME_USD_Y + (source_y - SPREADER_WORLD_Y_AT_GANTRY_HOME)
    trolley_source = (
        source_x - SPREADER_WORLD_X_AT_TROLLEY_ZERO
    ) / TROLLEY_USD_TO_WORLD_X
    trolley_truck = (
        target_x - SPREADER_WORLD_X_AT_TROLLEY_ZERO
    ) / TROLLEY_USD_TO_WORLD_X

    gantry_samples = (
        (FRAMES["start"], gantry_y),
        (FRAMES["raise_clear"], gantry_y),
    )
    trolley_samples = (
        (FRAMES["start"], trolley_source),
        (FRAMES["lift_source"], trolley_source),
        (FRAMES["travel_truck"], trolley_truck),
        (FRAMES["raise_clear"], trolley_truck),
    )
    hoist_samples = (
        (FRAMES["start"], hoist_high),
        (FRAMES["align_source"], hoist_high),
        (FRAMES["lower_pick"], hoist_pick),
        (FRAMES["clamp"], hoist_pick),
        (FRAMES["lift_source"], hoist_high),
        (FRAMES["travel_truck"], hoist_high),
        (FRAMES["lower_to_truck"], hoist_place),
        (FRAMES["release"], hoist_place),
        (FRAMES["raise_clear"], hoist_high),
    )

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
    points_attr.Set(rope_points(hoist_high))
    for frame, value in hoist_samples:
        points_attr.Set(rope_points(value), Usd.TimeCode(frame))

    stage.SetStartTimeCode(FRAMES["start"])
    stage.SetEndTimeCode(FRAMES["raise_clear"])
    stage.SetFramesPerSecond(24)
    stage.SetTimeCodesPerSecond(24)
    stage.GetRootLayer().customLayerData.update(
        {
            "outboundTruckLoadingDemoEnabled": True,
            "outboundTruckLoadingDemo": "empty truck 1C/010 -> 1C/004, load R06/T06",
            "outboundTruckHiddenPreloadPrefixes": ",".join(
                HIDDEN_PRELOADED_CONTAINER_PREFIXES
            ),
        }
    )
    stage.GetRootLayer().Save()
    return {
        "truckPartCount": len(truck_prims),
        "trailerStartCenter": tuple(round(v, 4) for v in start_center),
        "trailerStopCenter": tuple(round(v, 4) for v in stop_center),
        "gantryY": round(gantry_y, 4),
        "nominalGantryY": round(nominal_gantry_y, 4),
        "trolleySourceX": round(trolley_source, 4),
        "trolleyTruckX": round(trolley_truck, 4),
    }


def apply_outbound_truck_loading_demo() -> dict[str, object]:
    container_result = apply_container_to_truck_animation()
    source_x, source_y, _ = container_result["sourceCenter"]
    target_x, target_y, _ = container_result["truckTargetCenter"]
    rtg_result = apply_truck_and_rtg_animation(
        source_x,
        source_y,
        target_x,
        target_y,
        container_result["hoistPick"],
        container_result["hoistPlace"],
        container_result["hoistHigh"],
    )
    result = {"container": container_result, "rtgAndTruck": rtg_result}
    print("Applied outbound truck-loading demo:", result)
    return result


if __name__ == "__main__":
    apply_outbound_truck_loading_demo()
