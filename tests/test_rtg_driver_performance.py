"""Cached truck geometry must still track motion and authored shape changes."""
import sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from pxr import Gf,Tf,Usd,UsdGeom
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'omniverse/scripts'))
from rtg_driver_cargo import DriverCargo,TRUCK


class TruckShapeCacheTests(unittest.TestCase):
    def test_motion_reuses_local_shapes_but_geometry_edits_invalidate(self):
        stage=Usd.Stage.CreateInMemory()
        truck=UsdGeom.Xform.Define(stage,TRUCK);move=truck.AddTranslateOp()
        rail=UsdGeom.Cube.Define(stage,TRUCK+'/trailer_side_rail');rail.CreateSizeAttr(1)
        scale=rail.AddScaleOp();scale.Set(Gf.Vec3f(5,1,.2))
        cargo=DriverCargo.__new__(DriverCargo)
        cargo.stage=stage;cargo.truck_geometry={};cargo.geometry=SimpleNamespace(decks={})
        cargo.subscription=Tf.Notice.Register(Usd.Notice.ObjectsChanged,cargo.geometry_changed,stage)
        try:
            cargo.add_truck_decks();original=cargo.geometry.decks[TRUCK]
            move.Set(Gf.Vec3d(8,0,0))
            with patch.object(Usd,'PrimRange',side_effect=AssertionError('repeated mesh traversal')):
                for _ in range(60):cargo.add_truck_decks()
            changed=cargo.geometry.decks[TRUCK]
            self.assertAlmostEqual(changed['pose'].ExtractTranslation()[0]-original['pose'].ExtractTranslation()[0],8)
            scale.Set(Gf.Vec3f(7,1,.2));self.assertEqual(cargo.truck_geometry,{})
            cargo.add_truck_decks();self.assertAlmostEqual(cargo.geometry.decks[TRUCK]['size'][0],7)
        finally:cargo.close()


if __name__=='__main__':unittest.main()
