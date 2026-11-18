"""Real-model regressions; run with the bundled USD Python runtime."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "omniverse/scripts"))


class LiveAlignmentUsdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from pxr import Gf, Usd, UsdGeom
        except ImportError:
            raise unittest.SkipTest("Requires the Kit USD Python runtime")
        import rtg_live_control as live
        import rtg_twin_state_receiver as receiver
        cls.Gf, cls.Usd, cls.UsdGeom = Gf, Usd, UsdGeom
        cls.live, cls.receiver = live, receiver

    def setUp(self):
        self.stage = self.Usd.Stage.Open(str(ROOT / "omniverse/scenes/smart_port.usda"))
        self.source_before = self.stage.GetRootLayer().ExportToString()
        self.stage.SetEditTarget(self.stage.GetSessionLayer())
        self.controller = self.live.RTGController(self.stage)

    def tearDown(self):
        self.assertEqual(self.stage.GetRootLayer().ExportToString(), self.source_before)

    def feet(self):
        cache = self.UsdGeom.BBoxCache(self.Usd.TimeCode.Default(), [self.UsdGeom.Tokens.default_])
        hoist = self.stage.GetPrimAtPath(self.live.HOIST_PATH)
        boxes = [cache.ComputeWorldBound(prim).ComputeAlignedBox()
                 for prim in self.Usd.PrimRange(hoist)
                 if prim.IsA(self.UsdGeom.Boundable)
                 and "RTG_SPREAD_REDRAW_red_corner_lock_foot_" in str(prim.GetPath())]
        self.assertEqual(len(boxes), 4)
        return self.Gf.Vec3d(
            sum(box.GetMidpoint()[0] for box in boxes) / 4,
            sum(box.GetMidpoint()[1] for box in boxes) / 4,
            sum(box.GetMin()[2] for box in boxes) / 4,
        )

    def test_twenty_and_forty_foot_lock_feet_align_with_every_plc_row(self):
        from build_live_containers import row_center_x_m, container_center_y_for_bay
        c = self.controller
        for size, bay in ((20, 19), (40, 20)):
            c.set_spreader_size(size)
            c.set_gantry_bay("4C", bay, container_size_ft=size,
                             center_offset_y=self.receiver._spreader_center_offset_y(size))
            for index, (trolley, _) in enumerate(self.receiver.TROLLEY_CARRIED_BOX_X_CALIBRATION):
                c.set_trolley(trolley)
                feet = self.feet()
                expected_x = self.receiver.TRUCK_OPERATING_LANE_CENTER_X_M if index < 2 else row_center_x_m(8 - index)
                self.assertAlmostEqual(feet[0], expected_x, delta=0.00005)
                self.assertAlmostEqual(feet[1], container_center_y_for_bay("4C", bay, size), delta=0.05)
                if index >= 2:  # The truck stop intentionally has a 0.48..1.0 m plateau.
                    self.assertAlmostEqual(c.get_positions()[1], trolley, delta=0.001)

    def test_fourth_tier_contact_and_carried_box_top_stay_together(self):
        c = self.controller
        c.set_spreader_size(40)
        c.set_gantry_bay("4C", 28, container_size_ft=40)
        c.set_trolley(9.5)
        c.set_hoist(10.4)
        feet = self.feet()
        visual_height = self.receiver.REFERENCE_CONTAINER_VISUAL_HEIGHT_Z_M
        expected_top = 0.12 + 3 * (visual_height + 0.035) + visual_height
        self.assertAlmostEqual(feet[2], expected_top, delta=0.00001)
        carried = self.receiver.OmniverseRtgTwinDriver._hoisted_container_world_center(
            self.stage, visual_height, trolley_m=9.5,
        )
        self.assertAlmostEqual(carried[0], feet[0], delta=0.00005)
        self.assertAlmostEqual(carried[1], feet[1], delta=0.00001)
        self.assertAlmostEqual(carried[2] + visual_height * 0.5, feet[2], delta=0.00001)

    def test_rebinding_the_controller_does_not_accumulate_lateral_correction(self):
        c = self.controller
        c.set_gantry_bay("4C", 28, container_size_ft=40)
        c.set_trolley(9.5)
        original = self.feet()
        for _ in range(3):
            c = self.live.RTGController(self.stage)
            c.set_gantry_bay("4C", 28, container_size_ft=40)
            c.set_trolley(9.5)
            self.assertLess((self.feet() - original).GetLength(), 0.00001)

    def test_corrected_task_maps_refresh_visible_and_hidden_geometry_including_empty_bays(self):
        stage = self.Usd.Stage.CreateInMemory()
        stage.SetEditTarget(stage.GetSessionLayer())
        with tempfile.TemporaryDirectory() as temp:
            driver = self.receiver.OmniverseRtgTwinDriver(Path(temp) / "yard.json", inventory_autosave=False)
            task = {"taskId": "map-correction", "workType": "LOAD", "endPos": "4C2844",
                    "containerSize": 40, "containerHeight": 2.6, "bayMap": "011500"}
            driver._inventory.reconcile_task_snapshot(task)
            driver._sync_inventory_scene(stage)
            persistent = driver._inventory_stack_path("4C/028")
            self.assertTrue(stage.GetPrimAtPath(persistent + "/R04_T05_Container"))
            corrected = driver._prepare_inventory_task(stage, dict(task, bayMap="011300"))
            self.assertTrue(driver._sync_task_stack(stage, corrected))
            driver._hide_inventory_bay_for_task(stage, corrected)
            self.assertFalse(stage.GetPrimAtPath(self.receiver.LIVE_TASK_STACK_PATH + "/R04_T04_Container"))
            self.assertEqual(stage.GetPrimAtPath(persistent).GetAttribute("visibility").Get(), "invisible")
            driver._restore_hidden_inventory(stage)
            self.assertTrue(stage.GetPrimAtPath(persistent + "/R04_T03_Container"))
            self.assertFalse(stage.GetPrimAtPath(persistent + "/R04_T04_Container"))
            empty = driver._prepare_inventory_task(stage, dict(task, bayMap="000000"))
            self.assertTrue(driver._sync_task_stack(stage, empty))
            driver._hide_inventory_bay_for_task(stage, empty)
            self.assertEqual(stage.GetPrimAtPath(persistent).GetAttribute("visibility").Get(), "invisible")
            driver._restore_hidden_inventory(stage)
            self.assertFalse(stage.GetPrimAtPath(persistent))

    def test_task_map_hides_cached_source_even_when_root_is_the_edit_target(self):
        r = self.receiver
        stage = self.Usd.Stage.CreateInMemory()
        driver = r.OmniverseRtgTwinDriver(inventory_autosave=False)
        from yard_inventory import YardInventory

        driver._inventory = YardInventory.empty()
        task = {"taskId": "outbound", "workType": "UNLOAD", "startPos": "4C0712",
                "containerSize": 20, "bayMap": "200000"}
        driver._inventory.reconcile_task_snapshot(task)
        driver._sync_inventory_scene(stage)
        persistent = driver._inventory_stack_path("4C/007")
        before = stage.GetRootLayer().ExportToString()
        cached_source = stage.GetPrimAtPath(persistent + "/R01_T02_Container")
        with self.Usd.EditContext(stage, stage.GetSessionLayer()):
            # Inventory can have an explicit stronger visibility opinion.
            self.UsdGeom.Imageable(stage.GetPrimAtPath(persistent)).CreateVisibilityAttr().Set("inherited")

        prepared = driver._prepare_inventory_task(stage, task)
        self.assertTrue(driver._sync_task_stack(stage, prepared))
        driver._hide_inventory_bay_for_task(stage, prepared)
        source_path = driver._task_source_container_path(prepared)
        source = stage.GetPrimAtPath(source_path)
        driver._last_task_source_path = source_path
        original = driver._get_xform_translate(source)
        with self.Usd.EditContext(stage, stage.GetSessionLayer()):
            lifted = original + self.Gf.Vec3d(0, 0, 4)
            driver._move_task_source_to_local_target(stage, source, source.GetParent(), lifted)
        self.assertEqual(driver._get_xform_translate(source), lifted)
        self.assertEqual(self.UsdGeom.Imageable(cached_source).ComputeVisibility(), "invisible")
        self.assertEqual(stage.GetRootLayer().ExportToString(), before)

    def test_live_placement_survives_the_task_message_disappearing(self):
        r = self.receiver
        from yard_inventory import YardInventory

        for work, target, resulting_map in (("UNLOAD", None, "100000"),
                                            ("SWITCH", "4C0761", "100101")):
            with self.subTest(work=work):
                stage = self.Usd.Stage.CreateInMemory()
                driver = r.OmniverseRtgTwinDriver(inventory_autosave=False)
                driver._inventory = YardInventory.empty()
                driver._sync_inventory_scene(stage)
                task = {"taskId": "departing-task", "workType": work, "startPos": "4C0712",
                        "endPos": target, "containerSize": 20, "bayMap": "200000"}
                prepared = driver._prepare_inventory_task(stage, task)
                self.assertTrue(driver._sync_task_stack(stage, prepared))
                if work == "SWITCH":
                    extra_box = r.LIVE_TASK_STACK_PATH + "/R04_T01_Container"
                    self.assertFalse(stage.GetPrimAtPath(extra_box))
                    prepared = driver._prepare_inventory_task(stage, dict(task, bayMap="200100"))
                    self.assertTrue(driver._sync_task_stack(stage, prepared))
                    self.assertTrue(stage.GetPrimAtPath(extra_box))
                driver._hide_inventory_bay_for_task(stage, prepared)
                driver._previous_live_task = prepared
                machine = driver._build_operation_state_machine(prepared, driver._task_vehicle_key(prepared))
                driver._operation_state_machine = machine
                machine.state = r.OperationState.HOIST_TO_PLACE
                pose = {"lockState": 0, "unlockState": 1, "currentBay": 7,
                        "trolleyMeter": machine.target_trolley_m,
                        "hoistMeter": machine.placement_load_bottom_hoist_m}
                driver._settle_departed_live_task(stage, {}, dict(pose, plcStale=True))
                driver._settle_departed_live_task(stage, {}, dict(pose, hoistMeter=18.0))
                self.assertFalse(driver._inventory.task_is_settled(prepared))
                driver._settle_departed_live_task(stage, {}, pose)
                self.assertTrue(driver._inventory.task_is_settled(prepared))
                persistent = driver._inventory_stack_path("4C/007")
                stack = stage.GetPrimAtPath(persistent)
                self.assertEqual(stack.GetAttribute("inventory:bayMap").Get(), resulting_map)
                self.assertFalse(stage.GetPrimAtPath(persistent + "/R01_T02_Container"))
                self.assertEqual(bool(stage.GetPrimAtPath(persistent + "/R06_T01_Container")), bool(target))
                self.assertFalse(stage.GetPrimAtPath(r.LIVE_TASK_STACK_PATH))

    def test_runinfo_anchor_follows_motion_without_repeating_full_model_bounds(self):
        driver = self.receiver.OmniverseRtgTwinDriver(inventory_autosave=False)
        root = self.UsdGeom.Xform.Define(self.stage, "/World/LiveHUD/TestRuninfo").GetPrim()
        with patch.object(self.UsdGeom, "BBoxCache", wraps=self.UsdGeom.BBoxCache) as bounds:
            with patch.object(self.receiver, "camera_facing_rotate_xyz", return_value=(0, 0, 0)):
                driver._sync_floating_runinfo_pose(self.stage, root)
                original = root.GetAttribute("xformOp:translate").Get()
                self.controller.set_gantry(self.controller.get_positions()[0] + 2)
                driver._sync_floating_runinfo_pose(self.stage, root)
                moved = root.GetAttribute("xformOp:translate").Get()
        self.assertEqual(bounds.call_count, 1)
        self.assertAlmostEqual(moved[1] - original[1], 2.0, delta=0.00001)

    def test_truck_loads_fit_the_imported_trailer_and_keep_the_calibrated_stop(self):
        r = self.receiver
        from build_live_containers import container_center_y_for_bay
        driver = r.OmniverseRtgTwinDriver(inventory_autosave=False)
        for size, bay in ((20, 19), (40, 20)):
            for work in (r.WORK_TYPE_LOAD, r.WORK_TYPE_UNLOAD):
                with self.subTest(size=size, work=work):
                    driver._create_task_vehicle(self.stage, "4C", bay, size, "truck-test", work, 2.6)
                    driver._update_task_vehicle_pose(self.stage, "4C", bay, size, work, r.TASK_STATE_READY_TO_PICK)
                    cache = self.UsdGeom.BBoxCache(self.Usd.TimeCode.Default(), [self.UsdGeom.Tokens.default_])
                    load = self.stage.GetPrimAtPath(r.LIVE_TASK_TRUCK_PATH + "/LoadedContainer")
                    box = cache.ComputeWorldBound(load).ComputeAlignedBox()
                    visual = self.stage.GetPrimAtPath(r.LIVE_TASK_TRUCK_VISUAL_PATH)
                    rails = [cache.ComputeWorldBound(prim).ComputeAlignedBox()
                             for prim in self.Usd.PrimRange(visual)
                             if prim.IsA(self.UsdGeom.Boundable) and "trailer_side_rail" in str(prim.GetPath())]
                    self.assertEqual(len(rails), 2)
                    front = min(bound.GetMin()[1] for bound in rails)
                    rear = max(bound.GetMax()[1] for bound in rails)
                    self.assertGreaterEqual(box.GetMin()[1], front)
                    self.assertLessEqual(box.GetMax()[1], rear)
                    if size == 40:
                        self.assertAlmostEqual(box.GetMidpoint()[1], (front + rear) * 0.5, delta=0.00001)
                    else:
                        self.assertAlmostEqual(box.GetMin()[1] - front, r.TASK_TRUCK_FRONT_CONTAINER_CLEARANCE_Y_M, delta=0.00001)
                    expected = r._task_truck_stop_y_for_box_center(container_center_y_for_bay("4C", bay, size), size, work)
                    self.assertAlmostEqual(box.GetMidpoint()[1], expected, delta=0.00001)
                    cabs = [cache.ComputeWorldBound(prim).ComputeAlignedBox()
                            for prim in self.Usd.PrimRange(visual)
                            if prim.IsA(self.UsdGeom.Boundable) and "white_cab_body" in str(prim.GetPath())]
                    self.assertEqual(len(cabs), 1)
                    self.assertGreater(box.GetMin()[1] - cabs[0].GetMax()[1], 0.05)
                    # The corrected body follows the root during departure.
                    driver._task_flow_state_started_at = 100.0
                    with patch.object(r.time, "time", return_value=101.0):
                        driver._update_task_vehicle_pose(self.stage, "4C", bay, size, work, r.TASK_STATE_TRUCK_EXITING)
                    moved = self.UsdGeom.BBoxCache(self.Usd.TimeCode.Default(), [self.UsdGeom.Tokens.default_])
                    moved_box = moved.ComputeWorldBound(load).ComputeAlignedBox()
                    rail_prim = next(prim for prim in self.Usd.PrimRange(visual)
                                     if prim.IsA(self.UsdGeom.Boundable) and "trailer_side_rail" in str(prim.GetPath()))
                    moved_front = moved.ComputeWorldBound(rail_prim).ComputeAlignedBox().GetMin()[1]
                    self.assertAlmostEqual(moved_box.GetMin()[1] - moved_front, box.GetMin()[1] - front, delta=0.00001)

    def test_task_map_replaces_saved_inventory_definitions_in_weaker_layers(self):
        from yard_inventory import YardInventory
        r = self.receiver
        driver = r.OmniverseRtgTwinDriver(inventory_autosave=False)
        driver._inventory = YardInventory.empty()
        persistent = driver._inventory_stack_path("3C/002")
        old_box = persistent + "/R02_T04_Container"
        self.assertTrue(self.stage.GetPrimAtPath(old_box).IsActive())
        driver._sync_inventory_scene(self.stage)
        self.assertFalse(self.stage.GetPrimAtPath(persistent).IsActive())
        task = {"taskId": "weak-layer-map", "workType": "LOAD", "endPos": "3C0212",
                "containerSize": 40, "bayMap": "100000"}
        for pattern, expected in (("100000", {"R01_T01_Container"}),
                                  ("200000", {"R01_T01_Container", "R01_T02_Container"}),
                                  ("000000", set())):
            prepared = driver._prepare_inventory_task(self.stage, dict(task, bayMap=pattern))
            self.assertTrue(driver._sync_task_stack(self.stage, prepared))
            driver._hide_inventory_bay_for_task(self.stage, prepared)
            active_task = self.stage.GetPrimAtPath(r.LIVE_TASK_STACK_PATH)
            task_boxes = {str(child.GetName()) for child in active_task.GetChildren()
                          if str(child.GetName()).endswith("_Container")}
            self.assertEqual(task_boxes, expected)
            driver._restore_hidden_inventory(self.stage)
            inventory_root = self.stage.GetPrimAtPath(persistent)
            boxes = {str(child.GetName()) for child in inventory_root.GetChildren()
                     if str(child.GetName()).endswith("_Container")} if inventory_root.IsActive() else set()
            self.assertEqual(boxes, expected)
            old = self.stage.GetPrimAtPath(old_box)
            self.assertFalse(old and old.IsActive())

    def test_saved_task_meshes_and_hidden_boxes_are_replaced_in_the_session_layer(self):
        r = self.receiver
        stage = self.Usd.Stage.CreateInMemory()
        path = r.LIVE_TASK_STACK_PATH
        root = self.UsdGeom.Xform.Define(stage, path).GetPrim()
        first = self.UsdGeom.Cube.Define(stage, path + "/R01_T01_Container").GetPrim()
        self.UsdGeom.Imageable(first).MakeInvisible()
        stale = self.UsdGeom.Cube.Define(stage, path + "/R06_T06_Container").GetPrim()
        self.UsdGeom.Cube.Define(stage, path + "/R06_T06_Container_WeatheredTop")
        self.UsdGeom.Imageable(root).MakeInvisible()
        original_layer = stage.GetRootLayer().ExportToString()
        stage.SetEditTarget(stage.GetSessionLayer())
        driver = r.OmniverseRtgTwinDriver(inventory_autosave=False)
        task = {"taskId": "saved-task", "workType": "LOAD", "endPos": "3C0212",
                "containerSize": 40, "bayMap": "100000"}
        self.assertTrue(driver._sync_task_stack(stage, task))
        stale_path = path + "/R06_T06_Container"
        self.assertFalse(stage.GetPrimAtPath(stale_path).IsActive())
        self.assertEqual(self.UsdGeom.Imageable(stage.GetPrimAtPath(path + "/R01_T01_Container")).ComputeVisibility(), "inherited")
        self.assertFalse(stage.GetPrimAtPath(path + "/R06_T06_Container_WeatheredTop").IsActive())
        driver._discard_task_visuals(stage, remove_stack=True)
        self.assertFalse(stage.GetPrimAtPath(path).IsActive())
        self.assertTrue(driver._sync_task_stack(stage, dict(task, bayMap="200000")))
        self.assertTrue(stage.GetPrimAtPath(path + "/R01_T02_Container").IsActive())
        self.assertFalse(stage.GetPrimAtPath(stale_path).IsActive())
        self.assertEqual(stage.GetRootLayer().ExportToString(), original_layer)


if __name__ == "__main__":
    unittest.main()
