"""Two real native boxes: STS twin lift, truck, two RTG placements and reset."""
import math,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from pxr import Gf,Usd,UsdGeom
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'omniverse/scripts'));sys.path.insert(0,str(ROOT/'omniverse/extensions/smartport.agent_panel'))
from ship_transfer_plan import plan_transfer,box
from ship_double20_transfer import Double20ShipToYardTransfer,NEXT_UNLOAD_STOP,PAIR
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
        reverse=plan_transfer(self.s,**self.args(target_bay='3C/063',second_target_bay='3C/077'))
        self.assertEqual(reverse['unloading_order'],[1,0])
        for plan in (p,reverse):
            stops=plan['yard_unloading_stops_m']
            self.assertGreaterEqual(stops[0][1],stops[1][1])
            self.assertEqual(plan['truck_route_loaded'][-1],stops[0])
            self.assertEqual(plan['truck_route_return'][0],stops[1])
        self.assertEqual(before,(self.s.GetRootLayer().ExportToString(),self.s.GetSessionLayer().ExportToString()))
        for kwargs in (dict(second_target_bay=None),dict(second_target_row=None),dict(second_target_bay='3C/001'),dict(second_target_bay='2C/003'),dict(source_bay=9)):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):plan_transfer(self.s,**self.args(**kwargs))

    def test_complete_twin_lift_sequential_rtg_unload_cameras_and_reset(self):
        self.owner=SceneRuntime(ROOT);self.owner._viewport=lambda:SimpleNamespace(camera_path='/OmniverseKit_Persp')
        before=self.s.GetSessionLayer().ExportToString()
        preview=self.owner.execute(self.s,'preview_ship_to_yard_transfer',self.args(source_bay=5,target_bay='3C/077',second_target_bay='3C/063'))
        self.assertEqual(preview['crane_id'],'STS_01')
        self.assertEqual(preview['source_location'],'051292 + 071292')
        self.assertEqual([c['target_tier'] for c in preview['containers']],[3,3])
        self.owner.execute(self.s,'start_ship_to_yard_transfer',{'plan_id':preview['plan_id']})
        self.t=self.owner.transfer_task;self.assertIsInstance(self.t,Double20ShipToYardTransfer)
        director=self.owner.transfer_camera;self.assertIsNotNone(director)
        paths=[p.GetPath() for p in self.t.box_prims];initial=[box(self.s,p) for p in paths]
        last=[b.GetMidpoint() for b in initial];truck_last=Gf.Vec3d(self.t.truck_point);picked=set();landed_order=[]
        shot_frames={};truck_shot_centers=[];framed_contacts=set()
        initial_spacing=last[1]-last[0]
        moved_between_stops=False
        for _ in range(14000):
            was_landed=list(self.t.landed)
            previous_phase=self.t.phase
            previous_contact=self.t.rtg.contact()
            previous_centers=list(last)
            self.owner.tick(self.s,.1)
            if not director.closed and director.transition>=director.transition_duration:
                group=director.camera_group
                if group=='rtg_bay_transfer':
                    truck_shot_centers.append(director.center[1])
                elif group.startswith(('rtg_pick_close_','rtg_unload_medium_','rtg_place_close_')):
                    pose=UsdGeom.XformCache().GetLocalToWorldTransform(director.op.GetAttr().GetPrim())
                    if group in shot_frames:
                        self.assertTrue(Gf.IsClose(pose,shot_frames[group][0],1e-7))
                    else:
                        shot_frames[group]=(pose,(director.eye-director.center).GetLength(),director.center[1])
            for i,path in enumerate(paths):
                b=box(self.s,path);center=b.GetMidpoint()
                if (i==self.t.active_index and not director.closed and director.camera_group in shot_frames
                    and self.t.phase in ('RTG对位闭锁','RTG开锁落箱')):
                    group=director.camera_group
                    framed_contacts.add(group)
                    camera_inverse=shot_frames[group][0].GetInverse()
                    horizontal=math.tan(math.radians(55)/2)
                    for corner in range(8):
                        point=camera_inverse.Transform(Gf.Vec3d(*(
                            b.GetMax()[axis] if corner&(1<<axis) else b.GetMin()[axis] for axis in range(3))))
                        self.assertGreater(-point[2],0.)
                        self.assertLess(abs(point[0])/-point[2],horizontal)
                        self.assertLess(abs(point[1])/-point[2],horizontal/(16/9))
                self.assertLess((center-last[i]).GetLength(),1.2,'A carried box must never teleport')
                matrix=UsdGeom.XformCache().GetLocalToWorldTransform(self.s.GetPrimAtPath(path))
                self.assertAlmostEqual(abs(matrix.GetDeterminant()),1.,places=5)
                last[i]=center
                if self.t.landed[i] and not was_landed[i]:landed_order.append(i)
            if self.t.attached=='sts':
                delta=last[1]-last[0]
                self.assertAlmostEqual(delta[1],preview['pair_pitch_m'],places=4)
                self.assertLess((delta-initial_spacing).GetLength(),1e-5)
                self.assertEqual(self.t.c.spreader_size_ft,40)
            if self.t.attached=='rtg':
                i=self.t.active_index
                if i not in picked:
                    self.assertLess((self.t.rtg.contact()-self.t.yard_pick).GetLength(),1e-5)
                    self.assertLess((self.t.rtg.contact()-previous_centers[i]-Gf.Vec3d(0,0,initial[i].GetSize()[2]/2)).GetLength(),1e-5)
                picked.add(i);self.assertAlmostEqual(self.t.rtg.current_size_ft,20)
                self.assertTrue(self.t.rtg.spreader_ready)
                self.assertAlmostEqual(previous_contact[1],self.t.rtg.contact()[1],places=5)
                self.assertAlmostEqual(self.t.rtg.contact()[1],preview['containers'][i]['target_center_m'][1],places=5)
                self.assertLess((self.t.truck_point-truck_last).GetLength(),1e-8)
            if self.t.landed==[True,False] and (self.t.truck_point-truck_last).GetLength()>1e-8:
                self.assertEqual(previous_phase,NEXT_UNLOAD_STOP)
                self.assertIsNone(self.t.attached)
                self.assertLess(self.t.truck_point[1],truck_last[1])
                self.assertAlmostEqual(last[1][1]-self.t.truck_point[1],preview['truck_slots'][1]['load_offset_y_m'],places=5)
                self.assertAlmostEqual(last[0][1],preview['containers'][0]['target_center_m'][1],places=5)
                moved_between_stops=True
            truck_last=Gf.Vec3d(self.t.truck_point)
            if self.t.finished and director.closed:break
        self.assertTrue(self.t.finished,self.t.summary());self.assertEqual(picked,{0,1});self.assertEqual(landed_order,[0,1])
        self.assertTrue(moved_between_stops)
        self.assertEqual(self.t.landed,[True,True]);self.assertLess((self.t.truck_point-self.t.pivot).GetLength(),1e-8)
        unload_order=['rtg_pick_close_1','rtg_unload_medium_1','rtg_place_close_1',
                      'rtg_bay_transfer','rtg_pick_close_2','rtg_unload_medium_2','rtg_place_close_2','rtg_work']
        self.assertEqual(director.camera_history[-len(unload_order):],unload_order)
        self.assertEqual(framed_contacts,{'rtg_pick_close_1','rtg_pick_close_2','rtg_place_close_1','rtg_place_close_2'})
        self.assertGreater(truck_shot_centers[0]-truck_shot_centers[-1],5.)
        for index in (1,2):
            pick,move,place=(shot_frames[f'{shot}_{index}'] for shot in
                             ('rtg_pick_close','rtg_unload_medium','rtg_place_close'))
            self.assertLess(pick[1],move[1])
            self.assertLess(place[1],move[1])
            self.assertNotEqual(pick[0],place[0])
            self.assertAlmostEqual(place[2],preview['containers'][index-1]['target_center_m'][1],places=5)
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
