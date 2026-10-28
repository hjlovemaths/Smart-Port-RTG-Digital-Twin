"""Check service meshes, clear traffic corridors, and unchanged scene transforms."""
from pathlib import Path
from pxr import Usd,UsdGeom,UsdShade,UsdUtils
ROOT=Path(__file__).resolve().parents[2]
path=ROOT/'omniverse/scenes/port_service_areas.usda'
s=Usd.Stage.Open(str(path)); base='/World/PortServiceAreas'
count=triangles=0
for p in s.Traverse():
 assert not p.HasAPI('PhysicsCollisionAPI')
 assert not p.GetTypeName().endswith('Light')
 if not p.IsA(UsdGeom.Mesh): continue
 count+=1; m=UsdGeom.Mesh(p); pts=m.GetPointsAttr().Get(); counts=m.GetFaceVertexCountsAttr().Get(); indices=m.GetFaceVertexIndicesAttr().Get()
 assert sum(counts)==len(indices) and min(indices)>=0 and max(indices)<len(pts)
 assert UsdShade.MaterialBindingAPI(p).ComputeBoundMaterial()[0]
 triangles+=sum(n-2 for n in counts)
 for x,y,z in pts:
  if '/Bay40Services/' in str(p.GetPath()):
   assert 454<y<492.6,(p.GetPath(),x,y)
   # Parcel occupies the right half of each original yard column only.
   assert any(cx+.35<x<cx+7.375 for cx in (-65,-44,-23,-2,19,40,61,82)),(p.GetPath(),x,y)
  else: assert 125.5<x<150 and 430<y<492.6,(p.GetPath(),x,y)
assert count<=32 and triangles<15000
assert not s.GetCompositionErrors()
assert not UsdUtils.ComputeAllDependencies(str(path))[2]
# New layer must never override operational objects or transform opinions.
for p in s.Traverse(): assert str(p.GetPath())=='/World' or str(p.GetPath()).startswith(base)
print(f'PASS: {count} meshes / {triangles} triangles; bound materials; zero unresolved assets; all vertices clear of existing road corridors and bay 41; no motion/physics/light edits.')
