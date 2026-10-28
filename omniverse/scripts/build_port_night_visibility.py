"""Night-only ambient fill, recessed shore lights and work lights on static RTGs."""
from pathlib import Path
import json
from pxr import Gf,Sdf,Usd,UsdGeom,UsdLux,UsdShade
import smart_port_environment as w
import build_port_service_areas as g
import build_rtg_led_floodlights as led

ROOT=Path(__file__).resolve().parents[2]
LAYER=ROOT/'omniverse/scenes/port_night_visibility.usda'
BASE=w.NIGHT_STREET_LIGHTS_PATH+'/PortNightVisibility'
HARDWARE='/World/PortNightHardware'

def light(stage,path,pos,power,temp=4000,radius=.3):
    lamp=UsdLux.DiskLight.Define(stage,path)
    UsdGeom.Xformable(lamp).AddTranslateOp().Set(Gf.Vec3d(*pos))
    lamp.CreateIntensityAttr(power);lamp.CreateExposureAttr(0.);lamp.CreateNormalizeAttr(True);lamp.CreateRadiusAttr(radius)
    lamp.CreateEnableColorTemperatureAttr(True);lamp.CreateColorTemperatureAttr(temp)
    shape=UsdLux.ShapingAPI.Apply(lamp.GetPrim());shape.CreateShapingConeAngleAttr(78);shape.CreateShapingConeSoftnessAttr(.8)
    UsdLux.ShadowAPI.Apply(lamp.GetPrim()).CreateShadowEnableAttr(False)
    return lamp

def material(stage,path,color,emission=None,metal=0):
    m=UsdShade.Material.Define(stage,path);sh=UsdShade.Shader.Define(stage,path+'/Surface');sh.CreateIdAttr('UsdPreviewSurface')
    sh.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color));sh.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(.55)
    sh.CreateInput('metallic',Sdf.ValueTypeNames.Float).Set(metal)
    if emission:sh.CreateInput('emissiveColor',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*emission))
    m.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(),'surface');return m

