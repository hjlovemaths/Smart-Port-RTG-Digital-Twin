"""Build a same-scale cargo ship sailing about one kilometre into open water."""

from __future__ import annotations

from math import dist
from pathlib import Path

import omni.kit.app
from pxr import Gf, Sdf, Usd, UsdGeom


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "omniverse" / "scenes" / "ship_traffic_animation.usda"
SHIP_REFERENCE = "../assets/resource/ship/Cargo_Ship_09_variant_b.usdz"
SHIP_PATH = "/World/SMART_PORT_OUTBOUND_CARGO_SHIP"
COMMON_SHIP_SCALE = Gf.Vec3f(0.03883929, 0.027641999, 0.043764994)
SHIP_Z_M = 1.3012471449582028
ROUTE = (
    (1.0, Gf.Vec3d(-260.0, 495.0, SHIP_Z_M)),
    (240.0, Gf.Vec3d(-520.0, 555.0, SHIP_Z_M)),
    (480.0, Gf.Vec3d(-820.0, 630.0, SHIP_Z_M)),
    (720.0, Gf.Vec3d(-1260.0, 760.0, SHIP_Z_M)),
)


def build() -> dict[str, object]:
    if OUTPUT.exists():
        OUTPUT.unlink()
    stage = Usd.Stage.CreateNew(str(OUTPUT))
    stage.SetMetadata("metersPerUnit", 1.0)
    stage.SetMetadata("upAxis", UsdGeom.Tokens.z)
    stage.SetStartTimeCode(1.0)
    stage.SetEndTimeCode(925.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())

    ship = UsdGeom.Xform.Define(stage, SHIP_PATH)
    ship.GetPrim().GetReferences().AddReference(SHIP_REFERENCE)
    xform = UsdGeom.Xformable(ship.GetPrim())
    translate = xform.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble)
    rotate_z = xform.AddRotateZOp()
    rotate_x = xform.AddRotateXOp()
    scale = xform.AddScaleOp()
    for frame, position in ROUTE:
        translate.Set(position, Usd.TimeCode(frame))
    rotate_z.Set(165.0)
    rotate_x.Set(90.0)
    scale.Set(COMMON_SHIP_SCALE)

    visibility = ship.CreateVisibilityAttr(UsdGeom.Tokens.inherited)
    visibility.Set(UsdGeom.Tokens.inherited, Usd.TimeCode(720.0))
    visibility.Set(UsdGeom.Tokens.invisible, Usd.TimeCode(900.0))
    route_length = sum(
        dist(tuple(previous), tuple(following))
        for (_, previous), (_, following) in zip(ROUTE, ROUTE[1:])
    )
    ship.GetPrim().CreateAttribute(
        "ship:routeLengthMeters", Sdf.ValueTypeNames.Double, custom=True
    ).Set(route_length)
    ship.GetPrim().CreateAttribute(
        "ship:motion", Sdf.ValueTypeNames.String, custom=True
    ).Set("outbound to open sea")
    stage.GetRootLayer().Save()
    return {"output": str(OUTPUT), "route_length_m": route_length}


try:
    print(f"SHIP_TRAFFIC_RESULT {build()}")
finally:
    omni.kit.app.get_app().post_quit(0)
