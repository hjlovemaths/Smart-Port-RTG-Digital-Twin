"""Fixed-view live rain comparison. Run with runpy from Kit Script Editor.

No renderer quality, resolution, camera or stage files are changed. Screenshots
are outside the measured interval. The original weather controls are restored.
"""
import asyncio
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'outputs/rain_realism'


async def verify(compare_loaded=False, warmup_seconds=3., sample_seconds=6.):
    import carb.settings
    import omni.kit.app
    import omni.usd
    from omni.kit.viewport.utility import get_active_viewport, capture_viewport_to_file
    from pxr import UsdGeom
    import smart_port_environment as weather
    from preview_rain_effects import summarize_samples

    OUT.mkdir(parents=True, exist_ok=True)
    stage = omni.usd.get_context().get_stage()
    vp = get_active_viewport()
    app = omni.kit.app.get_app()
    saved_mode = weather.current_environment_mode(stage)
    env = stage.GetPrimAtPath(weather.ENVIRONMENT_ROOT_PATH)
    saved_amount = env.GetAttribute(weather.RAIN_AMOUNT_ATTRIBUTE).Get()
    def camera_state():
        return {'path': str(vp.camera_path), 'resolution': list(vp.resolution),
                'transform': [[round(float(v), 6) for v in row] for row in
                              UsdGeom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath(vp.camera_path))]}
    camera = camera_state()
    settings = carb.settings.get_settings()
    report = {'camera': camera, 'results': {}, 'warmup_seconds': warmup_seconds,
              'sample_seconds': sample_seconds, 'settings': {k: settings.get(k) for k in (
        '/rtx/rendermode', '/rtx/post/dlss/execMode', '/rtx/post/aa/op')},
        'note': 'Displayed FPS includes existing frame generation. Minimum is a 250 ms sampled HUD metric, not a per-frame guarantee.'}

    async def sample(label):
        start = time.perf_counter()
        while time.perf_counter()-start < warmup_seconds:
            await app.next_update_async()
        start = prev = last_sample = time.perf_counter()
        frames, native, displayed, mult = [], [], [], []
        while time.perf_counter()-start < sample_seconds:
            await app.next_update_async()
            if omni.usd.get_context().get_stage() != stage or camera_state() != camera:
                report['invalidated_camera'] = camera_state()
                (OUT/'performance.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
                raise RuntimeError('Stage, camera pose or resolution changed; comparison cancelled')
            now = time.perf_counter()
            frames.append(now-prev); prev = now
            if now-last_sample >= .25:
                native.append(float(vp.fps))
                m = max(1, int(vp.frame_info.get('subframe_count', 1)))
                mult.append(m); displayed.append(native[-1]*m); last_sample = now
        result = summarize_samples(frames, native, displayed)
        result['frame_generation_multipliers'] = sorted(set(mult))
        effects = weather._RAIN_SURFACE_EFFECTS.get(stage)
        if effects:
            result['surface_contacts'] = {
                'roof_rectangles': len(effects.roofs), 'surface_refreshes': effects.version,
                'ground_impacts': effects.pool.total_ground, 'roof_impacts': effects.pool.total_roof,
                'splash_capacity': effects.pool.CAPACITY,
            }
        else:
            raise RuntimeError('Surface effect runtime is absent; rain validation failed')
        report['results'][label] = result
        (OUT/'performance.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('RAIN_REALISM', label, result)
        await capture_viewport_to_file(vp, str(OUT/(label+'.png'))).wait_for_result()

    (OUT/'performance.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    try:
        if compare_loaded:
            await sample('before_warmed')
        import runpy
        root_before_reload = stage.GetRootLayer().ExportToString()
        runpy.run_path(str(ROOT/'omniverse/scripts/preview_rain_effects.py'))['reload_rain_runtime'](weather, stage)
        assert root_before_reload == stage.GetRootLayer().ExportToString()
        report['revision'] = weather.RAIN_EFFECT_REVISION
        weather.apply_environment_mode(stage, 'rain')
        for amount in ('heavy', 'torrential', 'medium', 'light'):
            weather.apply_weather_controls(stage, rain_amount=amount)
            await sample(amount)
        # Verify actual USD session opinions, not just the fake-stage tests.
        restore = dict(weather._RAIN_LOOK_RESTORE.get(stage, {}))
        root_before = stage.GetRootLayer().ExportToString()
        # Check restoration before the next preset intentionally hides the
        # source rain phases. Night's visibility is a new weather setting.
        with weather._session_edit_context(stage):
            weather._restore_rain_look(stage)
        restored = True
        for (path, name), (authored, value) in restore.items():
            spec = stage.GetSessionLayer().GetAttributeAtPath(f'{path}.{name}')
            has_default = bool(spec and spec.HasInfo('default'))
            restored &= has_default == authored and (not authored or spec.default == value)
        weather.apply_environment_mode(stage, 'night')
        report['validation'] = {'session_look_restored': restored,
                                'root_layer_unchanged': root_before == stage.GetRootLayer().ExportToString(),
                                'camera_unchanged': camera_state() == camera,
                                'impact_meshes_hidden_outside_rain': all(stage.GetPrimAtPath(p).GetAttribute('visibility').Get() == 'invisible' for p in (
                                    '/World/SMART_PORT_WEATHER/Rain/AirRain/ContactSplashes',
                                    '/World/SMART_PORT_WEATHER/Rain/AirRain/FallingDrops'))}
        (OUT/'performance.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    finally:
        if omni.usd.get_context().get_stage() == stage:
            weather.apply_weather_controls(stage, rain_amount=saved_amount)
            weather.apply_environment_mode(stage, saved_mode)
    print('RAIN_REALISM_DONE', str(OUT/'performance.json'))
    return report


if __name__ == '__main__':
    rain_realism_task = asyncio.ensure_future(verify())
    def report_error(task):
        if not task.cancelled() and task.exception():
            import traceback
            traceback.print_exception(task.exception())
    rain_realism_task.add_done_callback(report_error)
