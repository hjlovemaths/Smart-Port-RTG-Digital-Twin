"""Verify STS light aim, day/night control and attachment to moving assemblies."""
import json
from pxr import Gf,Usd,UsdGeom
import build_sts_work_lighting as b
import smart_port_time_of_day as tod
import smart_port_environment as w

s=Usd.Stage.Open(str(b.ROOT/'omniverse/scenes/smart_port.usda'));before=s.GetRootLayer().ExportToString()
roots=w.sts_night_light_paths(s);assert len(roots)==21
lamps=[p for root in roots for p in Usd.PrimRange(s.GetPrimAtPath(root)) if p.GetTypeName()=='DiskLight'];assert len(lamps)==63
cache=UsdGeom.XformCache()
for lamp in lamps:
 world=cache.GetLocalToWorldTransform(lamp);origin=world.ExtractTranslation();target=lamp.GetAttribute('smartPort:workTargetWorld').Get()
 aim=world.TransformDir(Gf.Vec3d(0,0,-1)).GetNormalized()
 assert Gf.Dot(aim,(target-origin).GetNormalized())>.9999,lamp.GetPath()
 assert 40<origin[2]<46,lamp.GetPath()
 assert not lamp.GetAttribute('inputs:shadow:enable').Get()
report={}
for hour in (21,3,6,12,24):
 tod.apply_time(s,hour,configure=False);expected='invisible' if hour==12 else 'inherited'
 assert all(UsdGeom.Imageable(p).ComputeVisibility()==expected for p in lamps)
 report[str(hour)]={'visibility':expected,'first_intensity':lamps[0].GetAttribute('inputs:intensity').Get()}
assert report['21']==report['3']==report['24']
for assembly in ('Fixed','Boom','Trolley'):
 path='/World/STSControls/STS_03/Gantry/'+assembly
 light=next(p for p in lamps if str(p.GetPath()).startswith(path+'/'))
 old=UsdGeom.XformCache().GetLocalToWorldTransform(light).ExtractTranslation()
 with Usd.EditContext(s,s.GetSessionLayer()):UsdGeom.Xformable(s.GetPrimAtPath(path)).AddTranslateOp(opSuffix='lightingAttachmentTest').Set(Gf.Vec3d(1,2,3))
 new=UsdGeom.XformCache().GetLocalToWorldTransform(light).ExtractTranslation()
 assert (new-old).GetLength()>1,assembly
assert before==s.GetRootLayer().ExportToString()
(b.ROOT/'outputs/sts_work_lighting/validation_usd.json').write_text(json.dumps(report,indent=2))
print('PASS: 7 cranes / 63 lights; correct ship-deck aim; all moving assemblies carry their lights; daylight off and midnight continuous; root unchanged')
