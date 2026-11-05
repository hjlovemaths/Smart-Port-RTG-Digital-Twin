"""Real composed geometry and full reverse-task lifecycle, run with Kit USD."""
import sys,unittest
from pathlib import Path
from pxr import Gf,Sdf,Usd,UsdGeom
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'omniverse/scripts'))
sys.path.insert(0,str(ROOT/'omniverse/extensions/smartport.agent_panel'))
from yard_to_ship_plan import plan_yard_to_ship
from yard_to_ship_transfer import YardToShipTransfer
from ship_to_yard_transfer import active_task
from ship_transfer_plan import box
from sts_control import STSController

class ReverseTransferTests(unittest.TestCase):
    def setUp(self):
        self.s=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'));self.c=None;self.t=None
    def tearDown(self):
        if self.t:self.t.close()
        if self.c:self.c.close()
    def plan(self,**kwargs):
        args=dict(source_bay='3C/005',source_row=2,target_location='011090');args.update(kwargs)
        return plan_yard_to_ship(self.s,**args)
    def test_preview_is_read_only_and_selects_top_source_and_valid_target(self):
        before=(self.s.GetRootLayer().ExportToString(),self.s.GetSessionLayer().ExportToString())
        p=self.plan();self.assertEqual(p['source_tier'],3);self.assertEqual(p['size_ft'],20)
        self.assertEqual(p['target_location'],'011090');self.assertEqual(p['crane_id'],'STS_01')
        self.assertEqual(before,(self.s.GetRootLayer().ExportToString(),self.s.GetSessionLayer().ExportToString()))
    def test_bad_sources_buried_boxes_unknown_and_buried_targets_are_rejected(self):
        for kwargs,message in ((dict(source_tier=1),'上方'),(dict(source_bay='3C/099'),'Bay|贝|箱位'),(dict(target_location='111090'),'未返回'),(dict(target_location='011088'),'顶部'),(dict(target_location='011016'),'船舱'),(dict(target_location='021090'),'尺型'),(dict(crane_id='STS_07'),'没有岸桥')):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(ValueError,message):self.plan(**kwargs)
    def test_full_reverse_transfer_is_continuous_unscaled_supported_and_reversible(self):
        p=self.plan();self.c=STSController(self.s,p['crane_id'])
        before=self.s.GetSessionLayer().ExportToString();source_before=Gf.Range3d(Gf.Vec3d(p['source_bounds'][0]),Gf.Vec3d(p['source_bounds'][1]))
        self.t=YardToShipTransfer(self.s,self.c,p,False)
        cargo=str(self.t.cargo.GetPath());initial=box(self.s,cargo)
        self.assertLess((initial.GetMidpoint()-source_before.GetMidpoint()).GetLength(),1e-5)
        self.assertLess((initial.GetSize()-source_before.GetSize()).GetLength(),1e-5)
        self.assertGreater(len(self.t.cargo.GetChildren()),10)
        self.assertEqual(UsdGeom.Imageable(self.s.GetPrimAtPath(p['source_path'])).ComputeVisibility(),'invisible')
        last=initial.GetMidpoint();phases=set();dims=initial.GetSize()
        for i in range(12000):
            self.t.tick(.1);b=box(self.s,cargo);center=b.GetMidpoint();phases.add(self.t.phase)
            self.assertLess((center-last).GetLength(),1.2,'Cargo must follow RTG, truck and STS continuously')
            self.assertAlmostEqual(abs(UsdGeom.XformCache().GetLocalToWorldTransform(self.t.cargo).GetDeterminant()),1.,places=5)
            if self.t.phase=='停车场集卡前往堆场':self.assertLess((center-initial.GetMidpoint()).GetLength(),1e-5)
            if self.t.cargo_on_truck:
                self.assertLess(((self.t.cargo_world.GetInverse()*self.t.cargo_op.Get()).Transform(self.t.cargo_origin)-self.t.truck_load_center()).GetLength(),1e-5)
            last=center
            if i==50:
                self.t.pause();snapshot=(dict(self.c.state),Gf.Vec3d(self.t.truck_point),center)
                self.t.tick(10);self.assertEqual(self.c.state,snapshot[0]);self.assertEqual(self.t.truck_point,snapshot[1]);self.assertEqual(box(self.s,cargo).GetMidpoint(),snapshot[2]);self.t.resume()
            if i==120:
                self.c.stop();self.t.tick(.1);self.assertTrue(self.t.paused)
                with self.assertRaises(ValueError):self.t.resume()
                self.c.resume();self.t.resume()
            if self.t.finished:break
        self.assertTrue(self.t.finished,self.t.summary())
        self.assertIn('集卡空车返回停车场',self.t.history);self.assertIn('集卡倒车归位',self.t.history)
        self.assertLess((self.t.truck_point-self.t.pivot).GetLength(),1e-8);self.assertEqual(self.t.truck_heading,0.)
        self.assertLess((UsdGeom.XformCache().GetLocalToWorldTransform(self.t.truck)-self.t.truck_world).GetRow(3).GetLength(),1e-5)
        self.assertIn('RTG源箱对位闭锁',phases);self.assertIn('集卡经引桥运往岸桥',phases);self.assertIn('岸桥船上开锁落箱',phases)
        final=box(self.s,cargo)
        self.assertLess((final.GetMidpoint()-Gf.Vec3d(p['target_center_m'])).GetLength(),1e-5)
        self.assertLess((final.GetSize()-dims).GetLength(),1e-5)
        self.assertAlmostEqual(final.GetMin()[2],p['target_supports'][0]['top_z'],places=4)
        self.t.close();self.assertEqual(before,self.s.GetSessionLayer().ExportToString());self.assertIsNone(active_task(self.s))

    def test_40ft_loading_spans_two_returned_cells_and_restores_both(self):
        p=self.plan(source_bay='3C/002',source_row=6,target_location='020090')
        self.assertEqual(p['size_ft'],40);self.assertEqual(len(p['target_placeholder_paths']),2)
        uneven=Sdf.Layer.CreateAnonymous('uneven_support_test.usda')
        self.s.GetSessionLayer().subLayerPaths.insert(0,uneven.identifier)
        try:
            with Usd.EditContext(self.s,uneven):
                group=self.s.GetPrimAtPath(p['target_placeholder_paths'][1]).GetParent()
                UsdGeom.Xformable(group).AddTranslateOp(opSuffix='testUneven').Set(Gf.Vec3d(0,0,.1))
            with self.assertRaisesRegex(ValueError,'高度不同'):
                self.plan(source_bay='3C/002',source_row=6,target_location='020090')
        finally:self.s.GetSessionLayer().subLayerPaths.remove(uneven.identifier)
        self.c=STSController(self.s,p['crane_id']);self.t=YardToShipTransfer(self.s,self.c,p,False)
        self.assertEqual(self.c.spreader_size_ft,40)
        for _ in range(12000):
            self.t.tick(.1)
            if self.t.finished:break
        self.assertTrue(self.t.finished,self.t.summary())
        self.assertIn('集卡空车返回停车场',self.t.history);self.assertIn('集卡倒车归位',self.t.history)
        self.assertLess((self.t.truck_point-self.t.pivot).GetLength(),1e-8);self.assertEqual(self.t.truck_heading,0.)
        self.assertLess((UsdGeom.XformCache().GetLocalToWorldTransform(self.t.truck)-self.t.truck_world).GetRow(3).GetLength(),1e-5)
        final=box(self.s,self.t.cargo.GetPath())
        self.assertAlmostEqual(final.GetSize()[1],p['cargo_dimensions_m'][1],places=5)
        self.assertAlmostEqual(final.GetMin()[2],p['target_supports'][0]['top_z'],places=4)
        self.t.close()
        for path in p['target_placeholder_paths']:
            self.assertNotEqual(UsdGeom.Imageable(self.s.GetPrimAtPath(path)).ComputeVisibility(),'invisible')

    def test_adapter_approvals_direction_ownership_plan_revalidation_and_reset(self):
        from smartport.agent_panel.scene_runtime import SceneRuntime,current_tasks
        from smartport.agent_panel.runtime_catalog import needs_approval
        owner=SceneRuntime(ROOT)
        args=dict(source_bay='3C/005',source_row=2,target_location='011090')
        try:
            self.assertFalse(needs_approval('preview_yard_to_ship_transfer',args))
            self.assertTrue(needs_approval('start_yard_to_ship_transfer',{}))
            self.assertTrue(needs_approval('control_yard_to_ship_transfer',{'action':'reset'}))
            preview=owner.execute(self.s,'preview_yard_to_ship_transfer',args)
            with self.assertRaises(ValueError):owner.execute(self.s,'start_yard_to_ship_transfer',{'plan_id':'wrong'})
            owner.execute(self.s,'start_yard_to_ship_transfer',{'plan_id':preview['plan_id']});self.t=owner.transfer_task
            self.assertIsNone(owner._yard_transfer_preview)
            self.assertEqual(current_tasks(self.s)['yard_to_ship']['status'],'running')
            self.assertEqual(current_tasks(self.s)['ship_to_yard']['status'],'idle')
            with self.assertRaises(ValueError):owner._guard_manual('rtg')
            with self.assertRaises(ValueError):owner._guard_manual('sts',self.t.plan['crane_id'])
            with self.assertRaises(ValueError):owner.execute(self.s,'control_ship_to_yard_transfer',{'action':'reset'})
            owner.execute(self.s,'control_yard_to_ship_transfer',{'action':'pause'});self.assertTrue(self.t.paused)
            owner.execute(self.s,'control_yard_to_ship_transfer',{'action':'resume'});self.assertFalse(self.t.paused)
            owner.execute(self.s,'control_yard_to_ship_transfer',{'action':'reset'});self.assertTrue(self.t.closed)
            preview=owner.execute(self.s,'preview_yard_to_ship_transfer',args)
            with Usd.EditContext(self.s,self.s.GetSessionLayer()):
                self.s.GetPrimAtPath(preview['source_path']).GetAttribute('live:tier').Set(6)
            with self.assertRaisesRegex(ValueError,'发生变化'):
                owner.execute(self.s,'start_yard_to_ship_transfer',{'plan_id':preview['plan_id']})
        finally:owner.release()

    def test_task_spreader_size_overrides_prior_manual_size_without_double_offset(self):
        from ship_to_yard_transfer import VisualRTG
        from rtg_live_control import HOIST_PATH,SPREADER_TELESCOPE_TRAVEL_M,SPREADER_LIVE_TELESCOPE_OP_SUFFIX
        def endpoints():
            cache=UsdGeom.XformCache()
            return [cache.GetLocalToWorldTransform(self.s.GetPrimAtPath(HOIST_PATH+'/ANIM_CTRL_SPREADER_TELESCOPE_'+side)).Transform(Gf.Vec3d(0)) for side in ('FRONT','REAR')]
        temp=Sdf.Layer.CreateAnonymous('neutral_spreader.usda');self.s.GetSessionLayer().subLayerPaths.insert(0,temp.identifier)
        with Usd.EditContext(self.s,temp):rtg=VisualRTG(self.s,20)
        with Usd.EditContext(self.s,temp):
            for _ in range(40):rtg.tick_spreader(.1)
        expected=endpoints();rtg.close();self.s.GetSessionLayer().subLayerPaths.remove(temp.identifier)
        manual=Sdf.Layer.CreateAnonymous('manual_spreader.usda');self.s.GetSessionLayer().subLayerPaths.insert(0,manual.identifier)
        with Usd.EditContext(self.s,manual):
            for side,sign in (('FRONT',1),('REAR',-1)):
                p=self.s.GetPrimAtPath(HOIST_PATH+'/ANIM_CTRL_SPREADER_TELESCOPE_'+side)
                UsdGeom.Xformable(p).AddTranslateOp(opSuffix=SPREADER_LIVE_TELESCOPE_OP_SUFFIX).Set(Gf.Vec3d(0,sign*SPREADER_TELESCOPE_TRAVEL_M,0))
        temp=Sdf.Layer.CreateAnonymous('task_spreader.usda');self.s.GetSessionLayer().subLayerPaths.insert(0,temp.identifier)
        with Usd.EditContext(self.s,temp):rtg=VisualRTG(self.s,20)
        for a,b in zip(endpoints(),expected):self.assertLess((a-b).GetLength(),1e-5)
        rtg.close()

if __name__=='__main__':unittest.main()
