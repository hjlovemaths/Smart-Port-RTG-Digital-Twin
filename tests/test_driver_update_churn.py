"""Unchanged driver input must not dirty USD or rebind the render camera."""
import sys, unittest
from pathlib import Path
from types import SimpleNamespace
from pxr import Gf, Tf, Usd, UsdGeom
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'omniverse/scripts'))
from truck_driver_cab import DriverCab
from role_viewport import DriverViewport,next_capture_deadline


class UpdateChurnTests(unittest.TestCase):
    def test_frame_clock_does_not_accumulate_main_thread_jitter(self):
        deadline=0;captures=0
        for i in range(300):
            now=1+i/30+(0.002 if i%2 else 0)
            if now+1e-6>=deadline:
                captures+=1;deadline=next_capture_deadline(deadline,now,30)
        self.assertGreaterEqual(captures,295)
        deadline=next_capture_deadline(deadline,30,30)
        self.assertGreater(deadline,30)
        self.assertLessEqual(deadline,30+1/30+1e-6)

    def test_cab_only_authors_changed_properties(self):
        stage=Usd.Stage.CreateInMemory()
        truck=UsdGeom.Xform.Define(stage,'/Truck').GetPrim()
        glass=UsdGeom.Cube.Define(stage,'/Truck/front_black_windshield')
        cab=DriverCab(stage,truck,'/Cab')
        pose=Gf.Matrix4d(1)
        cab.apply(True,pose,0)
        changes=[]
        notice=Tf.Notice.Register(Usd.Notice.ObjectsChanged,
            lambda change,sender:changes.extend(map(str,change.GetChangedInfoOnlyPaths())),stage)
        try:
            for _ in range(60):cab.apply(True,pose,0)
            self.assertEqual(changes,[])
            cab.apply(True,Gf.Matrix4d(1).SetTranslate(Gf.Vec3d(1,0,0)),0)
            self.assertTrue(changes)
            self.assertFalse(any('visibility' in path for path in changes))
            cab.apply(False,pose,1)
            self.assertEqual(glass.GetVisibilityAttr().Get(),'inherited')
            self.assertEqual(cab.steer_op.Get(),70)
        finally:notice.Revoke()

    def test_camera_binding_only_changes_on_camera_switch(self):
        calls=[]
        view=DriverViewport.__new__(DriverViewport)
        view._camera='/Camera';view.texture=SimpleNamespace(set_camera_path=calls.append)
        for _ in range(60):view.camera_path='/Camera'
        self.assertEqual(calls,[])
        view.camera_path='/OtherCamera'
        self.assertEqual(calls,['/OtherCamera'])


if __name__=='__main__':unittest.main()
