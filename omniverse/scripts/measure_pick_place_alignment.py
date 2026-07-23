"""Measure composed USD positions for the pick-place demo alignment."""

from __future__ import annotations

from pathlib import Path

from pxr import Gf, Usd, UsdGeom


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCENE_PATH = PROJECT_ROOT / "omniverse" / "scenes" / "smart_port.usda"

SOURCE_CONTAINER_PATH = (
    "/World/LiveContainers/Bay_1C_004_40FT_Pattern_123456/R01_T01_Container"
)
TARGET_CONTAINER_PATH = (
    "/World/LiveContainers/Bay_1C_004_40FT_Pattern_123456/R02_T02_Container"
)
SPREADER_NAME_TOKEN = "RTG_SPREAD_REDRAW_yellow_pair_lift_header_front"
GRIPPER_NAME_TOKENS = (
    "RTG_SPREAD_REDRAW_red_corner_lock_foot",
    "RTG_SPREAD_REDRAW_red_corner_hook_lip",
    "RTG_SPREAD_REDRAW_red_corner_guide_upright",
)


def bbox_center(stage: Usd.Stage, path: str, frame: int) -> tuple[Gf.Vec3d, Gf.Vec3d]:
    prim = stage.GetPrimAtPath(path)
    if not prim:
        raise RuntimeError(f"Missing prim: {path}")
    cache = UsdGeom.BBoxCache(
        Usd.TimeCode(frame),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
        useExtentsHint=False,
    )
    bounds = cache.ComputeWorldBound(prim).ComputeAlignedBox()
    mn = bounds.GetMin()
    mx = bounds.GetMax()
    center = (mn + mx) * 0.5
    dims = mx - mn
    return center, dims


def translate_value(stage: Usd.Stage, path: str, frame: int):
    prim = stage.GetPrimAtPath(path)
    if not prim:
        return None
    attr = prim.GetAttribute("xformOp:translate")
    return attr.Get(Usd.TimeCode(frame)) if attr else None


def find_spreader_prims(stage: Usd.Stage) -> list[str]:
    return [
        str(prim.GetPath())
        for prim in stage.Traverse()
        if SPREADER_NAME_TOKEN in prim.GetName()
    ]


def find_gripper_prims(stage: Usd.Stage) -> list[str]:
    paths: list[str] = []
    for prim in stage.Traverse():
        name = prim.GetName()
        if any(token in name for token in GRIPPER_NAME_TOKENS):
            paths.append(str(prim.GetPath()))
    return paths


def main() -> None:
    stage = Usd.Stage.Open(str(SCENE_PATH))
    if not stage:
        raise RuntimeError(f"Cannot open {SCENE_PATH}")
    spreader_paths = find_spreader_prims(stage)
    gripper_paths = find_gripper_prims(stage)
    print("SPREADER_PATHS", len(spreader_paths))
    for path in spreader_paths[:12]:
        print(path)
    print("GRIPPER_PATHS", len(gripper_paths))
    for path in gripper_paths[:16]:
        print(path)

    frames = (1, 55, 70, 100, 140, 175, 230)
    for frame in frames:
        print("FRAME", frame)
        for label, path in (
            ("source", SOURCE_CONTAINER_PATH),
            ("targetBase", TARGET_CONTAINER_PATH),
        ):
            center, dims = bbox_center(stage, path, frame)
            print(
                label,
                "center=", tuple(round(v, 4) for v in center),
                "dims=", tuple(round(v, 4) for v in dims),
            )
        for index, path in enumerate(spreader_paths[:4]):
            center, dims = bbox_center(stage, path, frame)
            print(
                f"spreader{index}",
                "center=", tuple(round(v, 4) for v in center),
                "dims=", tuple(round(v, 4) for v in dims),
                "path=", path,
            )
        for index, path in enumerate(gripper_paths[:8]):
            center, dims = bbox_center(stage, path, frame)
            print(
                f"gripper{index}",
                "center=", tuple(round(v, 4) for v in center),
                "dims=", tuple(round(v, 4) for v in dims),
                "path=", path,
            )
        for label, path in (
            (
                "gantry",
                "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL",
            ),
            (
                "trolley",
                "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL/ANIM_CTRL_RTG_TROLLEY_TRAVEL",
            ),
            (
                "hoist",
                "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL/ANIM_CTRL_RTG_TROLLEY_TRAVEL/ANIM_CTRL_RTG_HOIST_VERTICAL",
            ),
        ):
            print(label, "translate=", translate_value(stage, path, frame))


if __name__ == "__main__":
    main()
