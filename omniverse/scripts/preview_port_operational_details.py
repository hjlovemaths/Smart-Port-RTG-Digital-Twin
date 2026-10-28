"""Kit preview: compare matched views, attach layer without reopening dirty stage."""
import asyncio, json, time
from pathlib import Path
from pxr import Gf, Sdf, Usd, UsdGeom
import omni.usd
import omni.kit.app
from omni.kit.viewport.utility import get_active_viewport, capture_viewport_to_file
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/port_operational_details'
VIEWS=globals().get('PORT_DETAIL_VIEWS',[('overview_12',(195,430,150),(20,260,0),12),('coastal_16',(-65,269,12),(-87.3,276,1.2),16),('safety_16',(123,303,8),(109.6,312,1.0),16),('coastal_21',(-65,269,12),(-87.3,276,1.2),21)])
REPORT_NAME=globals().get('PORT_DETAIL_REPORT','validation_live.json')
async def run():
 s=omni.usd.get_context().get_stage(); vp=get_active_viewport(); app=omni.kit.app.get_app()
 selection=omni.usd.get_context().get_selection(); selected=selection.get_selected_prim_paths(); selection.set_selected_prim_paths([],False)
 original_camera=str(vp.camera_path); before=s.GetRootLayer().ExportToString(); edit=s.GetEditTarget()
 layer=str(ROOT/'omniverse/scenes/port_operational_details.usda')
 existing=Sdf.Layer.Find(layer)
 if existing: existing.Reload()
 if './port_operational_details.usda' not in s.GetRootLayer().subLayerPaths: s.GetRootLayer().subLayerPaths.insert(0,'./port_operational_details.usda')
 if layer in s.GetSessionLayer().subLayerPaths: s.GetSessionLayer().subLayerPaths.remove(layer)
 before=s.GetRootLayer().ExportToString()
 (OUT/'live_root_before.txt').write_text(before,encoding='utf-8')
 import smart_port_environment as w
 import smart_port_time_of_day as tod
 import importlib, harbor_daylight
 importlib.reload(harbor_daylight)
 importlib.reload(w)
 saved_mode,saved_hour=w.current_environment_mode(s),tod.current_hour(s)
 # Refresh only this layer's lamp calibration after an authored power revision.
 with Usd.EditContext(s,s.GetSessionLayer()):
  lights=s.GetPrimAtPath(w.NIGHT_STREET_LIGHTS_PATH+'/WayfindingLights')
  if lights:
   for lamp in Usd.PrimRange(lights):
    if lamp.GetTypeName()=='DiskLight':
     lamp.RemoveProperty('smartPort:unscaledLampIntensity')
     lamp.GetAttribute('inputs:intensity').Clear()
 report={'resolution':list(vp.resolution),'views':{}}
 try:
  with Usd.EditContext(s,s.GetSessionLayer()):
   cam=UsdGeom.Camera.Define(s,'/World/PortDetailsReviewCamera'); cam.CreateFocalLengthAttr(22); cam.CreateClippingRangeAttr(Gf.Vec2f(.1,5000))
   xf=UsdGeom.Xformable(cam); xf.ClearXformOpOrder(); op=xf.AddTransformOp()
  for name,eye,target,hour in VIEWS:
   with Usd.EditContext(s,s.GetSessionLayer()): op.Set(Gf.Matrix4d(1).SetLookAt(Gf.Vec3d(*eye),Gf.Vec3d(*target),Gf.Vec3d(0,0,1)).GetInverse())
   vp.camera_path=str(cam.GetPath())
   report['views'][name]={}
   for state in ('before','after'):
    s.MuteLayer(layer) if state=='before' else s.UnmuteLayer(layer)
    tod.apply_time(s,hour)
    start=time.perf_counter()
    while time.perf_counter()-start<8: await app.next_update_async()
    samples=[]; multi=[]; start=time.perf_counter(); last=0
    while time.perf_counter()-start<5:
     await app.next_update_async()
     if time.perf_counter()-last>.2:
      last=time.perf_counter(); samples.append(float(vp.fps)); multi.append(max(1,int(vp.frame_info.get('subframe_count',1))))
    report['views'][name][state]={'native_mean':sum(samples)/len(samples),'native_min':min(samples),'framegen_multipliers':sorted(set(multi)),'displayed_mean':sum(a*b for a,b in zip(samples,multi))/len(samples)}
    await capture_viewport_to_file(vp,str(OUT/(name+'_'+state+'.png'))).wait_for_result()
 finally:
  s.UnmuteLayer(layer); w.apply_environment_mode(s,saved_mode); tod.apply_time(s,saved_hour); vp.camera_path=original_camera
  for _ in range(10): await app.next_update_async()
  # Kit writes camera exposure opinions into the root even for a session camera.
  # Remove only this temporary review camera's specs after rebinding the viewport.
  with Usd.EditContext(s,s.GetSessionLayer()): s.RemovePrim('/World/PortDetailsReviewCamera')
  with Usd.EditContext(s,s.GetRootLayer()): s.RemovePrim('/World/PortDetailsReviewCamera')
  s.SetEditTarget(edit)
  selection.set_selected_prim_paths(selected,False)
  report['root_unchanged']=before==s.GetRootLayer().ExportToString()
  (OUT/'live_root_after.txt').write_text(s.GetRootLayer().ExportToString(),encoding='utf-8')
  (OUT/REPORT_NAME).write_text(json.dumps(report,indent=2))
 print('PORT_DETAILS_DONE',report)
service_preview_task=asyncio.ensure_future(run())
