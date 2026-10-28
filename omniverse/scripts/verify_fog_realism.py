"""Run in Kit Script Editor; capture three fog grades without saving the stage."""
import asyncio
import difflib
import json
from pathlib import Path
import runpy
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'outputs/fog_realism'

async def verify():
    import carb.settings
    import omni.kit.app
    import omni.usd
    from omni.kit.viewport.utility import get_active_viewport, capture_viewport_to_file
    from pxr import UsdGeom
    import smart_port_environment as weather
    from preview_rain_effects import summarize_samples
    stage = omni.usd.get_context().get_stage()
    app = omni.kit.app.get_app()
    vp = get_active_viewport()
    settings = carb.settings.get_settings()
    OUT.mkdir(parents=True, exist_ok=True)
    root_before = stage.GetRootLayer().ExportToString()
    def camera():
        return {'path': str(vp.camera_path), 'resolution': list(vp.resolution),
                'matrix': [[round(float(v), 6) for v in row] for row in UsdGeom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath(vp.camera_path))]}
    report = {'camera': camera(), 'results': {}}
    runpy.run_path(str(ROOT/'omniverse/scripts/preview_rain_effects.py'))['reload_rain_runtime'](weather, stage)
    weather.apply_environment_mode(stage, 'fog')
    report['revision'] = weather.FOG_EFFECT_REVISION
    try:
        for grade in ('light', 'medium', 'heavy'):
            weather.apply_weather_controls(stage, fog_amount=grade)
            start = time.perf_counter()
            while time.perf_counter()-start < 3:
                await app.next_update_async()
            frames, native, displayed, multipliers = [], [], [], []
            start = prev = sampled = time.perf_counter()
            while time.perf_counter()-start < 6:
                await app.next_update_async()
                if camera() != report['camera']:
                    raise RuntimeError('Camera changed; fixed-view fog test cancelled')
                now = time.perf_counter()
                frames.append(now-prev); prev=now
                if now-sampled >= .25:
                    native.append(float(vp.fps))
                    m = max(1, int(vp.frame_info.get('subframe_count',1)))
                    multipliers.append(m); displayed.append(native[-1]*m); sampled=now
            result = summarize_samples(frames,native,displayed)
            result['frame_generation_multipliers'] = sorted(set(multipliers))
            result['meets_sampled_displayed_140_fps'] = min(displayed) >= 140
            result['fog_settings'] = {a.GetName():str(a.Get()) for a in stage.GetPrimAtPath(vp.render_product_path).GetAttributes() if a.GetName().startswith('omni:rtx:fog:')}
            report['results'][grade] = result
            (OUT/'performance.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            await capture_viewport_to_file(vp,str(OUT/(grade+'.png'))).wait_for_result()
        weather.apply_environment_mode(stage,'day')
        for _ in range(10): await app.next_update_async()
        report['validation'] = {'root_layer_unchanged':stage.GetRootLayer().ExportToString()==root_before,
            'camera_unchanged':camera()==report['camera'],
            'legacy_global_fog_enabled_unused': bool(settings.get('/rtx/fog/enabled')),
            'day_render_product_fog_disabled': all(not p.GetAttribute('omni:rtx:fog:enabled').Get() for p in stage.Traverse() if p.GetTypeName()=='RenderProduct')}
        report['root_changes'] = list(difflib.unified_diff(root_before.splitlines(),stage.GetRootLayer().ExportToString().splitlines()))[:150]
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        weather.apply_environment_mode(stage,'fog')
        weather.apply_weather_controls(stage,fog_amount='medium')
        (OUT/'performance.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('FOG_VERIFY_DONE', report)

if __name__ == '__main__':
    fog_verify_task = asyncio.ensure_future(verify())
    def done(task):
        if not task.cancelled() and task.exception():
            import traceback
            traceback.print_exception(task.exception())
    fog_verify_task.add_done_callback(done)
