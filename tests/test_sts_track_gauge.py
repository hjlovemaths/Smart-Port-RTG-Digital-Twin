"""Final composed scene: wider lower portal, unchanged upper works and height."""
import sys
import unittest
from pathlib import Path

try:
    from pxr import Gf, Usd, UsdGeom
except ImportError:
    raise unittest.SkipTest('Requires pxr')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'omniverse/scripts'))
from build_sts_dimensions import bounds, wheel_center, QUAY
from build_sts_elevation import LEG_NAME
from build_sts_track_gauge import OUTPUT, PLATFORM_NAME, TOWER_TAGS
from sts_control import STSController


class STSTrackGaugeTest(unittest.TestCase):
    def setUp(self):
        path = str(ROOT / 'omniverse/scenes/smart_port.usda')
        self.stage = Usd.Stage.Open(path)
        self.old = Usd.Stage.Open(path)
        self.old.MuteLayer(str(OUTPUT))

    def test_seven_cranes_and_rails_widen_without_changing_leg_size(self):
        for crane in self.stage.GetPrimAtPath('/World/STSControls').GetChildren():
            path = str(crane.GetPath())
            old_sea, old_land = (wheel_center(self.old, path, i) for i in (0, 2))
            sea, land = (wheel_center(self.stage, path, i) for i in (0, 2))
            self.assertAlmostEqual(sea, old_sea, places=5)
            self.assertAlmostEqual(land-sea, (old_land-old_sea)*1.5, places=5)
            for index, x in ((1, land), (2, sea)):
                self.assertAlmostEqual(bounds(self.stage, QUAY + f'/Rails/STS_Rail_{index:02d}').GetMidpoint()[0], x, places=5)
            for i in range(4):
                leg = path + '/Gantry/Fixed/' + LEG_NAME.replace('leg_0_', f'leg_{i}_')
                old, new = (bounds(s, leg) for s in (self.old, self.stage))
                delta = Gf.Vec3d(land-old_land if i >= 2 else 0, 0, 0)
                self.assertLess((new.GetMidpoint()-old.GetMidpoint()-delta).GetLength(), 1e-5)
                self.assertLess((new.GetSize()-old.GetSize()).GetLength(), 1e-5)

    def test_main_boom_ground_deck_and_lanes_unchanged(self):
        for crane in self.stage.GetPrimAtPath('/World/STSControls').GetChildren():
            path = str(crane.GetPath())
            for suffix in ('/Gantry/Boom', '/Gantry/Trolley'):
                self.assertEqual(bounds(self.stage, path+suffix), bounds(self.old, path+suffix))
            fixed = path + '/Gantry/Fixed'
            top = bounds(self.old, fixed + '/' + LEG_NAME).GetMax()[2]
            for p in self.old.GetPrimAtPath(fixed).GetChildren():
                if (bounds(self.old, p.GetPath()).GetMin()[2] >= top-.08
                        and not any(t in p.GetName().lower() for t in TOWER_TAGS)):
                    self.assertEqual(bounds(self.stage, p.GetPath()), bounds(self.old, p.GetPath()))
        for suffix in ('/Decks', '/Markings', '/Edges', '/Supports'):
            self.assertEqual(bounds(self.stage, QUAY+suffix), bounds(self.old, QUAY+suffix))

    def test_platform_centered_and_tower_assembly_moves_rigidly(self):
        for crane in self.stage.GetPrimAtPath('/World/STSControls').GetChildren():
            path = str(crane.GetPath())
            sea, land = (wheel_center(self.stage, path, i) for i in (0, 2))
            roof_path = path + '/Gantry/Fixed/' + PLATFORM_NAME
            a, b = (bounds(s, roof_path) for s in (self.old, self.stage))
            self.assertAlmostEqual(b.GetMidpoint()[0], (sea+land)/2, places=5)
            delta = b.GetMidpoint() - a.GetMidpoint()
            self.assertAlmostEqual(delta[1], 0, places=5)
            self.assertAlmostEqual(delta[2], 0, places=5)
            count = 0
            for p in self.stage.GetPrimAtPath(path+'/Gantry/Fixed').GetChildren():
                if not any(t in p.GetName().lower() for t in TOWER_TAGS): continue
                count += 1
                a, b = (bounds(s, p.GetPath()) for s in (self.old, self.stage))
                self.assertLess((b.GetSize()-a.GetSize()).GetLength(), 1e-5)
                self.assertLess((b.GetMidpoint()-a.GetMidpoint()-delta).GetLength(), 1e-5)
            self.assertGreater(count, 10)

    def test_centered_tower_stays_match_saved_and_moving_poses(self):
        from sts_control import stay_endpoints
        c = STSController(self.stage)
        try:
            for angle in (0, 60):
                c.state.update(boom=angle, trolley=-8, hoist=c.park_hoist)
                c._apply()
                expected = stay_endpoints(angle, c.fixed_width_scale, c.fixed_width_anchor,
                                          c.upper_offset, c.tower_offset)
                old = stay_endpoints(angle, c.fixed_width_scale, c.fixed_width_anchor, c.upper_offset)
                for i, ((top, end), (old_top, old_end)) in enumerate(zip(expected, old), 4):
                    self.assertLess((end-old_end).GetLength(), 1e-6)
                    self.assertLess((top-old_top-c.tower_offset).GetLength(), 1e-6)
                    p = self.stage.GetPrimAtPath(c.path+f'/Gantry/DynamicCables/Cable_{i}')
                    m = UsdGeom.Xformable(p).GetLocalTransformation()
                    self.assertLess((m.Transform(Gf.Vec3d(0,0,-.5))-top).GetLength(), 1e-6)
                    self.assertLess((m.Transform(Gf.Vec3d(0,0,.5))-end).GetLength(), 1e-6)
        finally:
            c.close()
        expected = stay_endpoints(0, c.fixed_width_scale, c.fixed_width_anchor,
                                  c.upper_offset, c.tower_offset)
        for i, (top, end) in enumerate(expected, 4):
            m = UsdGeom.Xformable(self.stage.GetPrimAtPath(c.path+f'/Gantry/DynamicCables/Cable_{i}')).GetLocalTransformation()
            self.assertLess((m.Transform(Gf.Vec3d(0,0,-.5))-top).GetLength(), 1e-6)
            self.assertLess((m.Transform(Gf.Vec3d(0,0,.5))-end).GetLength(), 1e-6)

    def test_brace_end_connections_follow_legs(self):
        path = '/World/STSControls/STS_04'
        delta = wheel_center(self.stage, path, 2) - wheel_center(self.old, path, 2)
        count = 0
        for p in self.old.GetPrimAtPath(path+'/Gantry/Fixed').GetChildren():
            if 'brace' not in p.GetName().lower(): continue
            old, new = (bounds(s, p.GetPath()) for s in (self.old, self.stage))
            if old.GetSize()[0] < 8: continue
            count += 1
            self.assertAlmostEqual(old.GetMin()[0], new.GetMin()[0], places=5)
            self.assertAlmostEqual(old.GetMax()[0]+delta, new.GetMax()[0], places=5)
            self.assertAlmostEqual(old.GetSize()[2], new.GetSize()[2], places=5)
        self.assertGreater(count, 0)

    def test_final_composition_pick_and_drop_reachable(self):
        from sts_unload_task import UnloadTask, BOX, TRUCK
        c = STSController(self.stage, 'STS_01')
        try:
            task = UnloadTask.__new__(UnloadTask)
            task.c, task.stage = c, self.stage
            box, deck = bounds(self.stage, BOX), bounds(self.stage, TRUCK+'/TrailerDeck')
            pick = Gf.Vec3d(box.GetMidpoint()[0], box.GetMidpoint()[1], box.GetMax()[2])
            drop = Gf.Vec3d(-238, pick[1], deck.GetMax()[2]+box.GetSize()[2]+.02)
            for target in (pick, drop):
                c.state.update(task.pose(target)); c._apply()
                self.assertLess((task.contact()-target).GetLength(), 1e-5)
        finally:
            c.close()


if __name__ == '__main__':
    unittest.main()
