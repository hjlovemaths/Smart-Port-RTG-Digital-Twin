import sys,unittest,math
from pathlib import Path
from pxr import Gf,Sdf,Usd,UsdGeom
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'omniverse/scripts'));sys.path.insert(0,str(ROOT/'omniverse/extensions/smartport.agent_panel'))
from ship_transfer_plan import plan_transfer,box,cargo_bounds
from ship_to_yard_transfer import ShipToYardTransfer,active_task
from sts_control import STSController
class TransferTests(unittest.TestCase):
 def setUp(self):
  self.s=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'));self.c=None;self.t=None
 def tearDown(self):
  if self.t:self.t.close()
  if self.c:self.c.close()
 def test_preview_is_read_only_and_resolves_semantics_and_40ft_support(self):
  before=(self.s.GetRootLayer().ExportToString(),self.s.GetSessionLayer().ExportToString());p=plan_transfer(self.s)
  self.assertEqual(p['source_location'],'011090');self.assertEqual(p['source_row'],10)
  self.assertEqual(p['target_bay'],'3C/001');self.assertEqual(p['support_bay'],'3C/002');self.assertEqual(p['target_tier'],2)
  self.assertFalse(p['display_normalization_required']);self.assertEqual(before,(self.s.GetRootLayer().ExportToString(),self.s.GetSessionLayer().ExportToString()))
  with self.assertRaises(ValueError):plan_transfer(self.s,source_bay=99)
 def test_all_ship_color_prototypes_keep_actual_yard_dimensions(self):
  from large_vessel_layout import SHIP
  yard=Usd.Stage.Open(str(ROOT/'omniverse/scenes/live_containers.usda'))
  from ship_yard_container_models import PAINTS
  for feet in (20,40):
   for paint in PAINTS:
    prim=self.s.GetPrimAtPath(SHIP+f'/YardContainerPrototypes/C{feet}_{paint}')
    with Usd.EditContext(self.s,self.s.GetSessionLayer()):
     probe=UsdGeom.Xform.Define(self.s,'/World/NativeBoxProbe').GetPrim();probe.GetReferences().ClearReferences();probe.GetReferences().AddInternalReference(prim.GetPath())
    actual=box(self.s,probe.GetPath()).GetSize()
    expected=cargo_bounds(yard,yard.GetPrimAtPath(prim.GetAttribute('ship:yardSourcePrim').Get())).GetSize()
    self.assertLess((actual-expected).GetLength(),1e-5)
  with Usd.EditContext(self.s,self.s.GetSessionLayer()):self.s.RemovePrim('/World/NativeBoxProbe')
 def test_entire_transfer_has_continuous_cargo_and_correct_support_and_reset(self):
  p=plan_transfer(self.s);self.c=STSController(self.s,p['crane_id']);before=self.s.GetSessionLayer().ExportToString()
  source_initial=box(self.s,p['source_path']).GetMidpoint();self.t=ShipToYardTransfer(self.s,self.c,p,False)
  original_dims=Gf.Vec3d(p['cargo_dimensions_m']);last=source_initial;phases=set()
  for i in range(12000):
   self.t.tick(.1);b=box(self.s,p['source_path']);center=b.GetMidpoint();phases.add(self.t.phase)
   self.assertLess((center-last).GetLength(),1.2,'cargo must follow axes/vehicle without teleport')
   matrix=UsdGeom.XformCache().GetLocalToWorldTransform(self.s.GetPrimAtPath(p['source_path']))
   self.assertAlmostEqual(abs(matrix.GetDeterminant()),1.,places=5,msg='cargo must not be scaled in transit')
   if self.t.phase=='停车场集卡出车':self.assertLess((center-source_initial).GetLength(),1e-5)
   last=center
   if i==50:
    self.t.pause();snapshot=(dict(self.c.state),self.t.truck_point,center)
    self.t.tick(10);self.assertEqual(dict(self.c.state),snapshot[0]);self.assertEqual(self.t.truck_point,snapshot[1])
    with self.assertRaises(ValueError):self.c.command(trolley=2)
    self.t.resume()
   if i==120:
    self.c.stop();self.t.tick(.1);self.assertTrue(self.t.paused)
    with self.assertRaises(ValueError):self.t.resume()
    self.c.resume();self.t.resume()
   if self.t.finished:break
  self.assertTrue(self.t.finished,self.t.summary())
  self.assertIn('岸桥对位闭锁',phases);self.assertIn('RTG对位闭锁',phases);self.assertIn('集卡经引桥运往堆场',phases)
  final=box(self.s,p['source_path']);self.assertLess((final.GetMidpoint()-Gf.Vec3d(p['target_center_m'])).GetLength(),1e-5)
  self.assertAlmostEqual(final.GetMin()[2],p['support_bounds'][1][2]+.005,places=5)
  self.assertLess((final.GetSize()-original_dims).GetLength(),1e-5)
  self.t.close();self.assertEqual(before,self.s.GetSessionLayer().ExportToString());self.assertIsNone(active_task(self.s))
 def test_full_stack_and_neighbor_obstruction_are_rejected(self):
  p=plan_transfer(self.s);support=self.s.GetPrimAtPath(p['support_path'])
  with Usd.EditContext(self.s,self.s.GetSessionLayer()):support.GetAttribute('live:tier').Set(6)
  with self.assertRaises(ValueError):plan_transfer(self.s)
  with Usd.EditContext(self.s,self.s.GetSessionLayer()):
   support.GetAttribute('live:tier').Clear()
   block=UsdGeom.Cube.Define(self.s,str(support.GetParent().GetPath())+'/TransferObstacle')
   block.CreateSizeAttr(1);block.AddTranslateOp().Set(Gf.Vec3d(p['target_center_m']))
   block.GetPrim().CreateAttribute('live:containerSizeFt',Sdf.ValueTypeNames.Int).Set(20)
   block.GetPrim().CreateAttribute('live:bayId',Sdf.ValueTypeNames.String).Set('3C/002')
  with self.assertRaisesRegex(ValueError,'冲突'):plan_transfer(self.s)

 def test_agent_adapter_plan_id_start_status_and_reset(self):
  from smartport.agent_panel.scene_runtime import SceneRuntime
  owner=SceneRuntime(ROOT)
  try:
   preview=owner.execute(self.s,'preview_ship_to_yard_transfer',{'source_bay':1,'source_side':'port','row_from_side':1,'target_bay':'3c1','target_row':6})
   with self.assertRaises(ValueError):owner.execute(self.s,'start_ship_to_yard_transfer',{'plan_id':'wrong'})
   result=owner.execute(self.s,'start_ship_to_yard_transfer',{'plan_id':preview['plan_id']})
   self.assertEqual(result['status'],'running');self.t=owner.transfer_task
   with self.assertRaises(ValueError):owner._guard_manual('rtg')
   owner.execute(self.s,'control_ship_to_yard_transfer',{'action':'pause'});self.assertTrue(self.t.paused)
   owner.execute(self.s,'control_ship_to_yard_transfer',{'action':'resume'});self.assertFalse(self.t.paused)
   owner.execute(self.s,'control_ship_to_yard_transfer',{'action':'reset'});self.assertTrue(self.t.closed)
  finally:owner.release()
if __name__=='__main__':unittest.main()
