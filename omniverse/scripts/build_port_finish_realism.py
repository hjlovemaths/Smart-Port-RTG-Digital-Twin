"""Third visual pass: sheltered water, shared ship finishes and road wear.

Generate textures using --textures (NumPy/Pillow), then build with USD Python.
Ship instances reference a shared finish wrapper; their geometry/motion stays intact.
"""
from pathlib import Path
import math
import random
import sys

ROOT=Path(__file__).resolve().parents[1]
LAYER=ROOT/'scenes/port_finish_realism.usda'
WRAPPER=ROOT/'scenes/ship_finish_asset.usda'
BASE='/World/PortFinishRealism'
SHIP='/World/PortAndRTG/SMART_PORT_4C_FEEDER_SHIP_LEFT_IMPORTED'


def textures():
    import numpy as np
    from PIL import Image
    out=ROOT/'materials/textures'
    rng=np.random.default_rng(381014)
    n=1024
    y,x=np.mgrid[:n,:n]*math.tau/n
    sx=np.zeros((n,n)); sy=sx.copy(); broad=sx.copy()
    for lo,hi,count,amp in ((3,7,10,.0025),(8,24,35,.006),(25,85,45,.002)):
        for _ in range(count):
            k=rng.integers(lo,hi); angle=rng.normal(.45,.7)
            kx,ky=round(k*math.cos(angle)),round(k*math.sin(angle))
            phase=kx*x+ky*y+rng.uniform(0,math.tau)
            sx+=amp*np.cos(phase)*kx/k
            sy+=amp*np.cos(phase)*ky/k
            broad+=np.sin(phase)/90
    normals=np.stack((-sx,-sy,np.ones_like(sx)),-1)
    normals/=np.linalg.norm(normals,axis=-1,keepdims=True)
    Image.fromarray(np.uint8(np.clip(normals*.5+.5,0,1)*255)).save(out/'port_calm_water_normal.png')
    Image.fromarray(np.uint8(np.clip(.22+.035*broad,.19,.26)*255)).save(out/'port_calm_water_roughness.png')

    # Work in linear space, then encode sRGB to preserve dark paint gradients.
    # V maps to the first ship's rest Z.
    # Flip for file coordinates: image top corresponds to UV v=1.
    y,x=np.mgrid[:512,:1024]
    z=-8+16*y/511
    fade=np.clip((z+4.7)/.4,0,1)
    topside=np.array([.019,.022,.021])
    oxide=np.array([.08,.021,.015])
    hull=oxide[None,None,:]*(1-fade[:,:,None])+topside[None,None,:]*fade[:,:,None]
    grain=rng.normal(0,.0003,(512,1024))
    streak=np.zeros((512,1024))
    for _ in range(45):
        cx=rng.uniform(0,1024)
        streak+=rng.uniform(.0005,.0018)*np.exp(-((x-cx)/rng.uniform(2,7))**2)*np.clip((z+4.5)/5,0,1)
    hull+=grain[:,:,None]-streak[:,:,None]
    boot=np.exp(-((z+4.72)/.11)**4)
    hull*=1-.40*boot[:,:,None]
    hull=np.clip(hull,0,1)
    srgb=np.where(hull<=.0031308,hull*12.92,1.055*np.power(hull,1/2.4)-.055)
    Image.fromarray(np.uint8(srgb[::-1]*255)).save(out/'port_hull_finish.png')

    # Tread decal with soft shoulders and broken, low-density wear.
    y,x=np.mgrid[:1024,:256]
    u=(x-127.5)/127.5; v=y/1023
    edge=np.clip((1-abs(u))/.20,0,1)
    end=np.clip(np.minimum(v,1-v)*12,0,1)
    tread=.55+.45*(np.sin(y*.42+x*.10)>-.2)
    alpha=np.clip(edge*end*tread*(.3+.15*rng.random((1024,256))),0,1)
    # Match the existing asphalt's sRGB base at the border. Opaque color blending
    # avoids RTX treating a translucent decal as a hard-edged cutout.
    rgb=np.array([.25,.255,.25])[None,None,:]-alpha[:,:,None]*.065
    rgb+=rng.normal(0,.002,(1024,256,1))
    Image.fromarray(np.uint8(np.clip(rgb,0,1)*255)).save(out/'port_tire_wear.png')
    print('Generated sheltered-water maps, hull finish and tire decal')


