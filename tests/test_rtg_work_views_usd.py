"""Work camera geometry/lifecycle on the full RTG asset; no UI needed."""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "omniverse/scripts"))


class WorkViewsUsdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from pxr import Usd, UsdGeom, Gf
        except ImportError:
            raise unittest.SkipTest("Requires Kit USD Python")
        import rtg_live_control as live
        import rtg_work_views as views
        cls.Usd, cls.UsdGeom, cls.Gf = Usd, UsdGeom, Gf
        cls.live, cls.views = live, views

    def setUp(self):
        self.stage = self.Usd.Stage.Open(str(ROOT / "omniverse/scenes/smart_port.usda"))
        self.root_before = self.stage.GetRootLayer().ExportToString()
        self.layers_before = list(self.stage.GetSessionLayer().subLayerPaths)
        self.controller = self.live.RTGController(self.stage)
        self.service = self.views.RtgWorkViews(self.stage)

    def tearDown(self):
        self.service.close()
        self.assertEqual(list(self.stage.GetSessionLayer().subLayerPaths), self.layers_before)
        self.assertEqual(self.stage.GetRootLayer().ExportToString(), self.root_before)
        self.assertFalse(self.stage.GetPrimAtPath(self.views.ROOT_PATH))

    def matrix(self, name):
        return self.UsdGeom.XformCache().GetLocalToWorldTransform(
            self.stage.GetPrimAtPath(self.views.ROOT_PATH + "/" + name))

    def test_top_is_vertical_and_side_is_oblique_without_moving_equipment(self):
        before = self.controller.get_positions()
        self.service.update_cameras()
        self.assertEqual(before, self.controller.get_positions())
        top = self.matrix("Top").TransformDir(self.Gf.Vec3d(0, 0, -1))
        side = self.matrix("Side").TransformDir(self.Gf.Vec3d(0, 0, -1))
        self.assertLess((top - self.Gf.Vec3d(0, 0, -1)).GetLength(), 1e-8)
        self.assertGreater(abs(side[0]), .7)
        self.assertLess(abs(side[2]), .7)
        self.assertEqual(self.stage.GetEditTarget().GetLayer(), self.stage.GetRootLayer())

    def test_top_tracks_trolley_and_gantry_while_side_also_tracks_hoist(self):
        top = self.matrix("Top").ExtractTranslation()
        side = self.matrix("Side").ExtractTranslation()
        g, t, h = self.controller.get_positions()
        self.controller.set_hoist_safe(h + .5)
        self.service.update_cameras()
        self.assertLess((self.matrix("Top").ExtractTranslation() - top).GetLength(), 1e-8)
        self.assertGreater(abs(self.matrix("Side").ExtractTranslation()[2] - side[2]), .05)
        self.controller.set_trolley_safe(t + 2)
        self.controller.set_gantry_safe(g + 1)
        self.service.update_cameras()
        self.assertGreater(abs(self.matrix("Top").ExtractTranslation()[0] - top[0]), .1)
        self.assertAlmostEqual(self.matrix("Top").ExtractTranslation()[1] - top[1], 1, places=4)

    def test_side_tracks_trim_and_flip_and_zoom_do_not_change_top(self):
        top = self.matrix("Top")
        self.controller.set_spreader_trim(2, .1)
        self.service.update_cameras()
        first = self.service.poses()["Side"]
        self.service.flip_side()
        second = self.service.poses()["Side"]
        self.assertEqual(first[1], second[1])
        self.assertLess((first[0] + second[0] - 2 * (first[1] + self.Gf.Vec3d(0, 0, 2.3))).GetLength(), 1e-6)
        self.service.zoom_side(.8)
        self.assertEqual(top, self.matrix("Top"))
        self.assertAlmostEqual(self.service.side_distance, 4.4)

    def test_cleanup_is_idempotent_and_reopen_does_not_leave_camera_layers(self):
        self.service.close()
        self.service.close()
        self.service = self.views.RtgWorkViews(self.stage)
        self.assertEqual(len(self.stage.GetSessionLayer().subLayerPaths), len(self.layers_before) + 1)

    def focal(self, name="Top"):
        return self.UsdGeom.Camera(self.stage.GetPrimAtPath(self.views.ROOT_PATH + "/" + name)).GetFocalLengthAttr().Get()

    def test_lowering_through_reference_heights_zooms_in_and_raising_zooms_out(self):
        eye = self.matrix("Top")
        focals = []
        for height in (15, 10, 5):
            self.controller.set_hoist_safe(height)
            positions = self.controller.get_positions()
            self.service.update_cameras(dt=10)
            focals.append(self.focal())
            self.assertAlmostEqual(self.service.status()["hoist_height_m"], height, places=5)
            self.assertEqual(positions, self.controller.get_positions())
            self.assertEqual(eye, self.matrix("Top"))
        self.assertLess(focals[0], focals[1])
        self.assertLess(focals[1], focals[2])
        self.controller.set_hoist_safe(15)
        self.service.update_cameras(dt=10)
        self.assertAlmostEqual(self.focal(), focals[0], places=3)

    def test_zoom_is_smoothed_and_side_lens_is_independent(self):
        self.controller.set_hoist_safe(15)
        self.service.update_cameras(dt=10)
        before, side = self.focal(), self.focal("Side")
        self.controller.set_hoist_safe(5)
        self.service.update_cameras(dt=1 / 30)
        self.assertGreater(self.focal(), before)
        self.assertLess(self.focal(), self.service.top_target_focal_mm)
        for _ in range(150): self.service.update_cameras(dt=1 / 30)
        self.assertAlmostEqual(self.focal(), self.service.top_target_focal_mm, places=3)
        self.assertEqual(side, self.focal("Side"))
        self.service.set_top_auto_zoom(False)
        self.assertAlmostEqual(self.focal(), self.views.TOP_WIDE_FOCAL_MM, places=3)
        self.controller.set_hoist_safe(10)
        self.service.update_cameras(dt=1)
        self.assertAlmostEqual(self.focal(), self.views.TOP_WIDE_FOCAL_MM, places=3)

    def test_auto_zoom_keeps_all_feet_in_frame_during_trim_and_fast_raising(self):
        self.controller.set_spreader_trim(5, .3)
        for height in (5, .5, 10, 15, 18):
            self.controller.set_hoist_safe(height)
            self.service.update_cameras(dt=1 / 30)
            camera_inverse = self.matrix("Top").GetInverse()
            transforms = self.UsdGeom.XformCache()
            focal = self.focal()
            for prim, point in self.service.anchors:
                position = camera_inverse.Transform(transforms.GetLocalToWorldTransform(prim).Transform(point))
                self.assertLess(position[2], 0)
                self.assertLess(abs(position[0]) * focal / (-position[2] * 18), 1)
                self.assertLess(abs(position[1]) * focal / (-position[2] * 13.5), 1)

    def test_overview_frames_entire_crane_without_acquiring_equipment_control(self):
        before = self.controller.get_positions()
        self.service.update_cameras()
        self.assertEqual(before, self.controller.get_positions())
        self.assertEqual(self.service.status()["views"], ["under_trolley", "side", "rtg_overview"])
        self.assertEqual(len(self.service.status()["camera_paths"]), 3)
        for name in self.views.VIEW_NAMES:
            camera = self.UsdGeom.Camera(self.stage.GetPrimAtPath(self.views.ROOT_PATH + "/" + name))
            self.assertEqual(camera.GetVisibilityAttr().Get(), self.UsdGeom.Tokens.invisible)
        camera_inverse = self.matrix("Overview").GetInverse()
        gantry = self.UsdGeom.XformCache().GetLocalToWorldTransform(self.stage.GetPrimAtPath(self.live.GANTRY_PATH))
        focal = self.focal("Overview")
        for point in self.service.overview_local_corners:
            position = camera_inverse.Transform(gantry.Transform(point))
            self.assertLess(position[2], 0)
            self.assertLess(abs(position[0]) * focal / (-position[2] * 18), 1)
            self.assertLess(abs(position[1]) * focal / (-position[2] * 13.5), 1)
        direction = self.matrix("Overview").TransformDir(self.Gf.Vec3d(0, 0, -1))
        self.assertGreater(abs(direction[0]), .1)
        self.assertGreater(abs(direction[1]), .3)
        import math
        self.assertAlmostEqual(math.degrees(math.atan2(-direction[0], direction[1])), -12.0, places=6)
        self.assertAlmostEqual(math.degrees(math.asin(direction[2])), -8.0, places=6)
        centre = gantry.Transform(self.service.overview_local_center)
        to_centre = (centre - self.matrix("Overview").ExtractTranslation()).GetNormalized()
        self.assertLess((direction - to_centre).GetLength(), 1e-8)
        self.assertGreater(self.matrix("Overview").ExtractTranslation()[2], centre[2])

    def test_overview_follows_gantry_and_stays_stable_during_trolley_hoist_and_zoom(self):
        before = self.matrix("Overview")
        g, t, _ = self.controller.get_positions()
        self.controller.set_trolley_safe(t + 1)
        self.controller.set_hoist_safe(5)
        self.controller.set_spreader_trim(2, .1)
        self.service.zoom_side(.8)
        self.service.update_cameras(dt=1)
        self.assertEqual(before, self.matrix("Overview"))
        self.controller.set_gantry_safe(g + 2)
        self.service.update_cameras(dt=1)
        after = self.matrix("Overview")
        self.assertAlmostEqual(after.ExtractTranslation()[1] - before.ExtractTranslation()[1], 2, places=4)
        self.assertAlmostEqual(after.ExtractTranslation()[0], before.ExtractTranslation()[0], places=4)
        for axis in (self.Gf.Vec3d(1, 0, 0), self.Gf.Vec3d(0, 1, 0), self.Gf.Vec3d(0, 0, 1)):
            self.assertLess((before.TransformDir(axis) - after.TransformDir(axis)).GetLength(), 1e-8)
