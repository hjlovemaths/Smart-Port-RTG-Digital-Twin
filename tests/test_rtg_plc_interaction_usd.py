"""Measured contact/locking/continuous collision tests on the actual RTG asset.

The lightweight backend here isolates the geometry and command interlocks.
Real PhysX dynamics are verified separately by plc_physx_smoke.run in Kit.
"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "omniverse/scripts"))


class MemoryBackend:
    def __init__(self, stage):
        self.bodies, self.joint = {}, None
        self.steps = 0

    def start(self): pass
    def check(self): pass
    def close(self): pass
    def body(self, name, size, pose, kinematic=True):
        self.bodies[name] = pose
        return name
    def set_pose(self, path, pose): self.bodies[path] = pose
    def set_kinematic(self, path, enabled): pass
    def remove_body(self, path): self.bodies.pop(path, None)
    def lock(self, anchor, cargo, anchor_pose, cargo_pose):
        self.joint = anchor, cargo, cargo_pose * anchor_pose.GetInverse()
    def unlock(self): self.joint = None
    def step(self, dt):
        self.steps += 1
        if self.joint:
            anchor, cargo, relative = self.joint
            self.bodies[cargo] = relative * self.bodies[anchor]
    def pose(self, path): return self.bodies[path]


class PlcInteractionUsdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from pxr import Gf, Usd, UsdGeom, Sdf
        except ImportError:
            raise unittest.SkipTest("Requires Kit USD Python")
        import rtg_live_control as live
        from rtg_plc_interaction import PlcInteraction
        from rtg_plc_protocol import PlcEngine, PlcConfig
        cls.Gf, cls.Usd, cls.UsdGeom, cls.Sdf = Gf, Usd, UsdGeom, Sdf
        cls.live, cls.Interaction, cls.Engine, cls.Config = live, PlcInteraction, PlcEngine, PlcConfig

    def setUp(self):
        self.stage = self.Usd.Stage.Open(str(ROOT / "omniverse/scenes/smart_port.usda"))
        self.original = self.stage.GetRootLayer().ExportToString()
        self.stage.SetEditTarget(self.stage.GetSessionLayer())
        self.controller = self.live.RTGController(self.stage)
        self.controller.set_trolley_safe(.48)
        self.controller.set_hoist_safe(10)
        self.interaction = self.Interaction(self.stage, self.controller, MemoryBackend)
        self.interaction.set_enabled(True)
        points = self.interaction._foot_points()
        centre = sum(points, self.Gf.Vec3d(0)) / 4
        bounds = tuple(tuple(fn(p[i] for p in points) for i in range(3)) for fn in (min, max))
        self.size = (bounds[1][0] - bounds[0][0] + .316, bounds[1][1] - bounds[0][1] + .34, .904353)
        self.path = "/World/LiveContainers/PlcPhysicsTestBox"
        self.box(self.path, self.Gf.Vec3d(centre[0], centre[1], centre[2] - self.size[2] / 2), self.size)
        self.interaction.tick(0)
        self.engine = self.Engine(self.controller, (self.live.GANTRY_SOFT_LIMITS, self.live.TROLLEY_SOFT_LIMITS,
                                  self.live.HOIST_SOFT_LIMITS), self.Config(watchdog_s=5), interaction=self.interaction)
        self.command("ctr_sw_set", 1)

    def box(self, path, centre, size):
        cube = self.UsdGeom.Cube.Define(self.stage, path)
        cube.CreateSizeAttr(1)
        cube.AddTranslateOp().Set(centre)
        cube.AddScaleOp().Set(self.Gf.Vec3d(*size))
        cube.GetPrim().CreateAttribute("live:row", self.Sdf.ValueTypeNames.Int).Set(0)
        cube.GetPrim().CreateAttribute("live:containerSizeFt", self.Sdf.ValueTypeNames.Int).Set(40)

    def tearDown(self):
        self.interaction.close()
        self.assertEqual(self.stage.GetRootLayer().ExportToString(), self.original)

    def command(self, cmd, value):
        return self.engine.apply({"match": "CTR", "cmd": cmd, "value": value})

    def detailed_box(self):
        from build_live_containers import create_container_details
        record = self.interaction.records[self.path]
        create_container_details(self.stage, self.path, record["pose"].ExtractTranslation(),
                                 self.Gf.Vec3f(*self.size))
        self.interaction.tick(0)
        source = self.stage.GetPrimAtPath(self.path)
        return [source] + [p for p in source.GetParent().GetChildren()
                           if p.GetName().startswith(source.GetName() + "_")]

    def visual_worlds(self, parts):
        cache = self.UsdGeom.XformCache()
        return {str(p.GetPath()): cache.GetLocalToWorldTransform(p) for p in parts}

    def assert_visuals_follow(self, parts, initial):
        actual = self.visual_worlds(parts)
        delta = initial[self.path].GetInverse() * actual[self.path]
        for path, baseline in initial.items():
            expected = baseline * delta
            for row in range(4):
                for col in range(4):
                    self.assertAlmostEqual(actual[path][row][col], expected[row][col], places=6, msg=path)

    def test_all_generated_details_follow_lift_trim_and_released_physics_pose(self):
        parts = self.detailed_box()
        self.assertGreater(len(parts), 15)
        initial = self.visual_worlds(parts)
        neighbour = self.UsdGeom.Cube.Define(self.stage, "/World/LiveContainers/UnrelatedBox")
        neighbour.AddTranslateOp().Set(self.Gf.Vec3d(50, 50, 5))
        untouched = self.visual_worlds([neighbour.GetPrim()])
        self.command("tool_sw_set", 1)
        self.command("lock", 1)
        self.command("vel", [0, .5])
        self.engine.tick(.2)
        self.command("vel", [0, 0])
        self.command("tool_action", 11)
        self.command("tool_move", 11)
        self.engine.tick(.1)
        self.command("tool_action", 10)
        self.command("tool_move", 10)
        self.engine.tick(.02)
        self.assert_visuals_follow(parts, initial)
        self.assertEqual(untouched, self.visual_worlds([neighbour.GetPrim()]))
        record = self.interaction.records[self.path]
        bottom = self.interaction._bounds(record)[0][2]
        centre = record["pose"].ExtractTranslation()
        self.box("/World/LiveContainers/LandingSupport", self.Gf.Vec3d(centre[0], centre[1], bottom - self.size[2]/2),
                 (self.size[0]+.5, self.size[1]+.5, self.size[2]))
        self.interaction.tick(0)
        self.command("lock", 0)
        # A post-release PhysX result can translate and tilt the whole box.
        self.interaction.backend.set_pose(record["body"], record["pose"] *
            self.Gf.Matrix4d().SetRotate(self.Gf.Rotation(self.Gf.Vec3d(0, 1, 0), 2)))
        for _ in range(4):
            self.interaction.tick(0)
            self.assert_visuals_follow(parts, initial)

    def test_live_fix_repairs_details_left_behind_by_old_body_only_writer(self):
        parts = self.detailed_box()
        initial = self.visual_worlds(parts)
        self.command("tool_sw_set", 1)
        self.command("lock", 1)
        record = self.interaction.records[self.path]
        record["pose"] = record["pose"] * self.Gf.Matrix4d().SetTranslate(self.Gf.Vec3d(0, 0, .5))
        self.interaction.writing = True
        try:
            self.interaction._write_visual_pose(parts[0], record["shape"] * record["pose"], self.UsdGeom.XformCache())
        finally:
            self.interaction.writing = False
        record.pop("visuals")
        record.pop("visual_source_world")
        self.interaction._write_pose(record)
        self.assert_visuals_follow(parts, initial)

    def test_hidden_proxy_and_imported_model_move_as_one_container(self):
        source = self.stage.GetPrimAtPath(self.path)
        model = self.UsdGeom.Xform.Define(self.stage, self.path + "_ImportedModel")
        centre = self.interaction.records[self.path]["pose"].ExtractTranslation()
        model.AddTranslateOp().Set(centre)
        model.AddRotateXOp().Set(-90)
        model.AddScaleOp().Set(self.Gf.Vec3f(.2, .3, .4))
        self.UsdGeom.Cube.Define(self.stage, self.path + "_ImportedModel/Mesh")
        self.UsdGeom.Imageable(source).CreateVisibilityAttr().Set("invisible")
        self.interaction.tick(0)
        self.assertIn(self.path, self.interaction.records)
        parts = [source, model.GetPrim(), self.stage.GetPrimAtPath(self.path + "_ImportedModel/Mesh")]
        initial = self.visual_worlds(parts)
        self.command("tool_sw_set", 1)
        self.command("lock", 1)
        self.command("vel", [0, .5])
        self.engine.tick(.2)
        self.assert_visuals_follow(parts, initial)

    def test_rebuilt_inventory_resets_all_released_detail_pose_overrides(self):
        from build_live_containers import create_container_details
        parts = self.detailed_box()
        self.command("tool_sw_set", 1)
        self.command("lock", 1)
        record = self.interaction.records[self.path]
        bottom = self.interaction._bounds(record)[0][2]
        centre = record["pose"].ExtractTranslation()
        self.box("/World/LiveContainers/LandingSupport", self.Gf.Vec3d(centre[0], centre[1], bottom - self.size[2]/2), self.size)
        self.interaction.tick(0)
        self.command("lock", 0)
        replacement = centre + self.Gf.Vec3d(5, 0, 0)
        self.stage.RemovePrim(self.path)
        self.box(self.path, replacement, self.size)
        create_container_details(self.stage, self.path, replacement, self.Gf.Vec3f(*self.size))
        self.interaction.tick(0)
        self.assertNotIn(self.path, self.interaction.dynamic)
        for part in parts[1:]:
            ops = self.UsdGeom.Xformable(part).GetOrderedXformOps()
            self.assertNotIn("xformOp:transform:plcCargoPose", [op.GetOpName() for op in ops])
        self.assertLess((self.interaction.records[self.path]["pose"].ExtractTranslation() - replacement).GetLength(), 1e-7)

    def test_contact_rejects_pump_off_and_misalignment_then_locks_without_pose_snap(self):
        self.assertEqual(self.engine.snapshot()["content"]["drop_state"], 1)
        with self.assertRaisesRegex(ValueError, "pump"):
            self.command("lock", 1)
        self.command("tool_sw_set", 1)
        self.controller.set_spreader_trim(0, .1)
        with self.assertRaisesRegex(ValueError, "misaligned"):
            self.command("lock", 1)
        self.controller.set_spreader_trim(0, 0)
        before = self.interaction.records[self.path]["pose"]
        result = self.command("lock", 1)
        self.assertEqual(result["effect"], "digital_scene")
        self.assertEqual(self.interaction.records[self.path]["pose"], before)
        self.assertEqual(self.engine.snapshot()["content"]["lock_state"], 1)
        self.assertNotIn("lock_state", self.engine.snapshot()["digital_twin"]["virtual_fields"])

    def test_idle_empty_crane_skips_physics_but_keeps_roof_contact_and_locked_load_steps(self):
        backend = self.interaction.backend
        self.interaction.tick(.03)
        self.assertEqual(backend.steps, 0)
        self.assertEqual(self.engine.snapshot()["content"]["drop_state"], 1)
        self.command("tool_sw_set", 1)
        self.command("lock", 1)
        self.interaction.tick(.03)
        self.assertEqual(backend.steps, 1)

    def test_downward_motion_stops_at_roof_and_upward_motion_can_leave_contact(self):
        start = self.controller.get_positions()[2]
        self.command("vel", [0, -1])
        self.engine.tick(.5)
        after = self.controller.get_positions()[2]
        self.assertAlmostEqual(after, start, places=5)
        self.assertEqual(self.engine.stop_reason, "collision_stop")
        self.command("vel", [0, 1])
        self.engine.tick(.1)
        self.assertGreater(self.controller.get_positions()[2], start)
        self.assertEqual(self.engine.snapshot()["content"]["drop_state"], 0)

    def test_size_mismatch_and_suspended_unlock_rejected_and_load_tracks_trim(self):
        self.command("tool_sw_set", 1)
        self.command("lock", 1)
        with self.assertRaisesRegex(ValueError, "locked container"):
            self.command("tool_size", 1)
        before = self.interaction.records[self.path]["pose"].ExtractTranslation()
        self.command("vel", [0, .5])
        self.engine.tick(.2)
        self.command("vel", [0, 0])
        self.engine.tick(.01)
        after = self.interaction.records[self.path]["pose"].ExtractTranslation()
        self.assertGreater(after[2], before[2])
        visible = self.UsdGeom.XformCache().GetLocalToWorldTransform(self.stage.GetPrimAtPath(self.path)).ExtractTranslation()
        self.assertLess((visible - after).GetLength(), 1e-7)
        with self.assertRaisesRegex(ValueError, "supported"):
            self.command("lock", 0)
        self.command("tool_move", 11)
        self.engine.tick(.1)
        shifted = self.interaction.records[self.path]["pose"].ExtractTranslation()
        self.assertLess(shifted[0], after[0])

    def test_physics_cannot_be_disabled_with_load_and_emergency_stop_keeps_lock(self):
        self.command("tool_sw_set", 1)
        self.command("lock", 1)
        with self.assertRaisesRegex(ValueError, "land and unlock"):
            self.command("sim_physics", 0)
        self.command("estop", None)
        self.engine.tick(.1, connected=False)
        self.assertEqual(self.interaction.carried, self.path)
        self.assertTrue(self.interaction.enabled)
        self.assertEqual(self.engine.snapshot()["content"]["lock_state"], 1)

    def test_wrong_container_size_does_not_close_locks(self):
        self.stage.GetPrimAtPath(self.path).GetAttribute("live:containerSizeFt").Set(20)
        self.interaction.tick(0)
        self.command("tool_sw_set", 1)
        with self.assertRaisesRegex(ValueError, "20/40"):
            self.command("lock", 1)
        self.assertIsNone(self.interaction.carried)
        self.assertEqual(self.engine.snapshot()["content"]["lock_state"], 0)

    def test_task_rebuild_replaces_released_pose_and_collision_proxy(self):
        self.command("tool_sw_set", 1)
        self.command("lock", 1)
        record = self.interaction.records[self.path]
        bottom = self.interaction._bounds(record)[0][2]
        centre = record["pose"].ExtractTranslation()
        self.box("/World/LiveContainers/Support", self.Gf.Vec3d(centre[0], centre[1], bottom - .4521765), self.size)
        self.interaction.tick(.01)
        self.command("lock", 0)
        self.assertIn(self.path, self.interaction.dynamic)
        # A task rewrite at the same prim path must override our previous pose.
        self.stage.RemovePrim(self.path)
        replacement = centre + self.Gf.Vec3d(5, 0, 0)
        self.box(self.path, replacement, self.size)
        self.interaction.tick(0)
        updated = self.interaction.records[self.path]
        self.assertNotIn(self.path, self.interaction.dynamic)
        self.assertLess((updated["pose"].ExtractTranslation() - replacement).GetLength(), 1e-7)
        self.assertLess((self.interaction.backend.pose(updated["body"]).ExtractTranslation() - replacement).GetLength(), 1e-7)

    def test_task_rebuild_of_locked_container_stops_instead_of_grabbing_replacement(self):
        self.command("tool_sw_set", 1)
        self.command("lock", 1)
        self.stage.RemovePrim(self.path)
        with self.assertRaisesRegex(RuntimeError, "rebuilt"):
            self.interaction.tick(.01)

    def test_task_rebuild_of_locked_detail_stops_instead_of_leaving_detached_visual(self):
        parts = self.detailed_box()
        self.command("tool_sw_set", 1)
        self.command("lock", 1)
        self.stage.RemovePrim(str(parts[1].GetPath()))
        with self.assertRaisesRegex(RuntimeError, "rebuilt"):
            self.interaction.tick(.01)

    def test_loaded_box_cannot_cross_other_stack_and_releases_only_on_support(self):
        self.command("tool_sw_set", 1)
        self.command("lock", 1)
        record = self.interaction.records[self.path]
        bottom = self.interaction._bounds(record)[0][2]
        centre = record["pose"].ExtractTranslation()
        support_path = "/World/LiveContainers/PlcSupportBox"
        self.box(support_path, self.Gf.Vec3d(centre[0], centre[1], bottom - .4521765), self.size)
        self.interaction.tick(.01)
        self.assertTrue(self.interaction.support(record))
        self.command("lock", 0)
        self.assertFalse(self.interaction.carried)
        self.assertEqual(self.interaction.last_release["supports"], [support_path])
        self.assertEqual(self.engine.snapshot()["content"]["unlock_state"], 1)
        # Pick it back up; approaching a new adjacent column must stop the load.
        self.command("lock", 1)
        self.command("vel", [0, 1])
        self.engine.tick(.2)
        self.command("vel", [0, 0])
        self.engine.tick(.01)
        centre = self.interaction.records[self.path]["pose"].ExtractTranslation()
        self.box("/World/LiveContainers/PlcSideObstacle", centre + self.Gf.Vec3d(.9, 0, 0), self.size)
        self.interaction.tick(0)
        before = self.controller.get_positions()[1]
        self.command("vel", [1, 0])
        self.engine.tick(.5)
        self.assertLess(self.controller.get_positions()[1] - before, .5)
        self.assertEqual(self.engine.stop_reason, "collision_stop")

    def test_latest_inventory_geometry_is_used_without_reloading_disk_json(self):
        self.command("vel", [0, .5])
        self.engine.tick(.1)
        self.assertIsNone(self.interaction.contact())
        pose = self.interaction.records[self.path]["pose"]
        top = self.interaction._bounds(self.interaction.records[self.path])[1][2]
        self.box("/World/LiveContainers/LatestTaskBox", self.Gf.Vec3d(pose[3][0], pose[3][1], top + .4521765), self.size)
        self.interaction.tick(0)
        self.assertIn("/World/LiveContainers/LatestTaskBox", self.interaction.records)
