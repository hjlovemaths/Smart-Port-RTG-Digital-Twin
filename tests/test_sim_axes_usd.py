"""Exercise SIM axis commands and their readback on the port stage."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "omniverse/scripts"))
sys.path.insert(0, str(ROOT / "omniverse/extensions/smartport.agent_panel"))


class SimAxesUsdTests(unittest.TestCase):
    def setUp(self):
        try:
            from pxr import Usd, UsdGeom
        except ImportError:
            self.skipTest("Requires the Kit USD runtime")
        import rtg_live_control as live
        from smartport.agent_panel import sim_workbench
        from smartport.agent_panel.scene_runtime import SceneRuntime

        self.stage = Usd.Stage.Open(str(ROOT / "omniverse/scenes/smart_port.usda"))
        self.runtime = SceneRuntime(ROOT)
        self.runtime.bind(self.stage)
        self.addCleanup(self.runtime.release)
        self.sim = sim_workbench
        self.calibration = live
        self.live = live.RTGController(self.stage)
        self.xform_cache = UsdGeom.XformCache
        self.trolley_translate = self.stage.GetPrimAtPath(live.TROLLEY_PATH).GetAttribute("xformOp:translate")

    def jog(self, start, direction, end):
        previous_x = first_x = self.trolley_translate.Get()[0]
        previous_position = self.sim.state(self.runtime)["positions_m"]["trolley_m"]
        self.assertAlmostEqual(previous_position, start, places=5)
        self.sim.command(self.runtime, {"action": "jog", "axis": "trolley_m", "direction": direction})
        for _ in range(30):
            self.runtime.tick(self.stage, .1)
            position = self.sim.state(self.runtime)["positions_m"]["trolley_m"]
            x = self.trolley_translate.Get()[0]
            self.assertGreater((position - previous_position) * direction, 0.)
            self.assertGreater((previous_x - x) * direction, 0.)
            self.assertLess(abs(x - previous_x), .04)
            previous_position, previous_x = position, x

        report = self.sim.state(self.runtime)
        self.assertEqual(report["operation"]["status"], "completed")
        self.assertAlmostEqual(report["positions_m"]["trolley_m"], end, places=5)
        self.assertAlmostEqual(report["operation"]["positions_m"]["trolley_m"], end, places=5)
        self.assertGreater(abs(previous_x - first_x), .1)

    def test_jog_through_plc_truck_stop_plateau(self):
        self.live.set_trolley(.48)
        truck_stop_x = self.trolley_translate.Get()[0]
        self.jog(.48, 1, .98)
        self.jog(.98, -1, .48)
        self.assertAlmostEqual(self.trolley_translate.Get()[0], truck_stop_x, places=6)

    def test_jog_from_authored_home_and_back_to_lower_limit(self):
        home_x = self.trolley_translate.Get()[0]
        self.jog(0., 1, .5)
        self.jog(.5, -1, 0.)
        self.assertAlmostEqual(self.trolley_translate.Get()[0], home_x, places=6)

    def assert_smooth_motion(self, equipment, axis, prim_path, component, command, target, max_step):
        prim = self.stage.GetPrimAtPath(prim_path)

        def world_position():
            return self.xform_cache().GetLocalToWorldTransform(prim).ExtractTranslation()[component]

        origin = previous_pose = world_position()
        previous_readback = self.sim.state(self.runtime, equipment)["positions_m"][axis]
        direction = 1 if target > previous_readback else -1
        self.sim.command(self.runtime, {"equipment": equipment, "axis": axis, **command})
        self.assertAlmostEqual(world_position(), origin, places=6)
        for _ in range(32):
            self.runtime.tick(self.stage, .1)
            report = self.sim.state(self.runtime, equipment)
            pose = world_position()
            readback = report["positions_m"][axis]
            self.assertGreaterEqual((pose - previous_pose) * direction, -1e-6)
            self.assertGreaterEqual((readback - previous_readback) * direction, -1e-6)
            self.assertLessEqual(abs(pose - previous_pose), max_step)
            previous_pose, previous_readback = pose, readback
            if report["operation"]["status"] == "completed":
                break

        self.assertEqual(report["operation"]["status"], "completed")
        self.assertAlmostEqual(report["positions_m"][axis], target, places=4)
        self.assertGreater(abs(pose - origin), .1)

    def test_rtg_first_hoist_jog_from_authored_home_eases_to_target(self):
        path = self.calibration.HOIST_PATH
        self.assertEqual(self.sim.state(self.runtime)["positions_m"]["hoist_m"], 18.)
        self.assert_smooth_motion("rtg", "hoist_m", path, 2, {"action": "jog", "direction": -1}, 17.5, .025)
        hoist_z = self.stage.GetPrimAtPath(path).GetAttribute("xformOp:translate").Get()[2]
        self.assertAlmostEqual(hoist_z, self.calibration.hoist_sim_to_usd(17.5), places=6)
        self.assert_smooth_motion("rtg", "hoist_m", path, 2, {"action": "jog", "direction": 1}, 18., .025)

    def test_rtg_gantry_and_hoist_can_jog_exactly_to_soft_limits(self):
        for axis, path, component, limits, set_position in (
            ("gantry_m", self.calibration.GANTRY_PATH, 1, self.calibration.GANTRY_SOFT_LIMITS, self.live.set_gantry),
            ("hoist_m", self.calibration.HOIST_PATH, 2, self.calibration.HOIST_SOFT_LIMITS,
             self.calibration.RTGController(self.stage, simulation=True).set_hoist),
        ):
            for limit, direction in zip(limits, (-1, 1)):
                with self.subTest(axis=axis, limit=limit):
                    set_position(limit - direction * .5)
                    self.assert_smooth_motion("rtg", axis, path, component, {"action": "jog", "direction": direction}, limit, .026)
                    with self.assertRaisesRegex(ValueError, "限位"):
                        self.sim.command(self.runtime, {"action": "jog", "axis": axis, "direction": direction})

    def test_rtg_gantry_moves_continuously_to_distant_target(self):
        self.live.set_gantry(262.237)
        self.assert_smooth_motion("rtg", "gantry_m", self.calibration.GANTRY_PATH, 1,
                                  {"action": "move", "target_m": 280.}, 280., .89)

    def test_rtg_gantry_and_hoist_can_recover_smoothly_into_soft_limits(self):
        jog = {"action": "jog", "direction": 1}
        self.live.set_gantry(.5)
        self.assert_smooth_motion("rtg", "gantry_m", self.calibration.GANTRY_PATH, 1, jog, 1., .026)
        self.live.set_hoist(0.)
        self.assert_smooth_motion("rtg", "hoist_m", self.calibration.HOIST_PATH, 2, jog, .5, .026)

    def test_sts_first_gantry_and_hoist_jogs_start_from_existing_pose(self):
        gantry = "/World/STSControls/STS_01/Gantry"
        jog = {"action": "jog", "direction": 1}
        self.assert_smooth_motion("sts01", "gantry_m", gantry, 1, jog, .5, .101)
        hoist = self.sim.state(self.runtime, "sts01")["positions_m"]["hoist_m"]
        self.assert_smooth_motion("sts01", "hoist_m", gantry + "/Trolley/Hoist", 2, jog, hoist + .5, .145)
