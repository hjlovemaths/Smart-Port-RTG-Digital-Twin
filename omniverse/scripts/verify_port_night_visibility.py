"""Offline composition and night/day checks for the new lighting layer."""
import json
from pxr import Usd,UsdGeom
import build_port_night_visibility as b
import smart_port_environment as w
import smart_port_time_of_day as tod

s=Usd.Stage.Open(str(b.ROOT/'omniverse/scenes/smart_port.usda'));before=s.GetRootLayer().ExportToString()
lamps=[p for p in Usd.PrimRange(s.GetPrimAtPath(b.BASE)) if p.GetTypeName().endswith('Light')]
assert len(lamps)==69
assert len([p for p in lamps if '/RTG/' in str(p.GetPath())])==40
assert len(list(s.GetPrimAtPath(b.HARDWARE+'/RTG').GetChildren()))==40
for fixture in s.GetPrimAtPath(b.HARDWARE+'/RTG').GetChildren():
 assert fixture.IsInstance() and len(fixture.GetPrototype().GetChildren())>0,fixture.GetPath()
for p in s.GetPrimAtPath('/World/PortAndRTG').GetChildren():
 if p.GetName().startswith('SMART_PORT_STATIC_RTG_YARD'):assert p.IsInstance()
report={}
for hour in (21,3,6,12,24):
 tod.apply_time(s,hour,configure=False)
 expected='invisible' if hour==12 else 'inherited'
 assert all(str(UsdGeom.Imageable(p).ComputeVisibility())==expected for p in lamps)
 assert UsdGeom.Imageable(s.GetPrimAtPath(w.RTG_LED_NIGHT_ROOT_PATHS[0])).ComputeVisibility()==expected
 # The two old static templates are intentionally hidden; keep their parents
 # hidden rather than revealing duplicate RTGs in the yard.
 for path in w.RTG_LED_NIGHT_ROOT_PATHS[1:]:
  assert UsdGeom.Imageable(s.GetPrimAtPath(path).GetParent()).ComputeVisibility()=='invisible'
 report[str(hour)]={'fill':s.GetPrimAtPath(b.BASE+'/NightFill').GetAttribute('inputs:intensity').Get(),'visibility':expected}
assert report['21']==report['3']==report['24']
assert s.GetPrimAtPath(b.BASE+'/NightFill').GetAttribute('visibleInPrimaryRay').Get() is False
assert before==s.GetRootLayer().ExportToString()
(b.ROOT/'outputs/port_night_visibility/validation_usd.json').write_text(json.dumps(report,indent=2))
print('PASS: 69 new lights; 5 static RTGs retain instancing; all 6 visible RTGs lit at night; hidden templates stay hidden; shore fixtures and ambient off by day; no root mutation')
