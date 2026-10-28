"""Low-cost verge lighting for the outer collector, service apron and quay."""
from pathlib import Path
import json
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade
import build_port_service_areas as g
import smart_port_environment as w

ROOT = Path(__file__).resolve().parents[2]
BASE = '/World/SupplementalLighting'
LIGHTS = w.NIGHT_STREET_LIGHTS_PATH + '/Supplemental'
LAYER = ROOT/'omniverse/scenes/supplemental_night_lighting.usda'

def placements():
    # Just outside the collector; clear of truck slots, cross streets and buildings.
    for y in (12,42,72,102,144,174,204,232,274,304,334,365,413,443,479):
        yield 'Outer', 126.1, y, 9., -2., 3700., 65000.
    # Rear services: central buffer in front of each narrow service parcel.
    for x in (-61.2,-40.2,-19.2,1.8,22.8,43.8,64.8,85.8):
        yield 'Rear', x, 453., 7., 0., 3500., 42000.
    # Landward edge of quay collector, outside its 7 m clear width.
    for y in (18,58,98,150,190,230,280,320,360,412,452,488):
        yield 'Quay', -203.4, y, 10., -2., 4000., 85000.
    # Pedestrian apron edges; do not place supports in parking stalls or shelters.
    for x,y in ((147.8,35),(147.8,144),(147.8,232),(147.8,365),(147.8,413),(149.0,461)):
        yield 'Apron', x,y,6.,-1.5,3200.,32000.

def build():
    g.DATA.clear(); g.SOLIDS.clear()
    stage=Usd.Stage.CreateInMemory(); UsdGeom.SetStageUpAxis(stage,'Z'); UsdGeom.SetStageMetersPerUnit(stage,1)
    stage.OverridePrim('/World'); UsdGeom.Xform.Define(stage,BASE)
    UsdGeom.Xform.Define(stage,LIGHTS)
    items=[]
    for i,(site,x,y,h,arm,temp,power) in enumerate(placements()):
        g.box(site,'Concrete',x,y,.10,.65,.65,.18)
        g.cyl(site,'Steel',x,y,.28,.085,h-.28,8)
        g.box(site,'Steel',x+arm/2,y,h-.12,max(abs(arm),.3),.09,.12)
        g.box(site,'Roof',x+arm,y,h-.22,.65,.4,.15)
        path=LIGHTS+f'/{site}_{i:02d}'
        light=UsdLux.DiskLight.Define(stage,path)
        UsdGeom.Xformable(light).AddTranslateOp().Set(Gf.Vec3d(x+arm,y,h-.25))
        light.CreateRadiusAttr(.24); light.CreateIntensityAttr(power); light.CreateExposureAttr(1.3)
        light.GetPrim().CreateAttribute('smartPort:clearNightExposure',Sdf.ValueTypeNames.Float).Set(1.3)
        light.CreateEnableColorTemperatureAttr(True); light.CreateColorTemperatureAttr(temp)
        light.CreateNormalizeAttr(True)
        shape=UsdLux.ShapingAPI.Apply(light.GetPrim()); shape.CreateShapingConeAngleAttr(78.); shape.CreateShapingConeSoftnessAttr(.75)
        UsdLux.ShadowAPI.Apply(light.GetPrim()).CreateShadowEnableAttr(False)
        items.append({'site':site,'pole':[x,y,h],'light':path})
    for name,(color,rough,metal) in g.COLORS.items():
        m=UsdShade.Material.Define(stage,BASE+'/Looks/'+name); shader=UsdShade.Shader.Define(stage,str(m.GetPath())+'/Surface')
        shader.CreateIdAttr('UsdPreviewSurface'); shader.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(color)
        shader.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(rough); shader.CreateInput('metallic',Sdf.ValueTypeNames.Float).Set(metal)
        m.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(),'surface')
    for (site,mat),(points,counts,indices) in g.DATA.items():
        mesh=UsdGeom.Mesh.Define(stage,BASE+'/'+site+'/'+mat); mesh.CreatePointsAttr(points); mesh.CreateFaceVertexCountsAttr(counts); mesh.CreateFaceVertexIndicesAttr(indices); mesh.CreateSubdivisionSchemeAttr('none')
        mesh.CreateExtentAttr([Gf.Vec3f(*(min(p[j] for p in points) for j in range(3))),Gf.Vec3f(*(max(p[j] for p in points) for j in range(3)))])
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(UsdShade.Material(stage.GetPrimAtPath(BASE+'/Looks/'+mat)))
    # Match the sheltered apron reference: broad, downward warm pools instead
    # of beams aimed sideways across the adjacent container stacks.
    source=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))
    for prim in Usd.PrimRange(source.GetPrimAtPath(w.NIGHT_STREET_LIGHTS_PATH)):
        if prim.GetTypeName()=='DiskLight' and not str(prim.GetPath()).startswith(LIGHTS+'/'):
            override=stage.OverridePrim(prim.GetPath())
            override.CreateAttribute('inputs:exposure',Sdf.ValueTypeNames.Float).Set(.3)
            override.CreateAttribute('smartPort:clearNightExposure',Sdf.ValueTypeNames.Float).Set(.3)
            override.CreateAttribute('inputs:colorTemperature',Sdf.ValueTypeNames.Float).Set(3500.)
            override.CreateAttribute('inputs:color',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1))
            override.CreateAttribute('inputs:radius',Sdf.ValueTypeNames.Float).Set(.35)
            override.CreateAttribute('inputs:shaping:cone:angle',Sdf.ValueTypeNames.Float).Set(78.)
            override.CreateAttribute('inputs:shaping:cone:softness',Sdf.ValueTypeNames.Float).Set(.75)
            override.CreateAttribute('inputs:shaping:focus',Sdf.ValueTypeNames.Float).Set(0.)
            rotation=prim.GetAttribute('xformOp:rotateXYZ').Get()
            if rotation is not None:
                override.CreateAttribute('xformOp:rotateXYZ',Sdf.ValueTypeNames.Float3).Set(Gf.Vec3f(min(rotation[0],18.),rotation[1],rotation[2]))
    stage.GetRootLayer().Export(str(LAYER))
    out=ROOT/'outputs/supplemental_lighting';out.mkdir(parents=True,exist_ok=True)
    (out/'build.json').write_text(json.dumps({'lights':len(items),'meshes':len(g.DATA),'placements':items},indent=2))
    print('Built',len(items),'shadow-free lights',len(g.DATA),'batched meshes')

if __name__=='__main__': build()
