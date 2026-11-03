"""Render only valid locations from an STS 6.6 response as structural slots."""
import argparse
from collections import Counter
import json
from pathlib import Path
import zlib
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade
from large_vessel_layout import (
    ROOT,SHIP,DECK_Z,TIER_PITCH,row_x,STRUCTURE_HULL_SCALE,
    STRUCTURE_Y_OFFSET,STRUCTURE_SHIP_X,STRUCTURE_BEAM_M,STRUCTURE_DECK_Z,
    STRUCTURE_BAY_PITCH,structure_bay_y,BAYS,bay_y,STRUCTURE_STS_BAYS,
)
from ship_structure_data import DEFAULT_SOURCE,read_structure,tier_center
from ship_yard_container_models import create_yard_prototypes,PAINTS
from large_vessel_details import deck_height

OUTPUT=ROOT/'omniverse/scenes/ship_structure_boxes.usda'
SLOTS=SHIP+'/StructureSlots'


# Same longitudinal spacing and native geometry as the 20-foot yard boxes.
DISPLAY_BAY_PITCH=STRUCTURE_BAY_PITCH

def longitudinal_center(bay):
    # 6.6 locations define logical cells, not measured metre coordinates.
    # Keep the first foredeck bay anchored and fit the full 01--59/62 layout.
    return structure_bay_y(bay)


def support_height(y,feet):
    half=(2.636 if feet==20 else 5.272)/2
    # Evaluate the original deck profile in design coordinates, then apply the
    # same vertical fit used by the actual rendered deck geometry.
    lo=(y-half-STRUCTURE_Y_OFFSET)/STRUCTURE_HULL_SCALE[1]
    hi=(y+half-STRUCTURE_Y_OFFSET)/STRUCTURE_HULL_SCALE[1]
    heights=[DECK_Z+.125]
    if lo<=28:
        heights.append(max(deck_height(lo,DECK_Z),deck_height(min(hi,28),DECK_Z))+.15)
    if hi>=223:
        heights.append(max(deck_height(max(lo,223),DECK_Z),deck_height(hi,DECK_Z))+.15)
    return max(heights)*STRUCTURE_HULL_SCALE[2]


