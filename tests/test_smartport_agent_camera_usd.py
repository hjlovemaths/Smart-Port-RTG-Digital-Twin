"""Verify the Agent's under-trolley camera against the authored RTG model."""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "omniverse/scripts"))
sys.path.insert(0, str(ROOT / "omniverse/extensions/smartport.agent_panel"))


class AgentCameraUsdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from pxr import Gf, Usd, UsdGeom
        except ImportError:
            raise unittest.SkipTest("Requires the Kit USD Python runtime")
        from smartport.agent_panel import scene_runtime
        import rtg_live_control
        cls.Gf, cls.Usd, cls.UsdGeom = Gf, Usd, UsdGeom
        cls.scene, cls.live = scene_runtime, rtg_live_control

    def setUp(self):
        self.stage = self.Usd.Stage.Open(str(ROOT / "omniverse/scenes/smart_port.usda"))
        self.before = self.stage.GetRootLayer().ExportToString()
        self.viewport = SimpleNamespace(camera_path="/OmniverseKit_Persp", get_texture_resolution=lambda: (1280, 720))
        self.runtime = self.scene.SceneRuntime(ROOT)
        self.runtime._viewport = lambda: self.viewport
        self.controller = self.live.RTGController(self.stage)

    def tearDown(self):
        self.runtime.close()
        self.assertEqual(self.stage.GetRootLayer().ExportToString(), self.before)

    def matrix(self):
        return self.UsdGeom.Xformable(self.stage.GetPrimAtPath(self.scene.CAMERA_PATH)).GetLocalTransformation()

    def test_fourth_iecs_order_frames_gate_then_tracks_its_own_vehicle(self):
        from iecs_runtime import Runtime
        from iecs_core import IECS

        engine = IECS(":memory:")
        self.addCleanup(engine.close)
        for flow in ("unload", "load", "gate_in", "gate_out", "transfer"):
            engine.submit(flow)
        order = engine.orders["IECS-00004"]
        engine.assign(order)
        vehicle = engine.vehicles[order["vehicle"]]
        order.update(step_name="闸外候场", wait_reason="等待入场车道 yard:3C")
        port = SimpleNamespace(engine=engine, vehicle_path=lambda vid: Runtime.vehicle_path(None, vid))
        path = port.vehicle_path(vehicle["id"])
        with self.Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
            truck = self.UsdGeom.Cube.Define(self.stage, path)
            visibility = truck.CreateVisibilityAttr()
            visibility.Set("invisible")
            transform = truck.AddTranslateOp()
        args = {"target": "iecs_order", "order_index": 4}

        with patch.object(self.scene, "iecs_runtime", return_value=port):
            before = engine.snapshot()
            focused = self.runtime.execute(self.stage, "set_viewport_view", args)
            self.assertEqual(engine.snapshot(), before)
            self.assertEqual(focused["order_id"], "IECS-00004")
            self.assertEqual(focused["vehicle_id"], vehicle["id"])
            self.assertEqual(focused["focus_label"], "入口候场点")
            self.assertFalse(focused["vehicle_visible"])
            self.assertTrue(focused["following"])
            self.assertEqual(focused["look_at_m"][:2], vehicle["position"][:2])
            self.assertNotIn("order_index", self.runtime.follow)
            with self.assertRaisesRegex(ValueError, "没有第 6 条任务"):
                self.runtime.execute(self.stage, "set_viewport_view", {**args, "order_index": 6})

            with self.Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
                transform.Set(self.Gf.Vec3d(*vehicle["position"]))
                visibility.Set("inherited")
            focused = self.runtime.execute(self.stage, "set_viewport_view", args)
            self.assertTrue(focused["vehicle_visible"])
            self.assertEqual(focused["focus_label"], "E04 · 入口候场")

            vehicle.update(status="working", position=[133, 380, 0.085])
            with self.Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
                visibility.Set("inherited")
                transform.Set(self.Gf.Vec3d(*vehicle["position"]))
            self.runtime.tick(self.stage, 0.016)
            camera = self.stage.GetPrimAtPath(self.scene.CAMERA_PATH)
            self.assertEqual(camera.GetAttribute("smartport:navigationTarget").Get(), path)
            self.assertEqual(list(camera.GetAttribute("smartport:navigationCenter").Get())[:2], [133, 380])

            engine.orders[order["id"]] = dict(order, status="queued", vehicle=None)
            self.runtime.tick(self.stage, 0.016)
            self.assertIsNone(self.runtime.follow)

    def assert_vertical(self):
        matrix = self.matrix()
        direction = matrix.TransformDir(self.Gf.Vec3d(0, 0, -1))
        self.assertLess((direction - self.Gf.Vec3d(0, 0, -1)).GetLength(), 1e-9)
        for axis in (self.Gf.Vec3d(1, 0, 0), self.Gf.Vec3d(0, 1, 0), self.Gf.Vec3d(0, 0, 1)):
            self.assertAlmostEqual(matrix.TransformDir(axis).GetLength(), 1.0)

    def test_preset_is_below_the_platform_and_does_not_acquire_equipment_control(self):
        before = self.controller.get_positions()
        live_driver = SimpleNamespace(_subscription=object(), _current_stage=lambda: self.stage)
        with patch("builtins._smart_port_rtg_panel_facing_driver", live_driver, create=True):
            result = self.runtime.execute(self.stage, "set_viewport_view", {"target": "trolley"})
        self.assertTrue(result["following"])
        self.assertTrue(result["vertical_down"])
        self.assertEqual(result["field_of_view_deg"], 110)
        self.assertEqual(self.controller.get_positions(), before)
        transform = self.UsdGeom.XformCache().GetLocalToWorldTransform(self.stage.GetPrimAtPath(self.scene.TROLLEY))
        eye = transform.Transform(self.Gf.Vec3d(*self.scene.UNDER_TROLLEY_EYE))
        platform = transform.Transform(self.Gf.Vec3d(2.55, -5, 8.81))
        self.assertLess((self.matrix().ExtractTranslation() - eye).GetLength(), 1e-9)
        self.assertLess(eye[2], platform[2])
        self.assert_vertical()
        self.assertEqual(self.runtime.execute(self.stage, "list_viewpoints", {})["active_view"]["view"], "under_trolley")

    def test_camera_follows_trolley_and_gantry_but_not_hoist_height_or_viewport_focus(self):
        self.runtime.execute(self.stage, "set_viewport_view", {"target": "trolley"})
        eye = self.matrix().ExtractTranslation()
        self.controller.set_hoist(10.4)
        self.runtime.tick(self.stage, 0.016)
        self.assertLess((self.matrix().ExtractTranslation() - eye).GetLength(), 1e-8)
        other = SimpleNamespace(camera_path="/OtherCamera")
        self.runtime._viewport = lambda: other
        self.controller.set_trolley(9.5)
        self.runtime.tick(self.stage, 0.016)
        moved = self.matrix().ExtractTranslation()
        self.assertGreater(abs(moved[0] - eye[0]), 0.1)
        self.assertAlmostEqual(moved[2], eye[2])
        current_gantry = self.controller.get_positions()[0]
        self.controller.set_gantry(current_gantry + 1.0)
        self.runtime.tick(self.stage, 0.016)
        self.assertAlmostEqual(self.matrix().ExtractTranslation()[1] - moved[1], 1, delta=1e-5)
        self.assertEqual(other.camera_path, "/OtherCamera")
        self.assertEqual(self.viewport.camera_path, self.scene.CAMERA_PATH)
        self.assert_vertical()

    def test_widening_changes_the_lens_without_moving_or_tilting_the_mount(self):
        self.runtime.execute(self.stage, "set_viewport_view", {"target": "trolley"})
        eye = self.matrix().ExtractTranslation()
        camera = self.UsdGeom.Camera(self.stage.GetPrimAtPath(self.scene.CAMERA_PATH))
        original_focal = camera.GetFocalLengthAttr().Get()
        self.runtime.execute(self.stage, "set_viewport_view", {"target": "trolley", "field_of_view_deg": 130})
        self.assertLess(camera.GetFocalLengthAttr().Get(), original_focal)
        fov = math.degrees(2 * math.atan(camera.GetHorizontalApertureAttr().Get() / (2 * camera.GetFocalLengthAttr().Get())))
        self.assertAlmostEqual(fov, 130, delta=1e-5)
        self.assertLess((self.matrix().ExtractTranslation() - eye).GetLength(), 1e-9)
        self.assert_vertical()
        self.runtime.execute(self.stage, "set_viewport_view", {"target": "rtg", "view": "angled"})
        self.assertAlmostEqual(camera.GetFocalLengthAttr().Get(), 24)
        self.assertAlmostEqual(camera.GetHorizontalApertureAttr().Get(), 20.955, delta=1e-5)

    def test_fixed_view_and_shutdown_restore_camera_without_leaving_session_layers(self):
        paths_before = list(self.stage.GetSessionLayer().subLayerPaths)
        original_camera = bool(self.UsdGeom.Camera.Get(self.stage, self.scene.CAMERA_PATH))
        self.runtime.execute(self.stage, "set_viewport_view", {"target": "trolley", "follow": False})
        eye = self.matrix().ExtractTranslation()
        self.controller.set_trolley(6.66)
        self.runtime.tick(self.stage, 0.016)
        self.assertLess((self.matrix().ExtractTranslation() - eye).GetLength(), 1e-9)
        self.runtime.close()
        self.assertEqual(self.viewport.camera_path, "/OmniverseKit_Persp")
        self.assertEqual(list(self.stage.GetSessionLayer().subLayerPaths), paths_before)
        self.assertEqual(bool(self.UsdGeom.Camera.Get(self.stage, self.scene.CAMERA_PATH)), original_camera)

    def test_numbered_truck_defaults_to_exterior_and_still_supports_explicit_cab(self):
        from ship_transfer_plan import PARK

        result = self.runtime.execute(self.stage, "set_viewport_view", {"target": "truck", "truck_number": 17})
        self.assertEqual(result["truck_path"], "/World/ParkedYardTrucks/Group2_Bay07")
        self.assertEqual(result["view"], "chase")
        self.assertTrue(result["following"])
        cabin = self.runtime.execute(self.stage, "set_viewport_view", {
            "target": "truck", "truck_number": 17, "view": "cabin",
        })
        self.assertEqual(cabin["view"], "cabin")
        self.assertNotEqual(result["eye_m"], cabin["eye_m"])
        self.runtime.execute(self.stage, "set_viewport_view", {"target": "truck", "truck_number": 1, "view": "chase"})
        layer = self.stage.GetSessionLayer()
        with self.Usd.EditContext(self.stage, layer):
            truck = self.stage.DefinePrim("/World/TestTruck01")
            truck.GetReferences().AddInternalReference(PARK)
            truck.SetInstanceable(False)
            self.UsdGeom.Imageable(truck).CreateVisibilityAttr().Set("inherited")
            self.UsdGeom.Imageable(self.stage.GetPrimAtPath(PARK)).MakeInvisible()
            xf = self.UsdGeom.Xformable(truck)
            op = xf.AddTransformOp()
            xf.SetXformOpOrder([op])
            op.Set(self.Gf.Matrix4d(1).SetTranslate(self.Gf.Vec3d(4, 50, 0)))
        moving = SimpleNamespace(stage=self.stage, closed=False, truck=truck)
        with patch("builtins._smartport_ship_transfer_task", moving, create=True):
            self.runtime.tick(self.stage, .1)
            camera = self.stage.GetPrimAtPath(self.scene.CAMERA_PATH)
            self.assertEqual(camera.GetAttribute("smartport:navigationTarget").Get(), "/World/TestTruck01")
            eye = self.matrix().ExtractTranslation()
            with self.Usd.EditContext(self.stage, layer):
                op.Set(self.Gf.Matrix4d(1).SetTranslate(self.Gf.Vec3d(4, 57, 0)))
            self.runtime.tick(self.stage, .1)
            self.assertAlmostEqual(self.matrix().ExtractTranslation()[1] - eye[1], 7)

    def test_map_camera_switches_to_selected_truck_and_crane_prims(self):
        from smartport.agent_panel.device_locations import device_map_state
        from smartport.agent_panel.web_bridge import AgentWebBridge

        self.runtime.bind(self.stage)
        map_viewport = SimpleNamespace(camera_path="/OriginalMainCamera", time=self.Usd.TimeCode.Default())
        bridge = AgentWebBridge()
        bridge.navigation._context = lambda: (self.stage, map_viewport)
        panel = SimpleNamespace(_runtime=self.runtime)
        devices = {d["name"]: d for d in device_map_state(self.stage, map_viewport.time)["devices"]}
        truck = devices["17 号集卡"]
        result = bridge._focus_map_device(panel, {"id": truck["id"]})
        self.assertEqual(result["truck_path"], truck["id"])
        self.assertEqual(result["view"], "chase")
        self.assertTrue(result["following"])
        self.assertGreater(result["eye_m"][2], result["look_at_m"][2])
        self.assertEqual(map_viewport.camera_path, self.scene.CAMERA_PATH)
        self.assertEqual(self.viewport.camera_path, "/OmniverseKit_Persp")
        with self.assertRaisesRegex(ValueError, "所选设备已离开"):
            bridge._focus_map_device(panel, {"id": "/World/PortAndRTG"})
        camera = self.stage.GetPrimAtPath(self.scene.CAMERA_PATH)
        for device_name, target_suffix in (("M60 RTG", ""), ("STS_03", "/Gantry")):
            device = devices[device_name]
            result = bridge._focus_map_device(panel, {"id": device["id"]})
            self.assertEqual(result["view"], "angled")
            self.assertEqual(camera.GetAttribute("smartport:navigationTarget").Get(), device["id"] + target_suffix)
            self.assertIsNone(self.runtime.follow)

    def test_sts_hoist_direction_follows_the_displayed_axis(self):
        from sts_control import shared_sts_controller
        from sts_unload_task import UnloadTask

        self.runtime.bind(self.stage)
        controller = shared_sts_controller(self.stage, "STS_01")
        self.runtime.owned_sts["STS_01"] = controller
        controller.state["hoist"] = controller.targets["hoist"] = .30
        controller._apply()
        contact = UnloadTask.__new__(UnloadTask)
        contact.stage, contact.c = self.stage, controller
        at_bottom = contact.contact()[2]
        status = self.runtime.execute(self.stage, "get_equipment_status", {"equipment": "sts", "crane_id": "STS_01"})["sts"][0]
        self.assertEqual(status["hoist_lowest_m"], .30)
        self.assertTrue(status["hoist_axis_increases_upward"])
        with self.assertRaisesRegex(ValueError, "需要上升"):
            self.runtime.execute(self.stage, "set_sts_axis_positions",
                                 {"crane_id": "STS_01", "hoist_m": 5, "hoist_direction": "lower"})
        self.assertEqual(controller.targets["hoist"], .30)
        self.runtime.execute(self.stage, "set_sts_axis_positions",
                             {"crane_id": "STS_01", "hoist_m": 10, "hoist_direction": "raise"})
        for _ in range(180):
            self.runtime.tick(self.stage, .1)
        self.assertAlmostEqual(controller.state["hoist"], 10.)
        lifted = contact.contact()[2]
        self.assertGreater(lifted, at_bottom)
        self.runtime.execute(self.stage, "set_sts_axis_positions",
                             {"crane_id": "STS_01", "hoist_m": 5, "hoist_direction": "lower"})
        for _ in range(100):
            self.runtime.tick(self.stage, .1)
        self.assertAlmostEqual(controller.state["hoist"], 5.)
        self.assertLess(contact.contact()[2], lifted)
        result = self.runtime.execute(self.stage, "set_sts_axis_positions",
                                      {"crane_id": "STS_01", "hoist_distance_m": 2, "hoist_direction": "lower"})
        self.assertEqual(result["targets"]["hoist"], 3.)
        result = self.runtime.execute(self.stage, "set_sts_axis_positions",
                                      {"crane_id": "STS_01", "hoist_m": 16.55, "hoist_direction": "raise"})
        self.assertEqual(result["targets"]["hoist"], controller.limits["hoist"][1])


if __name__ == "__main__":
    unittest.main()
