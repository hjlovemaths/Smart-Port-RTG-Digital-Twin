"""Verge-mounted road lighting for the four 100 m quay causeways."""
from pathlib import Path
import json
from pxr import Gf,Sdf,Usd,UsdGeom,UsdShade
import build_port_service_areas as g
import smart_port_environment as w
from build_port_night_visibility import light,material

ROOT=Path(__file__).resolve().parents[2]
LAYER=ROOT/'omniverse/scenes/bridge_lighting.usda'
BASE='/World/BridgeLightingHardware'
NIGHT=w.NIGHT_STREET_LIGHTS_PATH+'/BridgeLights'
CROSS=(-5.375,126.05,256.3,386.55)

def build():
    stage=Usd.Stage.CreateInMemory();UsdGeom.SetStageUpAxis(stage,'Z');UsdGeom.SetStageMetersPerUnit(stage,1)
    UsdGeom.Xform.Define(stage,BASE);UsdGeom.Xform.Define(stage,NIGHT)
    g.DATA.clear();g.SOLIDS.clear();placements=[]
    for bridge,cy in enumerate(CROSS,1):
        for x in (-190.,-165.,-140.,-115.):
            for side in (-1,1):
                y=cy+side*4.03;head=y-side*1.8;name=f'Bridge_{bridge}_{int(-x)}_{side+1}'
                # The 0.32 m bases fit between the 7.4 m road clearance and
                # existing guardrail at +/-4.30 m, above the .183 m pavement.
                g.box('Poles','Concrete',x,y,.184,.32,.32,.12)
                g.cyl('Poles','Steel',x,y,.304,.075,7.50,10)
                g.box('Poles','Steel',x,(y+head)/2,7.72,.09,1.8,.10)
                g.box('Poles','Roof',x,head,7.65,.4,.65,.13)
                g.box('Poles','Lens',x,head,7.637,.31,.52,.012)
                lamp=light(stage,NIGHT+'/'+name,(x,head,7.62),36000,3500,.24)
                lamp.CreateExposureAttr(.5)
                placements.append({'bridge':bridge,'pole':[x,y],'head':[x,head,7.62]})
    mats={name:material(stage,(NIGHT if name=='Lens' else BASE)+'/Looks/'+name,
            (.85,.75,.55) if name=='Lens' else g.COLORS[name][0],
            (10,8,5) if name=='Lens' else None,
            g.COLORS[name][2] if name!='Lens' else 0) for name in ('Concrete','Steel','Roof','Lens')}
    for (site,mat),(points,counts,indices) in g.DATA.items():
        mesh=UsdGeom.Mesh.Define(stage,(NIGHT if mat=='Lens' else BASE)+'/'+site+'/'+mat)
        mesh.CreatePointsAttr(points);mesh.CreateFaceVertexCountsAttr(counts);mesh.CreateFaceVertexIndicesAttr(indices);mesh.CreateSubdivisionSchemeAttr('none')
        mesh.CreateExtentAttr([Gf.Vec3f(*(min(p[j] for p in points) for j in range(3))),Gf.Vec3f(*(max(p[j] for p in points) for j in range(3)))])
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mats[mat])
    assert len(placements)==32
    for p in placements:
        cy=CROSS[p['bridge']-1];assert abs(p['pole'][1]-cy)-.16>3.7
        assert abs(p['pole'][1]-cy)+.16<4.30-.0325
    stage.GetRootLayer().Export(str(LAYER))
    out=ROOT/'outputs/bridge_lighting';out.mkdir(parents=True,exist_ok=True)
    (out/'build.json').write_text(json.dumps({'bridges':4,'lights':32,'meshes':len(g.DATA),'placements':placements},indent=2))
    print('Built 32 road lights on 4 bridges; 4 batched meshes; road and guardrail clearance checked')
if __name__=='__main__':build()
