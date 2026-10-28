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
        self.runtime.execute(self.stage, "set_viewport_view", {"target": "trolley", "follow": False})
        eye = self.matrix().ExtractTranslation()
        self.controller.set_trolley(6.66)
        self.runtime.tick(self.stage, 0.016)
        self.assertLess((self.matrix().ExtractTranslation() - eye).GetLength(), 1e-9)
        self.runtime.close()
        self.assertEqual(self.viewport.camera_path, "/OmniverseKit_Persp")
        self.assertEqual(list(self.stage.GetSessionLayer().subLayerPaths), paths_before)
        self.assertFalse(self.stage.GetPrimAtPath(self.scene.CAMERA_PATH))


if __name__ == "__main__":
    unittest.main()
