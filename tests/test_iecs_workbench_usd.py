"""Run the workbench's workflows on the current six-six ship stage."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "omniverse/scripts"))
sys.path.insert(0, str(ROOT / "omniverse/extensions/smartport.agent_panel"))


class WorkbenchUsdTests(unittest.TestCase):
    def test_transfer_20ft_uses_front_trailer_position_at_both_bays(self):
        try:
            from pxr import Usd, UsdGeom
        except ImportError:
            self.skipTest("Requires the Kit USD runtime")
        from iecs_launch import launch

        stage = Usd.Stage.Open(str(ROOT / "omniverse/scenes/smart_port.usda"))
        demo = launch(stage=stage, port=None, with_ui=False, subscribe=False, db_path=":memory:")
        try:
            order = demo.engine.orders[demo.submit_order("transfer")]
            cargo = demo.engine.containers[order["container"]]
            self.assertEqual(cargo["size_ft"], 20)
            demo.engine.control("speed", 20)
            checked_bays = set()
            for _ in range(2200):
                previous_step = order["step"]
                demo.engine.tick(.1)
                demo.apply_scene()
                if order["step"] != previous_step and order["step"] in (2, 3):
                    bay = "source" if order["step"] == 2 else "target"
                    truck = demo.engine.vehicles[order["vehicle"]]
                    visual = stage.GetPrimAtPath(cargo["visual_prim"])
                    bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"]).ComputeWorldBound(
                        visual).ComputeAlignedBox()
                    self.assertEqual(truck["load"], cargo["id"])
                    self.assertAlmostEqual(truck["position"][1] - bounds.GetMidpoint()[1], 1.275, places=3)
                    self.assertAlmostEqual(bounds.GetMidpoint()[1], order[bay]["point"][1], places=3)
                    self.assertAlmostEqual(bounds.GetMin()[2], .625, places=3)
                    checked_bays.add(bay)
                if order["status"] == "completed":
                    break
            self.assertEqual(checked_bays, {"source", "target"})
            self.assertEqual(order["status"], "completed", order["wait_reason"])
            self.assertEqual(cargo["position"], order["target"]["point"])
        finally:
            demo.close()

    def test_mixed_demo_checks_pause_reset_and_history(self):
        try:
            from pxr import Gf, Usd, UsdGeom
        except ImportError:
            self.skipTest("Requires the Kit USD runtime")
        from iecs_launch import launch
        from rtg_live_control import GANTRY_PATH, gantry_actual_to_usd
        from smartport.agent_panel import sim_workbench
        from smartport.agent_panel.project_interfaces import _iecs
        from smartport.agent_panel.scene_runtime import SceneRuntime

        stage = Usd.Stage.Open(str(ROOT / "omniverse/scenes/smart_port.usda"))
        original = stage.GetRootLayer().ExportToString()
        layers = list(stage.GetSessionLayer().subLayerPaths)
        demo = launch(stage=stage, port=None, with_ui=False, subscribe=False, db_path=":memory:")
        owner=SceneRuntime(ROOT)
        owner.bind(stage)
        try:
            self.assertEqual(demo.size_ft, 20)
            self.assertIn("/StructureSlots/", demo.ship_box)
            self.assertIsNone(demo.server)
            parked_lens = UsdGeom.Mesh.Get(stage, "/World/IECS/TruckMarkers/ITT_002/Lens")
            parked_color = parked_lens.GetDisplayColorAttr().Get()[0]
            self.assertEqual(parked_color[0], parked_color[1])
            self.assertEqual(parked_color[1], parked_color[2])
            # The SIM workbench uses this same STS motion entry point before IECS opens its own layer.
            owner.move_sts({"crane_id": "STS_01", "trolley_m": 2.0})
            manual_sts = owner.owned_sts["STS_01"]
            for _ in range(10):
                manual_sts.tick(.1)
            hoist = UsdGeom.Xformable(stage.GetPrimAtPath(manual_sts.path + "/Gantry/Trolley/Hoist"))
            manual_ops = hoist.GetXformOpOrderAttr().Get()
            self.assertIn("xformOp:transform:stsControl", manual_ops)
            manual_pose = UsdGeom.XformCache().GetLocalToWorldTransform(hoist.GetPrim())
            gantry = stage.GetPrimAtPath(GANTRY_PATH)
            gantry_ops = UsdGeom.Xformable(gantry).GetXformOpOrderAttr().Get()
            demo.process({"action": "speed", "value": 20})
            _iecs(owner,{"action":"mixed"},web=True)
            demo.process({"action": "pause"})
            move = {"action": "move", "axis": "gantry_m", "target_m": 300.}
            with self.assertRaisesRegex(ValueError, "IECS 调度还有未完成任务"):
                sim_workbench.command(owner, move)
            before = demo.engine.time
            demo.engine.tick(.1)
            self.assertEqual(demo.engine.time, before)

            cargo = next(iter(demo.engine.containers.values()))
            prim = stage.GetPrimAtPath(cargo["visual_prim"])
            with Usd.EditContext(stage, demo.layer):
                UsdGeom.Imageable(prim).MakeInvisible()
            with self.assertRaisesRegex(ValueError, "Resume blocked"):
                demo.process({"action": "resume"})
            demo.apply_scene()
            demo.process({"action": "resume"})
            peak_trucks=peak_roads=peak_cranes=0
            unload = next(o for o in demo.engine.orders.values() if o["flow"] == "unload")
            gate_in = next(o for o in demo.engine.orders.values() if o["flow"] == "gate_in")
            gate_out = next(o for o in demo.engine.orders.values() if o["flow"] == "gate_out")
            unload_cargo = demo.engine.containers[unload["container"]]
            checked_front_slot = set()
            gate_wait_verified = False
            gate_out_seen = set()
            running_marker_seen = False
            for _ in range(2200):
                demo.advance(.1)
                if unload["vehicle"] and unload_cargo["owner"] == unload["vehicle"]:
                    truck = demo.engine.vehicles[unload["vehicle"]]
                    checkpoint = None
                    if unload["step"] == 1:
                        checkpoint = "STS release"
                    elif unload["step"] == 2 and 1 < truck["heading"] % 90 < 89:
                        checkpoint = "turn"
                    elif unload["step"] == 3:
                        checkpoint = "RTG arrival"
                    if checkpoint is not None and checkpoint not in checked_front_slot:
                        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"])
                        prim = stage.GetPrimAtPath(demo.vehicle_path(truck["id"]))
                        rail_bounds = Gf.Range3d()
                        for part in Usd.PrimRange(prim, Usd.TraverseInstanceProxies()):
                            if part.IsA(UsdGeom.Boundable) and "trailer_side_rail" in part.GetName():
                                rail_bounds.UnionWith(cache.ComputeRelativeBound(part, prim).ComputeAlignedRange())
                        visual = stage.GetPrimAtPath(unload_cargo["visual_prim"])
                        transforms = UsdGeom.XformCache()
                        truck_transform = transforms.GetLocalToWorldTransform(prim)
                        box_transform = transforms.GetLocalToWorldTransform(visual)
                        # The render asset is centred on its visual root. Its world AABB
                        # midpoint can drift slightly as the asymmetric mesh rotates.
                        local_center = truck_transform.GetInverse().Transform(box_transform.ExtractTranslation())
                        truck_axis = truck_transform.TransformDir(Gf.Vec3d(0, 1, 0))
                        box_axis = box_transform.TransformDir(Gf.Vec3d(0, 1, 0))
                        _, length, height = unload_cargo["visual_size"]
                        gap = (local_center[1] - rail_bounds.GetMin()[1]) * truck_axis.GetLength() - length / 2
                        self.assertAlmostEqual(gap, .08, places=3, msg=checkpoint)
                        self.assertAlmostEqual(local_center[2] - height / 2, rail_bounds.GetMax()[2], places=3, msg=checkpoint)
                        self.assertAlmostEqual(Gf.Dot(truck_axis.GetNormalized(), box_axis.GetNormalized()), 1., places=5)
                        checked_front_slot.add(checkpoint)
                if not gate_wait_verified and gate_in["step"] == 2 and gate_in["wait_reason"] == "等待冲突路线放行":
                    truck = demo.engine.vehicles[gate_in["vehicle"]]
                    cargo = demo.engine.containers[gate_in["container"]]
                    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"])
                    truck_prim = stage.GetPrimAtPath(demo.vehicle_path(truck["id"]))
                    cargo_prim = stage.GetPrimAtPath(cargo["visual_prim"])
                    truck_bounds = cache.ComputeWorldBound(truck_prim).ComputeAlignedBox()
                    cargo_bounds = cache.ComputeWorldBound(cargo_prim).ComputeAlignedBox()
                    self.assertEqual(truck["position"], [134., 388.4, .085])
                    follower = demo.engine.vehicles[gate_out["vehicle"]]
                    follower_prim = stage.GetPrimAtPath(demo.vehicle_path(follower["id"]))
                    follower_bounds = cache.ComputeWorldBound(follower_prim).ComputeAlignedBox()
                    self.assertEqual(follower["status"], "waiting_gate")
                    self.assertEqual(follower["position"], [144., 388.4, .085])
                    self.assertNotEqual(UsdGeom.Imageable(follower_prim).ComputeVisibility(), "invisible")
                    self.assertLess(truck_bounds.GetMax()[0], follower_bounds.GetMin()[0])
                    self.assertLess(follower_bounds.GetMax()[0], 150)
                    marker_cache = UsdGeom.XformCache()
                    for queued in (truck, follower):
                        marker_path = "/World/IECS/TruckMarkers/" + queued["id"].replace("-", "_")
                        marker = stage.GetPrimAtPath(marker_path)
                        self.assertNotEqual(UsdGeom.Imageable(marker).ComputeVisibility(), "invisible")
                        position = marker_cache.GetLocalToWorldTransform(marker).ExtractTranslation()
                        self.assertAlmostEqual(position[0], queued["position"][0])
                        self.assertAlmostEqual(position[1], queued["position"][1])
                        self.assertAlmostEqual(position[2], queued["position"][2] - .14)
                        self.assertEqual({p.GetName() for p in marker.GetChildren()}, {"Mount", "Lens"})
                        lens = UsdGeom.Mesh.Get(stage, marker_path + "/Lens")
                        with Usd.EditContext(stage, demo.layer):
                            demo.update_truck_marker(queued, False, 0.)
                            dim = lens.GetDisplayColorAttr().Get()[0][0]
                            opacity = lens.GetDisplayOpacityAttr().Get()[0]
                            points = lens.GetPointsAttr().Get()
                            demo.update_truck_marker(queued, False, .9)
                        color = lens.GetDisplayColorAttr().Get()[0]
                        self.assertLess(dim, color[0])
                        self.assertGreater(color[0], color[1])
                        self.assertGreater(color[1], color[2])
                        self.assertEqual(lens.GetDisplayOpacityAttr().Get()[0], opacity)
                        self.assertTrue(0 < opacity < 1)
                        self.assertEqual(lens.GetPointsAttr().Get(), points)
                        self.assertGreater(min(p[1] for p in points), -2.415)
                    self.assertLess(truck_bounds.GetMax()[0], 150)
                    self.assertEqual(truck["heading"], cargo["heading"])
                    self.assertGreater(cargo_bounds.GetSize()[0], cargo_bounds.GetSize()[1])
                    self.assertAlmostEqual(truck["position"][0] - cargo_bounds.GetMidpoint()[0], 1.275, places=3)
                    self.assertAlmostEqual(cargo_bounds.GetMidpoint()[1], truck["position"][1], places=3)
                    gate_wait_verified = True
                if gate_out["vehicle"]:
                    truck = demo.engine.vehicles[gate_out["vehicle"]]
                    truck_path = demo.vehicle_path(truck["id"])
                    roof = stage.GetPrimAtPath(truck_path + "/RoofNumber")
                    visible = UsdGeom.Imageable(roof).ComputeVisibility() != "invisible"
                    if not running_marker_seen and truck["status"] == "working" and not gate_out["wait_reason"]:
                        lens_path = "/World/IECS/TruckMarkers/" + truck["id"].replace("-", "_") + "/Lens"
                        color = UsdGeom.Mesh.Get(stage, lens_path).GetDisplayColorAttr().Get()[0]
                        self.assertGreater(color[1], color[2])
                        self.assertGreater(color[2], color[0])
                        running_marker_seen = True
                    if truck["status"] == "waiting_gate" and visible:
                        self.assertEqual(roof.GetAttribute("vehicle:number").Get(), "E04")
                        self.assertEqual(truck["position"], [144., 388.4, .085])
                        gate_out_seen.add("entry")
                    if gate_out["step"] == 1 and visible:
                        self.assertEqual(truck["position"], gate_out["source"]["stop"])
                        gate_out_seen.add("pickup")
                peak_trucks=max(peak_trucks,sum(o["status"]=="running" for o in demo.engine.orders.values()))
                peak_roads=max(peak_roads,len(demo.engine.road_claims))
                peak_cranes=max(peak_cranes,len(demo.engine.equipment))
                if not demo.mixed_pending and all(o["status"] == "completed" for o in demo.engine.orders.values()):
                    break
            self.assertEqual([o["flow"] for o in demo.engine.orders.values()],
                             ["unload", "load", "gate_in", "gate_out", "transfer"])
            self.assertTrue(all(o["status"] == "completed" for o in demo.engine.orders.values()),
                            [(o['flow'],o['step'],o['wait_reason']) for o in demo.engine.orders.values()])
            self.assertEqual(checked_front_slot, {"STS release", "turn", "RTG arrival"})
            self.assertAlmostEqual(demo.pickups[unload["id"]].place[1], unload["source"]["point"][1], places=4)
            self.assertAlmostEqual(demo.yard_drops[unload["id"]].pick[1], unload["target"]["point"][1], places=4)
            self.assertTrue(gate_wait_verified)
            self.assertTrue(running_marker_seen)
            self.assertEqual(gate_out_seen, {"entry", "pickup"})
            outbound_trucks = [truck for truck in demo.engine.vehicles.values()
                               if truck["id"].startswith("EXT-")]
            self.assertEqual(len(outbound_trucks), 2)
            for truck in outbound_trucks:
                self.assertEqual(truck["status"], "departed")
                prim = stage.GetPrimAtPath(demo.vehicle_path(truck["id"]))
                roof = stage.GetPrimAtPath(str(prim.GetPath()) + "/RoofNumber")
                self.assertEqual(roof.GetAttribute("vehicle:number").Get(), truck["number"])
                self.assertEqual(UsdGeom.Imageable(roof).ComputeVisibility(), "invisible")
                marker = stage.GetPrimAtPath("/World/IECS/TruckMarkers/" + truck["id"].replace("-", "_"))
                self.assertEqual(UsdGeom.Imageable(marker).ComputeVisibility(), "invisible")
                position = UsdGeom.XformCache().GetLocalToWorldTransform(prim).ExtractTranslation()
                self.assertAlmostEqual(position[1], 384.7)
            self.assertEqual(peak_trucks,5)
            self.assertGreaterEqual(peak_roads,2)
            self.assertEqual(peak_cranes,2)
            self.assertEqual(set(demo.engine.equipment),set())
            self.assertGreater(sum(o["wait_s"] for o in demo.engine.orders.values()),0)
            completion=[e for e in demo.engine.events if e["kind"]=="order_completed"]
            self.assertEqual(len(completion),5)
            for event in completion:
                order=demo.engine.orders[event["order"]]
                self.assertAlmostEqual(event["elapsed_s"],order["completed_at"]-order["started_at"],places=2)
                self.assertGreaterEqual(event["elapsed_s"],event["wait_s"])
            self.assertIsNone(demo.visual_rtg)
            self.assertIsNone(demo.shared_sts)
            self.assertEqual(UsdGeom.Xformable(gantry).GetXformOpOrderAttr().Get(), gantry_ops)
            landed = stage.GetPrimAtPath(unload_cargo["visual_prim"])
            self.assertNotEqual(UsdGeom.Imageable(landed).ComputeVisibility(), "invisible")
            start = sim_workbench.state(owner)["positions_m"]["gantry_m"]
            sim_workbench.command(owner, move)
            for _ in range(32):
                owner.tick(stage, .1)
                demo.advance(.1)
            report = sim_workbench.state(owner)
            self.assertEqual(report["operation"]["status"], "completed")
            self.assertAlmostEqual(report["positions_m"]["gantry_m"], 300., places=3)
            translate = gantry.GetAttribute("xformOp:translate").Get()
            matrix_y = UsdGeom.Xformable(gantry).GetLocalTransformation().ExtractTranslation()[1]
            self.assertGreater(abs(gantry_actual_to_usd(start) - translate[1]), 1.)
            self.assertAlmostEqual(matrix_y, translate[1], places=3)
            self.assertTrue(demo.check_scene()["ok"], demo.scene_report)
            history = demo.recent_history()
            self.assertGreater(len(history), 2)
            self.assertTrue(all(e["id"] < history[0]["id"] for e in demo.recent_history(history[0]["id"])))
            demo.process({"action": "reset"})
            self.assertFalse(demo.engine.orders)
            self.assertEqual(demo.recent_history()[0]["kind"], "reset")
            self.assertIsNone(demo.visual_rtg)
            self.assertEqual(hoist.GetXformOpOrderAttr().Get(), manual_ops)
            self.assertEqual(UsdGeom.XformCache().GetLocalToWorldTransform(hoist.GetPrim()), manual_pose)
        finally:
            owner.close()
            demo.close()
        self.assertEqual(stage.GetRootLayer().ExportToString(), original)
        self.assertEqual(list(stage.GetSessionLayer().subLayerPaths), layers)
