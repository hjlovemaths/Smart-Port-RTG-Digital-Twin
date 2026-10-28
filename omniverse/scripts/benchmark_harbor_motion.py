"""Compare the loaded and updated controllers in the current Kit viewport."""
import asyncio
import builtins
import importlib
import json
import statistics
import time
from pathlib import Path


async def benchmark():
    import carb.settings
    import omni.kit.app
    from omni.kit.viewport.utility import get_active_viewport
    from pxr import UsdGeom
    old=builtins._smart_port_sea_motion
    if old.closed or not old.enabled:
        raise RuntimeError('Enable sea motion before benchmarking')
    strength=old.strength
    held=set(old.held)
    smooth=old.batch_count==1
    app=omni.kit.app.get_app()
    viewport=get_active_viewport()
    stage=old.stage
    def camera():
        prim=stage.GetPrimAtPath(viewport.camera_path)
        return (str(viewport.camera_path),str(UsdGeom.XformCache().GetLocalToWorldTransform(prim)),str(viewport.resolution))
    async def sample(label,motion):
        start=time.perf_counter()
        while time.perf_counter()-start<1.5:
            await app.next_update_async()
        camera_before=camera()
        durations=[]
        costs=[]
        tick=motion.tick
        def timed_tick(dt):
            started=time.perf_counter()
            tick(dt)
            costs.append(time.perf_counter()-started)
        motion.tick=timed_tick
        started=last=time.perf_counter()
        try:
            while time.perf_counter()-started<5:
                await app.next_update_async()
                now=time.perf_counter()
                durations.append(now-last)
                last=now
        finally:
            motion.tick=tick
        result={'mode':label,'fps':round(len(durations)/sum(durations),2),
                'medianFrameMs':round(statistics.median(durations)*1000,3),
                'p95FrameMs':round(sorted(durations)[int(len(durations)*.95)]*1000,3),
                'controllerMsPerFrame':round(statistics.mean(costs)*1000,3) if costs else None,
                'cameraStable':camera_before==camera(),'camera':camera_before,'samples':len(durations)}
        print('HARBOR_BENCH',result)
        return result
    results=[]
    replacement=None
    try:
        results.append(await sample('before_same_quality',old))
        import ship_sea_motion
        module=importlib.reload(ship_sea_motion)
        replacement=module.start_sea_motion(stage)
        replacement.strength=strength
        replacement.set_quality(smooth)
        for path in held: replacement.hold(path)
        results.append(await sample('batched_same_quality',replacement))
        replacement.set_quality(False)
        results.append(await sample('batched_eco',replacement))
    finally:
        if replacement:
            final=module.show_sea_motion()
            final.strength=strength
            for path in held: final.hold(path)
    settings=carb.settings.get_settings()
    result={'measurements':results,'settings':{k:settings.get(k) for k in (
        '/rtx/post/dlss/execMode','/rtx/post/aa/op','/app/runLoops/main/rateLimitEnabled',
        '/app/runLoops/main/rateLimitFrequency','/app/vsync','/rtx/rendermode')},
        'finalMode':'eco','strength':strength}
    out=Path(__file__).resolve().parents[2]/'outputs/sea_motion'
    out.mkdir(parents=True,exist_ok=True)
    (out/'performance.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print('HARBOR_PERFORMANCE_DONE')


benchmark_task=asyncio.ensure_future(benchmark())
