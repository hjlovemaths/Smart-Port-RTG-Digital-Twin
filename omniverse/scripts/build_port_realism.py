"""Deterministic visual-only port surfacing, authored in a removable USD layer.

Run --textures with NumPy/Pillow Python, then run without flags with USD Python.
No vehicle transforms, inventory, physics, weather or control opinions are authored.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
LAYER = ROOT / 'scenes/port_realism.usda'
BASE = '/World/PortRealism'


def textures():
    import numpy as np
    from PIL import Image
    out = ROOT / 'materials/textures'
    out.mkdir(exist_ok=True)
    rng = np.random.default_rng(141026)
    n = 1024
    y, x = np.mgrid[:n, :n] * (2 * np.pi / n)
    # Periodic macro variation avoids seams; grain is sub-centimetre at 6 m/tile.
    broad = np.zeros((n, n))
    for _ in range(24):
        kx, ky = rng.integers(1, 12, 2)
        broad += np.sin(kx*x + ky*y + rng.uniform(0, 6.28)) / 24
    grain = rng.normal(0, 1, (n, n))
    for name, base, amplitude in [('asphalt', (.25, .255, .25), .035),
                                  ('concrete', (.49, .48, .445), .025)]:
        height = grain * .08 + broad * .35
        rgb = np.array(base)[None, None, :] + (grain * amplitude + broad * .04)[:, :, None]
        Image.fromarray(np.uint8(np.clip(rgb, 0, 1)*255)).save(out/f'port_{name}_color.png')
        nx = (np.roll(height, -1, 1)-np.roll(height, 1, 1))*.18
        ny = (np.roll(height, -1, 0)-np.roll(height, 1, 0))*.18
        normals = np.stack((-nx, -ny, np.ones_like(nx)), -1)
        normals /= np.linalg.norm(normals, axis=-1, keepdims=True)
        Image.fromarray(np.uint8((normals*.5+.5)*255)).save(out/f'port_{name}_normal.png')
        rough = np.clip(.84 + broad*.15 + grain*.025, .68, .97)
        Image.fromarray(np.uint8(rough*255)).save(out/f'port_{name}_roughness.png')
    print('Created six seamless 1024px surfacing maps')


def build():
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade
    source = Usd.Stage.Open(str(ROOT/'scenes/smart_port.usda'))
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    stage.OverridePrim('/World')
    UsdGeom.Xform.Define(stage, BASE)
    stage.GetRootLayer().documentation = 'Visual surfacing only. Rebuild with build_port_realism.py; remove this sublayer to revert.'

    def material(name, color, rough=.8, metal=0, textured=False):
        m = UsdShade.Material.Define(stage, BASE+'/Looks/'+name)
        s = UsdShade.Shader.Define(stage, str(m.GetPath())+'/Surface')
        s.CreateIdAttr('UsdPreviewSurface')
        s.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
        s.CreateInput('roughness', Sdf.ValueTypeNames.Float).Set(rough)
        s.CreateInput('metallic', Sdf.ValueTypeNames.Float).Set(metal)
        m.CreateSurfaceOutput().ConnectToSource(s.ConnectableAPI(), 'surface')
        if textured:
            uv = UsdShade.Shader.Define(stage, str(m.GetPath())+'/UV')
            uv.CreateIdAttr('UsdPrimvarReader_float2')
            uv.CreateInput('varname', Sdf.ValueTypeNames.Token).Set('st')
            uv.CreateOutput('result', Sdf.ValueTypeNames.Float2)
            for channel, target, typ, output in [('color','diffuseColor',Sdf.ValueTypeNames.Color3f,'rgb'),
                       ('normal','normal',Sdf.ValueTypeNames.Normal3f,'rgb'),
                       ('roughness','roughness',Sdf.ValueTypeNames.Float,'r')]:
                tex = UsdShade.Shader.Define(stage, str(m.GetPath())+'/'+channel)
                tex.CreateIdAttr('UsdUVTexture')
                tex.CreateInput('file', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(f'../materials/textures/port_{name}_{channel}.png'))
                tex.CreateInput('sourceColorSpace', Sdf.ValueTypeNames.Token).Set('sRGB' if channel=='color' else 'raw')
                tex.CreateInput('st', Sdf.ValueTypeNames.Float2).ConnectToSource(uv.ConnectableAPI(),'result')
                for axis in ('S','T'):
                    tex.CreateInput('wrap'+axis,Sdf.ValueTypeNames.Token).Set('repeat')
                if channel=='normal':
                    tex.CreateInput('scale',Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(2,2,2,1))
                    tex.CreateInput('bias',Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(-1,-1,-1,0))
                tex.CreateOutput(output,Sdf.ValueTypeNames.Float3 if output=='rgb' else Sdf.ValueTypeNames.Float)
                s.CreateInput(target,typ).ConnectToSource(tex.ConnectableAPI(),output)
        return m

    asphalt=material('asphalt',(.06,.065,.06),textured=True)
    concrete=material('concrete',(.20,.19,.17),textured=True)
    joint=material('Joint',(.032,.03,.027),.98)
    steel=material('GalvanizedSteel',(.31,.34,.36),.43,.75)
    rubber=material('DrainIron',(.035,.042,.045),.8,.55)

    def bind(prim,mat):
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(mat)

    def uv_mesh(path,mat):
        old=UsdGeom.Mesh(source.GetPrimAtPath(path))
        points=old.GetPointsAttr().Get()
        prim=stage.OverridePrim(path)
        UsdGeom.PrimvarsAPI(prim).CreatePrimvar('st', Sdf.ValueTypeNames.TexCoord2fArray, 'vertex').Set([(p[0]/6,p[1]/6) for p in points])
        bind(prim,mat)

    uv_mesh('/World/SMART_PORT_GROUND_COLOR_OVERRIDE/SMART_PORT_GROUND_COLOR_OVERRIDE_MAIN_YARD',asphalt)
    prefix='/World/SMART_PORT_QUAY_YARD_TRAFFIC_LAYOUT/SMART_PORT_QUAY_YARD_TRAFFIC_'
    uv_mesh(prefix+'ROAD_SURFACE',asphalt)
    uv_mesh(prefix+'SHORE_APRON',concrete)

    # Existing network paint sits at z=.245 even where roads are at .109;
    # place those quads 6-7 mm above their surface instead of floating.
    for name in ('WhiteTrafficLines','YellowTrafficDividers'):
        path='/World/GROUND_TRAFFIC_NETWORK/'+name
        mesh=UsdGeom.Mesh(source.GetPrimAtPath(path))
        if mesh:
            points=mesh.GetPointsAttr().Get()
            adjusted=[]
            for x,y,z in points:
                if z > .24:
                    z=.190 if x < -101.5 else .116
                adjusted.append(Gf.Vec3f(x,y,z))
            stage.OverridePrim(path).CreateAttribute('points',Sdf.ValueTypeNames.Point3fArray).Set(adjusted)

    def quads(name,faces,mat):
        mesh=UsdGeom.Mesh.Define(stage,BASE+'/'+name)
        pts=[p for face in faces for p in face]
        mesh.CreatePointsAttr(pts)
        mesh.CreateFaceVertexCountsAttr([4]*len(faces))
        mesh.CreateFaceVertexIndicesAttr(list(range(len(pts))))
        mesh.CreateSubdivisionSchemeAttr('none')
        mesh.CreateDoubleSidedAttr(True)
        UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st',Sdf.ValueTypeNames.TexCoord2fArray,'vertex').Set([(p[0]/6,p[1]/6) for p in pts])
        bind(mesh.GetPrim(),mat)

    def rect(x1,y1,x2,y2,z):
        return [(x1,y1,z),(x2,y1,z),(x2,y2,z),(x1,y2,z)]

    quads('QuaySurfacing',[rect(-241.5,-10,-201.5,510,.183)],asphalt)
    cross=(-5.375,126.05,256.3,386.55)
    quads('CausewaySurfacing',[rect(-201.5,y-4.5,-101.5,y+4.5,.183) for y in cross],asphalt)
    # Concrete service strip stays clear of crane rails and marked travel lanes.
    quads('QuayConcreteServiceStrip',[rect(-222.5,-10,-213,510,.186)],concrete)
    quads('ConcreteExpansionJoints',[rect(-222.5,y,-213,y+.025,.189) for y in range(-4,510,6)] +
          [rect(-217.76,-10,-217.735,510,.189)],joint)

    def boxes(name, items, mat):
        # Merge repeated details into one mesh per material to keep draw calls low.
        faces=[]
        for x,y,z,w,d,h in items:
            a,b=x-w/2,x+w/2; c,e=y-d/2,y+d/2; f,g=z-h/2,z+h/2
            faces.extend([[(a,c,g),(b,c,g),(b,e,g),(a,e,g)],[(a,e,f),(b,e,f),(b,c,f),(a,c,f)],
                          [(a,c,f),(b,c,f),(b,c,g),(a,c,g)],[(b,e,f),(a,e,f),(a,e,g),(b,e,g)],
                          [(a,e,f),(a,c,f),(a,c,g),(a,e,g)],[(b,c,f),(b,e,f),(b,e,g),(b,c,g)]])
        quads(name,faces,mat)

    curbs=[]; rails=[]
    for y in cross:
        for side in (-1,1):
            edge=y+side*4.30
            curbs.append((-151.5,edge,.27,100,.28,.18))
            for x in range(-200,-102,4):
                rails.append((x,edge,.81,.065,.065,1.08))
            for z in (.67,1.30):
                rails.append((-151.5,edge,z,99,.065,.065))
    boxes('CausewayEdgeCurbs',curbs,concrete)
    boxes('CausewayGuardrails',rails,steel)
    drains=[]; slots=[]
    for y in range(8,502,12):
        # Recessed-looking grates on the service strip, outside travel lanes.
        drains.append((-213.6,y,.19,.48,1.0,.008))
        for i in range(9):
            slots.append((-213.6,y-.44+i*.11,.199,.44,.025,.008))
    boxes('DrainBeds',drains,joint)
    boxes('DrainGrates',slots,rubber)

    # Replace the original box-shaped crowns at their existing positions.
    # Clustered, smooth irregular volumes retain a modest merged-mesh budget.
    import math
    import random
    rng=random.Random(141026)
    grass=material('DryCoastalGrass',(.055,.085,.026),.98)
    bark=material('TreeBark',(.09,.047,.019),.97)
    leaf_mats=[material('Foliage'+str(i),c,.94) for i,c in enumerate(
        ((.035,.085,.024),(.05,.115,.031),(.075,.135,.036),(.045,.098,.028)))]
    foliage=[[] for _ in leaf_mats]
    foliage_normals=[[] for _ in leaf_mats]
    for side in ('COASTAL','RIGHT_EDGE'):
        bind(stage.OverridePrim(prefix+side+'_GREEN_BELT'),grass)
        bind(stage.OverridePrim(prefix+side+'_TREE_TRUNKS'),bark)
        path=prefix+side+'_TREE_CROWNS'
        points=UsdGeom.Mesh(source.GetPrimAtPath(path)).GetPointsAttr().Get()
        stage.OverridePrim(path).CreateAttribute('visibility',Sdf.ValueTypeNames.Token).Set('invisible')
        for offset in range(0,len(points),24):
            block=points[offset:offset+24]
            cx=(min(p[0] for p in block)+max(p[0] for p in block))/2
            cy=(min(p[1] for p in block)+max(p[1] for p in block))/2
            for cluster in range(5):
                angle=cluster*math.tau/4
                central=cluster==4
                x=cx+(0 if central else math.cos(angle)*.42)
                y=cy+(0 if central else math.sin(angle)*.42)
                z=3.25 if central else rng.uniform(2.65,3.05)
                rx=ry=rng.uniform(.65,.88)
                rz=rng.uniform(.85,1.10)
                bucket=rng.randrange(4)
                def vertex(lat,lon):
                    t=math.pi*lat/8; p=math.tau*lon/12
                    wiggle=1+.06*math.sin(3*p+2*t+cluster)
                    n=Gf.Vec3f(math.sin(t)*math.cos(p),math.sin(t)*math.sin(p),math.cos(t))
                    return (x+rx*n[0]*wiggle,y+ry*n[1]*wiggle,z+rz*n[2]*wiggle),n
                for lat in range(8):
                    for lon in range(12):
                        v=[vertex(a,b) for a,b in ((lat,lon),(lat+1,lon),(lat+1,lon+1),(lat,lon+1))]
                        foliage[bucket].append([a for a,b in v])
                        foliage_normals[bucket].extend([b for a,b in v])
    for i,faces in enumerate(foliage):
        quads('Trees/Foliage'+str(i),faces,leaf_mats[i])
        mesh=UsdGeom.Mesh(stage.GetPrimAtPath(BASE+'/Trees/Foliage'+str(i)))
        mesh.CreateNormalsAttr(foliage_normals[i])
        mesh.SetNormalsInterpolation('vertex')
    # Authored cameras are optional; do not replace the operator's current view.
    for name,eye,target in [('QuayDetail',(-196,65,9),(-227,112,1)),
                            ('PortOverview',(-310,-125,145),(-95,190,5))]:
        cam=UsdGeom.Camera.Define(stage,BASE+'/Cameras/'+name)
        cam.GetPrim().SetMetadata('hide_in_stage_window', True)
        cam.CreateFocalLengthAttr(32)
        cam.CreateClippingRangeAttr(Gf.Vec2f(.1,5000))
        mat=Gf.Matrix4d(1).SetLookAt(Gf.Vec3d(*eye),Gf.Vec3d(*target),Gf.Vec3d(0,0,1))
        cam.AddTransformOp().Set(mat.GetInverse())
        cam.GetPrim().SetActive(False)
    stage.GetRootLayer().Export(str(LAYER))
    # Insert with a small text edit, preserving all root-layer operator settings.
    main=ROOT/'scenes/smart_port.usda'
    text=main.read_text(encoding='utf-8')
    if '@./port_realism.usda@' not in text:
        text=text.replace('subLayers = [','subLayers = [\n        @./port_realism.usda@,',1)
        main.write_text(text,encoding='utf-8')
    print(f'Wrote {LAYER}')


if __name__=='__main__':
    textures() if '--textures' in sys.argv else build()
