import math
import sys
from types import ModuleType, SimpleNamespace
import pytest
from test_smart_port_environment import FakePrim, FakeStage, value, environment as w
from smart_port_time_of_day import lighting_at, apply_time, TIME_ATTRIBUTE
import smart_port_time_of_day as tod


@pytest.mark.parametrize("stage_kind", ["none", "empty", "invalid"])
def test_startup_time_read_tolerates_unloaded_environment(stage_kind):
    stage = FakeStage()
    if stage_kind == "none":
        stage = None
    elif stage_kind == "empty":
        stage.prims.clear()
    else:
        stage.prims[w.ENVIRONMENT_ROOT_PATH].IsValid = lambda: False
    assert tod.current_hour(stage) == 12
    tod.reapply_if_set(stage)


def test_panel_builds_before_stage_load_and_syncs_without_writing_scene(monkeypatch):
    class Widget:
        def __init__(self, *args, **kwargs):
            self.model = self
            self.text = args[0] if args else ""
            self.callbacks = []
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def set_value(self, value):
            self.value = value
            for callback in self.callbacks:
                callback(self)
        def get_value_as_float(self): return self.value
        def add_value_changed_fn(self, callback): self.callbacks.append(callback)

    empty = FakeStage()
    empty.prims.clear()
    context = SimpleNamespace(get_stage=lambda: empty)
    omni = ModuleType("omni")
    usd = ModuleType("omni.usd")
    usd.get_context = lambda: context
    ui = ModuleType("omni.ui")
    ui.HStack = ui.Label = ui.FloatSlider = Widget
    omni.usd, omni.ui = usd, ui
    for name, module in (("omni", omni), ("omni.usd", usd), ("omni.ui", ui)):
        monkeypatch.setitem(sys.modules, name, module)
    owner = SimpleNamespace()
    tod.build_controls(owner)
    assert owner._time_label.text == "12:00"
    assert owner._time_pending is None
    tod.tick(owner)
    assert owner._time_stage is None

    loaded = FakeStage()
    loaded.prims[w.ENVIRONMENT_ROOT_PATH].CreateAttribute(TIME_ATTRIBUTE, None).Set(9.5)
    context.get_stage = lambda: loaded
    applied = []
    monkeypatch.setattr(tod, "apply_time", lambda stage, hour, **kwargs: applied.append((stage, hour)))
    tod.tick(owner)
    assert owner._time_label.text == "09:30"
    assert owner._time_pending is None
    assert applied == [(loaded, 9.5)]

    # A drag during loading is deferred, then applied when the scene is ready.
    context.get_stage = lambda: empty
    owner._time_slider.model.set_value(22)
    tod.tick(owner)
    assert owner._time_pending == 22
    context.get_stage = lambda: loaded
    tod.tick(owner)
    assert applied == [(loaded, 9.5), (loaded, 22)]
    assert owner._time_pending is None

    # Creating a viewport after lighting initialization must bind it once,
    # without continuously rewriting lighting on every frame.
    monkeypatch.setattr(tod, "_viewport_product_key", lambda stage: "/Render/LateViewport")
    tod.tick(owner)
    assert applied[-1] == (loaded, 9.5)
    count = len(applied)
    tod.tick(owner)
    assert len(applied) == count

    # A product exists before its first rendered frame. Rebind once after
    # settling, preserving the stage hour rather than the slider default.
    monkeypatch.setattr(tod.time, "monotonic", lambda: owner._time_settle_at + 1)
    monkeypatch.setattr(tod, "_viewport_is_rendering", lambda: False)
    tod.tick(owner)
    assert len(applied) == count
    monkeypatch.setattr(tod, "_viewport_is_rendering", lambda: True)
    tod.tick(owner)
    assert len(applied) == count + 1
    assert applied[-1] == (loaded, 9.5)
    assert owner._time_settle_at is None
    monkeypatch.setattr(tod.time, "monotonic", lambda: 1000)
    tod.tick(owner)
    assert len(applied) == count + 1


def test_noon_restores_texture_fallback_without_usd_bindings(monkeypatch):
    monkeypatch.setattr(tod, "_has_usd_bindings", lambda: False)
    stage = FakeStage()
    sky = stage.prims[w.DAY_SKY_PATH]
    sky.CreateAttribute("inputs:texture:file", None).Set("")
    sky.CreateAttribute("visibility", None).Set("invisible")
    apply_time(stage, 12)
    assert value(stage, w.DAY_SKY_PATH, "inputs:intensity") == 650
    assert value(stage, w.DAY_SKY_PATH, "visibility") == "inherited"
    assert value(stage, w.DAY_SKY_PATH, "inputs:texture:format") == "latlong"
    assert value(stage, w.DAY_SKY_PATH, "inputs:texture:file") == w.DAY_BACKGROUND_TEXTURE.resolve().as_posix()
    assert value(stage, "/Render/Viewport", "omni:rtx:lights:visibilityOverride") == "perLight"
    assert value(stage, "/Render/Viewport", "omni:rtx:background:source:type") == "domeLight"


