"""Run the workbench's workflows on the current six-six ship stage."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "omniverse/scripts"))


class WorkbenchUsdTests(unittest.TestCase):
    def test_mixed_demo_checks_pause_reset_and_history(self):
        try:
            from pxr import Usd, UsdGeom
        except ImportError:
            self.skipTest("Requires the Kit USD runtime")
        from iecs_launch import launch

        stage = Usd.Stage.Open(str(ROOT / "omniverse/scenes/smart_port.usda"))
        original = stage.GetRootLayer().ExportToString()
        layers = list(stage.GetSessionLayer().subLayerPaths)
        demo = launch(stage=stage, port=None, with_ui=False, subscribe=False, db_path=":memory:")
        try:
            self.assertEqual(demo.size_ft, 20)
            self.assertIn("/StructureSlots/", demo.ship_box)
            self.assertIsNone(demo.server)
            demo.process({"action": "speed", "value": 20})
            demo.process({"action": "mixed"})
            demo.apply_scene()
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
            for _ in range(2800):
                demo.engine.tick(.1)
                demo.advance_mixed()
                demo.apply_scene()
                if not demo.mixed_pending and all(o["status"] == "completed" for o in demo.engine.orders.values()):
                    break
            self.assertEqual([o["flow"] for o in demo.engine.orders.values()],
                             ["unload", "load", "gate_in", "gate_out", "transfer"])
            self.assertTrue(all(o["status"] == "completed" for o in demo.engine.orders.values()))
            self.assertTrue(demo.check_scene()["ok"], demo.scene_report)
            history = demo.recent_history()
            self.assertGreater(len(history), 2)
            self.assertTrue(all(e["id"] < history[0]["id"] for e in demo.recent_history(history[0]["id"])))
            demo.process({"action": "reset"})
            self.assertFalse(demo.engine.orders)
            self.assertEqual(demo.recent_history()[0]["kind"], "reset")
            self.assertIsNone(demo.visual_rtg)
        finally:
            demo.close()
        self.assertEqual(stage.GetRootLayer().ExportToString(), original)
        self.assertEqual(list(stage.GetSessionLayer().subLayerPaths), layers)
