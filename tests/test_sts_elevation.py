"""Column-only extension, rigid upper relocation and dynamic attachment tests."""
import sys
import unittest
from pathlib import Path

try:
    from pxr import Gf, Usd, UsdGeom
except ImportError:
    raise unittest.SkipTest('Requires pxr')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'omniverse/scripts'))
from build_sts_dimensions import bounds
from build_sts_elevation import OUTPUT, LEG_NAME
from sts_control import STSController, INITIAL, PIVOT, boom_matrix, stay_endpoints


class STSElevationTest(unittest.TestCase):
    def setUp(self):
        path = str(ROOT / 'omniverse/scenes/smart_port.usda')
        self.stage = Usd.Stage.Open(path)
        self.old = Usd.Stage.Open(path)
        for stage in (self.stage, self.old):
            stage.MuteLayer(str(ROOT / 'omniverse/scenes/sts_track_gauge.usda'))
        self.old.MuteLayer(str(OUTPUT))

    def test_all_columns_twice_as_tall_and_ground_unchanged(self):
        for crane in self.stage.GetPrimAtPath('/World/STSControls').GetChildren():
            fixed = str(crane.GetPath()) + '/Gantry/Fixed'
            for i in range(4):
                path = fixed + '/' + LEG_NAME.replace('leg_0_', f'leg_{i}_')
                a, b = (bounds(s, path) for s in (self.old, self.stage))
                self.assertAlmostEqual(b.GetMin()[2], a.GetMin()[2], places=5)
                self.assertAlmostEqual(b.GetSize()[2], a.GetSize()[2] * 2, places=5)
                for axis in (0, 1):
                    self.assertAlmostEqual(b.GetMin()[axis], a.GetMin()[axis], places=5)
                    self.assertAlmostEqual(b.GetMax()[axis], a.GetMax()[axis], places=5)
            for p in self.stage.GetPrimAtPath(fixed).GetChildren():
                if any(t in p.GetName().lower() for t in ('wheel', 'hub', 'bogie', 'axle', 'foot_gusset')):
                    self.assertEqual(bounds(self.stage, p.GetPath()), bounds(self.old, p.GetPath()))
        self.assertEqual(bounds(self.stage, '/World/SMART_PORT_OFFSHORE_QUAY'),
                         bounds(self.old, '/World/SMART_PORT_OFFSHORE_QUAY'))

    def test_upper_work_moves_rigidly_and_hoist_home_stays_low(self):
        for crane in self.stage.GetPrimAtPath('/World/STSControls').GetChildren():
            path = str(crane.GetPath())
            lift = crane.GetAttribute('sts:upperLiftMeters').Get()
            delta = Gf.Vec3d(-3, 0, lift)
            for suffix in ('/Gantry/Boom', '/Gantry/Fixed/SMART_PORT_quay_sts_crane_0_main_mast_data',
                           '/Gantry/Fixed/SMART_PORT_quay_sts_crane_0_orange_mid_girder_data_Rear'):
                a, b = (bounds(s, path + suffix) for s in (self.old, self.stage))
                self.assertLess((b.GetSize() - a.GetSize()).GetLength(), 1e-5)
                self.assertLess((b.GetMidpoint() - a.GetMidpoint() - delta).GetLength(), 1e-5)
            a, b = (bounds(s, path + '/Gantry/Trolley/Hoist') for s in (self.old, self.stage))
            self.assertLess((b.GetSize() - a.GetSize()).GetLength(), 1e-5)
            self.assertLess((b.GetMidpoint() - a.GetMidpoint() - Gf.Vec3d(-3, 0, 0)).GetLength(), 1e-5)

    def test_park_interlock_extended_and_stays_follow_shifted_hinge(self):
        c = STSController(self.stage)
        try:
            c.command(trolley=-8, hoist=7.7)
            for _ in range(600): c.tick(.1)
            with self.assertRaises(ValueError): c.command(boom=45)
            c.command(hoist=c.park_hoist)
            for _ in range(600): c.tick(.1)
            c.command(boom=60)
            for _ in range(600): c.tick(.1)
            ends = stay_endpoints(60, c.fixed_width_scale, c.fixed_width_anchor, c.upper_offset)
            for i, (top, end) in enumerate(ends, 4):
                p = self.stage.GetPrimAtPath(c.path + f'/Gantry/DynamicCables/Cable_{i}')
                m = UsdGeom.Xformable(p).GetLocalTransformation()
                self.assertLess((m.Transform(Gf.Vec3d(0, 0, -.5))-top).GetLength(), 1e-6)
                self.assertLess((m.Transform(Gf.Vec3d(0, 0, .5))-end).GetLength(), 1e-6)
            # Independent check: the front attachment rotates around the relocated hinge.
            forward = Gf.Vec3d(-2.05, -16.38, 9.86) + c.upper_offset
            expected = boom_matrix(60, PIVOT + c.upper_offset).Transform(forward)
            self.assertLess((ends[0][1] - expected).GetLength(), 1e-6)
        finally:
            c.close()

    def test_pick_drop_solver_and_spreader_rope_anchors(self):
        from sts_unload_task import UnloadTask, BOX, TRUCK
        c = STSController(self.stage, 'STS_01')
        try:
            task = UnloadTask.__new__(UnloadTask)
            task.c, task.stage = c, self.stage
            box = bounds(self.stage, BOX)
            deck = bounds(self.stage, TRUCK + '/TrailerDeck')
            pick = Gf.Vec3d(box.GetMidpoint()[0], box.GetMidpoint()[1], box.GetMax()[2])
            place = Gf.Vec3d(-238, pick[1], deck.GetMax()[2] + box.GetSize()[2] + .02)
            for target in (pick, place):
                c.state.update(task.pose(target))
                c._apply()
                self.assertLess((task.contact() - target).GetLength(), 1e-5)
                cache = UsdGeom.XformCache()
                hoist = cache.GetLocalToWorldTransform(self.stage.GetPrimAtPath(c.path + '/Gantry/Trolley/Hoist'))
                cable = cache.GetLocalToWorldTransform(self.stage.GetPrimAtPath(c.path + '/Gantry/DynamicCables/Cable_0'))
                self.assertLess((hoist.Transform(Gf.Vec3d(-.26, -.92, 4.24)) -
                                 cable.Transform(Gf.Vec3d(0, 0, .5))).GetLength(), 1e-5)
        finally:
            c.close()

    def test_saved_pose_cables_match_runtime_initialization(self):
        path = '/World/STSControls/STS_04/Gantry/DynamicCables'
        before = [UsdGeom.Xformable(self.stage.GetPrimAtPath(path + f'/Cable_{i}')).GetLocalTransformation()
                  for i in range(8)]
        c = STSController(self.stage)
        try:
            for i, old in enumerate(before):
                new = UsdGeom.Xformable(self.stage.GetPrimAtPath(path + f'/Cable_{i}')).GetLocalTransformation()
                for z in (-.5, .5):
                    self.assertLess((old.Transform(Gf.Vec3d(0, 0, z)) -
                                     new.Transform(Gf.Vec3d(0, 0, z))).GetLength(), 1e-6)
        finally:
            c.close()


if __name__ == '__main__':
    unittest.main()
