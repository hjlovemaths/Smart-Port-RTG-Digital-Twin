"""Two independent boxes share one trip; slot ownership survives every handoff."""
import math,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from pxr import Gf,Sdf,Usd,UsdGeom
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'omniverse/scripts'))
sys.path.insert(0,str(ROOT/'omniverse/extensions/smartport.agent_panel'))
from yard_to_ship_plan import plan_yard_to_ship
from yard_double20_transfer import CumulativeDouble20YardToShipTransfer
from ship_transfer_plan import box
from ship_to_yard_transfer import active_task
from smartport.agent_panel.scene_runtime import SceneRuntime
from smartport.agent_panel.agent_api import validate_tool_arguments


class CumulativeLoadingTests(unittest.TestCase):
    def setUp(self):
        self.s=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))
        self.owner=SceneRuntime(ROOT);self.t=None
    def tearDown(self):
        self.owner.release()
        if self.t:self.t.c.close()
    def args(self,**kwargs):
        args=dict(source_bay='3C/005',source_row=2,target_location='011090',
                  plan_type='cumulative_double20',second_source_bay='3C/005',
                  second_source_row=2,second_target_location='031090')
        args.update(kwargs);return args

    def test_preview_is_read_only_and_reserves_two_slots_and_successive_stack_lifts(self):
        before=(self.s.GetRootLayer().ExportToString(),self.s.GetSessionLayer().ExportToString())
        p=plan_yard_to_ship(self.s,**self.args())
        self.assertEqual(p['plan_type'],'cumulative_double20')
        self.assertEqual([c['source_tier'] for c in p['containers']],[3,2])
        self.assertEqual(len(set(p['source_paths'])),2)
        self.assertEqual([s['slot_id'] for s in p['truck_slots']],['front','rear'])
        self.assertEqual(p['departure_required_count'],2)
        a,b=p['truck_slots'];length=p['cargo_dimensions_m'][1]
        self.assertGreater(b['load_offset_m'][0]-a['load_offset_m'][0],length)
        self.assertGreater(a['yard_vehicle_stop_m'][1],b['yard_vehicle_stop_m'][1])
        self.assertAlmostEqual(a['yard_contact_m'][1],p['containers'][0]['source_pick_m'][1])
        self.assertAlmostEqual(b['yard_contact_m'][1],p['containers'][1]['source_pick_m'][1])
        self.assertEqual(before,(self.s.GetRootLayer().ExportToString(),self.s.GetSessionLayer().ExportToString()))

    def test_rejects_missing_duplicate_buried_oversize_and_cross_block_inputs(self):
        for kwargs in (dict(second_source_bay=None),dict(second_source_row=None),dict(second_target_location=None),
                       dict(second_target_location='011090'),dict(second_source_tier=3),dict(second_source_tier=1),
                       dict(source_bay='3C/002',source_row=6,target_location='020090'),
                       dict(second_source_bay='2C/005'),dict(plan_type='single'),dict(plan_type='unknown')):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):plan_yard_to_ship(self.s,**self.args(**kwargs))

    def test_two_boxes_one_trip_continuous_handoffs_pause_cinema_and_exact_reset(self):
        args=self.args(second_source_bay='3C/015',second_source_row=1)
        validate_tool_arguments('preview_yard_to_ship_transfer',args)
        self.owner._viewport=lambda:SimpleNamespace(camera_path='/OmniverseKit_Persp')
        before=self.s.GetSessionLayer().ExportToString()
        preview=self.owner.execute(self.s,'preview_yard_to_ship_transfer',args)
        self.assertNotEqual(preview['containers'][0]['resolved_source_bay'],preview['containers'][1]['resolved_source_bay'])
        self.owner.execute(self.s,'start_yard_to_ship_transfer',{'plan_id':preview['plan_id']})
        self.t=self.owner.transfer_task;t=self.t
        self.assertIsInstance(t,CumulativeDouble20YardToShipTransfer)
        director=self.owner.transfer_camera;self.assertIsNotNone(director)
        paths=[r[0].GetPath() for r in t.cargo_records]
        initial=[box(self.s,p) for p in paths];last=[b.GetMidpoint() for b in initial]
        pickup=set();sts_pickup=set();departures=0;was_transport=False;checked_pause=False
        gantry_holds={};between_seen=False
        for _ in range(16000):
            old_point=Gf.Vec3d(t.truck_point);old_heading=t.truck_heading;old_owner=t.attached;old_phase=t.phase
            self.owner.tick(self.s,.1)
            movement=t.truck_point-old_point
            forward=Gf.Vec3d(-math.cos(math.radians(old_heading)),-math.sin(math.radians(old_heading)),0)
            if old_phase!='集卡倒车归位':
                self.assertGreaterEqual(Gf.Dot(movement,forward),-1e-5,'Only parking-bay entry may reverse')
            if old_owner=='rtg' or t.attached=='rtg':self.assertLess(movement.GetLength(),1e-8)
            centers=[box(self.s,p).GetMidpoint() for p in paths]
            for i,center in enumerate(centers):
                self.assertLess((center-last[i]).GetLength(),1.2,'Boxes must move continuously')
                matrix=UsdGeom.XformCache().GetLocalToWorldTransform(self.s.GetPrimAtPath(paths[i]))
                self.assertAlmostEqual(abs(matrix.GetDeterminant()),1.,places=5)
                if t.loaded[i]:
                    offset=Gf.Vec3d(t.plan['truck_slots'][i]['load_offset_m'])
                    expected=t.truck_point+Gf.Rotation(Gf.Vec3d(0,0,1),t.truck_heading).TransformDir(offset)
                    expected+=Gf.Vec3d(0,0,t.plan['containers'][i]['cargo_dimensions_m'][2]/2)
                    _,op,world,origin=t.cargo_records[i]
                    self.assertLess(((world.GetInverse()*op.Get()).Transform(origin)-expected).GetLength(),1e-5)
            last=centers
            if t.attached=='rtg':
                pickup.add(t.active_index);self.assertTrue(t.rtg.spreader_ready)
                gantry_holds.setdefault(t.active_index,t.rtg.offset[0])
                self.assertAlmostEqual(t.rtg.offset[0],gantry_holds[t.active_index],places=7)
                self.assertAlmostEqual(t.rtg.contact()[1],t.plan['containers'][t.active_index]['source_pick_m'][1],places=5)
            if t.attached=='sts':sts_pickup.add(t.active_index)
            if t.loaded==[True,False] and not any(t.landed):
                if t.phase=='集卡前往下一装箱贝位' or old_phase=='集卡前往下一装箱贝位':
                    self.assertLessEqual(movement[1],1e-8);between_seen|=movement.GetLength()>1e-5
                else:self.assertLess(movement.GetLength(),1e-8,'Truck must stop for each RTG loading cycle')
                if not checked_pause:
                    self.owner.execute(self.s,'control_yard_to_ship_transfer',{'action':'pause'})
                    point=Gf.Vec3d(t.truck_point)
                    t.tick(5);self.assertEqual(t.loaded,[True,False]);self.assertEqual(t.truck_point,point)
                    self.assertEqual([box(self.s,p).GetMidpoint() for p in paths],centers)
                    t.c.stop()
                    with self.assertRaises(ValueError):t.resume()
                    t.c.resume();t.resume();checked_pause=True
            transport=t.phase=='集卡经引桥运往岸桥'
            if transport:
                self.assertEqual(t.loaded,[True,True]);self.assertIsNone(t.attached)
                anchors=[(world.GetInverse()*op.Get()).Transform(origin) for _,op,world,origin in t.cargo_records]
                self.assertAlmostEqual((anchors[1]-anchors[0]).GetLength(),
                                       t.plan['truck_slots'][1]['load_offset_m'][0]-t.plan['truck_slots'][0]['load_offset_m'][0],places=5)
            if transport and not was_transport:departures+=1
            was_transport=transport
            self.assertEqual(t.truck.GetAttribute('transfer:loadedCount').Get(),sum(t.loaded))
            if t.finished and director.closed:break
        self.assertTrue(t.finished,t.summary());self.assertEqual(pickup,{0,1});self.assertEqual(sts_pickup,{0,1})
        self.assertEqual(departures,1);self.assertTrue(checked_pause)
        self.assertTrue(between_seen);self.assertIn('集卡倒车归位',t.history)
        self.assertEqual(t.loaded,[False,False]);self.assertEqual(t.landed,[True,True])
        self.assertEqual(t.summary()['landed_count'],2)
        self.assertLess((t.truck_point-t.pivot).GetLength(),1e-8)
        self.assertEqual(t.history.count('RTG源箱对位闭锁'),2)
        self.assertEqual(t.history.count('岸桥集卡对位闭锁'),2)
        self.assertEqual(director.camera_history.count('rtg_work'),1)
        self.assertEqual(director.state()['status'],'completed')
        for i,p in enumerate(preview['containers']):
            b=box(self.s,paths[i]);self.assertLess((b.GetMidpoint()-Gf.Vec3d(p['target_center_m'])).GetLength(),1e-5)
            self.assertLess((b.GetSize()-initial[i].GetSize()).GetLength(),1e-5)
        self.owner.execute(self.s,'control_yard_to_ship_transfer',{'action':'reset'})
        self.assertIsNone(active_task(self.s));t.c.close()
        self.assertEqual(before,self.s.GetSessionLayer().ExportToString())

    def test_second_source_change_invalidates_preview(self):
        preview=self.owner.execute(self.s,'preview_yard_to_ship_transfer',self.args())
        second=self.s.GetPrimAtPath(preview['containers'][1]['source_path'])
        with Usd.EditContext(self.s,self.s.GetSessionLayer()):second.GetAttribute('live:tier').Set(1)
        with self.assertRaisesRegex(ValueError,'发生变化'):
            self.owner.execute(self.s,'start_yard_to_ship_transfer',{'plan_id':preview['plan_id'],'cinematic':False})

    def test_upstream_first_order_keeps_each_source_destination_in_either_request_order(self):
        a=plan_yard_to_ship(self.s,**self.args(second_source_bay='3C/015',second_source_row=1))
        b=plan_yard_to_ship(self.s,**self.args(source_bay='3C/015',source_row=1,target_location='031090',
                                               second_source_bay='3C/005',second_source_row=2,second_target_location='011090'))
        self.assertEqual(a['loading_order'],[1,0]);self.assertEqual(b['loading_order'],[0,1])
        self.assertEqual([(p['source_path'],p['target_location']) for p in a['containers']],
                         [(p['source_path'],p['target_location']) for p in b['containers']])
        for plan in (a,b):
            self.assertFalse(plan['yard_reverse_allowed']);self.assertTrue(plan['parking_reverse_allowed'])
            self.assertFalse(plan['rtg_loaded_gantry_motion_allowed']);self.assertTrue(plan['truck_route_parking'])
            for item,slot in zip(plan['containers'],plan['truck_slots']):
                self.assertAlmostEqual(slot['yard_contact_m'][1],item['source_pick_m'][1])
            route=plan['truck_route_between_loads']
            self.assertGreater(route[0][1],route[-1][1]);self.assertEqual(route[0][0],route[-1][0])

    def test_loaded_gantry_command_and_truck_reverse_are_rejected(self):
        preview=self.owner.execute(self.s,'preview_yard_to_ship_transfer',self.args())
        self.owner.execute(self.s,'start_yard_to_ship_transfer',{'plan_id':preview['plan_id'],'cinematic':False})
        self.t=self.owner.transfer_task;t=self.t
        for _ in range(5000):
            t.tick(.1)
            if t.attached=='rtg':break
        self.assertEqual(t.attached,'rtg')
        with self.assertRaisesRegex(ValueError,'大车'):t.command_rtg(t.rtg.contact()+Gf.Vec3d(0,1,0))
        with self.assertRaisesRegex(ValueError,'倒车'):t.drive(.1,reverse=True)
        with self.assertRaisesRegex(ValueError,'解锁'):t.drive(.1)

    def test_same_stack_two_successive_lifts_and_reset_before_departure(self):
        before=self.s.GetSessionLayer().ExportToString()
        preview=self.owner.execute(self.s,'preview_yard_to_ship_transfer',self.args())
        self.owner.execute(self.s,'start_yard_to_ship_transfer',{'plan_id':preview['plan_id'],'cinematic':False})
        self.t=self.owner.transfer_task
        for i,(prim,_,_,_) in enumerate(self.t.cargo_records):
            self.assertEqual(prim.GetAttribute('transfer:targetLocation').Get(),preview['containers'][i]['target_location'])
        for _ in range(5000):
            self.t.tick(.1)
            if all(self.t.loaded):break
        self.assertEqual(self.t.loaded,[True,True]);self.assertEqual(self.t.active_index,1)
        self.assertNotIn('集卡经引桥运往岸桥',self.t.history)
        self.assertEqual(self.t.history.count('RTG源箱对位闭锁'),2)
        self.owner.execute(self.s,'control_yard_to_ship_transfer',{'action':'reset'})
        self.t.c.close();self.assertEqual(before,self.s.GetSessionLayer().ExportToString())

    def test_quay_delivery_keeps_both_boxes_and_does_not_operate_sts_or_return_empty(self):
        args=self.args(target_location=None,second_target_location=None,delivery_mode='quay',crane_id='STS_01',
                       second_source_bay='3C/015',second_source_row=1)
        args={k:v for k,v in args.items() if v is not None}
        validate_tool_arguments('preview_yard_to_ship_transfer',args)
        self.owner._viewport=lambda:SimpleNamespace(camera_path='/OmniverseKit_Persp')
        events=[];self.owner.notify=events.append
        before=self.s.GetSessionLayer().ExportToString()
        preview=self.owner.execute(self.s,'preview_yard_to_ship_transfer',args)
        self.assertEqual(preview['target_placeholder_paths'],[])
        self.owner.execute(self.s,'start_yard_to_ship_transfer',{'plan_id':preview['plan_id']})
        self.t=self.owner.transfer_task;t=self.t;director=self.owner.transfer_camera
        self.assertIsNotNone(director)
        home=dict(t.c.state);spreader_size=t.c.spreader_size_ft
        for _ in range(12000):
            self.owner.tick(self.s,.1)
            self.assertEqual(t.c.state,home);self.assertEqual(t.c.spreader_size_ft,spreader_size)
            if t.finished and director.closed:break
        self.assertTrue(t.finished,t.summary());self.assertEqual(t.loaded,[True,True]);self.assertEqual(t.landed,[False,False])
        self.assertLess((t.truck_point-Gf.Vec3d(preview['truck_vehicle_stop_m'])).GetLength(),1e-8)
        self.assertNotIn('岸桥集卡对位闭锁',t.history);self.assertNotIn('集卡空车返回停车场',t.history)
        self.assertEqual(director.state()['destination'],'STS_01集卡交接区')
        self.assertNotIn('装船',director.state()['title'])
        complete=[e['text'] for e in events if e['kind']=='simulation_task_complete']
        self.assertEqual(len(complete),1);self.assertIn('车上保留两箱',complete[0]);self.assertNotIn('装船',complete[0])
        self.owner.execute(self.s,'control_yard_to_ship_transfer',{'action':'reset'})
        t.c.close();self.assertEqual(before,self.s.GetSessionLayer().ExportToString())

    def test_quay_delivery_requires_explicit_crane_and_rejects_ship_destinations(self):
        for kwargs in (dict(crane_id=None,target_location=None,second_target_location=None),
                       dict(crane_id='STS_99',target_location=None,second_target_location=None),
                       dict(crane_id='STS_01'),dict(crane_id='STS_01',target_location=None,second_target_location=None,delivery_mode='other')):
            args=self.args(delivery_mode='quay');args.update(kwargs)
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):plan_yard_to_ship(self.s,**args)


if __name__=='__main__':unittest.main()
