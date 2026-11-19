from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "omniverse/scripts"))
sys.path.insert(0, str(ROOT / "omniverse/extensions/smartport.agent_panel"))

from smartport.agent_panel.device_locations import device_for_hit, device_map_state, map_coordinates


def test_demo_map_coordinates_align_the_quay_north_and_control_center_southeast():
    assert map_coordinates(0, 0) == {"longitude": 0, "latitude": 0}
    assert map_coordinates(0, 100)["longitude"] == pytest.approx(0.0008983153)
    assert map_coordinates(-100, 0)["latitude"] == pytest.approx(0.0009043695)
    assert map_coordinates(138, 470)["latitude"] < 0 < map_coordinates(138, 470)["longitude"]


def test_sts_and_moving_truck_use_the_same_map_as_scene_picking():
    pytest.importorskip("pxr")
    from pxr import Gf, Usd, UsdGeom

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    sts_path = "/World/STSControls/STS_01"
    truck_path = "/World/ParkedYardTrucks/Group1_Bay01"
    sts = UsdGeom.Xform.Define(stage, sts_path)
    sts.AddTranslateOp().Set(Gf.Vec3d(-238, 23, 0))
    gantry = UsdGeom.Xform.Define(stage, sts_path + "/Gantry")
    gantry.AddTransformOp(opSuffix="stsControl").Set(Gf.Matrix4d(1).SetTranslate(Gf.Vec3d(-4, 0, 0)))
    truck = UsdGeom.Xform.Define(stage, truck_path)
    truck_position = truck.AddTranslateOp()
    truck_position.Set(Gf.Vec3d(138, 65, 0))
    time = Usd.TimeCode.Default()

    snapshot = device_map_state(stage, time)
    inventory = {device["id"]: device for device in snapshot["devices"]}
    assert snapshot["map"]["control_center"] == map_coordinates(138, 470)
    assert inventory[sts_path]["position"]["latitude"] > snapshot["map"]["control_center"]["latitude"]
    assert device_for_hit(stage, sts_path + "/Gantry/Fixed/Beam", time) == inventory[sts_path]
    assert inventory[sts_path]["axes"]["gantry"] == 4
    assert inventory[truck_path]["position"] == map_coordinates(138, 65)

    truck_position.Set(Gf.Vec3d(118, 80, 0))
    assert device_for_hit(stage, truck_path + "/Cab", time)["position"] == map_coordinates(118, 80)
    UsdGeom.Imageable(truck).MakeInvisible()
    assert device_for_hit(stage, truck_path + "/Cab", time) is None
