"""Integration tests against real USD meshes/transforms (run with pxr installed)."""
import sys
import unittest
from pathlib import Path

try:
    from pxr import Gf, Usd, UsdGeom
except ImportError:
    raise unittest.SkipTest('Run with the Kit USD Python environment')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'omniverse/scripts'))
from sts_control import STSController, INITIAL, PIVOT, boom_matrix, rope_endpoints


class STSTest(unittest.TestCase):
    def setUp(self):
        self.stage = Usd.Stage.Open(str(ROOT / 'omniverse/scenes/smart_port.usda'))
        self.before = self.stage.GetRootLayer().ExportToString()
        self.c = STSController(self.stage, 'STS_04')

    def tearDown(self):
        self.c.close()
        self.assertEqual(self.stage.GetRootLayer().ExportToString(), self.before)

    def settle(self, **values):
        self.c.command(**values)
        for _ in range(600):
            self.c.tick(0.1)
        for key, value in values.items():
            self.assertAlmostEqual(self.c.state[key], value)

    def bounds(self, suffix):
        return UsdGeom.BBoxCache(Usd.TimeCode(1), ['default', 'render']).ComputeWorldBound(
            self.stage.GetPrimAtPath(self.c.path + '/' + suffix)).ComputeAlignedRange()

    def center(self, suffix):
        return self.bounds(suffix).GetMidpoint()

    def test_composition_replaces_seven_visuals_and_keeps_tracks(self):
        self.assertEqual(len(self.stage.GetPrimAtPath('/World/STSControls').GetChildren()), 7)
        self.assertFalse(self.stage.GetPrimAtPath('/World/PortAndRTG/CODEx_STS_Crane1_boom_waterside_blue_nose_cap').IsActive())
        rail = self.stage.GetPrimAtPath('/World/PortAndRTG/SMART_PORT_quay_sts_crane_1_rail_front')
        self.assertTrue(rail.IsActive())
        for name in ('Gantry', 'Gantry/Fixed', 'Gantry/Boom', 'Gantry/Trolley/Hoist'):
            self.assertFalse(self.bounds(name).IsEmpty())

    def test_gantry_moves_whole_crane_world_y_only(self):
        p = self.center('Gantry')
        self.settle(gantry=4)
        self.assertLess((self.center('Gantry') - p - Gf.Vec3d(0, 4, 0)).GetLength(), 1e-5)

    def test_trolley_carries_hoist_but_not_girder(self):
        fixed = self.center('Gantry/Fixed')
        p = self.center('Gantry/Trolley/Hoist')
        self.settle(trolley=5.62)
        self.assertLess((self.center('Gantry/Trolley/Hoist')-p-Gf.Vec3d(-4, 0, 0)).GetLength(), 1e-5)
        self.assertLess((self.center('Gantry/Fixed')-fixed).GetLength(), 1e-7)

    def test_hoist_height_and_rope_attachment(self):
        before = self.bounds('Gantry/Trolley/Hoist').GetMin()[2]
        gantry = self.stage.GetPrimAtPath(self.c.path + '/Gantry')
        scale_z = UsdGeom.XformCache(Usd.TimeCode(1)).GetLocalToWorldTransform(gantry).TransformDir(Gf.Vec3d(0, 0, 1))[2]
        self.settle(hoist=7.7)
        self.assertAlmostEqual(self.bounds('Gantry/Trolley/Hoist').GetMin()[2],
                               before + (7.7 - INITIAL['hoist']) * scale_z, places=4)
        ends = rope_endpoints(self.c.state['trolley'], self.c.state['hoist'])
        for i, (top, bottom) in enumerate(ends):
            self.assertGreater(top[2], bottom[2])
            cable = self.stage.GetPrimAtPath(f'{self.c.path}/Gantry/DynamicCables/Cable_{i}')
            m = UsdGeom.Xformable(cable).GetLocalTransformation()
            self.assertLess((m.Transform(Gf.Vec3d(0, 0, -0.5))-top).GetLength(), 1e-6)
            self.assertLess((m.Transform(Gf.Vec3d(0, 0, 0.5))-bottom).GetLength(), 1e-6)

    def test_luffing_hinge_fixed_and_front_raises_without_rear_motion(self):
        self.settle(trolley=-8, hoist=7.7)
        fixed = self.center('Gantry/Fixed')
        self.settle(boom=60)
        self.assertLess((self.center('Gantry/Fixed')-fixed).GetLength(), 1e-7)
        self.assertGreater(self.bounds('Gantry/Boom').GetMax()[2], 20.0)
        self.assertLess((boom_matrix(60).Transform(PIVOT)-PIVOT).GetLength(), 1e-7)
        boom = self.stage.GetPrimAtPath(self.c.path+'/Gantry/Boom')
        g = self.stage.GetPrimAtPath(self.c.path+'/Gantry')
        cache = UsdGeom.XformCache()
        actual = cache.GetLocalToWorldTransform(boom).Transform(Gf.Vec3d(0))
        expected = cache.GetLocalToWorldTransform(g).Transform(PIVOT)
        self.assertLess((actual-expected).GetLength(), 1e-6)

    def test_interlock_rejects_unparked_and_simultaneous_bypass(self):
        for command in (dict(boom=45), dict(trolley=-8, hoist=7.7, boom=45)):
            with self.assertRaises(ValueError):
                self.c.command(**command)
            self.assertEqual(self.c.targets, INITIAL)
        self.settle(trolley=-8, hoist=7.7)
        self.settle(boom=45)
        for command in (dict(trolley=0), dict(hoist=2), dict(boom=0, trolley=4)):
            with self.assertRaises(ValueError):
                self.c.command(**command)
        self.settle(boom=0)
        self.settle(trolley=4, hoist=2)

    def test_invalid_commands_estop_and_cleanup(self):
        for command in (dict(boom=81), dict(hoist=-1), dict(gantry=999),
                        dict(trolley=float('nan')), dict(boom=float('inf'))):
            with self.assertRaises(ValueError):
                self.c.command(**command)
        self.c.command(gantry=4)
        self.c.tick(0.1)
        self.c.stop()
        pos = dict(self.c.state)
        self.c.tick(0.1)
        self.assertEqual(pos, self.c.state)
        with self.assertRaises(ValueError):
            self.c.command(gantry=0)
        self.c.resume()
        self.settle(gantry=0)
        identifier = self.c.layer.identifier
        self.c.close()
        self.assertNotIn(identifier, self.stage.GetSessionLayer().subLayerPaths)
        again = STSController(self.stage)
        again.close()

    def test_two_cranes_are_independent(self):
        other = STSController(self.stage, 'STS_05')
        before = UsdGeom.XformCache().GetLocalToWorldTransform(self.stage.GetPrimAtPath(other.path+'/Gantry'))
        self.settle(gantry=3, trolley=4, hoist=5)
        after = UsdGeom.XformCache().GetLocalToWorldTransform(self.stage.GetPrimAtPath(other.path+'/Gantry'))
        self.assertEqual(before, after)
        self.assertEqual(other.state, INITIAL)
        other.close()


if __name__ == '__main__':
    unittest.main()
