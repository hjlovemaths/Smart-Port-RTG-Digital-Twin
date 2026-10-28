"""Run with runpy in Kit; restore the selected weather/hour after capture."""
import asyncio
import gc
import importlib
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/"outputs/time_of_day"


async def verify():
    import omni.usd
    import omni.kit.app
    from omni.kit.viewport.utility import get_active_viewport, capture_viewport_to_file
    import smart_port_environment as w
    import smart_port_time_of_day as tod
    import harbor_daylight
    from pxr import UsdGeom
    from preview_rain_effects import summarize_samples
    stage = omni.usd.get_context().get_stage()
    app = omni.kit.app.get_app()
    vp = get_active_viewport()
    if not tod._environment_ready(stage) or vp is None:
        raise RuntimeError("Wait for the harbor and active viewport to finish loading")
    OUT.mkdir(parents=True, exist_ok=True)
    before = stage.GetRootLayer().ExportToString()
    saved_mode, saved_hour = w.current_environment_mode(stage), tod.current_hour(stage)
    importlib.reload(harbor_daylight)
    importlib.reload(tod)
    report = {"resolution": list(vp.resolution), "camera": str(vp.camera_path),
              "samples": {}, "target_displayed_fps": 140,
              "fps_note": "Displayed estimate includes the existing frame-generation subframes; native FPS is recorded separately."}
    camera = stage.GetPrimAtPath(str(vp.camera_path))
    def camera_matrix():
        return str(UsdGeom.XformCache().GetLocalToWorldTransform(camera)) if camera else None
    camera_transform = camera_matrix()

    def check_fixed_view():
        if omni.usd.get_context().get_stage() is not stage:
            raise RuntimeError("Stage changed during verification")
        if list(vp.resolution) != report["resolution"] or str(vp.camera_path) != report["camera"]:
            raise RuntimeError("Viewport resolution/camera changed during verification")
        if camera_matrix() != camera_transform:
            raise RuntimeError("Camera moved during verification")

    try:
        w.apply_environment_mode(stage, "day")
        for hour in (0, 6.5, 12, 17.5, 18.5, 21+5/60, 24):
            tod.apply_time(stage, hour)
            start = time.perf_counter()
            while time.perf_counter()-start < 4:
                await app.next_update_async()
                check_fixed_view()
            frames, native, displayed, multipliers = [], [], [], set()
            start = previous = sampled = time.perf_counter()
            while time.perf_counter()-start < 5:
                await app.next_update_async()
                check_fixed_view()
                now = time.perf_counter()
                frames.append(now-previous); previous = now
                if now-sampled >= .25:
                    sampled = now
                    multiplier = max(1, int(vp.frame_info.get("subframe_count", 1)))
                    multipliers.add(multiplier)
                    native.append(float(vp.fps))
                    displayed.append(float(vp.fps)*multiplier)
            result = summarize_samples(frames, native, displayed)
            result["frame_generation_multipliers"] = sorted(multipliers)
            result["meets_sampled_displayed_140_fps"] = result["displayed_min_sampled_fps"] >= 140
            report["samples"][str(hour)] = result
            await capture_viewport_to_file(vp, str(OUT/f"hour_{hour}.png")).wait_for_result()
        for mode in ("rain", "fog", "day"):
            w.apply_environment_mode(stage, mode)
            w.apply_weather_controls(stage)
            assert tod.current_hour(stage) == 24
            for _ in range(5): await app.next_update_async()
        report["weather_retains_hour"] = True
        report["all_sampled_views_above_140"] = all(
            sample["meets_sampled_displayed_140_fps"] for sample in report["samples"].values())
        report["visual_review_required"] = True
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        if omni.usd.get_context().get_stage() is stage:
            w.apply_environment_mode(stage, saved_mode)
            tod.apply_time(stage, saved_hour, configure=False)
            for obj in gc.get_objects():
                if type(obj).__name__ == "SmartPortRTGBridgeExtension" and getattr(obj, "_time_slider", None):
                    obj._time_syncing = True
                    try:
                        obj._time_slider.model.set_value(saved_hour)
                        obj._time_pending = None
                    finally:
                        obj._time_syncing = False
            for _ in range(10): await app.next_update_async()
        report["root_layer_unchanged"] = before == stage.GetRootLayer().ExportToString()
        (OUT/"validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("TIME_OF_DAY_CAPTURED_REVIEW_REQUIRED", report)


if __name__ == "__main__":
    time_verify_task = asyncio.ensure_future(verify())
