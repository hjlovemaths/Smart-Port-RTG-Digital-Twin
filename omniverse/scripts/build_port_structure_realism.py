"""Visual coastal civil works and equipment finishes, in a separate reversible layer.

Run --textures with NumPy/Pillow, then without flags with USD Python.
All dimensions are metres. These details are visual, not engineering/collision models.
"""
from pathlib import Path
import math
import random
import sys

ROOT=Path(__file__).resolve().parents[1]
LAYER=ROOT/'scenes/port_structure_realism.usda'
BASE='/World/PortStructureRealism'
SEA_Z=-4.9478
CROSS=(-5.375,126.05,256.3,386.55)


def textures():
    import numpy as np
    from PIL import Image
    n=1024
    y,x=np.mgrid[:n,:n]/n
    rng=np.random.default_rng(271014)
    mask=np.ones((n,n))*.97
    # Sparse elongated grime and tiny chips, not blanket rust on maintained paint.
    for _ in range(65):
        cx,cy=rng.random(2)
        dx=np.minimum(abs(x-cx),1-abs(x-cx))
        dy=np.minimum(abs(y-cy),1-abs(y-cy))
        mask-=rng.uniform(.03,.18)*np.exp(-(dx/rng.uniform(.002,.018))**2-(dy/rng.uniform(.018,.16))**2)
    mask=np.clip(mask+rng.normal(0,.008,(n,n)),.60,1)
    folder=ROOT/'materials/textures'
    Image.fromarray(np.uint8(mask*255)).save(folder/'port_equipment_patina.png')
    rough=np.clip(.63+(1-mask)*.7+rng.normal(0,.015,(n,n)),.6,.88)
    Image.fromarray(np.uint8(rough*255)).save(folder/'port_equipment_roughness.png')
    print('Generated deterministic paint patina and roughness maps')


