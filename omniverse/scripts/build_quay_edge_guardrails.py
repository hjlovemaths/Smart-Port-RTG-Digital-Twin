"""Rail the quay and the non-berthing shore beside the 1C crossroad."""
import math
import re
from pathlib import Path
from pxr import Gf,Sdf,Usd,UsdGeom,UsdShade
from build_quay_yard_traffic_layout import LAYOUT_START_Y,segments_without,box_quads
from build_ground_traffic_network import CAUSEWAYS
from build_waterfront_amenities import SHORE_RAIL_X,SHORE_RAIL_SECTIONS,SHORE_RAIL_HEIGHTS

ROOT=Path(__file__).resolve().parents[2]
OUTPUT=ROOT/'omniverse/scenes/quay_edge_guardrails.usda'
BASE='/World/QuayEdgeGuardrails'
YARD_SHORE_START_X=-101.7  # End of the first causeway's south-side return.
YARD_SHORE_Y=LAYOUT_START_Y+.20
YARD_SHORE_END_X=SHORE_RAIL_X
YARD_SHORE_FIXTURE=re.compile(
    r'CODEx_berth_fender_bollard_rope_bind_\d+_(?:lower_mooring_line|upper_mooring_line|thin_shadow_twist_line)__USD_MESH'
    r'|CODEx_berth_fender_bollard_rope_bind_\d+_(?:bollard|fender)_(?:rope_knot|wrap_loop)'
    r'|SMART_PORT_(?:yellow_quay_bollard|quay_black_fender)_+\d+'
    r'|SMART_PORT_DOCKED_BERTH_FILL_berth_slot_mark_\d+'
    r'|SMART_PORT_quay_edge_white_cap'
)


def build():
    source=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))
    for layer in source.GetLayerStack():
        if Path(layer.realPath)==OUTPUT:
            source.MuteLayer(layer.identifier)
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
    quay_span_count=len(spans)
    # The south side of the first causeway joins the yard shore. Continue the
    # rail from that edge without putting a crossbar through the bridge mouth.
    spans += [(YARD_SHORE_START_X,CAUSEWAYS[0]-4.30,YARD_SHORE_START_X,YARD_SHORE_Y),
              (YARD_SHORE_START_X,YARD_SHORE_Y,YARD_SHORE_END_X,YARD_SHORE_Y),
              (YARD_SHORE_END_X,YARD_SHORE_Y,YARD_SHORE_END_X,SHORE_RAIL_SECTIONS[0][0])]
    stage=Usd.Stage.CreateInMemory();UsdGeom.SetStageUpAxis(stage,'Z');UsdGeom.SetStageMetersPerUnit(stage,1.)
    port=source.GetPrimAtPath('/World/PortAndRTG')
    removed=0
    for fixture in port.GetChildren():
        if not YARD_SHORE_FIXTURE.fullmatch(fixture.GetName()):
            continue
        bounds=cache.ComputeWorldBound(fixture).ComputeAlignedRange()
        x,y,_=bounds.GetMidpoint()
        if YARD_SHORE_START_X<=x<=YARD_SHORE_END_X and abs(y-LAYOUT_START_Y)<2.3:
            stage.OverridePrim(fixture.GetPath()).SetActive(False)
            removed+=1
    # The original platform curb is continuous through every bridge mouth.
    # Replace it with the segmented Curbs mesh below; leaving it visible
    # creates a real, raised barrier across each otherwise-open access lane.
    legacy=stage.OverridePrim('/World/SMART_PORT_OFFSHORE_QUAY/Edges/LandsideEdge')
    UsdGeom.Imageable(legacy).CreateVisibilityAttr().Set('invisible')
    root=UsdGeom.Xform.Define(stage,BASE).GetPrim()
    root.CreateAttribute('guardrail:segments',Sdf.ValueTypeNames.Double4Array).Set([Gf.Vec4d(*s) for s in spans])
    root.CreateAttribute('guardrail:openingHalfWidth',Sdf.ValueTypeNames.Double).Set(4.30)
    root.CreateAttribute('guardrail:purpose',Sdf.ValueTypeNames.String).Set('Land-side quay edge, end caps and non-berthing yard shore; berth face and causeway lanes remain open')
    curbs=[];steel=[]
    def box(faces,x,y,z,w,d,h):faces.extend(tuple(reversed(face)) for face in box_quads(x,y,w,d,z-h/2,z+h/2))
    for index,(x1,y1,x2,y2) in enumerate(spans):
        dx,dy=x2-x1,y2-y1;length=math.hypot(dx,dy);horizontal=abs(dx)>abs(dy)
        yard_shore=index>=quay_span_count
        if index<curb_count or yard_shore:
            box(curbs,(x1+x2)/2,(y1+y2)/2,.175 if yard_shore else .27,
                length if horizontal else .28,.28 if horizontal else length,.18)
        heights=SHORE_RAIL_HEIGHTS if index==len(spans)-1 else (.67,1.30)
        for z in heights:box(steel,(x1+x2)/2,(y1+y2)/2,z,length if horizontal else .065,.065 if horizontal else length,.065)
        steps=max(1,math.ceil(length/4))
        for i in range(steps+1):
            box(steel,x1+dx*i/steps,y1+dy*i/steps,.7625 if yard_shore else .81,
                .065,.065,1.175 if yard_shore else 1.08)
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
    print(f'Built {OUTPUT.name}: {len(spans)} spans, four open causeways, {removed} legacy shore fixtures hidden')


if __name__=='__main__':build()
