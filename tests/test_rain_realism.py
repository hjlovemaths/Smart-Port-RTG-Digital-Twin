"""Weather transition regressions: borrowed day/night look must be restored."""
from test_smart_port_environment import FakeStage, FakePrim, value
import smart_port_environment as weather


def test_rain_uses_same_overcast_dome_for_background_and_lighting():
    stage = FakeStage()
    weather.apply_environment_mode(stage, 'rain')
    assert 'rain_overcast_sky_360.png' in str(value(stage, weather.DAY_SKY_PATH, 'inputs:texture:file'))
    assert value(stage, '/Render/Viewport', 'omni:rtx:background:source:type') == 'domeLight'
    assert value(stage, '/Render/Viewport', 'omni:rtx:background:source:texture:path') == ''
    assert value(stage, weather.DAY_SUN_PATH, 'inputs:angle') == 12
    assert value(stage, weather.DAY_SKY_PATH, 'xformOp:rotateXYZ') == (-90, 0, 18)


def test_repeated_rain_changes_restore_previous_custom_session_values():
    stage = FakeStage()
    roughness = '/World/PortRealism/Looks/asphalt/roughness'
    sheen = weather.RAIN_EFFECT_PATH + '/WetRoadSheen'
    stage.prims[roughness] = FakePrim()
    stage.prims[sheen] = FakePrim()
    weather._set_attribute(stage.prims[roughness], 'inputs:scale', (.9,.9,.9,1), 'Float4')
    weather._set_attribute(stage.prims[weather.DAY_SKY_PATH], 'inputs:texture:file', 'custom_day.exr', 'Asset')
    weather._set_attribute(stage.prims[weather.DAY_SUN_PATH], 'inputs:angle', .65, 'Float')
    for amount in ('medium', 'heavy', 'light', 'torrential'):
        weather.apply_environment_mode(stage, 'rain')
        weather.apply_weather_controls(stage, rain_amount=amount)
        assert value(stage, roughness, 'inputs:scale')[0] < .9
        assert value(stage, sheen, 'visibility') == 'invisible'
    weather.apply_environment_mode(stage, 'night')
    assert value(stage, roughness, 'inputs:scale') == (.9,.9,.9,1)
    assert value(stage, weather.DAY_SKY_PATH, 'inputs:texture:file') == 'custom_day.exr'
    assert value(stage, weather.DAY_SUN_PATH, 'inputs:angle') == .65
    assert value(stage, sheen, 'visibility') is None
    assert stage not in weather._RAIN_LOOK_RESTORE


def test_missing_rain_texture_does_not_reuse_blue_day_sky(monkeypatch, tmp_path):
    monkeypatch.setattr(weather, 'RAIN_SKY_TEXTURE', tmp_path/'missing.png')
    stage = FakeStage()
    weather.apply_environment_mode(stage, 'rain')
    assert value(stage, weather.DAY_SKY_PATH, 'inputs:texture:file') == ''
    assert value(stage, '/Render/Viewport', 'omni:rtx:background:source:type') == 'color'


def test_amount_changes_do_not_redisplay_merged_source_phases():
    stage = FakeStage()
    weather._RAIN_SURFACE_EFFECTS[stage] = object()
    try:
        for amount in ('heavy', 'light', 'torrential', 'medium'):
            weather._configure_rain_layers(stage, amount, True)
            for paths in weather.RAIN_LAYER_PHASE_PATHS:
                for path in paths:
                    assert value(stage, path, 'visibility') == 'invisible'
    finally:
        weather._RAIN_SURFACE_EFFECTS.pop(stage, None)
