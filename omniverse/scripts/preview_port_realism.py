"""Render matched before/after previews without saving camera/session changes."""
from pathlib import Path
import asyncio
import sys
from isaacsim import SimulationApp

ROOT=Path(__file__).resolve().parents[2]
STRUCTURE='--structure' in sys.argv
if STRUCTURE: sys.argv.remove('--structure')
FINISH='--finish' in sys.argv
if FINISH: sys.argv.remove('--finish')
OUT=ROOT/('outputs/port_finish_realism' if FINISH else 'outputs/port_structure_realism' if STRUCTURE else 'outputs/port_realism')
OUT.mkdir(parents=True,exist_ok=True)
app=SimulationApp({'headless':True,'multi_gpu':False,'renderer':'RaytracedLighting','width':1440,'height':900})
try:
    import omni.usd
    import carb.settings
    from pxr import Gf, UsdGeom
    from omni.kit.viewport.utility import get_active_viewport, capture_viewport_to_file
    ctx=omni.usd.get_context()
    ctx.open_stage(str(ROOT/'omniverse/scenes/smart_port.usda'))
    for _ in range(160): app.update()
    stage=ctx.get_stage()
    stage.SetEditTarget(stage.GetSessionLayer())
    import sys
    sys.path.insert(0,str(ROOT/'omniverse/scripts'))
    from smart_port_environment import apply_environment_mode
    apply_environment_mode(stage,'day')
    cam=UsdGeom.Camera.Define(stage,'/World/RealismReviewCamera')
    cam.CreateFocalLengthAttr(32)
    cam.CreateClippingRangeAttr(Gf.Vec2f(.1,5000))
    op=cam.AddTransformOp()
    vp=get_active_viewport()
    vp.camera_path=str(cam.GetPath())
    vp.set_texture_resolution((1440,900))
    layer=str(ROOT/('omniverse/scenes/port_finish_realism.usda' if FINISH else 'omniverse/scenes/port_structure_realism.usda' if STRUCTURE else 'omniverse/scenes/port_realism.usda'))
    views=[('quay',(-196,65,9),(-227,112,1)),('overview',(-310,-125,145),(-95,190,5))]
    if STRUCTURE:
        views=[('shore',(-155,80,5),(-101.5,142,-2)),
               ('berth',(-280,-30,8),(-238,42,-1)),
               ('fender',(-252,83,1),(-241.6,80,-2)),
               ('equipment',(-196,65,9),(-227,112,1)),
               ('overview',(-310,-125,100),(-95,190,0))]
    if FINISH:
        views=[('water',(-155,80,5),(-101.5,142,-2)),
               ('ship',(-280,-30,8),(-238,42,-1)),
               ('road',(-225,91,4),(-232,119,0)),
               ('overview',(-310,-125,100),(-95,190,0)),
               ('night',(-196,65,9),(-227,112,1))]
    for view,eye,target in views:
        if view=='night': apply_environment_mode(stage,'night')
        op.Set(Gf.Matrix4d(1).SetLookAt(Gf.Vec3d(*eye),Gf.Vec3d(*target),Gf.Vec3d(0,0,1)).GetInverse())
        for state in ('after','before'):
            stage.MuteLayer(layer) if state=='before' else stage.UnmuteLayer(layer)
            for _ in range(90): app.update()
            async def capture():
                await capture_viewport_to_file(vp,str(OUT/f'{view}_{state}.png')).wait_for_result(completion_frames=30)
            task=asyncio.ensure_future(capture())
            for _ in range(1200):
                app.update()
                # Give the asynchronous image writer wall time to finish.
                import time
                time.sleep(.01)
                if task.done(): break
            if not task.done(): raise RuntimeError('capture timeout')
            task.result()
            print(f'CAPTURE {view}_{state}',flush=True)
except Exception:
    import traceback
    traceback.print_exc()
    import sys
    sys.stderr.flush()
    raise
finally:
    app.close()
