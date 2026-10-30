"""Exercise browser camera constraints with real USD stages and transforms."""
import math
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "omniverse/extensions/smartport.agent_panel"))
from smartport.agent_panel.viewport_navigation import ViewportNavigation, CAMERA_PATH, RTG_PATH, rtg_visibility, ensure_complete_rtg_instances


class ViewportNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from pxr import Gf, Usd, UsdGeom
        except ImportError:
            raise unittest.SkipTest("Requires Kit USD Python")
        cls.Gf, cls.Usd, cls.UsdGeom = Gf, Usd, UsdGeom

    def setUp(self):
        self.stage = self.Usd.Stage.CreateInMemory()
        self.rtg = self.UsdGeom.Xform.Define(self.stage, RTG_PATH)
        self.rtg.AddTranslateOp().Set(self.Gf.Vec3d(100, 200, 0))
        self.original = self.UsdGeom.Camera.Define(self.stage, "/OriginalCamera")
        self.original.CreateFocalLengthAttr(18)
        self.UsdGeom.Xformable(self.original).AddTransformOp().Set(self.Gf.Matrix4d(1).SetTranslate(self.Gf.Vec3d(100, 160, 20)))
        self.authored = self.stage.GetRootLayer().ExportToString()
        self.viewport = SimpleNamespace(camera_path="/OriginalCamera")
        self.nav = ViewportNavigation()
        self.nav._context = lambda: (self.stage, self.viewport)
        omni = ModuleType("omni")
        omni.usd = ModuleType("omni.usd")
        omni.usd.get_context = lambda: SimpleNamespace(get_stage=lambda: self.stage)
        self.modules = patch.dict(sys.modules, {"omni": omni, "omni.usd": omni.usd})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def tearDown(self):
        self.assertEqual(self.stage.GetRootLayer().ExportToString(), self.authored)

    def check_bounds(self, result):
        self.assertGreaterEqual(result["height_m"], 2)
        self.assertLessEqual(result["height_m"], 180)
        self.assertGreaterEqual(result["distance_m"], 14)
        self.assertLessEqual(result["distance_m"], 160)
        self.assertGreaterEqual(result["pitch_deg"], 12)
        self.assertLessEqual(result["pitch_deg"], 78)
        self.assertLessEqual(math.hypot(*result["pan_m"]), min(35, result["distance_m"]*.25)+.00001)
        matrix = self.UsdGeom.Xformable(self.stage.GetPrimAtPath(CAMERA_PATH)).GetLocalTransformation()
        right = matrix.TransformDir(self.Gf.Vec3d(1, 0, 0))
        self.assertAlmostEqual(right[2], 0, places=7)  # horizon never rolls
        self.assertGreater(matrix.TransformDir(self.Gf.Vec3d(0, 1, 0))[2], 0)

    def test_wheel_changes_visible_camera_distance(self):
        before = self.nav.execute({"action": "reset"})
        after = self.nav.execute({"zoom": 1.2})
        self.assertLess(after["distance_m"], before["distance_m"])
        self.assertNotEqual(before["eye_m"], after["eye_m"])
        self.assertEqual(str(self.viewport.camera_path), CAMERA_PATH)
        self.check_bounds(after)

    def test_extreme_repeated_inputs_cannot_go_underwater_far_away_or_roll(self):
        for direction in (1, -1):
            for _ in range(100):
                result = self.nav.execute({"zoom": direction*1e6, "orbit_x": direction*1e6,
                                           "orbit_y": direction*1e6, "pan_x": 1e6, "pan_y": -1e6})
                self.check_bounds(result)

    def test_reset_recovers_a_lost_underwater_camera(self):
        with self.Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
            self.original.GetPrim().GetAttribute("xformOp:transform").Set(self.Gf.Matrix4d(1).SetTranslate(self.Gf.Vec3d(1e6, 1e6, -500)))
        self.check_bounds(self.nav.execute({"zoom": 1}))
        self.nav.execute({"pan_x": 100, "orbit_y": 100})
        result = self.nav.execute({"action": "reset"})
        self.assertEqual(result["pan_m"], [0, 0])
        self.assertEqual(result["distance_m"], 60)
        self.assertEqual(result["pitch_deg"], 20)

    def test_follows_rtg_translation_without_touching_equipment(self):
        before = self.nav.execute({"action": "reset"})
        with self.Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
            self.rtg.GetPrim().GetAttribute("xformOp:translate").Set(self.Gf.Vec3d(110, 200, 0))
        self.nav.tick()
        eye = self.nav.execute({"action": "state"})["eye_m"]
        self.assertAlmostEqual(eye[0]-before["eye_m"][0], 10)
        self.assertAlmostEqual(eye[2], before["eye_m"][2])

    def test_does_not_override_an_agent_camera_until_user_navigates(self):
        self.nav.execute({"action": "reset"})
        self.viewport.camera_path = "/OriginalCamera"
        self.nav.tick()
        self.assertEqual(self.viewport.camera_path, "/OriginalCamera")

    def test_native_changes_to_controlled_camera_are_corrected(self):
        self.nav.execute({"action": "reset"})
        prim = self.stage.GetPrimAtPath(CAMERA_PATH)
        pose = prim.GetAttribute("xformOp:transform").Get()
        with self.Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
            prim.GetAttribute("xformOp:transform").Set(self.Gf.Matrix4d(1).SetTranslate(self.Gf.Vec3d(0, 0, -100)))
        self.nav.tick()
        self.assertEqual(prim.GetAttribute("xformOp:transform").Get(), pose)

    def test_bad_requests_do_not_modify_camera(self):
        for payload in ({"action": "unknown"}, {"zoom": float("nan")}, {"orbit_x": True}, {"pan_y": "100"}):
            with self.assertRaises(ValueError):
                self.nav.execute(payload)
        self.assertFalse(self.stage.GetPrimAtPath(CAMERA_PATH))

    def test_restore_cranes_preserves_hidden_lamps_and_authored_parts(self):
        root = "/World/PortAndRTG/SMART_PORT_STATIC_RTG_YARD_M30"
        crane = self.UsdGeom.Xform.Define(self.stage, root)
        lamp = self.UsdGeom.Xform.Define(self.stage, root + "/NightLights")
        hidden_part = self.UsdGeom.Xform.Define(self.stage, root + "/UnusedPart")
        hidden_part.MakeInvisible()
        self.UsdGeom.Xform.Define(self.stage, "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC")
        self.authored = self.stage.GetRootLayer().ExportToString()
        with self.Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
            crane.MakeInvisible()
            lamp.MakeInvisible()
        result = rtg_visibility(self.stage, restore=True)
        self.assertEqual(result["restored"], 1)
        self.assertEqual(crane.ComputeVisibility(), self.UsdGeom.Tokens.inherited)
        self.assertEqual(lamp.ComputeVisibility(), self.UsdGeom.Tokens.invisible)
        self.assertEqual(hidden_part.ComputeVisibility(), self.UsdGeom.Tokens.invisible)

    def test_renderer_workaround_changes_only_static_crane_session_instancing(self):
        model = self.UsdGeom.Xform.Define(self.stage, "/Template")
        self.UsdGeom.Cube.Define(self.stage, "/Template/Column")
        paths = []
        for name in ("M60", "M30", "P30", "P60", "P90"):
            path = f"/World/PortAndRTG/SMART_PORT_STATIC_RTG_YARD_{name}"
            prim = self.UsdGeom.Xform.Define(self.stage, path).GetPrim()
            prim.GetReferences().AddInternalReference(model.GetPath())
            prim.SetInstanceable(True)
            paths.append(path)
        self.authored = self.stage.GetRootLayer().ExportToString()
        self.assertTrue(all(self.stage.GetPrimAtPath(p).IsInstance() for p in paths))
        self.assertEqual(set(ensure_complete_rtg_instances(self.stage)), set(paths))
        for path in paths:
            self.assertFalse(self.stage.GetPrimAtPath(path).IsInstance())
            self.assertTrue(self.stage.GetPrimAtPath(path + "/Column"))
        self.assertEqual(ensure_complete_rtg_instances(self.stage), [])

    def sts_agent_view(self):
        from smartport.agent_panel.scene_runtime import SceneRuntime
        root = self.UsdGeom.Xform.Define(self.stage, "/World/STSControls/STS_01")
        root.AddTranslateOp().Set(self.Gf.Vec3d(1000, -800, 40))
        self.UsdGeom.Cube.Define(self.stage, str(root.GetPath()) + "/Body").CreateSizeAttr(60)
        self.authored = self.stage.GetRootLayer().ExportToString()
        runtime = SceneRuntime(Path(__file__).resolve().parents[1])
        runtime.stage = self.stage
        runtime._viewport = lambda: self.viewport
        self.addCleanup(runtime.close)
        result = runtime.set_view({"target": "sts", "crane_id": "STS_01", "view": "angled", "follow": True})
        return runtime, result, root

    def test_agent_sts_view_first_drag_preserves_subject_distance_and_lens(self):
        runtime, before, root = self.sts_agent_view()
        camera = self.UsdGeom.Camera(self.stage.GetPrimAtPath(str(self.viewport.camera_path)))
        lens = camera.GetFocalLengthAttr().Get()
        after = self.nav.execute({"orbit_x": 1})
        self.assertEqual(after["focus_path"], str(root.GetPath()))
        self.assertLess((self.Gf.Vec3d(*after["target_m"])-self.Gf.Vec3d(*before["look_at_m"])).GetLength(), 1e-6)
        self.assertAlmostEqual(after["distance_m"], before["distance_m"])
        self.assertLess((self.Gf.Vec3d(*after["eye_m"])-self.Gf.Vec3d(*before["eye_m"])).GetLength(), 2)
        self.assertAlmostEqual(self.UsdGeom.Camera(self.stage.GetPrimAtPath(CAMERA_PATH)).GetFocalLengthAttr().Get(), lens)

    def test_sts_zoom_pan_reset_and_equipment_translation_stay_at_sts(self):
        runtime, before, root = self.sts_agent_view()
        for payload in ({"zoom": 1}, {"pan_x": 5}, {"action": "reset"}):
            result = self.nav.execute(payload)
            self.assertEqual(result["focus_path"], str(root.GetPath()))
            self.assertGreater(result["target_m"][0], 950)
        start = result["target_m"]
        with self.Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
            root.GetPrim().GetAttribute("xformOp:translate").Set(self.Gf.Vec3d(1020, -800, 40))
        self.nav.tick()
        self.assertAlmostEqual(self.nav._anchor(self.stage)[0]-start[0], 20)

    def test_controller_reload_recovers_sts_focus_from_web_camera(self):
        self.sts_agent_view()
        self.nav.execute({"pan_x": 5})
        before = self.nav.execute({})
        fresh = ViewportNavigation()
        fresh._context = lambda: (self.stage, self.viewport)
        after = fresh.execute({"orbit_x": .1})
        self.assertEqual(after["focus_path"], "/World/STSControls/STS_01")
        self.assertLess((self.Gf.Vec3d(*after["eye_m"])-self.Gf.Vec3d(*before["eye_m"])).GetLength(), 1)

    def test_explicit_agent_top_view_remains_top_down_when_zooming(self):
        runtime, before, root = self.sts_agent_view()
        runtime.set_view({"target": "sts", "crane_id": "STS_01", "view": "top"})
        after = self.nav.execute({"zoom": .1})
        self.assertEqual(after["focus_path"], str(root.GetPath()))
        self.assertGreater(after["pitch_deg"], 89.99)

    def test_manual_navigation_yields_agent_camera_follow_writer(self):
        import threading
        from smartport.agent_panel.web_bridge import AgentWebBridge
        runtime, before, root = self.sts_agent_view()
        bridge = AgentWebBridge()
        bridge.navigation = self.nav
        command = {"action": "navigate", "payload": {"zoom": .1}, "cancelled": False, "done": threading.Event()}
        bridge.commands.put(command)
        bridge.tick(SimpleNamespace(_runtime=runtime))
        self.assertNotIn("error", command)
        self.assertTrue(command["done"].is_set())
        self.assertIsNone(runtime.follow)
        self.assertIsNone(runtime.follow_viewport)
        self.assertEqual(command["result"]["focus_path"], str(root.GetPath()))


if __name__ == "__main__":
    unittest.main()
