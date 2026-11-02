"""Set all STS spreader lower faces to a 24 m idle elevation."""
from pathlib import Path
from pxr import Usd,UsdGeom,Sdf,Gf
from build_sts_dimensions import prepend,bounds
from sts_control import INITIAL,rope_endpoints,cylinder_matrix
ROOT=Path(__file__).resolve().parents[2]
OUTPUT=ROOT/'omniverse/scenes/sts_spreader_home.usda'
def build():
 s=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))
 if OUTPUT.exists():s.MuteLayer(str(OUTPUT))
 plans=[]
 for crane in s.GetPrimAtPath('/World/STSControls').GetChildren():
  path=str(crane.GetPath());hoist=s.GetPrimAtPath(path+'/Gantry/Trolley/Hoist')
  bottom=bounds(s,hoist.GetPath()).GetMin()[2]
  scale=UsdGeom.XformCache().GetLocalToWorldTransform(hoist.GetParent()).TransformDir(Gf.Vec3d(0,0,1))[2]
  plans.append((crane,hoist,(24-bottom)/scale))
 layer=Sdf.Layer.CreateAnonymous('sts_spreader_home.usda');s.GetSessionLayer().subLayerPaths.insert(0,layer.identifier)
 with Usd.EditContext(s,layer):
  for crane,hoist,delta in plans:
   crane.CreateAttribute('sts:raisedHomeOffset',Sdf.ValueTypeNames.Double).Set(delta)
   prepend(hoist,Gf.Matrix4d(1).SetTranslate(Gf.Vec3d(0,0,delta)),'stsRaisedHome')
   ends=rope_endpoints(INITIAL['trolley'],INITIAL['hoist']+delta,upper_offset=crane.GetAttribute('sts:upperOffset').Get())
   for i,(a,b) in enumerate(ends):
    cable=s.GetPrimAtPath(str(crane.GetPath())+f'/Gantry/DynamicCables/Cable_{i}')
    UsdGeom.Xformable(cable).GetOrderedXformOps()[0].Set(cylinder_matrix(a,b,.012))
 layer.Export(str(OUTPUT));print('Raised',len(plans),'spreaders to 24m')
if __name__=='__main__':build()
