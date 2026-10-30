"""Full-model PLC command adapter tests. Requires Kit's bundled USD Python."""
import builtins
from pathlib import Path
import queue
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "omniverse/scripts"))
sys.path.insert(0, str(ROOT / "omniverse/extensions/smartport.agent_panel"))


class PlcUsdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from pxr import Usd, UsdGeom
        except ImportError:
            raise unittest.SkipTest("Requires Kit USD Python")
        import rtg_live_control
        import rtg_plc_scene_driver
        from smartport.agent_panel.scene_runtime import SceneRuntime
        cls.Usd, cls.UsdGeom = Usd, UsdGeom
        cls.live, cls.adapter = rtg_live_control, rtg_plc_scene_driver
        cls.Runtime = SceneRuntime

    def setUp(self):
        self.patch = patch.dict(builtins.__dict__, {
            "_SMART_PORT_RTG_PLC_CONTROL_ACTIVE": False,
            "_SMART_PORT_RTG_REMOTE_MANUAL_ACTIVE": False,
            "_smart_port_rtg_panel_facing_driver": None, "_smart_port_sts_panel": None,
            "_iecs_runtime": None})
        self.patch.start()
        self.stage = self.Usd.Stage.Open(str(ROOT / "omniverse/scenes/smart_port.usda"))
        self.original = self.stage.GetRootLayer().ExportToString()
        self.stage.SetEditTarget(self.stage.GetSessionLayer())
        self.controller = self.live.RTGController(self.stage)
        g, _, _ = self.controller.get_positions()
        self.controller.set_positions_safe(gantry_m=g, trolley_m=5, hoist_m=10)
        self.sent = []
        self.driver = self.adapter.PlcSceneDriver()
        self.driver.guard_runtime = self.Runtime(ROOT)
        self.driver.guard_runtime.plc_driver_owner = True
        self.driver.link = SimpleNamespace(connected=True, epoch=1, incoming=queue.Queue(), emit=self.sent.append,
                                           close=lambda: None, reset_event=threading.Event())
        self.driver.link.incoming.put((1, {"type": "welcome", "motion": {"watchdog_s": 5}}))
        self.driver.update(self.stage, .02)

    def tearDown(self):
        self.driver.close()
        self.assertEqual(self.stage.GetRootLayer().ExportToString(), self.original)
        self.patch.stop()

    def command(self, cmd, value):
        self.driver.link.incoming.put((1, {"type": "command", "id": len(self.sent), "expires_ms": 9999999999999,
            "message": {"match": "CTR", "cmd": cmd, "value": value, "CRC": "0", "time": "123"}}))
        self.driver.update(self.stage, .02)
        result = next(packet["result"] for packet in reversed(self.sent) if packet["type"] == "result")
        self.assertTrue(result["accepted"], result)

    def test_commands_move_visible_model_and_readback_uses_same_controller(self):
        before = self.controller.get_positions()
        self.command("ctr_sw_set", 1)
        self.command("vel", [.5, .25])
        for _ in range(20): self.driver.update(self.stage, .02)
        after = self.controller.get_positions()
        self.assertGreater(after[1], before[1])
        self.assertGreater(after[2], before[2])
        state = self.driver.engine.snapshot()
        self.assertAlmostEqual(state["content"]["little_car_pos"], after[1])
        self.assertAlmostEqual(state["digital_twin"]["hoist_height_m"], after[2])
        self.assertAlmostEqual(state["content"]["rope_pos"], 18 - after[2])
        prim = self.stage.GetPrimAtPath(self.live.HOIST_PATH)
        translated = prim.GetAttribute("xformOp:translate").Get()
        self.assertAlmostEqual(float(translated[2]), self.live.hoist_actual_to_usd(after[2]))
        self.command("ctr_sw_set", 0)
        stopped = self.controller.get_positions()
        for _ in range(5): self.driver.update(self.stage, .1)
        self.assertEqual(stopped, self.controller.get_positions())

    def test_trolley_crosses_flat_truck_calibration_interval(self):
        self.controller.set_trolley_safe(.48)
        self.command("ctr_sw_set", 1)
        self.command("vel", [1, 0])
        for _ in range(80): self.driver.update(self.stage, .02)
        self.assertGreater(self.controller.get_positions()[1], 1.5)

    def lock_feet(self):
        cache = self.UsdGeom.BBoxCache(self.Usd.TimeCode.Default(), ["default", "render"])
        return [cache.ComputeWorldBound(p).ComputeAlignedBox().GetMidpoint()
                for p in self.Usd.PrimRange(self.stage.GetPrimAtPath(self.live.HOIST_PATH))
                if p.IsA(self.UsdGeom.Boundable) and "red_corner_lock_foot_" in str(p.GetPath())]

    def test_spreader_rotates_rigidly_around_own_centre_under_nonuniform_asset_scale(self):
        from pxr import Gf
        before = self.lock_feet()
        axes = self.controller.get_positions()
        self.command("ctr_sw_set", 1)
        self.command("tool_action", 11)
        for _ in range(10): self.driver.update(self.stage, .1)
        after = self.lock_feet()
        self.assertEqual(axes, self.controller.get_positions())
        self.assertAlmostEqual(self.driver.controller.get_spreader_trim()[0], 1, places=5)
        self.assertLess((sum(before, Gf.Vec3d(0)) - sum(after, Gf.Vec3d(0))).GetLength(), 1e-5)
        for index in range(1, 4):
            self.assertAlmostEqual((before[index] - before[0]).GetLength(),
                                   (after[index] - after[0]).GetLength(), places=5)
        self.assertGreater((after[0] - before[0]).GetLength(), .01)
        self.command("tool_action", 10)
        self.command("tool_size", 1)
        self.assertAlmostEqual(self.driver.controller.current_spreader_size_ft(), 20)
        self.command("tool_size", 2)
        self.assertAlmostEqual(self.driver.controller.current_spreader_size_ft(), 40)
        for a, b in zip(after, self.lock_feet()):
            # The stopping command settles one last 0.02-second frame first.
            self.assertLess((a - b).GetLength(), .002)

    def test_spreader_sideshift_ropes_and_controller_recreation_preserve_alignment(self):
        before = self.lock_feet()
        axes = self.controller.get_positions()
        rope = self.stage.GetPrimAtPath(self.live.ROPE_SYSTEM_PATH)
        ropes_before = list(rope.GetAttribute("points").Get())
        self.command("ctr_sw_set", 1)
        self.command("tool_move", 11)
        for _ in range(10): self.driver.update(self.stage, .1)
        after = self.lock_feet()
        for a, b in zip(before, after):
            self.assertAlmostEqual(b[0] - a[0], -.05, places=5)
            self.assertAlmostEqual(b[1], a[1], places=5)
            self.assertAlmostEqual(b[2], a[2], places=5)
        ropes_after = list(rope.GetAttribute("points").Get())
        for index in range(len(ropes_before)):
            if index % 2:
                self.assertNotEqual(ropes_before[index], ropes_after[index])
            else:
                self.assertEqual(ropes_before[index], ropes_after[index])
        self.driver.controller.set_spreader_trim(2, .05)
        after = self.lock_feet()
        ropes_after = list(rope.GetAttribute("points").Get())
        recreated = self.live.RTGController(self.stage)
        recreated.set_trolley_safe(axes[1])
        recreated.set_hoist_safe(axes[2])
        for a, b in zip(after, self.lock_feet()):
            self.assertLess((a - b).GetLength(), 1e-5)
        self.assertEqual(axes, recreated.get_positions())
        self.assertAlmostEqual(recreated.get_spreader_trim()[1], .05, places=5)
        self.assertEqual(ropes_after, list(rope.GetAttribute("points").Get()))

    def test_spreader_smooth_return_and_reconnect_preserve_pose_without_duplicate_ops(self):
        import rtg_spreader_trim as trim
        self.command("ctr_sw_set", 1)
        self.command("move", [0, 0, 11, 21, 0, 0])
        for _ in range(10): self.driver.update(self.stage, .1)
        pose = self.driver.controller.get_spreader_trim()
        self.driver._make_engine()
        self.assertEqual(pose, self.driver.controller.get_spreader_trim())
        self.assertFalse(self.driver.engine.armed)
        self.command("ctr_sw_set", 1)
        self.command("tool_reset", 11)
        self.command("tool_reset", 21)
        for _ in range(20): self.driver.update(self.stage, .1)
        self.assertEqual(self.driver.controller.get_spreader_trim(), (0, 0))
        ops = self.UsdGeom.Xformable(self.stage.GetPrimAtPath(self.live.HOIST_PATH)).GetOrderedXformOps()
        self.assertEqual(sum(op.GetOpName() == trim.OP_NAME for op in ops), 1)

    def test_rope_anchors_stay_on_headers_across_hoist_trim_and_legacy_sync(self):
        from pxr import Gf
        import rtg_isaac_physics as physics
        rope = self.stage.GetPrimAtPath(self.live.ROPE_SYSTEM_PATH)
        hoist = self.stage.GetPrimAtPath(self.live.HOIST_PATH)
        anchors, upper = None, None
        try:
            for height, yaw, shift in [(5, 0, 0), (10, 2, .1), (15, -3, -.15), (10, 0, 0), (5, 0, 0)]:
                self.controller.set_hoist(height)
                self.controller.set_spreader_trim(yaw, shift)
                points = list(rope.GetAttribute("points").Get())
                xforms = self.UsdGeom.XformCache()
                to_hoist = (xforms.GetLocalToWorldTransform(rope)
                            * xforms.GetLocalToWorldTransform(hoist).GetInverse())
                local = [to_hoist.Transform(Gf.Vec3d(p)) for p in points[1::2]]
                if anchors is None:
                    anchors, upper = local, points[::2]
                    # Header top is below the green markers and labels. The
                    # cable must meet the yellow metal, not a marker's bound.
                    self.assertEqual(len({tuple(round(float(c), 4) for c in p) for p in local}), 4)
                    self.assertTrue(all(abs(p[2] - 3.7775) < 1e-5 for p in local))
                    self.assertTrue(all(min(abs(p[0] - x) for x in (2.4042, 2.69379)) < 1e-5 for p in local))
                self.assertEqual(points[::2], upper)
                for before, after in zip(anchors, local):
                    self.assertLess((before - after).GetLength(), 1e-5)
                self.assertTrue(physics.update_rtg_physics_visuals(self.stage, force=True))
                self.assertEqual(points, list(rope.GetAttribute("points").Get()))
        finally:
            physics.stop_rtg_physics_visual_sync(self.stage)

    def test_legacy_sync_cannot_apply_stale_targets_when_plc_owns_scene(self):
        import rtg_isaac_physics as physics
        rope = self.stage.GetPrimAtPath(self.live.ROPE_SYSTEM_PATH)
        paths = (self.live.GANTRY_PATH, self.live.TROLLEY_PATH, self.live.HOIST_PATH)
        before = [self.stage.GetPrimAtPath(p).GetAttribute("xformOp:translate").Get() for p in paths]
        points = list(rope.GetAttribute("points").Get())
        physics._ARTICULATION_TARGETS[id(self.stage)] = [0, 0, 0]
        try:
            with patch.dict(builtins.__dict__, {self.adapter.OWNER_FLAG: True}):
                physics.update_rtg_physics_visuals(self.stage, force=True)
            self.assertEqual(before, [self.stage.GetPrimAtPath(p).GetAttribute("xformOp:translate").Get() for p in paths])
            self.assertEqual(points, list(rope.GetAttribute("points").Get()))
        finally:
            physics._ARTICULATION_TARGETS.pop(id(self.stage), None)
            physics.stop_rtg_physics_visual_sync(self.stage)

    def test_trim_carries_external_container_rigidly_without_accumulation(self):
        from pxr import Gf
        import rtg_spreader_trim as trim
        cube = self.UsdGeom.Cube.Define(self.stage, "/World/TestTrimContainer")
        self.UsdGeom.Xformable(cube).AddTranslateOp().Set(Gf.Vec3d(2, 3, 4))
        original = self.UsdGeom.XformCache().GetLocalToWorldTransform(cube.GetPrim())
        self.driver.controller.set_spreader_trim(2, .1)
        helper = trim.SpreaderTrim(self.stage, self.live.HOIST_PATH)
        expected = original * helper.world_adjustment()
        for _ in range(5): helper.apply_to_carried_container(cube.GetPrim())
        actual = self.UsdGeom.XformCache().GetLocalToWorldTransform(cube.GetPrim())
        for row in range(4):
            for col in range(4): self.assertAlmostEqual(actual[row][col], expected[row][col], places=6)

    def test_gantry_motion_and_spreader_size_apply_to_scene(self):
        before = self.controller.get_positions()
        self.command("ctr_sw_set", 1)
        self.command("set_ctrl_cmd", {"switch": 1, "bigCarSpeed": .5, "speed": [0, 0]})
        for _ in range(10): self.driver.update(self.stage, .02)
        self.assertGreater(self.controller.get_positions()[0], before[0])
        self.command("bigcarstop", None)
        self.command("tool_size", 1)
        self.assertAlmostEqual(self.driver.controller.current_spreader_size_ft(), 20)
        self.command("tool_size", 2)
        self.assertAlmostEqual(self.driver.controller.current_spreader_size_ft(), 40)

    def test_agent_and_plc_control_cannot_claim_axes_simultaneously(self):
        other = self.Runtime(ROOT)
        other.bind(self.stage)
        self.command("ctr_sw_set", 1)
        with self.assertRaisesRegex(ValueError, "ZMQ PLC"):
            other._guard_manual("rtg")
        self.command("ctr_sw_set", 0)
        other.motion = {"id": "test-motion"}
        with self.assertRaisesRegex(RuntimeError, "Agent RTG"):
            self.driver.engine.apply({"match": "CTR", "cmd": "ctr_sw_set", "value": 1})
        self.assertFalse(self.driver.engine.armed)
        other.motion = None
        other.close()

    def test_stage_close_invalidates_queued_control_enable(self):
        self.command("ctr_sw_set", 1)
        self.driver.link.incoming.put((1, {"type": "command", "id": 999, "expires_ms": 9999999999999,
            "message": {"match": "CTR", "cmd": "ctr_sw_set", "value": 1}}))
        self.driver.update(None, .02)
        self.assertTrue(self.driver.link.reset_event.is_set())
        self.assertFalse(self.driver.link.connected)
        self.assertFalse(getattr(builtins, self.adapter.OWNER_FLAG))

    def test_new_velocity_does_not_apply_to_elapsed_time_before_its_arrival(self):
        self.command("ctr_sw_set", 1)
        before = self.controller.get_positions()
        self.driver.link.incoming.put((1, {"type": "command", "id": 999, "expires_ms": 9999999999999,
            "message": {"match": "CTR", "cmd": "vel", "value": [1, 0]}}))
        self.driver.update(self.stage, .4)
        self.assertEqual(self.controller.get_positions(), before)
        self.driver.update(self.stage, .1)
        self.assertAlmostEqual(self.controller.get_positions()[1], before[1] + .1, places=4)

    def test_rcms_bigcarvel_moves_full_model_left_and_bigcarstop_stops_it(self):
        before = self.controller.get_positions()
        self.command("ctr_sw_set", 1)
        self.command("bigcarvel", [2.0, 2.0, 2.0])
        for _ in range(10):
            self.driver.update(self.stage, .02)
        after = self.controller.get_positions()
        self.assertAlmostEqual(after[0], before[0] - .4, places=4)
        state = self.driver.engine.snapshot()["content"]
        self.assertAlmostEqual(state["bigcar1_vel_fb"], 2, places=3)
        self.assertEqual(state["bigcar_left_state"], 1)
        self.command("bigcarstop", None)
        stopped = self.controller.get_positions()
        for _ in range(5):
            self.driver.update(self.stage, .02)
        self.assertEqual(stopped, self.controller.get_positions())
        self.assertEqual(stopped[1:], before[1:])

    def test_latest_combined_move_updates_all_three_visible_axis_transforms(self):
        before = self.controller.get_positions()
        self.command("ctr_sw_set", 1)
        self.command("move", [.5, .25, 10.0, 20.0, .4, .4])
        for _ in range(10):
            self.driver.update(self.stage, .02)
        after = self.controller.get_positions()
        for value, expected in zip(after, (before[0] - .08, before[1] + .1, before[2] + .05)):
            self.assertAlmostEqual(value, expected, places=4)
        state = self.driver.engine.snapshot()["content"]
        self.assertEqual(state["bigcar_left_state"], 1)
        self.assertEqual(state["smallcar_front_state"], 1)
        self.assertEqual(state["lifting_up_state"], 1)
        self.command("estop", None)
        stopped = self.controller.get_positions()
        for _ in range(5):
            self.driver.update(self.stage, .02)
        self.assertEqual(stopped, self.controller.get_positions())
        self.assertFalse(self.driver.engine.armed)


if __name__ == "__main__":
    unittest.main()
