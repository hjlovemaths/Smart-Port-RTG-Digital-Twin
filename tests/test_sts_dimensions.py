"""Geometry regressions for the non-destructive enlarged STS/quay layer."""
import sys
import unittest
from pathlib import Path

try:
    from pxr import Gf, Usd, UsdGeom
except ImportError:
    raise unittest.SkipTest('Requires pxr')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'omniverse/scripts'))
from build_sts_dimensions import bounds, wheel_center, OUTPUT, QUAY
from sts_control import STSController, stay_endpoints


class STSDimensionsTest(unittest.TestCase):
    def setUp(self):
        path = str(ROOT / 'omniverse/scenes/smart_port.usda')
        self.stage = Usd.Stage.Open(path)
        self.old = Usd.Stage.Open(path)
        # Test the width/initial-height layer independently of the subsequent
        # column-only elevation, which has its own regression suite.
        for stage in (self.stage, self.old):
            stage.MuteLayer(str(ROOT / 'omniverse/scenes/sts_track_gauge.usda'))
            stage.MuteLayer(str(ROOT / 'omniverse/scenes/sts_elevation.usda'))
        self.old.MuteLayer(str(OUTPUT))

    def test_all_seven_cranes_double_height_and_further_widen_gauge(self):
        for crane in self.stage.GetPrimAtPath('/World/STSControls').GetChildren():
            path = str(crane.GetPath())
            old = bounds(self.old, path + '/Gantry')
            new = bounds(self.stage, path + '/Gantry')
            self.assertAlmostEqual(new.GetSize()[2], 2 * old.GetSize()[2], places=4)
            sea, land = (wheel_center(self.stage, path, i) for i in (0, 2))
            old_sea, old_land = (wheel_center(self.old, path, i) for i in (0, 2))
            self.assertAlmostEqual(sea, old_sea, places=5)
            previous_gauge = 2 * (old_land - old_sea)
            self.assertAlmostEqual(land - sea, 1.5 * previous_gauge, places=5)
            for i, x in ((1, land), (2, sea)):
                rail = bounds(self.stage, QUAY + f'/Rails/STS_Rail_{i:02d}')
                self.assertAlmostEqual(rail.GetMidpoint()[0], x, places=5)
                self.assertAlmostEqual(new.GetMin()[2], rail.GetMax()[2], places=5)
            old_boom, new_boom = (bounds(s, path + '/Gantry/Boom') for s in (self.old, self.stage))
            self.assertAlmostEqual(old_boom.GetMin()[0], new_boom.GetMin()[0], places=5)
            self.assertAlmostEqual(old_boom.GetMax()[0], new_boom.GetMax()[0], places=5)

    def test_stay_cables_match_fixed_structure_and_runtime(self):
        c = STSController(self.stage)
        try:
            for angle in (0, 60):
                c.state.update(boom=angle, trolley=-8, hoist=7.7)
                c._apply()
                ends = stay_endpoints(angle, c.fixed_width_scale, c.fixed_width_anchor)
                for i, (top, end) in enumerate(ends, 4):
                    p = self.stage.GetPrimAtPath(c.path + f'/Gantry/DynamicCables/Cable_{i}')
                    m = UsdGeom.Xformable(p).GetLocalTransformation()
                    self.assertLess((m.Transform(Gf.Vec3d(0, 0, -.5)) - top).GetLength(), 1e-6)
                    self.assertLess((m.Transform(Gf.Vec3d(0, 0, .5)) - end).GetLength(), 1e-6)
        finally:
            c.close()
        ends = stay_endpoints(0, 3, c.fixed_width_anchor)
        for i, (top, end) in enumerate(ends, 4):
            p = self.stage.GetPrimAtPath(c.path + f'/Gantry/DynamicCables/Cable_{i}')
            m = UsdGeom.Xformable(p).GetLocalTransformation()
            self.assertLess((m.Transform(Gf.Vec3d(0, 0, .5)) - end).GetLength(), 1e-6)

    def test_ground_access_and_waterside_lane(self):
        deck = bounds(self.stage, QUAY + '/Decks/OffshoreQuayPlatform')
        self.assertAlmostEqual(deck.GetSize()[0], 40)
        self.assertAlmostEqual(deck.GetMin()[0], -241.5)
        for crane in self.stage.GetPrimAtPath('/World/STSControls').GetChildren():
            self.assertLess(bounds(self.stage, str(crane.GetPath()) + '/Gantry/Fixed').GetMax()[0],
                            deck.GetMax()[0])
        for i in range(1, 5):
            bridge = bounds(self.stage, QUAY + f'/Decks/Causeway_{i:02d}')
            self.assertAlmostEqual(bridge.GetMin()[0], deck.GetMax()[0])
            self.assertAlmostEqual(bridge.GetMax()[0], -101.5)
        for p in self.stage.GetPrimAtPath(QUAY + '/Markings').GetChildren():
            if p.GetName().startswith('WatersideLane_'):
                self.assertEqual(bounds(self.stage, p.GetPath()), bounds(self.old, p.GetPath()))

    def test_ship_pick_and_truck_drop_remain_reachable(self):
        from sts_unload_task import UnloadTask, BOX, TRUCK
        c = STSController(self.stage, 'STS_01')
        try:
            # Exercise only the coordinate solver, without starting the delivery.
            task = UnloadTask.__new__(UnloadTask)
            task.c, task.stage = c, self.stage
            box = bounds(self.stage, BOX)
            deck = bounds(self.stage, TRUCK + '/TrailerDeck')
            pick = Gf.Vec3d(box.GetMidpoint()[0], box.GetMidpoint()[1], box.GetMax()[2])
            place = Gf.Vec3d(-238, pick[1], deck.GetMax()[2] + box.GetSize()[2] + .02)
            for target in (pick, place):
                pose = task.pose(target)  # validates all axis limits
                c.state.update(pose)
                c._apply()
                self.assertLess((task.contact() - target).GetLength(), 1e-5)
        finally:
            c.close()


if __name__ == '__main__':
    unittest.main()