def build():
    from pxr import Gf,Sdf,Usd,UsdGeom,UsdShade
    source=Usd.Stage.Open(str(ROOT/'scenes/smart_port.usda'))
    # Always derive materials from the previous look, never compound our edits.
    source.MuteLayer(str(LAYER))
    stage=Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage,1)
    UsdGeom.SetStageUpAxis(stage,UsdGeom.Tokens.z)
    stage.OverridePrim('/World')
    UsdGeom.Xform.Define(stage,BASE)
    stage.GetRootLayer().documentation='Visual coastal structures and equipment finishes. No physics, inventory or control transforms.'
    root=stage.GetPrimAtPath(BASE)
    root.CreateAttribute('seaLevel',Sdf.ValueTypeNames.Double).Set(SEA_Z)
    rng=random.Random(271014)

    def material(name,color,rough=.9,metal=0,concrete=False):
        m=UsdShade.Material.Define(stage,BASE+'/Looks/'+name)
        s=UsdShade.Shader.Define(stage,str(m.GetPath())+'/Surface')
        s.CreateIdAttr('UsdPreviewSurface')
        s.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
        s.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(rough)
        s.CreateInput('metallic',Sdf.ValueTypeNames.Float).Set(metal)
        s.CreateOutput('surface',Sdf.ValueTypeNames.Token)
        m.CreateSurfaceOutput().ConnectToSource(s.ConnectableAPI(),'surface')
        if concrete:
            uv=UsdShade.Shader.Define(stage,str(m.GetPath())+'/UV')
            uv.CreateIdAttr('UsdPrimvarReader_float2')
            uv.CreateInput('varname',Sdf.ValueTypeNames.Token).Set('st')
            uv.CreateOutput('result',Sdf.ValueTypeNames.Float2)
            tex=UsdShade.Shader.Define(stage,str(m.GetPath())+'/Texture')
            tex.CreateIdAttr('UsdUVTexture')
            tex.CreateInput('file',Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('../materials/textures/port_concrete_color.png'))
            tex.CreateInput('sourceColorSpace',Sdf.ValueTypeNames.Token).Set('sRGB')
            tex.CreateInput('scale',Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(*color,1))
            for axis in ('S','T'): tex.CreateInput('wrap'+axis,Sdf.ValueTypeNames.Token).Set('repeat')
            tex.CreateInput('st',Sdf.ValueTypeNames.Float2).ConnectToSource(uv.ConnectableAPI(),'result')
            tex.CreateOutput('rgb',Sdf.ValueTypeNames.Float3)
            s.GetInput('diffuseColor').ConnectToSource(tex.ConnectableAPI(),'rgb')
        return m

    concrete=material('MarineConcrete',(.94,.96,.95),concrete=True)
    coping=material('CopingConcrete',(1.1,1.1,1.04),concrete=True)
    wet=material('WetTidalConcrete',(.11,.12,.09),.93)
    joint=material('ExpansionJoint',(.04,.037,.03),.98)
    rust=material('RunoffStain',(.16,.135,.105),.98)
    rubber=material('FenderRubber',(.014,.017,.019),.93)
    steel=material('WeatheredGalvanizedSteel',(.32,.34,.33),.48,.72)

    def mesh(name,faces,mat):
        m=UsdGeom.Mesh.Define(stage,BASE+'/'+name)
        pts=[p for f in faces for p in f]
        m.CreatePointsAttr(pts)
        m.CreateFaceVertexCountsAttr([len(f) for f in faces])
        m.CreateFaceVertexIndicesAttr(list(range(len(pts))))
        m.CreateSubdivisionSchemeAttr('none')
        m.CreateDoubleSidedAttr(True)
        uv=[]
        for f in faces:
            v1=Gf.Vec3d(*f[1])-Gf.Vec3d(*f[0]); v2=Gf.Vec3d(*f[2])-Gf.Vec3d(*f[0])
            normal=Gf.Cross(v1,v2)
            axis=max(range(3),key=lambda i:abs(normal[i]))
            a,b=((1,2),(0,2),(0,1))[axis]
            uv.extend((p[a]/4,p[b]/4) for p in f)
        UsdGeom.PrimvarsAPI(m).CreatePrimvar('st',Sdf.ValueTypeNames.TexCoord2fArray,'vertex').Set(uv)
        UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(mat)
        return m

    def boxes(name,items,mat):
        faces=[]
        for x,y,z,w,d,h in items:
            a,b=x-w/2,x+w/2; c,e=y-d/2,y+d/2; f,g=z-h/2,z+h/2
            faces.extend([[(a,c,g),(b,c,g),(b,e,g),(a,e,g)],[(a,e,f),(b,e,f),(b,c,f),(a,c,f)],
                          [(a,c,f),(b,c,f),(b,c,g),(a,c,g)],[(b,e,f),(a,e,f),(a,e,g),(b,e,g)],
                          [(a,e,f),(a,c,f),(a,c,g),(a,e,g)],[(b,c,f),(b,e,f),(b,e,g),(b,c,g)]])
        return mesh(name,faces,mat)

    # Walls terminate at the existing deck underside; no new road obstructions.
    walls=[(-241.1,250,-3.47,.8,520,7.06),(-201.9,250,-3.47,.8,520,7.06),
           (-221.5,-9.6,-3.47,39.2,.8,7.06),(-221.5,509.6,-3.47,39.2,.8,7.06),
           (-101.35,244.875,-3.49,.8,510.25,7.02),(149.6,244.875,-3.49,.8,510.25,7.02),
           (24.125,-9.85,-3.49,250.15,.8,7.02),(24.125,499.6,-3.49,250.15,.8,7.02)]
    boxes('Civil/RetainingWalls',walls,concrete)
    caps=[(-241.1,250,-.04,.8,520,.2),(-201.9,250,-.04,.8,520,.2),
          (-101.35,244.875,-.04,.8,510.25,.16),(149.6,244.875,-.04,.8,510.25,.16)]
    boxes('Civil/WallCaps',caps,coping)
    seams=[]; tide=[]; drips=[]
    for x,start,end,sign in [(-241.5,-10,510,-1),(-201.5,-10,510,1),(-101.75,-10.25,500,-1),(150,-10.25,500,1)]:
        xf=x+sign*.006
        for y in range(math.ceil(start),math.floor(end),6):
            seams.append([(xf,y,-4.4),(xf,y+.024,-4.4),(xf,y+.024,.02),(xf,y,.02)])
        # Irregular splash/algae band follows the actual water elevation.
        y=start
        def tide_height(t):
            return SEA_Z+.29+.07*math.sin(t*.63)+.035*math.sin(t*2.71)+.015*math.sin(t*9.1)
        while y<end:
            yn=min(y+.35,end)
            tide.append([(xf,y,SEA_Z-.45),(xf,yn,SEA_Z-.45),(xf,yn,tide_height(yn)),(xf,y,tide_height(y))])
            y=yn
        for y in range(math.ceil(start)+3,math.floor(end)-1,11):
            width=rng.uniform(.06,.19); bottom=rng.uniform(-2.5,-.7)
            drips.append([(xf,y,-.12),(xf,y+width,-.12),(xf,y+width*.55,bottom),(xf,y+width*.4,bottom-.15)])
    mesh('Civil/PanelJoints',seams,joint)
    mesh('Civil/TidalStaining',tide,wet)
    mesh('Civil/DrainageRunoff',drips,rust)

    # Four pile-supported bridges, five 20 m spans each, with paired piers.
    pile_faces=[]; beams=[]
    for y in CROSS:
        for lane in (-2.9,2.9): beams.append((-151.5,y+lane,-.50,100,.42,.8))
        for x in (-181.5,-161.5,-141.5,-121.5):
            beams.append((x,y,-1.05,1.65,8.75,.85))
            for yy in (y-2.9,y+2.9):
                for i in range(20):
                    a,b=math.tau*i/20,math.tau*(i+1)/20
                    p=(x+.66*math.cos(a),yy+.66*math.sin(a))
                    q=(x+.66*math.cos(b),yy+.66*math.sin(b))
                    pile_faces.append([(p[0],p[1],-8),(q[0],q[1],-8),(q[0],q[1],-.9),(p[0],p[1],-.9)])
    mesh('Civil/BridgePiles',pile_faces,concrete)
    boxes('Civil/BridgeBeamsAndCaps',beams,concrete)

    # Replace the floating small blocks with panel-and-elastomer berth fenders.
    for p in source.GetPrimAtPath('/World/SMART_PORT_OFFSHORE_QUAY/Edges').GetChildren():
        if p.GetName().startswith('Fender_'):
            stage.OverridePrim(p.GetPath()).CreateAttribute('visibility',Sdf.ValueTypeNames.Token).Set('invisible')
    pads=[]; back=[]; ribs=[]; bolts=[]
    for y in range(0,510,20):
        back.append((-241.60,y,-2.5,.18,1.9,2.6))
        pads.append((-241.745,y,-2.5,.11,1.7,2.4))
        for offset in (-.58,0,.58): ribs.append((-241.815,y+offset,-2.5,.03,.15,2.22))
        for yy in (-.67,.67):
            for zz in (-3.42,-1.58): bolts.append((-241.839,y+yy,zz,.018,.11,.11))
    boxes('Berth/FenderBacking',back,steel)
    boxes('Berth/FenderPanels',pads,rubber)
    boxes('Berth/FenderRibs',ribs,rubber)
    boxes('Berth/FenderFasteners',bolts,steel)

    # Tune existing materials in place, so instance/prototype bindings still work.
    painted={'CODEx_crane1_deep_teal_blue','CODEx_crane1_edge_blue_highlight',
             'CODEx_crane1_spreader_safety_yellow','RTG_deep_cobalt_blue_steel',
             'RTG_clean_white_beam_paint','RTG_light_gray_platform_paint','RTG_safety_yellow_rails'}
    adjusted=0
    for prim in source.Traverse():
        if not prim.IsA(UsdShade.Material): continue
        name=prim.GetName()
        ispaint=name in painted
        isrubber=name in {'CODEx_crane1_dark_rubber_cable','RTG_dark_black_rubber','RTG_black_cable_rubber','truck_black_rubber_tires'}
        issteel=name in {'CODEx_crane1_oiled_steel','RTG_reference_warm_galvanized_upper_steel','truck_gray_steel'}
        if not (ispaint or isrubber or issteel): continue
        for child in prim.GetChildren():
            old=UsdShade.Shader(child)
            if not old or old.GetIdAttr().Get()!='UsdPreviewSurface': continue
            sh=UsdShade.Shader(stage.OverridePrim(child.GetPath()))
            sh.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(.69 if ispaint else .9 if isrubber else .39)
            sh.CreateInput('metallic',Sdf.ValueTypeNames.Float).Set(.02 if ispaint else 0 if isrubber else .75)
            sh.CreateInput('clearcoat',Sdf.ValueTypeNames.Float).Set(0)
            if ispaint:
                c=old.GetInput('diffuseColor').Get()
                gray=sum(c)/3
                color=[v*.9+gray*.1 for v in c]
                base=str(prim.GetPath())+'/PortFinish'
                uv=UsdShade.Shader.Define(stage,base+'UV')
                uv.CreateIdAttr('UsdPrimvarReader_float2')
                uv.CreateInput('varname',Sdf.ValueTypeNames.Token).Set('st')
                uv.CreateOutput('result',Sdf.ValueTypeNames.Float2)
                for channel,outfile,outtype,output in [('diffuseColor','patina',Sdf.ValueTypeNames.Color3f,'rgb'),('roughness','roughness',Sdf.ValueTypeNames.Float,'r')]:
                    tex=UsdShade.Shader.Define(stage,base+outfile)
                    tex.CreateIdAttr('UsdUVTexture')
                    tex.CreateInput('file',Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('../materials/textures/port_equipment_'+outfile+'.png'))
                    tex.CreateInput('sourceColorSpace',Sdf.ValueTypeNames.Token).Set('raw')
                    tex.CreateInput('st',Sdf.ValueTypeNames.Float2).ConnectToSource(uv.ConnectableAPI(),'result')
                    for axis in ('S','T'): tex.CreateInput('wrap'+axis,Sdf.ValueTypeNames.Token).Set('repeat')
                    if channel=='diffuseColor':
                        tex.CreateInput('scale',Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(*color,1))
                        tex.CreateInput('fallback',Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(1))
                    tex.CreateOutput(output,Sdf.ValueTypeNames.Float3 if output=='rgb' else Sdf.ValueTypeNames.Float)
                    sh.CreateInput(channel,outtype).ConnectToSource(tex.ConnectableAPI(),output)
            adjusted+=1
    root.CreateAttribute('adjustedShaderCount',Sdf.ValueTypeNames.Int).Set(adjusted)
    stage.GetRootLayer().Export(str(LAYER))
    main=ROOT/'scenes/smart_port.usda'
    text=main.read_text(encoding='utf-8')
    if '@./port_structure_realism.usda@' not in text:
        main.write_text(text.replace('subLayers = [','subLayers = [\n        @./port_structure_realism.usda@,',1),encoding='utf-8')
    print(f'Wrote {LAYER}; {adjusted} equipment shaders refined')


if __name__=='__main__':
    textures() if '--textures' in sys.argv else build()