def build():
    source=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))
    s=Usd.Stage.CreateInMemory();UsdGeom.SetStageUpAxis(s,'Z');UsdGeom.SetStageMetersPerUnit(s,1)
    UsdGeom.Xform.Define(s,BASE);UsdGeom.Xform.Define(s,HARDWARE)
    # RTX uses the existing procedural dome for the environment. A second
    # dome may be ignored, so use a weak, shadow-free directional fill.
    ambient=UsdLux.DistantLight.Define(s,BASE+'/NightFill')
    ambient.CreateIntensityAttr(350);ambient.CreateColorAttr(Gf.Vec3f(.45,.52,.65));ambient.CreateSpecularAttr(.15)
    ambient.CreateAngleAttr(45.)
    UsdGeom.Xformable(ambient).AddRotateXYZOp().Set(Gf.Vec3f(25,-25,0))
    ambient.GetPrim().CreateAttribute('visibleInPrimaryRay',Sdf.ValueTypeNames.Bool).Set(False)
    UsdLux.ShadowAPI.Apply(ambient.GetPrim()).CreateShadowEnableAttr(False)
    lensmat=material(s,BASE+'/Looks/WarmLens',(.8,.65,.4),(5.,3.5,1.8))
    steel=material(s,HARDWARE+'/Looks/Steel',(.08,.09,.10),metal=.7)
    g.DATA.clear();g.SOLIDS.clear()
    shore=[]
    # Flush housings on the verges, clear of the quay's vehicle lanes.
    for row,x,segments in [('Outer',149.0,((10,118),(134,248),(264,378),(395,490))),('Quay',-241.25,((10,490),))]:
        ground=.183 if row=='Quay' else .125
        for a,b in segments:
            for k,y in enumerate(range(a,b,12)):
                g.cyl('Shore','Steel',x,y,ground-.022,.19,.025,12)
                g.cyl('Shore','Lens',x,y,ground+.004,.135,.004,12)
                shore.append((x,y))
                # Every third fixture has a modest real grazing light. The
                # remaining visible lenses are batched emissive geometry.
                if k%3==0:
                    lamp=light(s,BASE+f'/Shore/{row}_{y}',(x,y,ground+.025),1500,3000,.14)
                    UsdGeom.Xformable(lamp).AddRotateXYZOp().Set(Gf.Vec3f(0,180,0))
    cache=UsdGeom.BBoxCache(0,['default','render']);rtgs=[]
    for prim in source.GetPrimAtPath('/World/PortAndRTG').GetChildren():
        if not prim.GetName().startswith('SMART_PORT_STATIC_RTG_YARD'):continue
        beams=[]
        for part in Usd.PrimRange(prim,Usd.TraverseInstanceProxies()):
            if part.GetTypeName()=='Mesh' and 'twin_main_girder_upper_beam' in part.GetName():
                beams.append(cache.ComputeWorldBound(part).ComputeAlignedRange())
        assert len(beams)==2,(prim.GetPath(),len(beams))
        beams.sort(key=lambda b:b.GetMin()[1])
        for side,bounds in enumerate(beams):
            lo,hi=bounds.GetMin(),bounds.GetMax();y=(lo[1]+hi[1])/2
            for i,fraction in enumerate(led.LED_WORLD_FRACTIONS):
                x=lo[0]+fraction*(hi[0]-lo[0]);name=f'{prim.GetName()}_{side}_{i}'
                fixture=UsdGeom.Xform.Define(s,HARDWARE+'/RTG/'+name)
                fixture.GetPrim().GetReferences().AddReference(str(ROOT/'omniverse/assets/resource/led/RTG_LED_Floodlight.usdc'))
                fixture.GetPrim().SetInstanceable(True)
                xf=UsdGeom.Xformable(fixture);xf.AddTranslateOp().Set(Gf.Vec3d(x,y,led.LED_TRANSLATE_Z_M))
                xf.AddRotateXYZOp().Set(Gf.Vec3f(*(led.NEAR_LED_ROTATION if side==0 else led.FAR_LED_ROTATION)))
                xf.AddScaleOp().Set(Gf.Vec3f(*led.LED_SCALE))
                lamp=light(s,BASE+'/RTG/'+name,(x,y+(.24 if side==0 else -.24),7.53),65000,4200)
                UsdGeom.Xformable(lamp).AddRotateXYZOp().Set(Gf.Vec3f(*(led.NEAR_LIGHT_ROTATION if side==0 else led.FAR_LIGHT_ROTATION)))
        rtgs.append(str(prim.GetPath()))
    for (site,mat),(points,counts,indices) in g.DATA.items():
        path=(BASE if mat=='Lens' else HARDWARE)+'/'+site+'/'+mat
        mesh=UsdGeom.Mesh.Define(s,path);mesh.CreatePointsAttr(points);mesh.CreateFaceVertexCountsAttr(counts);mesh.CreateFaceVertexIndicesAttr(indices);mesh.CreateSubdivisionSchemeAttr('none')
        mesh.CreateExtentAttr([Gf.Vec3f(*(min(p[j] for p in points) for j in range(3))),Gf.Vec3f(*(max(p[j] for p in points) for j in range(3)))])
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(lensmat if mat=='Lens' else steel)
    # Lift the yard pools half a stop while retaining their soft downward aim.
    for prim in Usd.PrimRange(source.GetPrimAtPath(w.NIGHT_STREET_LIGHTS_PATH)):
        if prim.GetTypeName()=='DiskLight' and '/Supplemental/' not in str(prim.GetPath()) and '/PortNightVisibility/' not in str(prim.GetPath()):
            over=s.OverridePrim(prim.GetPath())
            for name in ('inputs:exposure','smartPort:clearNightExposure'):over.CreateAttribute(name,Sdf.ValueTypeNames.Float).Set(.8)
    s.GetRootLayer().Export(str(LAYER))
    # Resolve the asset during construction, then keep the exported layer portable.
    asset=(ROOT/'omniverse/assets/resource/led/RTG_LED_Floodlight.usdc').as_posix()
    text=LAYER.read_text().replace(asset,'../assets/resource/led/RTG_LED_Floodlight.usdc').replace(asset.replace('/','\\'),'../assets/resource/led/RTG_LED_Floodlight.usdc')
    LAYER.write_text(text)
    report={'recessed_fixtures':len(shore),'new_rtg_count':len(rtgs),'rtg_work_lights':len(rtgs)*8,'rtgs':rtgs,'real_lights':sum(p.GetTypeName().endswith('Light') for p in s.Traverse())}
    out=ROOT/'outputs/port_night_visibility';out.mkdir(parents=True,exist_ok=True);(out/'build.json').write_text(json.dumps(report,indent=2));print(report)
if __name__=='__main__':build()
