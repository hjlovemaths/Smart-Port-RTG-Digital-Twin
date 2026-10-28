"""Check the third pass while comparing ship geometry to the prior scene."""
from pathlib import Path
from pxr import Usd,UsdGeom,UsdShade

ROOT=Path(__file__).resolve().parents[1]
layer=ROOT/'scenes/port_finish_realism.usda'
s=Usd.Stage.Open(str(ROOT/'scenes/smart_port.usda'))
assert not s.GetCompositionErrors(),s.GetCompositionErrors()
ships=s.GetPrimAtPath('/World/PortFinishRealism').GetAttribute('finishedShips').Get()
assert len(ships)==6

def snapshot():
    cache=UsdGeom.XformCache()
    result={}
    for path in ships:
        root=s.GetPrimAtPath(path)
        result[path+'.instanceable']=(root.IsInstance(),root.IsInstanceable())
        for p in Usd.PrimRange(root,Usd.TraverseInstanceProxies()):
            if p.IsA(UsdGeom.Mesh):
                mesh=UsdGeom.Mesh(p)
                result[str(p.GetPath())]=(str(cache.GetLocalToWorldTransform(p)),str(mesh.GetPointsAttr().Get()),str(mesh.GetFaceVertexIndicesAttr().Get()))
    return result

current=snapshot()
prototypes={str(s.GetPrimAtPath(p).GetPrototype().GetPath()) for p in ships if s.GetPrimAtPath(p).IsInstance()}
assert len(prototypes)<=2,prototypes
uv_count=0
for p in Usd.PrimRange(s.GetPrimAtPath(ships[0]),Usd.TraverseInstanceProxies()):
    if not p.IsA(UsdGeom.Mesh):continue
    uv=UsdGeom.PrimvarsAPI(p).GetPrimvar('portHullUV')
    if uv:
        assert len(uv.Get())==len(UsdGeom.Mesh(p).GetPointsAttr().Get())
        mat,_=UsdShade.MaterialBindingAPI(p).ComputeBoundMaterial()
        assert '/PortFinish/Hull' in str(mat.GetPath()),mat.GetPath()
        uv_count+=1
assert uv_count>=5
assert s.GetPrimAtPath('/World/Environment/Looks/HarborWater/UVTransform').GetAttribute('inputs:translation')
for name in ('harbor_water_multiscale_normal.png','harbor_water_multiscale_roughness.png','port_hull_finish.png','port_tire_wear.png'):
    assert (ROOT/'materials/textures'/name).is_file()
s.MuteLayer(str(layer))
assert snapshot()==current,'Ship geometry or transforms changed'
assert not s.GetCompositionErrors()
print(f'PASS: composition, {len(current)-6} ship meshes unchanged, original instancing preserved ({len(prototypes)} shared variants), hull UVs/materials, water animation input and assets')
