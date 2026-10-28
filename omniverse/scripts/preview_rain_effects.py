"""Reload only rain assets and benchmark the current Kit viewport.

Run this file in Kit Script Editor. No root/session layer is saved or cleared.
The benchmark changes Day/Rain presets temporarily and restores the original
weather controls in finally. Keep the camera and viewport size unchanged.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import statistics
import sys
import time
import types

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


def reload_rain_runtime(weather, stage):
    """Load project sources explicitly, including newly added Kit helpers."""
    with weather._session_edit_context(stage):
        weather._restore_rain_look(stage)
        for effects in getattr(weather, '_RAIN_SURFACE_EFFECTS', {}).values():
            effects.close()
    getattr(weather, '_RAIN_SURFACE_EFFECTS', {}).clear()
    getattr(weather, '_RAIN_PARTICLE_BATCHES', {}).clear()
    for name in ('rain_particle_motion', 'rain_surface_effects'):
        source = SCRIPT_DIR / (name + '.py')
        module = sys.modules.get(name) or types.ModuleType(name)
        module.__file__ = str(source)
        sys.modules[name] = module
        exec(compile(source.read_text(encoding='utf-8'), str(source), 'exec'), module.__dict__)
    source = SCRIPT_DIR / 'smart_port_environment.py'
    weather.__file__ = str(source)
    exec(compile(source.read_text(encoding='utf-8'), str(source), 'exec'), weather.__dict__)


def reload_rain_effects():
    import omni.usd
    import smart_port_environment as weather

    stage = omni.usd.get_context().get_stage()
    if stage is None:
        raise RuntimeError("Open smart_port.usda first")
    asset = SCRIPT_DIR.parent / "scenes" / "weather_effects.usda"
    layers = [layer for layer in stage.GetUsedLayers()
              if layer.realPath and Path(layer.realPath).resolve() == asset.resolve()]
    if len(layers) != 1 or layers[0].dirty:
        raise RuntimeError("Weather layer missing or has unsaved edits; reload cancelled")
    # Capture controls BEFORE reloading, without touching the live RTG driver.
    mode = weather.current_environment_mode(stage)
    layers[0].Reload()
    # Kit's fast-import hook can serve stale code even to importlib.reload.
    # Execute the known project source into the existing module so both the
    # bridge and receiver retain the same module reference, now with new code.
    reload_rain_runtime(weather, stage)
    weather.apply_environment_mode(stage, mode)
    print(f"[Rain] Loaded {weather.RAIN_EFFECT_REVISION}")
    return weather, stage


def summarize_samples(frame_times, viewport_fps, displayed_fps=None):
    frames = sorted(t for t in frame_times if t > 0)
    samples = sorted(f for f in viewport_fps if f > 0)
    displayed = sorted(f for f in (displayed_fps if displayed_fps is not None else viewport_fps) if f > 0)
    if not frames or not samples:
        raise RuntimeError("No valid frame samples; keep the viewport rendering")
    p99_ms = frames[min(len(frames) - 1, int(len(frames) * .99))] * 1000
    return {
        "app_average_fps": len(frames) / sum(frames),
        "app_p99_frame_ms": p99_ms,
        "viewport_median_fps": statistics.median(samples),
        "viewport_min_sampled_fps": min(samples),
        "displayed_median_fps": statistics.median(displayed),
        "displayed_min_sampled_fps": min(displayed),
        "viewport_samples": len(samples),
        # Sampled viewport FPS is the same smoothed metric as the Kit HUD;
        # don't mislabel these quarter-second readings as per-frame 1% lows.
        "meets_sampled_displayed_150_fps": min(displayed) >= 150,
        "meets_native_150_fps": min(samples) >= 150 and p99_ms <= 1000 / 150,
    }


async def benchmark_rain_effects(warmup_seconds=4.0, sample_seconds=10.0):
    import omni.kit.app
    import omni.usd
    from omni.kit.viewport.utility import get_active_viewport

    weather, stage = reload_rain_effects()
    app = omni.kit.app.get_app()
    viewport = get_active_viewport()
    if viewport is None:
        raise RuntimeError("No active viewport")
    env = stage.GetPrimAtPath(weather.ENVIRONMENT_ROOT_PATH)
    saved = {
        "mode": weather.current_environment_mode(stage),
        "rain_amount": env.GetAttribute(weather.RAIN_AMOUNT_ATTRIBUTE).Get(),
        "fog_amount": env.GetAttribute(weather.FOG_AMOUNT_ATTRIBUTE).Get(),
        "wind_speed_mps": env.GetAttribute(weather.WIND_SPEED_ATTRIBUTE).Get(),
    }
    report = {"revision": weather.RAIN_EFFECT_REVISION,
              "resolution": list(viewport.resolution),
              "camera": str(viewport.camera_path), "results": {},
              "fps_note": "Displayed FPS includes the existing frame generation multiplier; no renderer settings changed."}
    output = ROOT / "tmp" / "rain_benchmark.json"
    output.parent.mkdir(exist_ok=True)
    try:
        for amount in ("day", "light", "medium", "heavy", "torrential"):
            weather.apply_environment_mode(stage, "day" if amount == "day" else "rain")
            if amount != "day":
                weather.apply_weather_controls(stage, rain_amount=amount)
            start = time.perf_counter()
            while time.perf_counter() - start < warmup_seconds:
                await app.next_update_async()
            start = previous = last_fps_sample = time.perf_counter()
            frame_times, fps_samples, displayed_samples, multipliers = [], [], [], []
            while time.perf_counter() - start < sample_seconds:
                await app.next_update_async()
                if omni.usd.get_context().get_stage() != stage:
                    raise RuntimeError("Stage changed during benchmark")
                if list(viewport.resolution) != report["resolution"] or str(viewport.camera_path) != report["camera"]:
                    raise RuntimeError("Viewport size/camera changed; rerun with a fixed view")
                now = time.perf_counter()
                frame_times.append(now - previous)
                previous = now
                if now - last_fps_sample >= .25:
                    fps_samples.append(float(viewport.fps))
                    multiplier = max(1, int(viewport.frame_info.get("subframe_count", 1)))
                    displayed_samples.append(round(fps_samples[-1], 2) * multiplier)
                    multipliers.append(multiplier)
                    last_fps_sample = now
            report["results"][amount] = summarize_samples(frame_times, fps_samples, displayed_samples)
            report["results"][amount]["frame_generation_multipliers"] = sorted(set(multipliers))
            output.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"[Rain benchmark] {amount}: {report['results'][amount]}")
    finally:
        if omni.usd.get_context().get_stage() == stage:
            weather.apply_weather_controls(stage, **{k: v for k, v in saved.items() if k != "mode"})
            weather.apply_environment_mode(stage, saved["mode"])
    print(f"[Rain benchmark] Results: {output}")
    return report


if __name__ == "__main__":
    _rain_benchmark_task = asyncio.ensure_future(benchmark_rain_effects())
    def _report_failure(task):
        if not task.cancelled() and task.exception() is not None:
            import traceback
            traceback.print_exception(task.exception())
    _rain_benchmark_task.add_done_callback(_report_failure)
