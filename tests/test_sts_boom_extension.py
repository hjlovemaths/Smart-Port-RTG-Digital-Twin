import sys,unittest
from pathlib import Path
from pxr import Gf,Usd,UsdGeom
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'omniverse/scripts'))
from sts_control import STSController,stay_endpoints
from build_sts_boom_extension import OUTPUT
class BoomExtensionTest(unittest.TestCase):
 def setUp(self):
  self.s=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))
 def test_geometry_and_untouched_cranes(self):
  old=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'));old.MuteLayer(str(OUTPUT))
  for n in range(1,8):
   path=f'/World/STSControls/STS_{n:02d}/Gantry/'
   for part in ('Fixed','Trolley/Hoist'):
    a=UsdGeom.XformCache().GetLocalToWorldTransform(self.s.GetPrimAtPath(path+part))
    b=UsdGeom.XformCache().GetLocalToWorldTransform(old.GetPrimAtPath(path+part))
    self.assertEqual(a,b)
   name='Boom/SMART_PORT_quay_sts_crane_0_trolley_rail__10_data'
   def box(s):return UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render']).ComputeWorldBound(s.GetPrimAtPath(path+name)).ComputeAlignedRange()
   a,b=box(self.s),box(old)
   self.assertAlmostEqual(a.GetSize()[0]/b.GetSize()[0],2.5,places=5)
   self.assertAlmostEqual(a.GetSize()[2],b.GetSize()[2],places=5)
   self.assertLess(a.GetMin()[0],-283.1)
 def test_limits_interlocks_and_stays(self):
  c=STSController(self.s,'STS_01')
  try:
   c.command(trolley=40)
   with self.assertRaises(ValueError):c.command(boom=60)
   with self.assertRaises(ValueError):c.command(trolley=44)
   for angle in (0,60,80):
    c.state.update(boom=angle,trolley=-8,hoist=c.park_hoist);c.targets=dict(c.state);c._apply()
    ends=stay_endpoints(angle,c.fixed_width_scale,c.fixed_width_anchor,c.upper_offset,c.tower_offset,c.boom_scale)
    for i,(a,b) in enumerate(ends,4):
     m=UsdGeom.Xformable(self.s.GetPrimAtPath(c.path+f'/Gantry/DynamicCables/Cable_{i}')).GetLocalTransformation()
     self.assertLess((m.Transform(Gf.Vec3d(0,0,.5))-b).GetLength(),1e-6)
   c.stop()
   with self.assertRaises(ValueError):c.command(trolley=0)
  finally:c.close()
if __name__=='__main__':unittest.main()
