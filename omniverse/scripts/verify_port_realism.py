"""Check composed bindings, UVs and referenced texture assets with USD Python."""
from pathlib import Path
from pxr import Sdf, Usd, UsdGeom, UsdShade, UsdUtils

ROOT=Path(__file__).resolve().parents[1]
entry=ROOT/'scenes/smart_port.usda'
stage=Usd.Stage.Open(str(entry))
assert not stage.GetCompositionErrors(), stage.GetCompositionErrors()
layer=Sdf.Layer.FindOrOpen(str(ROOT/'scenes/port_realism.usda'))
assert stage.GetRootLayer().subLayerPaths.count('./port_realism.usda')==1
for path in ['/World/SMART_PORT_GROUND_COLOR_OVERRIDE/SMART_PORT_GROUND_COLOR_OVERRIDE_MAIN_YARD',
             '/World/SMART_PORT_QUAY_YARD_TRAFFIC_LAYOUT/SMART_PORT_QUAY_YARD_TRAFFIC_ROAD_SURFACE']:
    prim=stage.GetPrimAtPath(path)
    mat,_=UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
    assert str(mat.GetPath())=='/World/PortRealism/Looks/asphalt', (path,mat.GetPath())
    assert len(UsdGeom.PrimvarsAPI(prim).GetPrimvar('st').Get())==len(UsdGeom.Mesh(prim).GetPointsAttr().Get())
for prim in Usd.PrimRange(stage.GetPrimAtPath('/World/PortRealism')):
    if prim.IsA(UsdGeom.Mesh):
        mesh=UsdGeom.Mesh(prim)
        pts=mesh.GetPointsAttr().Get()
        assert max(mesh.GetFaceVertexIndicesAttr().Get())<len(pts)
        assert not prim.HasAPI('PhysicsCollisionAPI')
    asset=prim.GetAttribute('inputs:file')
    if asset:
        assert Path(asset.Get().resolvedPath).is_file(), asset.Get()
_,_,unresolved=UsdUtils.ComputeAllDependencies(str(ROOT/'scenes/port_realism.usda'))
assert not unresolved, unresolved
print('PASS: composed stage, effective materials, UVs, mesh indices, texture assets and visual-only geometry')