def build(source=DEFAULT_SOURCE):
    data=read_structure(source)
    stage=Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage,1)
    UsdGeom.SetStageUpAxis(stage,'Z')
    stage.SetDefaultPrim(UsdGeom.Xform.Define(stage,'/World').GetPrim())
    root=stage.OverridePrim(SHIP)
    def attr(prim,name,kind,value):
        prim.CreateAttribute(name,kind,custom=True).Set(value)
    # Scale hull/fittings only: the box prototypes retain native yard size.
    # Keep the waterline Z and the quay-side hull edge fixed while fitting.
    root.CreateAttribute('xformOp:translate',Sdf.ValueTypeNames.Double3).Set(Gf.Vec3d(STRUCTURE_SHIP_X,0,-4.9478))
    fit=(Gf.Matrix4d(1).SetScale(Gf.Vec3d(*STRUCTURE_HULL_SCALE))*
         Gf.Matrix4d(1).SetTranslate(Gf.Vec3d(0,STRUCTURE_Y_OFFSET,0)))
    for name in ('Hull','Deck','Hatches','Bridge','EndDetails'):
        xf=UsdGeom.Xform.Define(stage,SHIP+'/'+name)
        component_fit=fit
        if name=='Bridge':
            bridge_y=(structure_bay_y(43)+structure_bay_y(45))/2
            component_fit=(Gf.Matrix4d(1).SetScale(Gf.Vec3d(*STRUCTURE_HULL_SCALE))*
                           Gf.Matrix4d(1).SetTranslate(Gf.Vec3d(0,bridge_y-162.8*STRUCTURE_HULL_SCALE[1],0)))
        xf.AddTransformOp(UsdGeom.XformOp.PrecisionDouble,'compactHull').Set(component_fit)
    for name,value in {'lengthMeters':248*STRUCTURE_HULL_SCALE[1],
                       'beamMeters':STRUCTURE_BEAM_M,'deckZ':STRUCTURE_DECK_Z,
                       'bridgeY':(structure_bay_y(43)+structure_bay_y(45))/2}.items():
        attr(root,'ship:'+name,Sdf.ValueTypeNames.Double,value)
    attr(root,'ship:hullDisplayScale',Sdf.ValueTypeNames.Double3,Gf.Vec3d(*STRUCTURE_HULL_SCALE))
    attr(root,'ship:referenceBays',Sdf.ValueTypeNames.IntArray,list(BAYS))
    # Reserve every reference bay independently of the partial 6.6 snapshot.
    # These are logical layout markers, with no invented cargo/occupancy.
    for bay in BAYS:
        marker=UsdGeom.Xform.Define(stage,SHIP+f'/ReferenceBayLayout/Bay{bay:02d}')
        marker.AddTranslateOp().Set(Gf.Vec3d(0,structure_bay_y(bay),STRUCTURE_DECK_Z))
        attr(marker.GetPrim(),'ship:bay',Sdf.ValueTypeNames.Int,bay)
        attr(marker.GetPrim(),'ship:sizeFt',Sdf.ValueTypeNames.Int,40 if bay==62 else 20)
        attr(marker.GetPrim(),'ship:representation',Sdf.ValueTypeNames.String,'reference_layout_not_confirmed_cargo')
    # Align every hatch pair with the tighter bay pitch without changing hull
    # length. The bridge keeps its own accommodation gap between 43 and 45.
    sy=STRUCTURE_HULL_SCALE[1]
    ratio=STRUCTURE_BAY_PITCH/(6.3*sy)
    for first in range(1,60,4):
        old=(bay_y(first)+bay_y(first+2))/2
        new=((structure_bay_y(first)+structure_bay_y(first+2))/2-STRUCTURE_Y_OFFSET)/sy
        hatch=UsdGeom.Xform.Define(stage,SHIP+f'/Hatches/Hatch_{first:02d}_{first+2:02d}')
        hatch.AddTransformOp(UsdGeom.XformOp.PrecisionDouble,'baySpacing').Set(
            Gf.Matrix4d(1).SetScale(Gf.Vec3d(1,ratio,1))*
            Gf.Matrix4d(1).SetTranslate(Gf.Vec3d(0,new-old*ratio,0)))
    # Close the working deck behind the compact cargo layout and support 62.
    def deck_cube(path,center_y,length):
        cube=UsdGeom.Cube.Define(stage,SHIP+path)
        cube.CreateSizeAttr(1)
        cube.AddTranslateOp().Set(Gf.Vec3d(0,(center_y-STRUCTURE_Y_OFFSET)/sy,DECK_Z))
        cube.AddScaleOp().Set(Gf.Vec3d(40.6-1.4,length/sy,.25))
        UsdShade.MaterialBindingAPI.Apply(cube.GetPrim()).Bind(UsdShade.Material(stage.OverridePrim(SHIP+'/Looks/HatchSteel')))
    deck_cube('/Hatches/Stern62Cover',structure_bay_y(62),5.3)
    aft_start=structure_bay_y(62)+5.3/2
    aft_end=223*sy+STRUCTURE_Y_OFFSET
    if aft_end>aft_start:deck_cube('/Deck/AfterCargoDeck',(aft_start+aft_end)/2,aft_end-aft_start)
    # Read the original composed assets without this override: useful both for
    # source deck numerals and measured crane body spans/unchanged track datums.
    design=Usd.Stage.Open(str(ROOT/'omniverse/scenes/smart_port.usda'))
    design.MuteLayer(OUTPUT.as_posix())
    numbers=UsdGeom.Mesh(design.GetPrimAtPath(SHIP+'/Deck/BayNumbers')).GetPointsAttr().Get()
    adjusted=[]
    for point in numbers:
        bay=min(BAYS,key=lambda b:abs(point[1]-bay_y(b)))
        adjusted.append(Gf.Vec3f(point[0],point[1]+(structure_bay_y(bay)-STRUCTURE_Y_OFFSET)/sy-bay_y(bay),point[2]))
    UsdGeom.Mesh(stage.OverridePrim(SHIP+'/Deck/BayNumbers')).CreatePointsAttr(adjusted)
    # Position the first three cranes along the same vessel, changing Y only.
    # Repartition their independent travel slots using the actual crane width,
    # so even simultaneous opposite-limit commands keep 3 m mesh clearance.
    crane_bounds=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default','render'])
    names=list(STRUCTURE_STS_BAYS)
    homes={name:structure_bay_y(bay) for name,bay in STRUCTURE_STS_BAYS.items()}
    spans={}
    for name in names:
        prim=design.GetPrimAtPath('/World/STSControls/'+name)
        b=crane_bounds.ComputeWorldBound(prim).ComputeAlignedBox()
        old=prim.GetAttribute('sts:homeY').Get()
        spans[name]=max(old-b.GetMin()[1],b.GetMax()[1]-old)
    for i,name in enumerate(names):
        path='/World/STSControls/'+name
        original=design.GetPrimAtPath(path)
        p=stage.OverridePrim(path);position=Gf.Vec3d(original.GetAttribute('xformOp:translate').Get())
        position[1]=homes[name]
        low=-7 if i==0 else (homes[names[i-1]]+homes[name])/2+spans[name]+1.5
        next_y=homes[names[i+1]] if i+1<len(names) else design.GetPrimAtPath('/World/STSControls/STS_04').GetAttribute('sts:homeY').Get()
        high=(homes[name]+next_y)/2-spans[name]-1.5
        if not low<=homes[name]<=high:raise ValueError('Insufficient crane clearance for '+name)
        p.CreateAttribute('xformOp:translate',Sdf.ValueTypeNames.Double3).Set(position)
        for key,value in {'homeY':homes[name],'gantryMin':low-homes[name],'gantryMax':high-homes[name]}.items():
            attr(p,'sts:'+key,Sdf.ValueTypeNames.Double,value)
        attr(p,'sts:servingVessel',Sdf.ValueTypeNames.String,SHIP)
    # Remove every hand-authored demo box and its schematic bay plan.
    # The source hull, fittings, hatch geometry and prototypes are retained.
    stage.OverridePrim(SHIP+'/Model').SetActive(False)
    stage.OverridePrim(SHIP+'/BayPlan').SetActive(False)
    attr(root,'ship:displayScale',Sdf.ValueTypeNames.String,'yard_native')
    attr(root,'ship:containerCount',Sdf.ValueTypeNames.Int,0)
    attr(root,'ship:structureSlotCount',Sdf.ValueTypeNames.Int,len(data['slots']))
    attr(root,'ship:visualization',Sdf.ValueTypeNames.String,'6.6 vessel structure; occupancy unknown')
    slot_root=UsdGeom.Xform.Define(stage,SLOTS).GetPrim()
    attr(slot_root,'source:filename',Sdf.ValueTypeNames.String,Path(source).name)
    attr(slot_root,'source:displayBayPitch',Sdf.ValueTypeNames.Double,DISPLAY_BAY_PITCH)
    attr(slot_root,'source:layoutAssumption',Sdf.ValueTypeNames.String,'reference silhouette and full 01--59/62 bay layout; native yard boxes; not measured vessel coordinates')
    attr(slot_root,'source:vbtId',Sdf.ValueTypeNames.String,str(data['vbt_id']))
    attr(slot_root,'source:units',Sdf.ValueTypeNames.String,'yard-native compact display geometry; source dimensions are unspecified')
    attr(slot_root,'source:sizeCodeMeaning',Sdf.ValueTypeNames.String,'C/D/1 retained verbatim; length follows the user-specified odd/even bay rule')
    max_hold=max([s['tier'] for s in data['slots'] if s['zone']=='H'],default=16)
    prototypes=create_yard_prototypes(stage)
    stack_min={}
    for slot in data['slots']:
        if slot['zone']=='D':
            key=(slot['bay'],slot['row'])
            stack_min[key]=min(stack_min.get(key,slot['tier']),slot['tier'])
    for group in data['groups']:
        name=f"Bay{group['bay']:02d}_{group['zone']}"
        p=UsdGeom.Xform.Define(stage,SLOTS+'/'+name).GetPrim()
        for n,kind,value in [('bay',Sdf.ValueTypeNames.Int,group['bay']),('zone',Sdf.ValueTypeNames.String,group['zone']),
                             ('bayId',Sdf.ValueTypeNames.String,str(group['bay_id'])),
                             ('rowsBySeq',Sdf.ValueTypeNames.IntArray,[v for _,v in sorted(group['rows'].items())]),
                             ('tiersBySeq',Sdf.ValueTypeNames.IntArray,[v for _,v in sorted(group['tiers'].items())])]:
            attr(p,'source:'+n,kind,value)
    counts=Counter()
    for slot in sorted(data['slots'],key=lambda x:x['location']):
        path=SLOTS+f"/Bay{slot['bay']:02d}_{slot['zone']}/L{slot['location']}"
        p=UsdGeom.Xform.Define(stage,path)
        paint=PAINTS[zlib.crc32(slot['location'].encode('ascii'))%len(PAINTS)]
        p.GetPrim().GetReferences().AddInternalReference(prototypes[slot['size_ft'],paint])
        # RTX can keep stale prototypes when a live layer replaces the copied
        # source parts of several color variants. Plain references force each
        # visible box to refresh completely, as for the live yard itself.
        p.GetPrim().SetInstanceable(False)
        y=longitudinal_center(slot['bay'])
        height=stage.GetPrimAtPath(prototypes[slot['size_ft'],paint]).GetAttribute('ship:displayDimensions').Get()[2]
        z=STRUCTURE_DECK_Z-.2*STRUCTURE_HULL_SCALE[2]-(max_hold-slot['tier'])/2*TIER_PITCH-height/2
        if slot['zone']=='D':
            base_tier=stack_min[slot['bay'],slot['row']]
            # The first returned tier varies by stack (e.g. starts at 84).
            # Keep the source label while grounding the visible stack on its
            # actual foredeck/hatch surface. Dimensions are copied directly from the matching yard model.
            support=support_height(y,slot['size_ft'])
            z=support+(slot['tier']-base_tier)/2*TIER_PITCH+height/2
            attr(p.GetPrim(),'ship:displayBaseTier',Sdf.ValueTypeNames.Int,base_tier)
            attr(p.GetPrim(),'ship:supportLocalZ',Sdf.ValueTypeNames.Double,support)
        attr(p.GetPrim(),'ship:yardPaint',Sdf.ValueTypeNames.String,paint)
        p.AddTranslateOp().Set(Gf.Vec3d(row_x(slot['row']),longitudinal_center(slot['bay']),
                                      z))
        for key in ('bay','row','tier','size_ft','row_seq','tier_seq'):
            name={'size_ft':'sizeFt','row_seq':'rowSeq','tier_seq':'tierSeq'}.get(key,key)
            attr(p.GetPrim(),'ship:'+name,Sdf.ValueTypeNames.Int,slot[key])
        for key,name in [('location','location'),('zone','zone'),('size_code','sizeCode')]:
            attr(p.GetPrim(),'ship:'+name,Sdf.ValueTypeNames.String,slot[key])
        attr(p.GetPrim(),'ship:representation',Sdf.ValueTypeNames.String,'structural_slot_not_confirmed_cargo')
        attr(p.GetPrim(),'source:rfFg',Sdf.ValueTypeNames.String,str(slot['source'].get('rfFg','')))
        attr(p.GetPrim(),'source:vesselWgtClass',Sdf.ValueTypeNames.String,str(slot['source'].get('vesselWgtClass','')))
        counts[slot['zone']]+=1
    # Preserve raw definitions, including repeated-hatch normalization and zero dimensions.
    attr(slot_root,'source:hatchDefinitions',Sdf.ValueTypeNames.String,json.dumps(data['hatches'],ensure_ascii=False))
    attr(slot_root,'source:hatchCovers',Sdf.ValueTypeNames.String,json.dumps(data['hatch_covers'],ensure_ascii=False))
    stage.GetRootLayer().documentation='Strict 6.6 structural locations; no inferred occupancy, missing slots, or invented C/D/1 size dictionary.'
    temporary=OUTPUT.with_name('ship_structure_boxes.building.usda')
    temporary.write_text(stage.GetRootLayer().ExportToString(),encoding='utf-8')
    temporary.replace(OUTPUT)
    return {'output':str(OUTPUT),'slots':len(data['slots']),'hold':counts['H'],'deck':counts['D'],
            'bays':sorted({s['bay'] for s in data['slots']}),'size_codes':dict(Counter(s['size_code'] for s in data['slots']))}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=DEFAULT_SOURCE)
    print(json.dumps(build(parser.parse_args().source),ensure_ascii=False))