def build():
    from pxr import Gf,Sdf,Usd,UsdGeom,UsdShade
    source=Usd.Stage.Open(str(ROOT/'scenes/smart_port.usda'))
    source.MuteLayer(str(LAYER))
    stage=Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage,1)
    UsdGeom.SetStageUpAxis(stage,UsdGeom.Tokens.z)
    stage.OverridePrim('/World')
    UsdGeom.Xform.Define(stage,BASE)
    stage.GetRootLayer().documentation='Visual water, shared ship material wrapper and sparse road wear. No motion/physics edits.'

    def material(target,path,color,rough):
        m=UsdShade.Material.Define(target,path)
        s=UsdShade.Shader.Define(target,path+'/Surface')
        s.CreateIdAttr('UsdPreviewSurface')
        s.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
        s.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(rough)
        s.CreateInput('metallic',Sdf.ValueTypeNames.Float).Set(0)
        s.CreateOutput('surface',Sdf.ValueTypeNames.Token)
        m.CreateSurfaceOutput().ConnectToSource(s.ConnectableAPI(),'surface')
        return m,s

    def texture(target,path,file,varname='st',color_space='raw'):
        uv=UsdShade.Shader.Define(target,path+'UV')
        uv.CreateIdAttr('UsdPrimvarReader_float2')
        uv.CreateInput('varname',Sdf.ValueTypeNames.Token).Set(varname)
        uv.CreateOutput('result',Sdf.ValueTypeNames.Float2)
        t=UsdShade.Shader.Define(target,path)
        t.CreateIdAttr('UsdUVTexture')
        t.CreateInput('file',Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('../materials/textures/'+file))
        t.CreateInput('sourceColorSpace',Sdf.ValueTypeNames.Token).Set(color_space)
        t.CreateInput('st',Sdf.ValueTypeNames.Float2).ConnectToSource(uv.ConnectableAPI(),'result')
        t.CreateInput('wrapS',Sdf.ValueTypeNames.Token).Set('repeat')
        t.CreateInput('wrapT',Sdf.ValueTypeNames.Token).Set('clamp')
        t.CreateOutput('rgb',Sdf.ValueTypeNames.Float3)
        t.CreateOutput('a',Sdf.ValueTypeNames.Float)
        return t

    from harbor_water_look import author_harbor_water_look
    author_harbor_water_look(stage)
    # Keep the existing UVTransform path and translation for the sea-motion driver.

    wrapper=Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(wrapper,1)
    UsdGeom.SetStageUpAxis(wrapper,UsdGeom.Tokens.z)
    shiproot=UsdGeom.Xform.Define(wrapper,'/CustomFeederShip').GetPrim()
    wrapper.SetDefaultPrim(shiproot)
    asset_path=(ROOT/'assets/ship/Custom_Feeder_Ship.usdc').as_posix()
    shiproot.GetReferences().AddReference(asset_path,'/CustomFeederShip')
    m,sh=material(wrapper,'/CustomFeederShip/PortFinish/Hull',(.019,.022,.021),.78)
    tex=texture(wrapper,str(m.GetPath())+'/Color','port_hull_finish.png','portHullUV','sRGB')
    sh.GetInput('diffuseColor').ConnectToSource(tex.ConnectableAPI(),'rgb')
    xforms=UsdGeom.XformCache()
    hulls=0
    for p in Usd.PrimRange(source.GetPrimAtPath(SHIP),Usd.TraverseInstanceProxies()):
        if not p.IsA(UsdGeom.Mesh): continue
        bound,_=UsdShade.MaterialBindingAPI(p).ComputeBoundMaterial()
        if not bound or bound.GetPrim().GetName() not in ('CODEx_v2_ship_deep_black_hull','CODEx_v2_ship_faded_black_side'): continue
        path='/CustomFeederShip'+str(p.GetPath())[len(SHIP):]
        over=wrapper.OverridePrim(path)
        transform=xforms.GetLocalToWorldTransform(p)
        pts=UsdGeom.Mesh(p).GetPointsAttr().Get()
        world=[transform.Transform(Gf.Vec3d(pt)) for pt in pts]
        uv=[((pt[1]-5.88)/68.24,(pt[2]+8)/16) for pt in world]
        UsdGeom.PrimvarsAPI(over).CreatePrimvar('portHullUV',Sdf.ValueTypeNames.TexCoord2fArray,'vertex').Set(uv)
        UsdShade.MaterialBindingAPI.Apply(over).Bind(m)
        hulls+=1
    assert hulls>=5,'Hull source structure changed'
    # Resolve references during construction, then persist portable relative paths.
    WRAPPER.write_text(wrapper.GetRootLayer().ExportToString().replace('@'+asset_path+'@','@../assets/ship/Custom_Feeder_Ship.usdc@'),encoding='utf-8')
    replaced=[]
    for p in source.GetPrimAtPath('/World/PortAndRTG').GetChildren():
        refs=p.GetMetadata('references')
        if not refs: continue
        items=refs.GetAppliedItems()
        if len(items)==1 and items[0].assetPath=='../assets/ship/Custom_Feeder_Ship.usdc':
            stage.OverridePrim(p.GetPath()).GetReferences().SetReferences([Sdf.Reference(WRAPPER.as_posix(),'/CustomFeederShip')])
            replaced.append(str(p.GetPath()))
    assert len(replaced)==6, replaced
    stage.GetPrimAtPath(BASE).CreateAttribute('finishedShips',Sdf.ValueTypeNames.StringArray).Set(replaced)

    rubber,surface=material(stage,BASE+'/Looks/TireWear',(.045,.047,.045),.86)
    decal=texture(stage,BASE+'/Looks/TireWear/Texture','port_tire_wear.png',color_space='sRGB')
    surface.GetInput('diffuseColor').ConnectToSource(decal.ConnectableAPI(),'rgb')
    patch,_=material(stage,BASE+'/Looks/AsphaltRepair',(.036,.038,.036),.96)
    seam,_=material(stage,BASE+'/Looks/RepairEdge',(.025,.027,.025),.96)

    def mesh(name,faces,mat):
        m=UsdGeom.Mesh.Define(stage,BASE+'/'+name)
        pts=[p for f in faces for p in f]
        m.CreatePointsAttr(pts)
        m.CreateFaceVertexCountsAttr([4]*len(faces))
        m.CreateFaceVertexIndicesAttr(list(range(len(pts))))
        m.CreateSubdivisionSchemeAttr('none')
        m.CreateDoubleSidedAttr(True)
        UsdGeom.PrimvarsAPI(m).CreatePrimvar('st',Sdf.ValueTypeNames.TexCoord2fArray,'vertex').Set([(0,0),(1,0),(1,1),(0,1)]*len(faces))
        UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(mat)

    faces=[]
    rng=random.Random(381014)
    for y in (104,234,364,474):
        for lane in (-238.15,-231.15,-208-1.8):
            for offset in (-.91,.91):
                x=lane+offset; end=y+rng.uniform(7,13)
                faces.append([(x-.13,y,.187),(x+.13,y,.187),(x+.13,end,.187),(x-.13,end,.187)])
    # Broad-radius turn wear on bridge approaches, below existing road paint.
    for cy in (126.05,256.3,386.55):
        for lane in (-.88,.88):
            radius=6+lane
            for i in range(12):
                a=i*math.pi/24; b=(i+1)*math.pi/24
                faces.append([(-208+math.cos(a)*(radius-.13),cy-6+math.sin(a)*(radius-.13),.188),
                              (-208+math.cos(a)*(radius+.13),cy-6+math.sin(a)*(radius+.13),.188),
                              (-208+math.cos(b)*(radius+.13),cy-6+math.sin(b)*(radius+.13),.188),
                              (-208+math.cos(b)*(radius-.13),cy-6+math.sin(b)*(radius-.13),.188)])
    mesh('RoadWear/TireMarks',faces,rubber)
    patches=[]; edges=[]
    for x,y in [(-227.1,56),(-238.3,175),(-209.4,218),(-230.8,321),(-208.9,447),(-236.1,490)]:
        w=rng.uniform(.8,1.25); d=rng.uniform(1.5,2.8)
        poly=[(x-w/2,y-d/2),(x+w/2,y-d/2+.08),(x+w/2-.04,y+d/2),(x-w/2+.07,y+d/2-.1)]
        edges.append([(a,b,.186) for a,b in poly])
        patches.append([(x+(a-x)*.95,y+(b-y)*.97,.187) for a,b in poly])
    mesh('RoadWear/RepairEdges',edges,seam)
    mesh('RoadWear/RepairPatches',patches,patch)
    LAYER.write_text(stage.GetRootLayer().ExportToString().replace('@'+WRAPPER.as_posix()+'@','@./ship_finish_asset.usda@'),encoding='utf-8')
    main=ROOT/'scenes/smart_port.usda'
    text=main.read_text(encoding='utf-8')
    if '@./port_finish_realism.usda@' not in text:
        main.write_text(text.replace('subLayers = [','subLayers = [\n        @./port_finish_realism.usda@,',1),encoding='utf-8')
    print(f'Wrote third pass: {hulls} hull meshes, {len(replaced)} ships with original instance flags')


if __name__=='__main__':
    textures() if '--textures' in sys.argv else build()
