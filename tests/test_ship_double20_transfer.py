"""Two real native boxes: STS twin lift, truck, two RTG placements and reset."""
import sys,unittest
from pathlib import Path
from types import SimpleNamespace
from pxr import Gf,Usd,UsdGeom
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'omniverse/scripts'));sys.path.insert(0,str(ROOT/'omniverse/extensions/smartport.agent_panel'))
from ship_transfer_plan import plan_transfer,box
from ship_double20_transfer import Double20ShipToYardTransfer,PAIR
from ship_to_yard_transfer import active_task
from smartport.agent_panel.scene_runtime import SceneRuntime


class Twin20Tests(unittest.TestCase):
    def setUp(self):
        self.s=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'));self.owner=None;self.t=None
    def tearDown(self):
        if self.owner:self.owner.release()
        if self.t:self.t.close();self.t.c.close()
    def args(self,**kwargs):
        args=dict(source_bay=1,source_side='port',row_from_side=1,target_bay='3C/001',target_row=6,
                  double20=True,second_target_bay='3C/003',second_target_row=6)
        args.update(kwargs);return args

    def test_preview_uses_same_row_pair_and_requires_user_destinations(self):
        before=(self.s.GetRootLayer().ExportToString(),self.s.GetSessionLayer().ExportToString())
        p=plan_transfer(self.s,**self.args())
        self.assertEqual(p['container_count'],2);self.assertEqual(p['rtg_size_ft'],20);self.assertEqual(p['size_ft'],40)
        self.assertEqual([c['source_location'] for c in p['containers']],['011090','031090'])
        self.assertEqual([c['target_bay'] for c in p['containers']],['3C/001','3C/003'])
        self.assertAlmostEqual(p['cargo_dimensions_m'][1],5.272,places=4)
        self.assertEqual(before,(self.s.GetRootLayer().ExportToString(),self.s.GetSessionLayer().ExportToString()))
        for kwargs in (dict(second_target_bay=None),dict(second_target_row=None),dict(second_target_bay='3C/001'),dict(second_target_bay='2C/003'),dict(source_bay=9)):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):plan_transfer(self.s,**self.args(**kwargs))

    def test_complete_twin_lift_sequential_rtg_unload_cameras_and_reset(self):
        self.owner=SceneRuntime(ROOT);self.owner._viewport=lambda:SimpleNamespace(camera_path='/OmniverseKit_Persp')
        before=self.s.GetSessionLayer().ExportToString()
        preview=self.owner.execute(self.s,'preview_ship_to_yard_transfer',self.args(target_bay='3C/005',target_row=1,second_target_bay='3C/005',second_target_row=2))
        self.owner.execute(self.s,'start_ship_to_yard_transfer',{'plan_id':preview['plan_id']})
        self.t=self.owner.transfer_task;self.assertIsInstance(self.t,Double20ShipToYardTransfer)
        director=self.owner.transfer_camera;self.assertIsNotNone(director)
        paths=[p.GetPath() for p in self.t.box_prims];initial=[box(self.s,p) for p in paths]
        last=[b.GetMidpoint() for b in initial];truck_last=Gf.Vec3d(self.t.truck_point);picked=set();landed_order=[];rtg_eye=None
        for _ in range(14000):
            was_landed=list(self.t.landed)
            self.owner.tick(self.s,.1)
            if director.camera_group=='rtg_work' and director.transition>=director.transition_duration:
                if rtg_eye is None:rtg_eye=Gf.Vec3d(director.eye)
                self.assertLess((director.eye-rtg_eye).GetLength(),1e-7,'Do not cut back and forth for the two RTG boxes')
            for i,path in enumerate(paths):
                b=box(self.s,path);center=b.GetMidpoint()
                self.assertLess((center-last[i]).GetLength(),1.2,'A carried box must never teleport')
                matrix=UsdGeom.XformCache().GetLocalToWorldTransform(self.s.GetPrimAtPath(path))
                self.assertAlmostEqual(abs(matrix.GetDeterminant()),1.,places=5)
                last[i]=center
                if self.t.landed[i] and not was_landed[i]:landed_order.append(i)
            if self.t.attached=='sts':
                delta=last[1]-last[0]
                self.assertAlmostEqual(delta[1],preview['pair_pitch_m'],places=4)
                self.assertAlmostEqual(delta[0],0,places=4);self.assertAlmostEqual(delta[2],0,places=4)
                self.assertEqual(self.t.c.spreader_size_ft,40)
            if self.t.attached=='rtg':
                picked.add(self.t.active_index);self.assertAlmostEqual(self.t.rtg.current_size_ft,20)
                self.assertTrue(self.t.rtg.spreader_ready)
            if self.t.landed[0] and not self.t.landed[1]:self.assertLess((self.t.truck_point-truck_last).GetLength(),1e-8)
            truck_last=Gf.Vec3d(self.t.truck_point)
            if self.t.finished and director.closed:break
        self.assertTrue(self.t.finished,self.t.summary());self.assertEqual(picked,{0,1});self.assertEqual(landed_order,[0,1])
        self.assertEqual(self.t.landed,[True,True]);self.assertLess((self.t.truck_point-self.t.pivot).GetLength(),1e-8)
        self.assertEqual(director.history.count('rtg_pick'),2)
        self.assertEqual(director.camera_history.count('rtg_work'),1)
        self.assertEqual(director.state()['status'],'completed')
        for i,(path,target) in enumerate(zip(paths,preview['containers'])):
            b=box(self.s,path)
            self.assertLess((b.GetMidpoint()-Gf.Vec3d(target['target_center_m'])).GetLength(),1e-5)
            self.assertLess((b.GetSize()-initial[i].GetSize()).GetLength(),1e-5)
            self.assertAlmostEqual(b.GetMin()[2],target['support_bounds'][1][2]+.005,places=5)
        self.owner.execute(self.s,'control_ship_to_yard_transfer',{'action':'reset'})
        self.assertIsNone(active_task(self.s));self.assertFalse(self.s.GetPrimAtPath(PAIR))
        self.t.c.close();self.assertEqual(before,self.s.GetSessionLayer().ExportToString())

    def test_twin20_inner_beams_overlap_sleeves_and_preserve_outer_locks(self):
        from sts_control import STSController
        p=plan_transfer(self.s,**self.args())
        c=STSController(self.s,p['crane_id']);before=self.s.GetSessionLayer().ExportToString()
        self.t=Double20ShipToYardTransfer(self.s,c,p,False)
        self.assertEqual(len(self.t.spreader_frame_joints),6)
        for offset in (0.,.5):
            c.state['hoist']+=offset;c._apply()
            for moving,fixed in self.t.spreader_frame_joints:
                arm=box(self.s,moving);sleeve=box(self.s,fixed)
                overlap=[min(arm.GetMax()[a],sleeve.GetMax()[a])-max(arm.GetMin()[a],sleeve.GetMin()[a]) for a in range(3)]
                self.assertGreaterEqual(overlap[1],.1499)
                self.assertGreater(min(overlap),.01)
                end=box(self.s,str(self.s.GetPrimAtPath(moving).GetParent().GetPath())+'/MainBeam')
                self.assertGreater(min(arm.GetMax()[1],end.GetMax()[1])-max(arm.GetMin()[1],end.GetMin()[1]),.3)
            contact=self.t.sts_contact();cache=UsdGeom.XformCache()
            for end in ('Front','Rear'):
                for side in ('Front','Rear'):
                    prim=self.s.GetPrimAtPath(c.path+f'/Gantry/Trolley/Hoist/STS_SpreaderEnd{end}/CornerLock{side}')
                    point=cache.GetLocalToWorldTransform(prim).Transform(Gf.Vec3d(0,0,-.5))
                    self.assertAlmostEqual(abs(point[1]-contact[1]),p['cargo_dimensions_m'][1]/2,places=5)
                    self.assertAlmostEqual(point[2],contact[2],places=5)
        self.t.close();self.assertEqual(before,self.s.GetSessionLayer().ExportToString())


if __name__=='__main__':unittest.main()
