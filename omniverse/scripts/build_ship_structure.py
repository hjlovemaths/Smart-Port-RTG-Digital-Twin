"""Render only valid locations from an STS 6.6 response as structural slots."""
import argparse
from collections import Counter
import json
from pathlib import Path
import zlib
from pxr import Gf, Sdf, Usd, UsdGeom
from large_vessel_layout import ROOT,SHIP,DECK_Z,row_x,bay_y
from ship_structure_data import DEFAULT_SOURCE,read_structure,tier_center
from ship_yard_container_models import create_yard_prototypes,PAINTS
from large_vessel_details import deck_height

OUTPUT=ROOT/'omniverse/scenes/ship_structure_boxes.usda'
SLOTS=SHIP+'/StructureSlots'


def longitudinal_center(bay):
    if bay%2 or bay==62:
        return bay_y(bay)
    a,b=bay_y(bay-1),bay_y(bay+1)
    if b-a>6.5:
        raise ValueError(f'40-foot bay {bay} crosses the bridge gap')
    return (a+b)/2


def support_height(y,feet):
    half=(6.058 if feet==20 else 12.192)/2
    lo,hi=y-half,y+half
    heights=[DECK_Z+.125]
    if lo<=28:
        heights.append(max(deck_height(lo,DECK_Z),deck_height(min(hi,28),DECK_Z))+.15)
    if hi>=223:
        heights.append(max(deck_height(max(lo,223),DECK_Z),deck_height(hi,DECK_Z))+.15)
    return max(heights)


def build(source=DEFAULT_SOURCE):
    data=read_structure(source)
    stage=Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage,1)
    UsdGeom.SetStageUpAxis(stage,'Z')
    stage.SetDefaultPrim(UsdGeom.Xform.Define(stage,'/World').GetPrim())
    root=stage.OverridePrim(SHIP)
    def attr(prim,name,kind,value):
        prim.CreateAttribute(name,kind,custom=True).Set(value)
    # Remove every hand-authored demo box and its schematic bay plan.
    # The source hull, fittings, hatch geometry and prototypes are retained.
    stage.OverridePrim(SHIP+'/Model').SetActive(False)
    stage.OverridePrim(SHIP+'/BayPlan').SetActive(False)
    attr(root,'ship:containerCount',Sdf.ValueTypeNames.Int,0)
    attr(root,'ship:structureSlotCount',Sdf.ValueTypeNames.Int,len(data['slots']))
    attr(root,'ship:visualization',Sdf.ValueTypeNames.String,'6.6 vessel structure; occupancy unknown')
    slot_root=UsdGeom.Xform.Define(stage,SLOTS).GetPrim()
    attr(slot_root,'source:filename',Sdf.ValueTypeNames.String,Path(source).name)
    attr(slot_root,'source:vbtId',Sdf.ValueTypeNames.String,str(data['vbt_id']))
    attr(slot_root,'source:units',Sdf.ValueTypeNames.String,'metre geometry uses project standard dimensions; source dimensions are unspecified')
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
        z=tier_center(slot['tier'],DECK_Z,max_hold)
        if slot['zone']=='D':
            base_tier=stack_min[slot['bay'],slot['row']]
            # The first returned tier varies by stack (e.g. starts at 84).
            # Keep the source label while grounding the visible stack on its
            # actual foredeck/hatch surface. Full model height is 2.591 m.
            support=support_height(y,slot['size_ft'])
            z=support+(slot['tier']-base_tier)/2*2.591+2.591/2
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
