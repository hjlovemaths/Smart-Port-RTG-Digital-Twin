"""Animate a simple RTG pick-and-place operation for the live bay map.

Scenario:
    Re-handle R01/T01 from the rightmost first row and place it at R02/T03,
    directly above the two existing boxes in the second row.

This is a visual validation/demo layer.  It animates the live container prims
and writes matching RTG gantry/trolley/hoist/rope samples into smart_port.usda.
"""

from __future__ import annotations

from pathlib import Path
import sys

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.append(str(SCRIPT_DIR))

from build_rtg_simready import rope_points
from rtg_live_control import (
    GANTRY_JOINT_PATH,
    GANTRY_PATH,
    HOIST_JOINT_PATH,
    HOIST_PATH,
    ROPE_SYSTEM_PATH,
    TROLLEY_JOINT_PATH,
    TROLLEY_PATH,
    gantry_bay_id_to_usd,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCENE_PATH = PROJECT_ROOT / "omniverse" / "scenes" / "smart_port.usda"
LIVE_CONTAINER_PATH = PROJECT_ROOT / "omniverse" / "scenes" / "live_containers.usda"

BAY_ID = "1C/004"
STACK_PATH = "/World/LiveContainers/Bay_1C_004_40FT_Pattern_123456"
SOURCE_PREFIX = f"{STACK_PATH}/R01_T01_Container"
SOURCE_MAIN_PATH = f"{STACK_PATH}/R01_T01_Container"
TARGET_UNDER_PATH = f"{STACK_PATH}/R02_T02_Container"
TARGET_SLOT_LABEL = "R02/T03"

CONTAINER_VISUAL_HEIGHT_Z_M = 0.904353
TIER_GAP_Z_M = 0.035

# Visual calibration measured from the composed USD scene with
# measure_pick_place_alignment.py.  At GANTRY_HOME_USD_Y / trolley 0 / hoist high
# the yellow lifting header centre is at this world XY.  The trolley controller
# is authored under a negative X scale, so increasing negative trolley USD moves
# the spreader toward positive world X.
GANTRY_HOME_USD_Y = 23.9
SPREADER_WORLD_X_AT_TROLLEY_ZERO = -3.7426
SPREADER_WORLD_Y_AT_GANTRY_HOME = 29.46
TROLLEY_USD_TO_WORLD_X = -1.4741

# Hoist values are USD visual offsets.  Pick/place are calibrated from the red
# corner hook/lock face touching the container top face; the lifted container
# must then use the same hoist delta so it stays visually attached to the
# spreader instead of floating independently.
HOIST_HIGH = 2.30
REFERENCE_PICK_CONTAINER_CENTER_Z = 3.3902355
REFERENCE_PICK_HOIST_Z = 0.90

FRAMES = {
    "start": 1,
    "align_source": 30,
    "lower_pick": 55,
    "clamp": 70,
    "lift_source": 100,
    "travel_target": 140,
    "lower_place": 175,
    "release": 195,
    "raise_clear": 230,
}


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


def reset_and_set_samples(attr, samples) -> None:
    attr.Clear()
    attr.Set(samples[0][1])
    for frame, value in samples:
        attr.Set(value, Usd.TimeCode(frame))


def get_translate(prim) -> Gf.Vec3d:
    attr = prim.GetAttribute("xformOp:translate")
    if not attr:
        raise RuntimeError(f"Missing xformOp:translate on {prim.GetPath()}")
    return Gf.Vec3d(attr.Get())


def set_translate_samples(prim, samples: list[tuple[int, Gf.Vec3d]]) -> None:
    attr = prim.GetAttribute("xformOp:translate")
    if not attr:
        raise RuntimeError(f"Missing xformOp:translate on {prim.GetPath()}")
    reset_and_set_samples(attr, samples)


def hoist_for_container_center_z(container_center_z: float) -> float:
    """Return the visual hoist offset whose red clamp face meets a box top."""

    return REFERENCE_PICK_HOIST_Z + (
        float(container_center_z) - REFERENCE_PICK_CONTAINER_CENTER_Z
    )


def apply_container_animation() -> dict[str, object]:
    stage = Usd.Stage.Open(str(LIVE_CONTAINER_PATH))
    if not stage:
        raise RuntimeError(f"Cannot open {LIVE_CONTAINER_PATH}")

    source_main = stage.GetPrimAtPath(SOURCE_MAIN_PATH)
    target_under = stage.GetPrimAtPath(TARGET_UNDER_PATH)
    if not source_main:
        raise RuntimeError(f"Missing source container: {SOURCE_MAIN_PATH}")
    if not target_under:
        raise RuntimeError(f"Missing target base container: {TARGET_UNDER_PATH}")

    source_center = get_translate(source_main)
    target_under_center = get_translate(target_under)
    target_center = Gf.Vec3d(
        target_under_center[0],
        target_under_center[1],
        target_under_center[2] + CONTAINER_VISUAL_HEIGHT_Z_M + TIER_GAP_Z_M,
    )
    hoist_pick = hoist_for_container_center_z(source_center[2])
    hoist_place = hoist_for_container_center_z(target_center[2])
    source_lifted = Gf.Vec3d(
        source_center[0],
        source_center[1],
        source_center[2] + (HOIST_HIGH - hoist_pick),
    )
    target_lifted = Gf.Vec3d(
        target_center[0],
        target_center[1],
        target_center[2] + (HOIST_HIGH - hoist_place),
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
        samples = [
            (FRAMES["start"], original),
            (FRAMES["lower_pick"], original),
            (FRAMES["clamp"], original),
            (FRAMES["lift_source"], source_lifted + rel),
            (FRAMES["travel_target"], target_lifted + rel),
            (FRAMES["lower_place"], target_center + rel),
            (FRAMES["release"], target_center + rel),
            (FRAMES["raise_clear"], target_center + rel),
        ]
        set_translate_samples(prim, samples)
        prim.CreateAttribute("live:pickPlaceDemoRole", Sdf.ValueTypeNames.String).Set(
            "moved_source_R01_T01_to_R02_T03"
        )

    stage.GetRootLayer().customLayerData.update(
        {
            "pickPlaceDemoEnabled": True,
            "pickPlaceSource": "R01/T01",
            "pickPlaceTarget": TARGET_SLOT_LABEL,
            "pickPlaceFramePlan": "1-70 clamp, 70-100 lift, 100-140 trolley, 140-195 lower/release",
        }
    )
    stage.GetRootLayer().Save()
    return {
        "sourceCenter": tuple(round(v, 4) for v in source_center),
        "targetCenter": tuple(round(v, 4) for v in target_center),
        "animatedPrims": len(source_prims),
        "hoistPick": round(hoist_pick, 4),
        "hoistPlace": round(hoist_place, 4),
    }


def apply_rtg_animation(
    source_x: float,
    target_x: float,
    work_center_y: float,
    hoist_pick: float,
    hoist_place: float,
) -> dict[str, object]:
    stage = Usd.Stage.Open(str(SCENE_PATH))
    if not stage:
        raise RuntimeError(f"Cannot open {SCENE_PATH}")

    nominal_gantry_y = gantry_bay_id_to_usd(BAY_ID)
    gantry_y = GANTRY_HOME_USD_Y + (
        work_center_y - SPREADER_WORLD_Y_AT_GANTRY_HOME
    )
    trolley_source = (
        source_x - SPREADER_WORLD_X_AT_TROLLEY_ZERO
    ) / TROLLEY_USD_TO_WORLD_X
    trolley_target = (
        target_x - SPREADER_WORLD_X_AT_TROLLEY_ZERO
    ) / TROLLEY_USD_TO_WORLD_X

    gantry_samples = (
        (FRAMES["start"], gantry_y),
        (FRAMES["raise_clear"], gantry_y),
    )
    trolley_samples = (
        (FRAMES["start"], trolley_source),
        (FRAMES["lift_source"], trolley_source),
        (FRAMES["travel_target"], trolley_target),
        (FRAMES["raise_clear"], trolley_target),
    )
    hoist_samples = (
        (FRAMES["start"], HOIST_HIGH),
        (FRAMES["align_source"], HOIST_HIGH),
        (FRAMES["lower_pick"], hoist_pick),
        (FRAMES["clamp"], hoist_pick),
        (FRAMES["lift_source"], HOIST_HIGH),
        (FRAMES["travel_target"], HOIST_HIGH),
        (FRAMES["lower_place"], hoist_place),
        (FRAMES["release"], hoist_place),
        (FRAMES["raise_clear"], HOIST_HIGH),
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
    points_attr.Set(rope_points(HOIST_HIGH))
    for frame, value in hoist_samples:
        points_attr.Set(rope_points(value), Usd.TimeCode(frame))

    stage.SetStartTimeCode(FRAMES["start"])
    stage.SetEndTimeCode(FRAMES["raise_clear"])
    stage.SetFramesPerSecond(24)
    stage.SetTimeCodesPerSecond(24)
    stage.GetRootLayer().customLayerData.update(
        {
            "rtgPickPlaceDemoEnabled": True,
            "rtgPickPlaceDemo": f"R01/T01 -> {TARGET_SLOT_LABEL}",
            "rtgPickPlaceAlignment": "spreader header centre aligned to source/target container centres",
        }
    )
    stage.GetRootLayer().Save()
    return {
        "gantryY": round(gantry_y, 4),
        "nominalGantryY": round(nominal_gantry_y, 4),
        "trolleySourceX": round(trolley_source, 4),
        "trolleyTargetX": round(trolley_target, 4),
        "hoistPickZ": round(hoist_pick, 4),
        "hoistPlaceZ": round(hoist_place, 4),
    }


def apply_pick_place_demo() -> dict[str, object]:
    container_result = apply_container_animation()
    source_x = container_result["sourceCenter"][0]
    target_x = container_result["targetCenter"][0]
    work_center_y = container_result["sourceCenter"][1]
    rtg_result = apply_rtg_animation(
        source_x,
        target_x,
        work_center_y,
        container_result["hoistPick"],
        container_result["hoistPlace"],
    )
    result = {"container": container_result, "rtg": rtg_result}
    print("Applied RTG pick-place demo:", result)
    return result


if __name__ == "__main__":
    apply_pick_place_demo()
