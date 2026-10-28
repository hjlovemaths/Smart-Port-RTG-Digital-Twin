"""Ship-deck floodlights attached to each controlled STS moving assembly."""
from pathlib import Path
import json
from pxr import Gf,Sdf,Usd,UsdGeom,UsdLux,UsdShade
from build_port_night_visibility import material

ROOT=Path(__file__).resolve().parents[2]
LAYER=ROOT/'omniverse/scenes/sts_work_lighting.usda'
GROUP='NightWorkLights'

def build():
    source=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))
    if LAYER.exists():source.MuteLayer(str(LAYER))
    stage=Usd.Stage.CreateInMemory();UsdGeom.SetStageUpAxis(stage,'Z');UsdGeom.SetStageMetersPerUnit(stage,1)
    cache=UsdGeom.BBoxCache(0,['default','render']);xf=UsdGeom.XformCache()
    ships=[cache.ComputeWorldBound(p).ComputeAlignedRange() for p in source.GetPrimAtPath('/World/PortAndRTG').GetChildren() if 'SHIP' in p.GetName()]
    steel=material(stage,'/World/STSWorkLightingLooks/Housing',(.06,.07,.08),metal=.6)
    lens=material(stage,'/World/STSWorkLightingLooks/Lens',(.9,.95,1),(12,13,14))
    placements=[]
    for crane in source.GetPrimAtPath('/World/STSControls').GetChildren():
        path=str(crane.GetPath());boom=source.GetPrimAtPath(path+'/Gantry/Boom')
        bounds=cache.ComputeWorldBound(boom).ComputeAlignedRange();lo,hi=bounds.GetMin(),bounds.GetMax();cy=(lo[1]+hi[1])/2
        ship=min(ships,key=lambda b:abs(b.GetMidpoint()[1]-cy));sx=ship.GetMidpoint()[0];deck=ship.GetMax()[2]
        specs=[]
        for i,f in enumerate((.2,.5,.8)):
            for side in (-1,1):
                origin=Gf.Vec3d(lo[0]+f*(hi[0]-lo[0]),cy+side*6.8,lo[2]-.22)
                target=Gf.Vec3d(sx+(f-.5)*8,max(ship.GetMin()[1]+4,min(ship.GetMax()[1]-4,cy+side*17)),deck-1)
                specs.append(('Boom',f'Deck_{i}_{side+1}',origin,target,1500000.,52.))
        for side in (-1,1):
            specs.append(('Fixed',f'Quay_{side+1}',Gf.Vec3d(-243.2,cy+side*6.8,lo[2]-.25),Gf.Vec3d(-230,cy+side*10,.2),550000.,48.))
        trolley=source.GetPrimAtPath(path+'/Gantry/Trolley');tb=cache.ComputeWorldBound(trolley).ComputeAlignedRange()
        origin=Gf.Vec3d(tb.GetMidpoint()[0],tb.GetMidpoint()[1],tb.GetMax()[2]-.7)
        specs.append(('Trolley','Pickup',origin,Gf.Vec3d(origin[0],origin[1],deck),950000.,40.))
        for assembly in ('Fixed','Boom','Trolley'):
            parent=source.GetPrimAtPath(path+'/Gantry/'+assembly)
            # Cancel only the build-time placement. Subsequent gantry, luffing
            # and trolley deltas still move the mounted fixtures and emitters.
            for group in (GROUP,'WorkLightHardware'):
                root=UsdGeom.Xform.Define(stage,str(parent.GetPath())+'/'+group)
                root.AddTransformOp().Set(xf.GetLocalToWorldTransform(parent).GetInverse())
                if group==GROUP:root.CreateVisibilityAttr('invisible')
        for assembly,name,origin,target,power,cone in specs:
            parent=path+'/Gantry/'+assembly
            orient=Gf.Rotation(Gf.Vec3d(0,0,-1),(target-origin).GetNormalized()).GetQuat()
            def fixture(path):
                p=UsdGeom.Xform.Define(stage,path);p.AddTranslateOp().Set(origin);p.AddOrientOp(UsdGeom.XformOp.PrecisionDouble).Set(orient);return p
            fixture(parent+'/WorkLightHardware/'+name)
            housing=UsdGeom.Cube.Define(stage,parent+'/WorkLightHardware/'+name+'/Housing');housing.CreateSizeAttr(1);housing.AddScaleOp().Set(Gf.Vec3f(.75,.48,.20));UsdShade.MaterialBindingAPI.Apply(housing.GetPrim()).Bind(steel)
            fixture(parent+'/'+GROUP+'/'+name)
            panel=UsdGeom.Cube.Define(stage,parent+'/'+GROUP+'/'+name+'/Lens');panel.CreateSizeAttr(1);panel.AddTranslateOp().Set(Gf.Vec3d(0,0,-.105));panel.AddScaleOp().Set(Gf.Vec3f(.62,.36,.012));UsdShade.MaterialBindingAPI.Apply(panel.GetPrim()).Bind(lens)
            lamp=UsdLux.DiskLight.Define(stage,parent+'/'+GROUP+'/'+name+'/Flood')
            lamp.AddTranslateOp().Set(Gf.Vec3d(0,0,-.13));lamp.CreateIntensityAttr(power);lamp.CreateNormalizeAttr(True);lamp.CreateRadiusAttr(.5);lamp.CreateExposureAttr(0)
            lamp.CreateEnableColorTemperatureAttr(True);lamp.CreateColorTemperatureAttr(4300)
            shaping=UsdLux.ShapingAPI.Apply(lamp.GetPrim());shaping.CreateShapingConeAngleAttr(cone);shaping.CreateShapingConeSoftnessAttr(.8)
            UsdLux.ShadowAPI.Apply(lamp.GetPrim()).CreateShadowEnableAttr(False)
            lamp.GetPrim().CreateAttribute('smartPort:workTargetWorld',Sdf.ValueTypeNames.Double3).Set(target)
            placements.append({'crane':crane.GetName(),'assembly':assembly,'light':str(lamp.GetPath()),'origin':list(origin),'target':list(target)})
    stage.GetRootLayer().Export(str(LAYER));out=ROOT/'outputs/sts_work_lighting';out.mkdir(parents=True,exist_ok=True)
    (out/'build.json').write_text(json.dumps({'cranes':7,'lights':len(placements),'placements':placements},indent=2));print('Built',len(placements),'work lights on 7 STS cranes')
if __name__=='__main__':build()
