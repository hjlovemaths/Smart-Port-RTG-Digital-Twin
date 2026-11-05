"""Three simultaneous RTX contexts, isolated driver servers and scoped release."""
import asyncio,sys,json,traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'omniverse/scripts'))
import omni.kit.app,omni.usd
from pxr import UsdGeom,Gf
import role_viewport
role_viewport.DRIVER_SIGNAL_PORT=49330
role_viewport.DRIVER_MEDIA_PORT=48026
role_viewport.DRIVER_RESOLUTION=(640,360)
from role_viewport import DriverViewport
async def run():
 app=omni.kit.app.get_app();view=rtg=None
 try:
  await omni.usd.get_context().new_stage_async()
  stage=omni.usd.get_context().get_stage()
  UsdGeom.Cube.Define(stage,'/World/Cube')
  camera=UsdGeom.Camera.Define(stage,'/World/DriverCamera');camera.AddTranslateOp().Set(Gf.Vec3d(0,0,10))
  admin_camera=UsdGeom.Camera.Define(stage,'/World/AdminCamera');admin_camera.AddTranslateOp().Set(Gf.Vec3d(0,0,20))
  rtg_camera=UsdGeom.Camera.Define(stage,'/World/RTGCamera');rtg_camera.AddTranslateOp().Set(Gf.Vec3d(0,0,30))
  from omni.kit.hydra_texture import create_hydra_texture
  omni.usd.create_hydra_engine('rtx',omni.usd.get_context())
  admin_texture=create_hydra_texture('AdminConcurrentTest',640,360,'','/World/AdminCamera','rtx',is_async=True)
  view=DriverViewport(stage,'/World/DriverCamera')
  rtg=DriverViewport(stage,'/World/RTGCamera','smartport_rtg_driver','RTGConcurrentTest',49340,48028)
  for _ in range(600):
   await app.next_update_async();view.tick();rtg.tick()
   if view.ready() and rtg.ready() and min(view.frames,rtg.frames)>=3:break
  assert view.ready(), 'Driver server failed to start: '+str(view.frames)
  assert rtg.ready(), 'RTG server failed to start: '+str(rtg.frames)
  assert view.encoder.signal_port!=rtg.encoder.signal_port
  assert rtg.texture.get_camera_path()=='/World/RTGCamera'
  rtg_before=rtg.frames;truck_before=view.frames
  before=admin_texture.get_frame_info()['frame_number']
  for _ in range(120):await app.next_update_async();view.tick();rtg.tick()
  assert rtg.frames>rtg_before and view.frames>truck_before, 'Both driver encoders must keep advancing'
  assert admin_texture.get_frame_info()['frame_number']>before, 'Admin rendering stopped while driver was active'
  assert admin_texture.get_camera_path()=='/World/AdminCamera'
  assert view.texture.get_camera_path()=='/World/DriverCamera'
  print('ROLE_VIEW_READY',view.frames,flush=True)
  info=view.texture.get_frame_info();print('ROLE_VIEW_FRAME',info,flush=True)
  assert info and info.get('frame_number',0)>0
  view.close();view=None
  rtg_before=rtg.frames
  for _ in range(60):await app.next_update_async();rtg.tick()
  assert rtg.frames>rtg_before, 'Truck shutdown stalled RTG'
  view=DriverViewport(stage,'/World/DriverCamera')
  for _ in range(600):
   await app.next_update_async();view.tick();rtg.tick()
   if view.ready() and view.frames>=3:break
  assert view.ready() and view.frames>=3, 'reacquire failed'
  truck_before=view.frames;rtg.close();rtg=None
  for _ in range(60):await app.next_update_async();view.tick()
  assert view.frames>truck_before, 'RTG shutdown stalled truck'
  before=admin_texture.get_frame_info()['frame_number']
  view.close();view=None
  for _ in range(60):await app.next_update_async()
  assert admin_texture.get_frame_info()['frame_number']>before, 'Driver shutdown stalled admin'
  admin_texture=None
  (ROOT/'.cache/role-view-smoke-result.json').write_text(json.dumps({'ok':True,'contexts':3,'isolated_ports':True,'scoped_release':True}))
 except Exception:
  (ROOT/'.cache/role-view-smoke-result.json').write_text(json.dumps({'ok':False,'error':traceback.format_exc()}));traceback.print_exc()
 finally:
  if view:view.close()
  if rtg:rtg.close()
  app.post_quit()
asyncio.ensure_future(run())
