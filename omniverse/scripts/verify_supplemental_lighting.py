"""Check composed positions and dusk/dawn switching without editing saved layers."""
import json
from pathlib import Path
from pxr import Usd, UsdGeom
import smart_port_time_of_day as tod
import build_supplemental_night_lighting as b

stage=Usd.Stage.Open(str(b.ROOT/'omniverse/scenes/smart_port.usda'))
before=stage.GetRootLayer().ExportToString()
lamps=list(Usd.PrimRange(stage.GetPrimAtPath(b.LIGHTS)))
lamps=[p for p in lamps if p.GetTypeName()=='DiskLight']
assert len(lamps)==41
cache=UsdGeom.XformCache()
for lamp,(_,x,y,h,arm,temp,power) in zip(lamps,b.placements()):
    pos=cache.GetLocalToWorldTransform(lamp).ExtractTranslation()
    assert (pos-(x+arm,y,h-.25)).GetLength()<.001
    assert lamp.GetAttribute('inputs:shadow:enable').Get() is False
report={}
for hour in (21,0,3,5.5,6,6.5,7,12,24):
    tod.apply_time(stage,hour,configure=False)
    report[str(hour)]={'visibility':str(UsdGeom.Imageable(lamps[0]).ComputeVisibility()),'intensity':lamps[0].GetAttribute('inputs:intensity').Get()}
assert report['21']==report['3']==report['24']==report['0']
assert report['12']['visibility']=='invisible'
assert report['3']['intensity']>0
assert report['6']['intensity']>report['6.5']['intensity']>=report['7']['intensity']
assert before==stage.GetRootLayer().ExportToString()
(b.ROOT/'outputs/supplemental_lighting/validation_usd.json').write_text(json.dumps(report,indent=2))
print('PASS: 41 composed lights, correct world positions, midnight continuity, dawn fade and noon off; root unchanged')
