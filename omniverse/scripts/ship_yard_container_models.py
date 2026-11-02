"""Reuse the actual yard box parts and paint materials in the ship layer."""
from pxr import Gf,Sdf,Usd,UsdGeom,UsdShade
from large_vessel_layout import ROOT,SHIP

PAINTS=('weathered_blue','oxide_red','warm_gray','faded_teal','sand_yellow','dark_navy')
SOURCE_LOOKS='/World/LiveContainers/Looks'
DEST_LOOKS=SHIP+'/YardContainerLooks'


def create_yard_prototypes(stage):
    yard=Usd.Stage.Open(str(ROOT/'omniverse/scenes/live_containers.usda'))
    if yard is None:raise ValueError('Yard container source could not be loaded')
    Sdf.CopySpec(yard.GetRootLayer(),Sdf.Path(SOURCE_LOOKS),stage.GetRootLayer(),Sdf.Path(DEST_LOOKS))
    # Shader connections are absolute paths and must follow the copied scope.
    def remap(path):
        text=str(path)
        return Sdf.Path(DEST_LOOKS+text[len(SOURCE_LOOKS):]) if text.startswith(SOURCE_LOOKS+'/') else path
    for p in Usd.PrimRange.AllPrims(stage.GetPrimAtPath(DEST_LOOKS)):
        for a in p.GetAttributes():
            links=a.GetConnections()
            if links:a.SetConnections([remap(x) for x in links])
    result={}
    cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
    sources={}
    for p in yard.Traverse():
        if not p.IsA(UsdGeom.Cube) or not p.HasAttribute('live:containerSizeFt'):
            continue
        mat,_=UsdShade.MaterialBindingAPI(p).ComputeBoundMaterial()
        if mat:
            paint=mat.GetPrim().GetName().removeprefix('Container_')
            sources.setdefault((p.GetAttribute('live:containerSizeFt').Get(),paint),p)
    for feet in (20,40):
        for paint in PAINTS:
            source=sources.get((feet,paint))
            if not source:raise ValueError(f'No actual yard sample for {feet} ft / {paint}')
            parent=source.GetParent();prefix=source.GetName()
            parts=[p for p in parent.GetChildren() if p.GetName()==prefix or p.GetName().startswith(prefix+'_')]
            bounds=Gf.Range3d()
            for p in parts:bounds.UnionWith(cache.ComputeWorldBound(p).ComputeAlignedRange())
            centre=bounds.GetMidpoint();size=bounds.GetSize()
            scale=Gf.Vec3d(2.438/size[0],(6.058 if feet==20 else 12.192)/size[1],2.591/size[2])
            parent_world=UsdGeom.XformCache().GetLocalToWorldTransform(parent)
            matrix=(parent_world*Gf.Matrix4d(1).SetTranslate(-centre)*Gf.Matrix4d(1).SetScale(scale))
            path=SHIP+f'/YardContainerPrototypes/C{feet}_{paint}'
            proto=UsdGeom.Xform.Define(stage,path).GetPrim()
            proto.SetSpecifier(Sdf.SpecifierClass)
            proto.CreateAttribute('ship:yardSourcePrim',Sdf.ValueTypeNames.String,custom=True).Set(str(source.GetPath()))
            # A distinct render namespace prevents old per-color vertex buffers
            # being reused after replacing the original red sample in live Kit.
            relative_geometry='Geometry/Source_'+paint
            proto.CreateAttribute('ship:geometryPath',Sdf.ValueTypeNames.String,custom=True).Set(relative_geometry)
            geometry=UsdGeom.Xform.Define(stage,path+'/'+relative_geometry)
            geometry.AddTransformOp().Set(matrix)
            for part in parts:
                # Normalize the sample's inventory-specific prefix only.
                name='R01_T01_Container'+part.GetName()[len(prefix):]
                Sdf.CopySpec(yard.GetRootLayer(),part.GetPath(),stage.GetRootLayer(),Sdf.Path(path+'/'+relative_geometry+'/'+name))
            # Every part, including baked displayColor on corrugations and roof,
            # now comes from a source already painted in this exact yard color.
            for p in Usd.PrimRange.AllPrims(proto):
                for rel in p.GetRelationships():
                    targets=rel.GetTargets()
                    mapped=[Sdf.Path(DEST_LOOKS+'/Container_'+paint) if str(t).startswith(SOURCE_LOOKS+'/') else t for t in targets]
                    if mapped!=targets:rel.SetTargets(mapped)
                for a in p.GetAttributes():
                    links=a.GetConnections()
                    if links:a.SetConnections([remap(x) for x in links])
            result[feet,paint]=path
    return result
