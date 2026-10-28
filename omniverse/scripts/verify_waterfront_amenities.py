from pathlib import Path
from pxr import Usd,UsdGeom,UsdShade,UsdUtils
ROOT=Path(__file__).resolve().parents[2]
s=Usd.Stage.Open(str(ROOT/'omniverse/scenes/waterfront_amenities.usda'))
triangles=meshes=0
for p in s.Traverse():
 assert str(p.GetPath())=='/World' or str(p.GetPath()).startswith('/World/WaterfrontAmenities')
 assert not p.HasAPI('PhysicsCollisionAPI') and not p.GetTypeName().endswith('Light')
 if not p.IsA(UsdGeom.Mesh):continue
 meshes+=1;m=UsdGeom.Mesh(p);pts=m.GetPointsAttr().Get();ids=m.GetFaceVertexIndicesAttr().Get();counts=m.GetFaceVertexCountsAttr().Get()
 assert len(ids)==sum(counts) and 0<=min(ids) and max(ids)<len(pts)
 assert UsdShade.MaterialBindingAPI(p).ComputeBoundMaterial()[0]
 triangles+=sum(n-2 for n in counts)
 for x,y,z in pts:
  assert 125.5<x<150 and 9<y<430,(p.GetPath(),x,y)
  assert not any(a<y<b for a,b in ((118,134),(248,264),(378,395))),(p.GetPath(),y)
  if 65<=y<=118 or 160<=y<=213:assert x>147,(p.GetPath(),x,y)
  if 290<=y<=346:assert x>148,(p.GetPath(),x,y)
assert meshes<=55 and triangles<16000
assert not s.GetCompositionErrors() and not UsdUtils.ComputeAllDependencies(str(ROOT/'omniverse/scenes/waterfront_amenities.usda'))[2]
main=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))
assert not main.GetCompositionErrors();assert main.GetPrimAtPath('/World/WaterfrontAmenities/DriverServices')
assert main.GetRootLayer().subLayerPaths.count('./waterfront_amenities.usda')==1
print(f'PASS {meshes} meshes, {triangles} triangles; all vertices clear of collector, truck bays, safety reserve and crossroad/gate gaps; materials, composition and references valid.')

