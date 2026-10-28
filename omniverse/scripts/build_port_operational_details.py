"""Batched verge details and night sign lights; preserve traffic and physics."""
from pathlib import Path
import json, math
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade
import build_port_service_areas as g
from build_yard_expansion_prep import DIGIT_SEGMENTS
from yard_coordinate_mapping import YARD_BLOCKS, YARD_LAYOUT_START_Y_M
from build_port_night_visibility import light
from smart_port_environment import NIGHT_STREET_LIGHTS_PATH

ROOT=Path(__file__).resolve().parents[2]
BASE='/World/PortOperationalDetails'
LAYER=ROOT/'omniverse/scenes/port_operational_details.usda'
NIGHT=NIGHT_STREET_LIGHTS_PATH+'/WayfindingLights'
SEGMENTS=((.125,120.55),(131.55,250.8),(261.8,381.05),(392.05,492.6))
CROSS=(-5.375,126.05,256.3,386.55)

def label(site,text,x,y,z,h,side):
    """Seven-segment glyphs consistent with the existing bay markings, on YZ."""
    w=h*.48;t=h*.105;step=h*.72
    bars={'top':(0,h/2,w,t),'middle':(0,0,w,t),'bottom':(0,-h/2,w,t),
          'upper_left':(-w/2,h/4,t,h/2),'upper_right':(w/2,h/4,t,h/2),
          'lower_left':(-w/2,-h/4,t,h/2),'lower_right':(w/2,-h/4,t,h/2)}
    for i,char in enumerate(text):
        cy=y+side*(i*step-(len(text)-1)*step/2)
        for bar in (('middle',) if char=='-' else DIGIT_SEGMENTS[char]):
            dy,dz,ww,hh=bars[bar]
            g.box(site,'White',x,cy+side*dy,z+dz-hh/2,.008,ww,hh)

def sign(site,x,y,block):
    g.box(site,'Concrete',x,y,.112,.95,3.1,.13)
    for dy in (-.9,.9):g.cyl(site,'Steel',x,y+dy,.242,.055,2.7)
    g.box(site,'Steel',x,y,1.45,.20,2.55,1.65)
    for side in (-1,1):
        g.box(site,'Blue',x+side*.105,y,1.5,.015,2.44,1.55)
        label(site,block.name,x+side*.12,y,2.56,.61,side)
        label(site,f'001-{block.bay_count:03d}',x+side*.12,y,1.85,.29,side)
    g.box(site,'Yellow',x,y,3.10,.22,2.55,.075)
    g.box(site,'Steel',x+.31,y,3.19,.75,.08,.075)
    g.box(site,'Steel',x+.60,y,3.15,.26,.60,.09)

def station(site,x,y):
    # Visual emergency/service point, entirely inside the existing green verge.
    g.box(site,'Concrete',x,y,.112,1.25,3.8,.13)
    g.box(site,'Red',x,y-.65,.242,.65,1.0,1.25)
    g.box(site,'Steel',x,y-.65,1.492,.73,1.08,.055)
    for side in (-1,1):
        g.box(site,'White',x+side*.331,y-.65,.75,.012,.52,.12)
        g.box(site,'White',x+side*.331,y-.65,.55,.012,.12,.52)
        g.box(site,'Steel',x+side*.342,y-.30,.68,.02,.035,.20)
    g.cyl(site,'Red',x,y+.65,.242,.14,.82,10)
    g.cyl(site,'Steel',x,y+.65,1.04,.18,.06,10)
    g.box(site,'Red',x,y+.65,.77,.5,.18,.18)
    for yy in (y-1.60,y+1.60):
        g.cyl(site,'Yellow',x,yy,.242,.10,.8)
        for z in (.42,.72):g.cyl(site,'Rubber',x,yy,z,.103,.15)

def drain(site,x,y):
    g.box(site,'Concrete',x,y,.112,.62,1.35,.025)
    g.box(site,'Rubber',x,y,.137,.48,1.18,.006)
    for k in range(10):g.box(site,'Steel',x,y-.53+k*.118,.144,.45,.035,.008)

def shrub(site,x,y,seed):
    # Closed, opaque low crowns; avoid alpha vegetation and per-frame wind.
    n=8;rings=[]
    for z,r in ((.14,.65),(.45,1.0),(.79,.50)):
        rings.append([(x+.32*r*math.cos(k*math.tau/n),y+.85*r*math.sin(k*math.tau/n),z+.04*math.sin(k+seed)) for k in range(n)])
    g.face(site,'Leaves',list(reversed(rings[0])))
    for j in range(2):
        for k in range(n):g.face(site,'LeavesLight' if (k+seed)%5==0 else 'Leaves',[rings[j][k],rings[j][(k+1)%n],rings[j+1][(k+1)%n],rings[j+1][k]])
    g.face(site,'LeavesLight',rings[-1])

