"""Set the default visual pose for the primary RTG in smart_port.usda."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.append(str(SCRIPT_DIR))

from rtg_live_control import (
    RTG_BAY_VISUAL_ALIGNMENT_OFFSET_Y_M,
    RTG_WORK_CENTER_LOCAL_Y_M,
    _rope_points,
    gantry_actual_to_usd,
    hoist_actual_to_usd,
)
from yard_coordinate_mapping import bay_id_position_m, bay_id_scene_y_m


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGE_PATH = PROJECT_ROOT / "omniverse" / "scenes" / "smart_port.usda"

GANTRY_JOINT_PATH = "/World/RTGPhysics/GantryTravelJoint"
TROLLEY_JOINT_PATH = "/World/RTGPhysics/TrolleyTravelJoint"
HOIST_JOINT_PATH = "/World/RTGPhysics/HoistVerticalJoint"

GANTRY_PATH = "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL"
TROLLEY_PATH = f"{GANTRY_PATH}/ANIM_CTRL_RTG_TROLLEY_TRAVEL"
HOIST_PATH = f"{TROLLEY_PATH}/ANIM_CTRL_RTG_HOIST_VERTICAL"
ROPE_SYSTEM_PATH = f"{TROLLEY_PATH}/RTG_DYNAMIC_HOIST_ROPES"


def set_translate(
    stage: Usd.Stage, path: str, value: Gf.Vec3d, frames: tuple[int, ...]
) -> None:
    prim = stage.OverridePrim(path)
    attr = prim.CreateAttribute("xformOp:translate", Sdf.ValueTypeNames.Double3)
    attr.Set(value)
    for frame in frames:
        attr.Set(value, Usd.TimeCode(frame))


def set_drive_target(
    stage: Usd.Stage, joint_path: str, value: float, frames: tuple[int, ...]
) -> None:
    joint = UsdPhysics.PrismaticJoint.Get(stage, joint_path)
    if not joint:
        return
    drive = UsdPhysics.DriveAPI.Get(joint.GetPrim(), UsdPhysics.Tokens.linear)
    attr = drive.GetTargetPositionAttr()
    attr.Set(value)
    for frame in frames:
        attr.Set(value, Usd.TimeCode(frame))


def set_rope_points(stage: Usd.Stage, hoist_usd: float, frames: tuple[int, ...]) -> None:
    curves = UsdGeom.BasisCurves.Get(stage, ROPE_SYSTEM_PATH)
    if not curves:
        return
    attr = curves.GetPointsAttr()
    points = _rope_points(hoist_usd)
    attr.Set(points)
    for frame in frames:
        attr.Set(points, Usd.TimeCode(frame))


def hide_hoisted_load(stage: Usd.Stage, frames: tuple[int, ...]) -> int:
    hidden = 0
    for prim in list(stage.Traverse()):
        name = prim.GetName()
        if not name.upper().startswith("YLOAD"):
            continue
        imageable = UsdGeom.Imageable(stage.OverridePrim(prim.GetPath()))
        attr = imageable.CreateVisibilityAttr()
        attr.Set(UsdGeom.Tokens.invisible)
        for frame in frames:
            attr.Set(UsdGeom.Tokens.invisible, Usd.TimeCode(frame))
        prim.CreateAttribute("rtg:hiddenAsInitialHoistedLoad", Sdf.ValueTypeNames.Bool).Set(
            True
        )
        hidden += 1
    return hidden


def set_initial_pose(bay_id: str, hoist_m: float, hide_load: bool) -> dict[str, object]:
    stage = Usd.Stage.Open(str(STAGE_PATH))
    stage.SetEditTarget(stage.GetRootLayer())

    frames = (1, int(stage.GetEndTimeCode()))
    gantry_m = bay_id_position_m(bay_id, "center")
    work_center_scene_y = bay_id_scene_y_m(bay_id, "center")
    gantry_controller_y = gantry_actual_to_usd(gantry_m)
    trolley_m = 0.0
    trolley_usd = 0.0
    hoist_usd = hoist_actual_to_usd(hoist_m)

    set_translate(stage, GANTRY_PATH, Gf.Vec3d(0.0, gantry_controller_y, 0.0), frames)
    set_translate(stage, TROLLEY_PATH, Gf.Vec3d(trolley_usd, 0.0, 0.0), frames)
    set_translate(stage, HOIST_PATH, Gf.Vec3d(0.0, 0.0, hoist_usd), frames)

    set_drive_target(stage, GANTRY_JOINT_PATH, gantry_controller_y, frames)
    set_drive_target(stage, TROLLEY_JOINT_PATH, trolley_usd, frames)
    set_drive_target(stage, HOIST_JOINT_PATH, hoist_usd, frames)
    set_rope_points(stage, hoist_usd, frames)

    hidden_load_prims = hide_hoisted_load(stage, frames) if hide_load else 0

    stage.GetRootLayer().Save()
    return {
        "stage": str(STAGE_PATH),
        "bayId": bay_id,
        "gantryMeters": gantry_m,
        "workCenterSceneY": work_center_scene_y,
        "rtgWorkCenterLocalY": RTG_WORK_CENTER_LOCAL_Y_M,
        "rtgBayVisualAlignmentOffsetY": RTG_BAY_VISUAL_ALIGNMENT_OFFSET_Y_M,
        "gantryControllerY": gantry_controller_y,
        "trolleyMeters": trolley_m,
        "hoistMeters": hoist_m,
        "hoistUsd": hoist_usd,
        "hiddenLoadPrims": hidden_load_prims,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bay", default="1C/005")
    parser.add_argument("--hoist", type=float, default=15.0)
    parser.add_argument("--show-load", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else None)
    print(set_initial_pose(args.bay, args.hoist, not args.show_load))


if __name__ == "__main__":
    main()
