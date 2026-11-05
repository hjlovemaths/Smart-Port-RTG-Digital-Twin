"""Complete the quay's land-side water edge, leaving all causeways open."""
import math
from pathlib import Path
from pxr import Gf,Sdf,Usd,UsdGeom,UsdShade
from build_quay_yard_traffic_layout import segments_without,box_quads
from build_ground_traffic_network import CAUSEWAYS

ROOT=Path(__file__).resolve().parents[2]
OUTPUT=ROOT/'omniverse/scenes/quay_edge_guardrails.usda'
BASE='/World/QuayEdgeGuardrails'


def build():
    source=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))
    cache=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
    deck=cache.ComputeWorldBound(source.GetPrimAtPath('/World/SMART_PORT_OFFSHORE_QUAY/Decks/OffshoreQuayPlatform')).ComputeAlignedRange()
    edge=deck.GetMax()[0]-.20;lo=deck.GetMin()[1]+.25;hi=deck.GetMax()[1]-.25
    sea=deck.GetMin()[0]+.25
    spans=[(edge,a,edge,b) for a,b in segments_without(lo,hi,tuple((y-4.30,y+4.30) for y in CAUSEWAYS))]
    spans += [(sea,y,edge,y) for y in (lo,hi)]
    curb_count=len(spans)
    # Join the existing bridge rails instead of leaving exposed half-metre gaps.
    spans += [(edge,y+side*4.30,-201.,y+side*4.30) for y in CAUSEWAYS for side in (-1,1)]
    spans += [(-102.,y+side*4.30,-101.7,y+side*4.30) for y in CAUSEWAYS for side in (-1,1)]
    stage=Usd.Stage.CreateInMemory();UsdGeom.SetStageUpAxis(stage,'Z');UsdGeom.SetStageMetersPerUnit(stage,1.)
    # The original platform curb is continuous through every bridge mouth.
    # Replace it with the segmented Curbs mesh below; leaving it visible
    # creates a real, raised barrier across each otherwise-open access lane.
    legacy=stage.OverridePrim('/World/SMART_PORT_OFFSHORE_QUAY/Edges/LandsideEdge')
    UsdGeom.Imageable(legacy).CreateVisibilityAttr().Set('invisible')
    root=UsdGeom.Xform.Define(stage,BASE).GetPrim()
    root.CreateAttribute('guardrail:segments',Sdf.ValueTypeNames.Double4Array).Set([Gf.Vec4d(*s) for s in spans])
    root.CreateAttribute('guardrail:openingHalfWidth',Sdf.ValueTypeNames.Double).Set(4.30)
    root.CreateAttribute('guardrail:purpose',Sdf.ValueTypeNames.String).Set('Land-side quay edge and end caps; berth face and causeway lanes remain open')
    curbs=[];steel=[]
    def box(faces,x,y,z,w,d,h):faces.extend(tuple(reversed(face)) for face in box_quads(x,y,w,d,z-h/2,z+h/2))
    for index,(x1,y1,x2,y2) in enumerate(spans):
        dx,dy=x2-x1,y2-y1;length=math.hypot(dx,dy);horizontal=abs(dx)>abs(dy)
        if index<curb_count:box(curbs,(x1+x2)/2,(y1+y2)/2,.27,length if horizontal else .28,.28 if horizontal else length,.18)
        for z in (.67,1.30):box(steel,(x1+x2)/2,(y1+y2)/2,z,length if horizontal else .065,.065 if horizontal else length,.065)
        steps=max(1,math.ceil(length/4))
        for i in range(steps+1):box(steel,x1+dx*i/steps,y1+dy*i/steps,.81,.065,.065,1.08)
    for name,faces,color,metal,rough in (('Rails',steel,(.31,.34,.36),.75,.43),('Curbs',curbs,(.20,.19,.17),0.,.8)):
        mesh=UsdGeom.Mesh.Define(stage,BASE+'/'+name);points=[Gf.Vec3f(*p) for f in faces for p in f]
        mesh.CreatePointsAttr(points);mesh.CreateFaceVertexCountsAttr([4]*len(faces));mesh.CreateFaceVertexIndicesAttr(list(range(len(points))))
        mesh.CreateSubdivisionSchemeAttr('none');mesh.CreateDoubleSidedAttr(True);mesh.CreateExtentAttr(UsdGeom.PointBased(mesh).ComputeExtent(points))
        material=UsdShade.Material.Define(stage,BASE+'/Looks/'+name);shader=UsdShade.Shader.Define(stage,str(material.GetPath())+'/Surface')
        shader.CreateIdAttr('UsdPreviewSurface');shader.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
        shader.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(rough);shader.CreateInput('metallic',Sdf.ValueTypeNames.Float).Set(metal)
        material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(),'surface');UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
    temporary=OUTPUT.with_suffix('.building.usda');stage.GetRootLayer().Export(str(temporary));temporary.replace(OUTPUT)
    main=ROOT/'omniverse/scenes/smart_port.usda';text=main.read_text(encoding='utf-8')
    if '@./quay_edge_guardrails.usda@' not in text:
        main.write_text(text.replace('@./port_realism.usda@,','@./quay_edge_guardrails.usda@,\n        @./port_realism.usda@,',1),encoding='utf-8')
    print(f'Built {OUTPUT.name}: {len(spans)} connected spans, two batched meshes, four open causeways')


if __name__=='__main__':build()