def test_midnight_wrap_and_bad_inputs():
    assert lighting_at(0)["rotation"] == lighting_at(24)["rotation"]
    assert lighting_at(0)["daylight"] == 0
    for bad in (-1, 25, math.nan, math.inf):
        with pytest.raises(ValueError):
            lighting_at(bad)


def test_sunrise_and_sunset_are_continuous():
    for h in (5, 6, 7, 12, 17, 18, 19):
        assert abs(lighting_at(h-.001)["daylight"]-lighting_at(h+.001)["daylight"]) < .002
    assert lighting_at(12)["sun"] > lighting_at(7)["sun"] > lighting_at(0)["sun"]


def test_direct_sun_is_absent_below_horizon_and_sky_has_twilight():
    assert lighting_at(5.5)["sun"] == 0
    assert lighting_at(18.5)["sun"] == 0
    assert lighting_at(5.5)["twilight"] > lighting_at(0)["twilight"]
    for hour in (6, 18):
        before, after = lighting_at(hour-.0001), lighting_at(hour+.0001)
        for key in ("sun", "twilight", "sky_daylight", "sky_strength"):
            assert abs(before[key]-after[key]) < .001


@pytest.mark.parametrize("hour", [20, 21, 21+5/60, 23, 0, 24])
def test_late_evening_sky_is_dim_even_on_texture_fallback(hour, monkeypatch):
    monkeypatch.setattr(tod, "_has_usd_bindings", lambda: False)
    noon = lighting_at(12)
    night = lighting_at(hour)
    assert night["sky_strength"] < .01 * noon["sky_strength"]
    assert night["sky_daylight"] == 0
    assert night["sun"] == 0
    stage = FakeStage()
    apply_time(stage, 12)
    noon_intensity = value(stage, w.DAY_SKY_PATH, "inputs:intensity")
    apply_time(stage, hour)
    assert value(stage, w.DAY_SKY_PATH, "inputs:intensity") < .01 * noon_intensity
    assert value(stage, w.NIGHT_STREET_LIGHTS_PATH, "visibility") == "inherited"


def test_sky_fades_through_evening_without_a_hard_switch():
    strengths = [lighting_at(h)["sky_strength"] for h in (17, 18, 18.5, 19, 20, 21)]
    assert strengths == sorted(strengths, reverse=True)


def test_lamps_fade_without_compounding_and_weather_restores_power(monkeypatch):
    import harbor_daylight as harbor
    pxr = ModuleType("pxr")
    pxr.Usd = SimpleNamespace(PrimRange=lambda root: [root])
    pxr.Sdf = SimpleNamespace(ValueTypeNames=SimpleNamespace(Float=None))
    monkeypatch.setitem(sys.modules, "pxr", pxr)
    stage = FakeStage()
    lamp = FakePrim("SphereLight")
    lamp.CreateAttribute("inputs:intensity", None).Set(1000.)
    stage.prims[w.NIGHT_STREET_LIGHTS_PATH] = lamp
    for _ in range(5):
        harbor._fade_lamps(stage, lighting_at(0), True)
    assert lamp.GetAttribute("inputs:intensity").Get() == pytest.approx(700)
    harbor._fade_lamps(stage, {"daylight": .325}, True)
    assert lamp.GetAttribute("inputs:intensity").Get() == pytest.approx(350)
    harbor._fade_lamps(stage, lighting_at(12), True)
    assert lamp.GetAttribute("inputs:intensity").Get() == 0
    harbor._fade_lamps(stage, lighting_at(0), False)
    assert lamp.GetAttribute("inputs:intensity").Get() == 1000


def test_clear_cycle_switches_lights_and_preserves_controls():
    stage = FakeStage()
    w.apply_environment_mode(stage, "day")
    w.apply_weather_controls(stage, wind_speed_mps=9, rain_amount="heavy")
    for hour, lights in ((0, "inherited"), (12, "invisible"), (24, "inherited")):
        apply_time(stage, hour)
        assert value(stage, w.NIGHT_STREET_LIGHTS_PATH, "visibility") == lights
        assert value(stage, w.ENVIRONMENT_ROOT_PATH, TIME_ATTRIBUTE) == hour
        assert value(stage, w.ENVIRONMENT_ROOT_PATH, w.WIND_SPEED_ATTRIBUTE) == 9


@pytest.mark.parametrize("mode", ["rain", "fog"])
def test_weather_and_grade_changes_retain_selected_time(mode):
    stage = FakeStage()
    w.apply_environment_mode(stage, mode)
    apply_time(stage, 0)
    intensity = value(stage, w.DAY_SKY_PATH, "inputs:intensity")
    w.apply_weather_controls(stage)
    assert value(stage, w.DAY_SKY_PATH, "inputs:intensity") == pytest.approx(intensity)
    assert w.current_environment_mode(stage) == mode
    apply_time(stage, 12)
    assert value(stage, w.DAY_SKY_PATH, "inputs:intensity") > intensity
    assert w.current_environment_mode(stage) == mode
