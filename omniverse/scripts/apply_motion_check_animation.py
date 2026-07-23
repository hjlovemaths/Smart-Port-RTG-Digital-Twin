"""Author a short RTG motion-check animation into smart_port.usda.

Frame plan:
    001-090  gantry travel: 1C/005 -> 1C/007 -> 1C/005
    090-160  trolley travel: 0 m -> 18 m -> 0 m
    160-230  hoist travel: 15 m -> 0.5 m -> 15 m

The first frame remains the current parked pose at 1C/005 with the spreader at
the highest position.  This script is intended as a visual validation helper.
"""

from __future__ import annotations

from pathlib import Path
import sys

from pxr import Gf, Usd, UsdGeom, UsdPhysics

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
    hoist_actual_to_usd,
    trolley_actual_to_usd,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCENE_PATH = PROJECT_ROOT / "omniverse" / "scenes" / "smart_port.usda"


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
        raise RuntimeError(f"Missing transform prim: {prim_path}")
    attr = prim.GetAttribute("xformOp:translate")
    if not attr:
        raise RuntimeError(f"Missing xformOp:translate on {prim_path}")
    return attr


def reset_and_set_samples(attr, samples) -> None:
    attr.Clear()
    attr.Set(samples[0][1])
    for frame, value in samples:
        attr.Set(value, Usd.TimeCode(frame))


def apply_motion_check_animation() -> Path:
    stage = Usd.Stage.Open(str(SCENE_PATH))
    if not stage:
        raise RuntimeError(f"Cannot open {SCENE_PATH}")

    gantry_home = gantry_bay_id_to_usd("1C/005")
    gantry_forward = gantry_bay_id_to_usd("1C/007")
    trolley_left = trolley_actual_to_usd(0.0)
    trolley_right = trolley_actual_to_usd(18.0)
    hoist_high = hoist_actual_to_usd(15.0)
    hoist_low_safe = hoist_actual_to_usd(0.5)

    gantry_samples = (
        (1, gantry_home),
        (45, gantry_forward),
        (90, gantry_home),
        (230, gantry_home),
    )
    trolley_samples = (
        (1, trolley_left),
        (90, trolley_left),
        (125, trolley_right),
        (160, trolley_left),
        (230, trolley_left),
    )
    hoist_samples = (
        (1, hoist_high),
        (160, hoist_high),
        (195, hoist_low_safe),
        (230, hoist_high),
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

    stage.SetStartTimeCode(1)
    stage.SetEndTimeCode(230)
    stage.SetFramesPerSecond(24)
    stage.SetTimeCodesPerSecond(24)
    stage.GetRootLayer().customLayerData.update(
        {
            "rtgMotionCheckEnabled": True,
            "rtgMotionCheckFrames": "1-90 gantry, 90-160 trolley, 160-230 hoist",
        }
    )
    stage.GetRootLayer().Save()
    print(
        "Applied RTG motion-check animation:",
        {
            "scene": str(SCENE_PATH),
            "gantryHomeY": round(gantry_home, 3),
            "gantryForwardY": round(gantry_forward, 3),
            "trolleyLeftX": round(trolley_left, 3),
            "trolleyRightX": round(trolley_right, 3),
            "hoistHighZ": round(hoist_high, 3),
            "hoistLowSafeZ": round(hoist_low_safe, 3),
        },
    )
    return SCENE_PATH


if __name__ == "__main__":
    apply_motion_check_animation()
