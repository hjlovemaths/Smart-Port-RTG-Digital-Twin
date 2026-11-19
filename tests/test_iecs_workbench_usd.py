"""Run the workbench's workflows on the current six-six ship stage."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "omniverse/scripts"))
sys.path.insert(0, str(ROOT / "omniverse/extensions/smartport.agent_panel"))


class WorkbenchUsdTests(unittest.TestCase):
    def test_mixed_demo_checks_pause_reset_and_history(self):
        try:
            from pxr import Usd, UsdGeom
        except ImportError:
            self.skipTest("Requires the Kit USD runtime")
        from iecs_launch import launch
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
            demo.process({"action": "speed", "value": 20})
            _iecs(owner,{"action":"mixed"},web=True)
            demo.process({"action": "pause"})
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
            for _ in range(2200):
                demo.engine.tick(.1)
                demo.apply_scene()
                peak_trucks=max(peak_trucks,sum(o["status"]=="running" for o in demo.engine.orders.values()))
                peak_roads=max(peak_roads,len(demo.engine.road_claims))
                peak_cranes=max(peak_cranes,len(demo.engine.equipment))
                if not demo.mixed_pending and all(o["status"] == "completed" for o in demo.engine.orders.values()):
                    break
            self.assertEqual([o["flow"] for o in demo.engine.orders.values()],
                             ["unload", "load", "gate_in", "gate_out", "transfer"])
            self.assertTrue(all(o["status"] == "completed" for o in demo.engine.orders.values()),
                            [(o['flow'],o['step'],o['wait_reason']) for o in demo.engine.orders.values()])
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
            self.assertTrue(demo.check_scene()["ok"], demo.scene_report)
            history = demo.recent_history()
            self.assertGreater(len(history), 2)
            self.assertTrue(all(e["id"] < history[0]["id"] for e in demo.recent_history(history[0]["id"])))
            demo.process({"action": "reset"})
            self.assertFalse(demo.engine.orders)
            self.assertEqual(demo.recent_history()[0]["kind"], "reset")
            self.assertIsNone(demo.visual_rtg)
        finally:
            owner.close()
            demo.close()
        self.assertEqual(stage.GetRootLayer().ExportToString(), original)
        self.assertEqual(list(stage.GetSessionLayer().subLayerPaths), layers)