def build():
    g.DATA.clear();g.SOLIDS.clear();counts={'signs':0,'emergency_points':0,'drains':0,'shrubs':0,'bridge_reflectors':0}
    for side,x in (('Coastal',-87.3),('Outer',109.6)):
        for block in YARD_BLOCKS:
            start=YARD_LAYOUT_START_Y_M+block.start_m
            sign(side+'Signs',x,start+16,block);counts['signs']+=1
            station(side+'Safety',x,start+52);counts['emergency_points']+=1
        for start,end in SEGMENTS:
            for y in range(math.ceil(start)+5,math.floor(end)-3,12):
                if any(abs(y-(YARD_LAYOUT_START_Y_M+b.start_m+v))<3 for b in YARD_BLOCKS for v in (16,52)):continue
                drain(side+'Drainage',x,y);counts['drains']+=1
            for y in range(math.ceil(start)+5,math.floor(end)-3,6):
                if any(abs(y-(YARD_LAYOUT_START_Y_M+b.start_m+v))<4 for b in YARD_BLOCKS for v in (16,52)):continue
                # Shrubs on opposite side of the trees from the drainage edge.
                shrub(side+'Planting',-99.5 if side=='Coastal' else 105.85,y,y);counts['shrubs']+=1
    for cy in CROSS:
        for side in (-1,1):
            for x in range(-196,-105,10):
                y=cy+side*4.25
                g.box('BridgeMarkers','Steel',x,y,1.0,.22,.07,.24)
                g.box('BridgeMarkers','Yellow',x,y-side*.04,1.02,.17,.012,.20)
                g.box('BridgeMarkers','White',x,y-side*.049,1.10,.17,.006,.04)
                counts['bridge_reflectors']+=1
    stage=Usd.Stage.CreateInMemory();UsdGeom.SetStageUpAxis(stage,'Z');UsdGeom.SetStageMetersPerUnit(stage,1)
    root=UsdGeom.Xform.Define(stage,BASE).GetPrim()
    root.CreateAttribute('designNotes',Sdf.ValueTypeNames.String).Set('Static visual verge equipment and bridge rail markers; eight shadow-free night sign lights. No new texture assets, collision, inventory or motion callbacks. Safety equipment is decorative.')
    for side,x in (('Coastal',-87.3),('Outer',109.6)):
        for block in YARD_BLOCKS:
            y=YARD_LAYOUT_START_Y_M+block.start_m+16
            origin=Gf.Vec3d(x+.60,y,3.13);target=Gf.Vec3d(x+.12,y,2.2)
            lamp=light(stage,NIGHT+'/'+side+'_'+block.name,origin,2500,3500,.12)
            UsdLux.ShapingAPI(lamp).CreateShapingConeSoftnessAttr(.35)
            UsdGeom.Xformable(lamp).AddOrientOp(precision=UsdGeom.XformOp.PrecisionDouble).Set(Gf.Rotation(Gf.Vec3d(0,0,-1),(target-origin).GetNormalized()).GetQuat())
    mats={}
    for name in sorted({mat for site,mat in g.DATA}):
        color,rough,metal=g.COLORS[name]
        m=UsdShade.Material.Define(stage,BASE+'/Looks/'+name);sh=UsdShade.Shader.Define(stage,str(m.GetPath())+'/Surface');sh.CreateIdAttr('UsdPreviewSurface')
        sh.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(color);sh.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(rough);sh.CreateInput('metallic',Sdf.ValueTypeNames.Float).Set(metal)
        m.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(),'surface');mats[name]=m
    # Smooth foliage across material boundaries while keeping hard equipment edges.
    foliage_normals={}
    for (site,mat),(pts,counts_,ids) in g.DATA.items():
        if not site.endswith('Planting'):continue
        offset=0
        for count in counts_:
            verts=[Gf.Vec3d(*pts[k]) for k in ids[offset:offset+count]];offset+=count
            normal=Gf.Cross(verts[1]-verts[0],verts[2]-verts[0])
            for vertex in verts:
                key=(site,tuple(round(v,5) for v in vertex))
                foliage_normals[key]=foliage_normals.get(key,Gf.Vec3d(0))+normal
    for (site,mat),(pts,counts_,ids) in g.DATA.items():
        m=UsdGeom.Mesh.Define(stage,BASE+'/'+site+'/'+mat);m.CreatePointsAttr(pts);m.CreateFaceVertexCountsAttr(counts_);m.CreateFaceVertexIndicesAttr(ids);m.CreateSubdivisionSchemeAttr('none')
        m.CreateExtentAttr([Gf.Vec3f(*(min(p[j] for p in pts) for j in range(3))),Gf.Vec3f(*(max(p[j] for p in pts) for j in range(3)))])
        if site.endswith('Planting'):
            m.CreateNormalsAttr([Gf.Vec3f(foliage_normals[site,tuple(round(v,5) for v in p)].GetNormalized()) for p in pts]);m.SetNormalsInterpolation('vertex')
        UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(mats[mat])
    stage.GetRootLayer().Export(str(LAYER))
    report={**counts,'meshes':len(g.DATA),'triangles':sum(sum(n-2 for n in v[1]) for v in g.DATA.values()),'new_lights':8,'new_textures':0}
    out=ROOT/'outputs/port_operational_details';out.mkdir(parents=True,exist_ok=True);(out/'build.json').write_text(json.dumps(report,indent=2));print(report)
if __name__=='__main__':build()
