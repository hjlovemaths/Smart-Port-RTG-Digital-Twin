"""Kit preview: compare matched views, attach layer without reopening dirty stage."""
import asyncio, json, time
from pathlib import Path
from pxr import Gf, Sdf, Usd, UsdGeom
import omni.usd
import omni.kit.app
from omni.kit.viewport.utility import get_active_viewport, capture_viewport_to_file
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/port_service_areas'
async def run():
 s=omni.usd.get_context().get_stage(); vp=get_active_viewport(); app=omni.kit.app.get_app()
 original_camera=str(vp.camera_path); before=s.GetRootLayer().ExportToString(); edit=s.GetEditTarget()
 layer=str(ROOT/'omniverse/scenes/port_service_areas.usda')
 existing=Sdf.Layer.Find(layer)
 if existing: existing.Reload()
 if './port_service_areas.usda' not in s.GetRootLayer().subLayerPaths: s.GetRootLayer().subLayerPaths.insert(0,'./port_service_areas.usda')
 if layer in s.GetSessionLayer().subLayerPaths: s.GetSessionLayer().subLayerPaths.remove(layer)
 before=s.GetRootLayer().ExportToString()
 (OUT/'live_root_before.txt').write_text(before,encoding='utf-8')
 report={'resolution':list(vp.resolution),'views':{}}
 try:
  with Usd.EditContext(s,s.GetSessionLayer()):
   cam=UsdGeom.Camera.Define(s,'/World/ServiceAreaReviewCamera'); cam.CreateFocalLengthAttr(30); cam.CreateClippingRangeAttr(Gf.Vec2f(.1,5000))
   xf=UsdGeom.Xformable(cam); xf.ClearXformOpOrder(); op=xf.AddTransformOp()
  for name,eye,target in [('overview',(195,555,95),(30,466,0)),('control_center',(190,526,48),(138,465,5)),('bay40',(-50,529,37),(4,471,0))]:
   with Usd.EditContext(s,s.GetSessionLayer()): op.Set(Gf.Matrix4d(1).SetLookAt(Gf.Vec3d(*eye),Gf.Vec3d(*target),Gf.Vec3d(0,0,1)).GetInverse())
   vp.camera_path=str(cam.GetPath())
   report['views'][name]={}
   for state in ('before','after'):
    s.MuteLayer(layer) if state=='before' else s.UnmuteLayer(layer)
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
  s.UnmuteLayer(layer); vp.camera_path=original_camera
  for _ in range(10): await app.next_update_async()
  # Kit writes camera exposure opinions into the root even for a session camera.
  # Remove only this temporary review camera's specs after rebinding the viewport.
  with Usd.EditContext(s,s.GetSessionLayer()): s.RemovePrim('/World/ServiceAreaReviewCamera')
  with Usd.EditContext(s,s.GetRootLayer()): s.RemovePrim('/World/ServiceAreaReviewCamera')
  s.SetEditTarget(edit)
  report['root_unchanged']=before==s.GetRootLayer().ExportToString()
  (OUT/'live_root_after.txt').write_text(s.GetRootLayer().ExportToString(),encoding='utf-8')
  (OUT/'validation_live.json').write_text(json.dumps(report,indent=2))
 print('SERVICE_AREAS_PREVIEW_DONE',report)
service_preview_task=asyncio.ensure_future(run())
