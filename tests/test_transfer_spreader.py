"""Regression: strongest manual session opinions must not defeat task sizing."""
import sys,unittest
from pathlib import Path
from pxr import Gf,Sdf,Usd,UsdGeom
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'omniverse/scripts'))
from rtg_transfer_motion import VisualRTG,read_spreader_size_ft
from rtg_live_control import HOIST_PATH,SPREADER_TELESCOPE_TRAVEL_M,SPREADER_LIVE_TELESCOPE_OP_SUFFIX
from yard_to_ship_plan import plan_yard_to_ship
from yard_to_ship_transfer import YardToShipTransfer
from sts_control import STSController
from ship_transfer_plan import box

class TransferSpreaderTests(unittest.TestCase):
    def setUp(self):
        self.s=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'));self.r=None;self.t=None;self.c=None
        self.layer=Sdf.Layer.CreateAnonymous('task_spreader_test.usda')
    def tearDown(self):
        if self.t:self.t.close()
        if self.r:self.r.close()
        if self.c:self.c.close()
    def manual_size(self,feet):
        with Usd.EditContext(self.s,self.s.GetSessionLayer()):
            for side,sign in (('FRONT',1),('REAR',-1)):
                p=self.s.GetPrimAtPath(HOIST_PATH+'/ANIM_CTRL_SPREADER_TELESCOPE_'+side)
                UsdGeom.Xformable(p).AddTranslateOp(opSuffix=SPREADER_LIVE_TELESCOPE_OP_SUFFIX).Set(Gf.Vec3d(0,sign*SPREADER_TELESCOPE_TRAVEL_M if feet==20 else 0,0))
            self.s.OverridePrim('/KeepUserSessionEdit').CreateAttribute('user:keep',Sdf.ValueTypeNames.String).Set('preserve')
    def span(self):
        cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']);ys=[]
        for p in Usd.PrimRange(self.s.GetPrimAtPath(HOIST_PATH)):
            if p.IsA(UsdGeom.Boundable) and 'red_corner_lock_foot_' in str(p.GetPath()):ys.append(cache.ComputeWorldBound(p).ComputeAlignedBox().GetMidpoint()[1])
        return max(ys)-min(ys)
    def check_transition(self,start,target):
        self.manual_size(start);before=self.s.GetSessionLayer().ExportToString();original_span=self.span()
        self.s.GetSessionLayer().subLayerPaths.insert(0,self.layer.identifier)
        with Usd.EditContext(self.s,self.layer):self.r=VisualRTG(self.s,target)
        self.assertAlmostEqual(self.span(),original_span,places=5,msg='No snap at task construction')
        self.assertAlmostEqual(self.r.current_size_ft,start)
        self.assertEqual(self.r.spreader_ready,start==target)
        last=self.span()
        for _ in range(40):
            with Usd.EditContext(self.s,self.layer):self.r.tick_spreader(.1)
            now=self.span();self.assertLessEqual(abs(now-last),.12001);last=now
            if self.r.spreader_ready:break
        self.assertTrue(self.r.spreader_ready)
        self.assertAlmostEqual(read_spreader_size_ft(self.s),target)
        delta=(2*SPREADER_TELESCOPE_TRAVEL_M)*(target-start)/20
        self.assertAlmostEqual(self.span(),original_span+delta,places=4)
        self.r.close();self.s.GetSessionLayer().subLayerPaths.remove(self.layer.identifier)
        self.assertEqual(self.s.GetSessionLayer().ExportToString(),before)
    def test_strongest_manual_40_to_20_moves_actual_lock_feet(self):self.check_transition(40,20)
    def test_strongest_manual_20_to_40_moves_actual_lock_feet(self):self.check_transition(20,40)
    def test_already_20_does_not_telescope_twice(self):self.check_transition(20,20)
    def test_task_waits_for_size_before_moving_or_closing_locks(self):
        self.manual_size(40)
        p=plan_yard_to_ship(self.s,'3C/005',2,'011090')
        self.assertEqual(p['size_ft'],20);self.assertTrue(p['spreader_adjustment']['required'])
        self.c=STSController(self.s,p['crane_id']);before=self.s.GetSessionLayer().ExportToString()
        self.t=YardToShipTransfer(self.s,self.c,p,False)
        self.assertIn('20尺',self.t.phase)
        source=box(self.s,self.t.cargo.GetPath()).GetMidpoint();truck=Gf.Vec3d(self.t.truck_point);sts=dict(self.c.state)
        with self.assertRaises(ValueError):
            with Usd.EditContext(self.s,self.t.layer):self.t.lock('rtg')
        self.t.tick(.1)
        self.assertEqual(self.c.state,sts);self.assertEqual(self.t.truck_point,truck)
        self.assertEqual(box(self.s,self.t.cargo.GetPath()).GetMidpoint(),source)
        self.assertEqual(self.t.rtg.offset,[0,0,0])
        for _ in range(40):
            self.t.tick(.1)
            if not self.t.spreader_preparing:break
        self.assertTrue(self.t.rtg.spreader_ready)
        self.assertEqual(self.t.phase,'停车场集卡前往堆场')
        self.t.close();self.assertEqual(self.s.GetSessionLayer().ExportToString(),before)

    def test_manual_axis_orders_cannot_override_task_motion_and_reset_is_exact(self):
        from rtg_live_control import GANTRY_PATH,TROLLEY_PATH,ROPE_SYSTEM_PATH
        self.manual_size(40)
        with Usd.EditContext(self.s,self.s.GetSessionLayer()):
            for path in (GANTRY_PATH,TROLLEY_PATH,HOIST_PATH):
                xf=UsdGeom.Xformable(self.s.GetPrimAtPath(path));xf.SetXformOpOrder(xf.GetOrderedXformOps())
            rope=UsdGeom.BasisCurves(self.s.GetPrimAtPath(ROPE_SYSTEM_PATH))
            rope.GetPointsAttr().Set(rope.GetPointsAttr().Get())
        before=self.s.GetSessionLayer().ExportToString()
        self.s.GetSessionLayer().subLayerPaths.insert(0,self.layer.identifier)
        with Usd.EditContext(self.s,self.layer):
            self.r=VisualRTG(self.s,40);origin=self.r.contact()
            self.r.command(origin+Gf.Vec3d(0,1,0));self.r.tick(.1);self.r.tick(.1)
        self.assertLess((self.r.contact()-origin-Gf.Vec3d(0,1,0)).GetLength(),1e-5)
        self.r.close();self.s.GetSessionLayer().subLayerPaths.remove(self.layer.identifier)
        self.assertEqual(self.s.GetSessionLayer().ExportToString(),before)

if __name__=='__main__':unittest.main()
