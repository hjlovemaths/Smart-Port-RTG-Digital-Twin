"""Validate the coastal layer and check that existing motion remains untouched."""
from pathlib import Path
from pxr import Usd,UsdGeom,UsdShade,UsdUtils

root=Path(__file__).resolve().parents[1]
layer=root/'scenes/port_structure_realism.usda'
s=Usd.Stage.Open(str(root/'scenes/smart_port.usda'))
assert not s.GetCompositionErrors(),s.GetCompositionErrors()
assert s.GetRootLayer().subLayerPaths.count('./port_structure_realism.usda')==1
base='/World/PortStructureRealism'
assert s.GetPrimAtPath(base).GetAttribute('adjustedShaderCount').Get()>50
for p in Usd.PrimRange(s.GetPrimAtPath(base)):
    assert not p.HasAPI('PhysicsCollisionAPI')
    if p.IsA(UsdGeom.Mesh):
        m=UsdGeom.Mesh(p)
        pts=m.GetPointsAttr().Get()
        assert max(m.GetFaceVertexIndicesAttr().Get())<len(pts)
        assert len(UsdGeom.PrimvarsAPI(p).GetPrimvar('st').Get())==len(pts)
        assert UsdShade.MaterialBindingAPI(p).ComputeBoundMaterial()[0]
b=UsdGeom.BBoxCache(0,['default','render'])
for name in ('Civil/RetainingWalls','Civil/BridgePiles'):
    r=b.ComputeWorldBound(s.GetPrimAtPath(base+'/'+name)).ComputeAlignedRange()
    assert r.GetMin()[2]<-4.9478 and r.GetMax()[2]>-1
panels=b.ComputeWorldBound(s.GetPrimAtPath(base+'/Berth/FenderPanels')).ComputeAlignedRange()
assert panels.GetMin()[0]>-241.9 and panels.GetMax()[0]<-241.5
assert panels.GetMin()[2]>-4.9478 and panels.GetMax()[2]<0
fender=b.ComputeWorldBound(s.GetPrimAtPath(base+'/Berth')).ComputeAlignedRange()
for p in s.GetPrimAtPath('/World/PortAndRTG').GetChildren():
    if 'SHIP' in p.GetName() and ('IMPORTED' in p.GetName() or 'REMOTE' in p.GetName()):
        ship=b.ComputeWorldBound(p).ComputeAlignedRange()
        assert fender.GetMin()[0]-ship.GetMax()[0]>.1, p.GetPath()
_,_,unresolved=UsdUtils.ComputeAllDependencies(str(layer))
assert not unresolved,unresolved

def motion_snapshot(stage):
    return {str(p.GetPath())+'.'+a.GetName():str(a.Get()) for p in stage.Traverse()
            if not str(p.GetPath()).startswith(base)
            for a in p.GetAttributes() if a.GetName().startswith(('xformOp','physics:','joint:'))}
active=motion_snapshot(s)
s.MuteLayer(str(layer))
assert motion_snapshot(s)==active,'Original motion or physics opinions changed'
print('PASS: USD composition, assets, UVs, bindings, submerged supports, fender bounds and unchanged motion/physics')
