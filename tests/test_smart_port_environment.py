from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "omniverse" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import smart_port_environment as environment  # noqa: E402


class FakeAttribute:
    def __init__(self, value=None):
        self.value = value

    def IsValid(self):
        return True

    def Get(self):
        return self.value

    def Set(self, value):
        self.value = value

    def Clear(self):
        self.value = None


class FakePrim:
    def __init__(self, type_name=""):
        self.attributes = {}
        self.type_name = type_name

    def IsValid(self):
        return True

    def GetAttribute(self, name):
        return self.attributes.get(name)

    def CreateAttribute(self, name, _type_name):
        attribute = FakeAttribute()
        self.attributes[name] = attribute
        return attribute

    def GetTypeName(self):
        return self.type_name


class FakeStage:
    def __init__(self):
        self.prims = {
            environment.ENVIRONMENT_ROOT_PATH: FakePrim(),
            environment.DAY_SKY_PATH: FakePrim(),
            environment.DAY_SUN_PATH: FakePrim(),
            environment.NIGHT_STREET_LIGHTS_PATH: FakePrim(),
            environment.RAIN_EFFECT_PATH: FakePrim(),
            environment.AIR_RAIN_EFFECT_PATH: FakePrim(),
            environment.ATMOSPHERE_OVERLAY_PATH: FakePrim(),
            environment.ATMOSPHERE_OVERLAY_SHADER_PATH: FakePrim(),
            f"{environment.WEATHER_ROOT_PATH}/Looks/RainStreakMaterial/PreviewSurface": FakePrim(),
            f"{environment.WEATHER_ROOT_PATH}/Looks/WetRoadMaterial/PreviewSurface": FakePrim(),
            **{path: FakePrim() for path in environment.RAIN_LAYER_PATHS},
            **{
                path: FakePrim()
                for phase_paths in environment.RAIN_LAYER_PHASE_PATHS
                for path in phase_paths
            },
            **{path: FakePrim() for path in environment.RAIN_SPLASH_LAYER_PATHS},
            **{path: FakePrim() for path in environment.RAIN_MIST_LAYER_PATHS},
            **{path: FakePrim() for path in environment.RTG_LED_NIGHT_ROOT_PATHS},
            **{path: FakePrim() for path in environment.RTG_LED_MODEL_ROOT_PATHS},
            "/Render/Viewport": FakePrim("RenderProduct"),
        }

    def GetPrimAtPath(self, path):
        return self.prims.get(path)

    def Traverse(self):
        return iter(self.prims.values())


def value(stage, path, name):
    return stage.prims[path].attributes[name].value


@pytest.fixture(autouse=True)
def reset_animation_caches():
    environment._RAIN_LAST_ANIMATION_UPDATE.clear()
    environment._RAIN_FALL_DISTANCES.clear()
    environment._RAIN_CAMERA_ANCHORS.clear()
    environment._ATMOSPHERE_OVERLAY_LAST_UPDATE.clear()


def test_environment_mode_aliases_and_labels_are_ready_for_panel_buttons():
    assert environment.normalize_environment_mode("白天") == "day"
    assert environment.normalize_environment_mode("夜晚") == "night"
    assert environment.normalize_environment_mode("雨天") == "rain"
    assert environment.normalize_environment_mode("雾天") == "fog"
    assert environment.environment_mode_label("day") == "白天"
    assert environment.environment_mode_label("night") == "夜晚"
    assert environment.environment_mode_label("rain") == "雨天"
    assert environment.environment_mode_label("fog") == "雾天"
    assert environment.normalize_rain_amount("暴雨") == "torrential"
    assert environment.normalize_fog_amount("浓雾") == "heavy"
    with pytest.raises(ValueError):
        environment.normalize_environment_mode("snow")


def test_three_rtg_under_beam_floodlights_share_the_night_preset():
    assert len(environment.RTG_LED_NIGHT_ROOT_PATHS) == 3
    assert all(
        path.endswith("SMART_PORT_RTG_LED_NIGHT_LIGHTS")
        for path in environment.RTG_LED_NIGHT_ROOT_PATHS
    )


def test_three_rtg_led_model_roots_share_the_night_preset():
    assert len(environment.RTG_LED_MODEL_ROOT_PATHS) == 3
    assert all(
        path.endswith("SMART_PORT_RTG_LED_MODELS")
        for path in environment.RTG_LED_MODEL_ROOT_PATHS
    )


def test_all_3d_hud_materials_are_registered_for_self_illumination():
    assert len(environment.HUD_EMISSIVE_MATERIAL_PATHS) == 7
    assert "/World/Looks/RTG_RunInfo_Floating_Billboard" in environment.HUD_EMISSIVE_MATERIAL_PATHS
    assert "/World/Looks/RTG_Task_Info_Panel" in environment.HUD_EMISSIVE_MATERIAL_PATHS
    assert "/World/Looks/RTG_Device_Location_Panel_Live" in environment.HUD_EMISSIVE_MATERIAL_PATHS


def test_environment_switch_hides_light_icons_without_hiding_light_output():
    source = (SCRIPT_DIR / "smart_port_environment.py").read_text(encoding="utf-8")
    assert "toggle_light_visibility(viewport_api=viewport, visible=False)" in source
    assert 'settings.set_bool("/app/viewport/show/lights", False)' in source
    assert "display_options & ~(1 << 8)" in source


def test_night_mode_dims_environment_and_turns_on_street_lights():
    stage = FakeStage()

    assert environment.apply_environment_mode(stage, "night") == "night"

    assert value(
        stage,
        environment.ENVIRONMENT_ROOT_PATH,
        environment.ENVIRONMENT_MODE_ATTRIBUTE,
    ) == "night"
    assert value(stage, environment.DAY_SKY_PATH, "inputs:intensity") == 160.0
    assert value(stage, environment.DAY_SKY_PATH, "inputs:exposure") == -0.4
    assert value(stage, environment.DAY_SUN_PATH, "inputs:intensity") == 450.0
    assert value(stage, "/Render/Viewport", "omni:rtx:background:source:type") == "color"
    assert value(stage, environment.NIGHT_STREET_LIGHTS_PATH, "visibility") == "inherited"
    assert all(
        value(stage, path, "visibility") == "inherited"
        for path in environment.RTG_LED_NIGHT_ROOT_PATHS
    )
    assert all(
        value(stage, path, "visibility") == "inherited"
        for path in environment.RTG_LED_MODEL_ROOT_PATHS
    )
    assert environment.current_environment_mode(stage) == "night"


def test_day_mode_restores_authored_light_levels_and_hides_street_lights():
    stage = FakeStage()
    environment.apply_environment_mode(stage, "night")

    assert environment.apply_environment_mode(stage, "day") == "day"

    assert value(stage, environment.DAY_SKY_PATH, "inputs:intensity") == 650.0
    assert value(stage, environment.DAY_SKY_PATH, "inputs:exposure") == 0.0
    assert value(stage, environment.DAY_SUN_PATH, "inputs:intensity") == 2200.0
    assert value(stage, "/Render/Viewport", "omni:rtx:background:source:type") is None
    assert value(stage, environment.DAY_SKY_PATH, "visibility") == "inherited"
    assert value(stage, environment.NIGHT_STREET_LIGHTS_PATH, "visibility") == "invisible"
    assert all(
        value(stage, path, "visibility") == "invisible"
        for path in environment.RTG_LED_NIGHT_ROOT_PATHS
    )
    assert all(
        value(stage, path, "visibility") == "inherited"
        for path in environment.RTG_LED_MODEL_ROOT_PATHS
    )
    assert value(stage, environment.RAIN_EFFECT_PATH, "visibility") == "invisible"


def test_rain_mode_lights_roads_and_working_rtg_not_stationary_rtgs():
    stage = FakeStage()

    assert environment.apply_environment_mode(stage, "rain") == "rain"
    assert value(stage, environment.RAIN_EFFECT_PATH, "visibility") == "inherited"
    assert value(stage, environment.DAY_SKY_PATH, "inputs:intensity") == pytest.approx(540.0)
    assert value(stage, environment.NIGHT_STREET_LIGHTS_PATH, "visibility") == "inherited"
    assert value(stage, environment.RTG_LED_NIGHT_ROOT_PATHS[0], "visibility") == "inherited"
    assert all(
        value(stage, path, "visibility") == "invisible"
        for path in environment.RTG_LED_NIGHT_ROOT_PATHS[1:]
    )


def test_rain_animation_can_advance_without_timeline_playback():
    stage = FakeStage()
    environment.apply_environment_mode(stage, "rain")
    environment._RAIN_LAST_ANIMATION_UPDATE.pop(id(stage), None)
    environment._RAIN_FALL_DISTANCES.pop(id(stage), None)
    environment.update_weather_animation(stage, 0.0)
    assert environment.update_weather_animation(stage, 0.5) is True
    translation = value(stage, environment.RAIN_LAYER_PHASE_PATHS[0][0], "xformOp:translate")
    assert translation[0] >= 0.0
    assert value(stage, environment.AIR_RAIN_EFFECT_PATH, "xformOp:translate") == (
        0.0,
        0.0,
        0.0,
    )
    assert translation[0] > 0.5
    assert translation[1] > 0.2
    assert translation[2] > -(
        environment.RAIN_EMITTER_MIN_Z_M
        - environment.RAIN_STREAK_MAX_LENGTHS_M[0]
        - environment.RAIN_SURFACE_CLIP_Z_M
    )
    assert value(stage, environment.RAIN_LAYER_PHASE_PATHS[0][1], "xformOp:translate") != translation


def test_rain_animation_is_throttled_to_60_hz_before_camera_work(monkeypatch):
    stage = FakeStage()
    environment.apply_environment_mode(stage, "rain")
    environment._RAIN_LAST_ANIMATION_UPDATE.pop(id(stage), None)
    assert environment.update_weather_animation(stage, 1.0) is True
    first = value(
        stage,
        environment.RAIN_LAYER_PHASE_PATHS[0][0],
        "xformOp:translate",
    )
    def unexpected_camera_query(*args):
        pytest.fail("Throttled calls must not query the camera")

    with monkeypatch.context() as patch:
        patch.setattr(environment, "_active_camera_ground_xy", unexpected_camera_query)
        patch.setattr(environment, "_update_atmosphere_overlay_transform", unexpected_camera_query)
        assert environment.update_weather_animation(stage, 1.01) is True
    assert value(
        stage,
        environment.RAIN_LAYER_PHASE_PATHS[0][0],
        "xformOp:translate",
    ) == first
    assert environment.update_weather_animation(stage, 1.02) is True
    assert value(
        stage,
        environment.RAIN_LAYER_PHASE_PATHS[0][0],
        "xformOp:translate",
    ) != first


def test_weather_controls_change_wind_rain_layers_and_fog_amount():
    stage = FakeStage()
    environment.apply_environment_mode(stage, "rain")
    result = environment.apply_weather_controls(
        stage,
        wind_speed_mps=12.0,
        rain_amount="暴雨",
        fog_amount="浓雾",
    )
    assert result["wind_speed_mps"] == 12.0
    assert result["rain_amount"] == "torrential"
    assert result["fog_amount"] == "heavy"
    assert result["rain_label"] == "Torrential Rain"
    assert result["fog_label"] == "Heavy Fog"
    assert result["rain_drop_count"] == 2000
    assert result["rain_drop_length_m"] == (0.42, 0.92)
    assert result["rain_drop_width_m"] == (0.010, 0.018)
    assert result["rain_fall_speed_mps"] == 9.5
    assert value(stage, environment.RAIN_EFFECT_PATH, "smartPort:rainDropCount") == 2000
    assert all(
        value(stage, path, "visibility") == "inherited"
        for path in environment.RAIN_LAYER_PATHS
    )
    assert all(
        value(stage, path, "visibility") == "inherited"
        for path in environment.RAIN_SPLASH_LAYER_PATHS
    )
    assert all(
        value(stage, path, "visibility") == "inherited"
        for path in environment.RAIN_MIST_LAYER_PATHS
    )

    environment.apply_weather_controls(stage, rain_amount="小雨")
    assert value(stage, environment.RAIN_LAYER_PATHS[0], "visibility") == "inherited"
    assert all(
        value(stage, path, "visibility") == "invisible"
        for path in environment.RAIN_LAYER_PATHS[1:]
    )


def test_fog_profiles_are_separated_for_light_medium_and_heavy_visibility():
    source = (SCRIPT_DIR / "smart_port_environment.py").read_text(encoding="utf-8")
    assert '"light": (1.20, 0.18, 0.12)' in source
    assert '"medium": (0.65, 0.65, 0.45)' in source
    assert '"heavy": (0.28, 2.00, 1.80)' in source
    assert "FOG_AMOUNT_BACKGROUND_COLORS" in source


def test_fog_profiles_are_written_to_the_active_render_product():
    stage = FakeStage()
    environment.apply_environment_mode(stage, "fog")
    expected = {
        "light": (150.0, 0.1656, 0.0216),
        "medium": (81.25, 0.598, 0.081),
        "heavy": (35.0, 1.84, 0.324),
    }
    for amount, (end_dist, distance_density, height_density) in expected.items():
        environment.apply_weather_controls(stage, fog_amount=amount)
        assert value(
            stage,
            "/Render/Viewport",
            "omni:rtx:fog:endDist",
        ) == pytest.approx(end_dist)
        assert value(
            stage,
            "/Render/Viewport",
            "omni:rtx:fog:distanceDensity",
        ) == pytest.approx(distance_density)
        assert value(
            stage,
            "/Render/Viewport",
            "omni:rtx:fog:height:density",
        ) == pytest.approx(height_density)
        assert value(stage, "/Render/Viewport", "omni:rtx:fog:enabled") is True


def test_fog_and_rain_grades_drive_the_low_cost_visibility_overlay():
    stage = FakeStage()
    environment.apply_environment_mode(stage, "fog")
    environment.apply_weather_controls(stage, fog_amount="light")
    light_opacity = value(
        stage,
        environment.ATMOSPHERE_OVERLAY_SHADER_PATH,
        "inputs:opacity",
    )
    environment.apply_weather_controls(stage, fog_amount="heavy")
    heavy_opacity = value(
        stage,
        environment.ATMOSPHERE_OVERLAY_SHADER_PATH,
        "inputs:opacity",
    )
    assert value(stage, environment.ATMOSPHERE_OVERLAY_PATH, "visibility") == "inherited"
    assert heavy_opacity > light_opacity

    environment.apply_environment_mode(stage, "rain")
    assert value(stage, environment.ATMOSPHERE_OVERLAY_PATH, "visibility") == "invisible"
    rain_opacities = []
    for amount in ("light", "medium", "heavy", "torrential"):
        environment.apply_weather_controls(stage, rain_amount=amount)
        rain_opacities.append(
            value(
                stage,
                environment.ATMOSPHERE_OVERLAY_SHADER_PATH,
                "inputs:opacity",
            )
        )
    assert rain_opacities == sorted(rain_opacities)


def test_weather_zone_is_limited_to_the_modeled_quay_yard_footprint():
    assert environment.WEATHER_ZONE_BOUNDS == (-105.0, 155.0, -15.0, 505.0)
    assert environment._camera_is_inside_weather_zone(0.0, 100.0)
    assert not environment._camera_is_inside_weather_zone(300.0, 100.0)


def test_rain_profiles_change_both_layer_count_and_streak_visibility():
    source = (SCRIPT_DIR / "smart_port_environment.py").read_text(encoding="utf-8")
    profiles = list(environment.RAIN_AMOUNT_PROFILES.values())
    assert [p["visible_layers"] for p in profiles] == [1, 2, 3, 4]
    assert [p["opacity"] for p in profiles] == [0.24, 0.34, 0.44, 0.54]
    assert max(p["emission"] for p in profiles) < 0.02
    assert 'smartPort:visibleLayerCount' in source
    assert 'smartPort:coverageScale' in source
    assert 'inputs:opacity' in source


def test_rain_amount_is_a_combined_count_length_width_and_speed_profile():
    expected = {
        "light": (100, (0.18, 0.38), (0.006, 0.010), 5.5),
        "medium": (350, (0.24, 0.52), (0.007, 0.012), 6.5),
        "heavy": (850, (0.32, 0.70), (0.009, 0.015), 8.0),
        "torrential": (2000, (0.42, 0.92), (0.010, 0.018), 9.5),
    }
    for amount, (count, length, width, speed) in expected.items():
        profile = environment.RAIN_AMOUNT_PROFILES[amount]
        assert profile["drop_count"] == count
        assert profile["streak_length_m"] == length
        assert profile["streak_width_m"] == width
        assert profile["speed_mps"] == speed


def test_hidden_rain_layers_are_not_animated_and_become_live_when_enabled():
    stage = FakeStage()
    environment.apply_environment_mode(stage, "rain")
    environment.apply_weather_controls(stage, rain_amount="light")
    assert environment.update_weather_animation(stage, 10.0)
    for paths in environment.RAIN_LAYER_PHASE_PATHS[1:]:
        for path in paths:
            assert stage.prims[path].GetAttribute("xformOp:translate") is None
    environment.apply_weather_controls(stage, rain_amount="torrential")
    assert environment.update_weather_animation(stage, 10.02)
    for paths in environment.RAIN_LAYER_PHASE_PATHS:
        for path in paths:
            assert stage.prims[path].GetAttribute("xformOp:translate") is not None


def test_grade_switch_integrates_speed_without_teleporting_existing_drops():
    stage = FakeStage()
    environment.apply_environment_mode(stage, "rain")
    environment.update_weather_animation(stage, 5000.0)
    environment.update_weather_animation(stage, 5000.1)
    path = environment.RAIN_LAYER_PHASE_PATHS[0][0]
    before = value(stage, path, "xformOp:translate")[2]
    environment.apply_weather_controls(stage, rain_amount="torrential")
    environment.update_weather_animation(stage, 5000.12)
    after = value(stage, path, "xformOp:translate")[2]
    assert before - after == pytest.approx(.02 * 9.5)
    environment.update_weather_animation(stage, 1.0)  # monotonic clock reset
    assert value(stage, path, "xformOp:translate")[2] == pytest.approx(after)
    environment.apply_environment_mode(stage, "day")
    assert id(stage) not in environment._RAIN_FALL_DISTANCES


def test_rain_outside_weather_zone_skips_batch_and_camera_work(monkeypatch):
    stage = FakeStage()
    environment.apply_environment_mode(stage, "rain")
    stage.prims[environment.RAIN_EFFECT_PATH].attributes["visibility"].Set("invisible")
    monkeypatch.setattr(environment, "_update_atmosphere_overlay_transform", lambda *_: True)
    def fail(*_):
        pytest.fail("Hidden rain should not query its camera")
    monkeypatch.setattr(environment, "_active_camera_ground_xy", fail)
    assert environment.update_weather_animation(stage, 2.0)
    assert stage.prims[environment.RAIN_LAYER_PHASE_PATHS[0][0]].GetAttribute("xformOp:translate") is None


def test_wet_road_is_dielectric_and_rain_emission_is_neutral():
    stage = FakeStage()
    environment.apply_environment_mode(stage, "rain")
    wet = f"{environment.WEATHER_ROOT_PATH}/Looks/WetRoadMaterial/PreviewSurface"
    rain = f"{environment.WEATHER_ROOT_PATH}/Looks/RainStreakMaterial/PreviewSurface"
    wetness = []
    for amount in environment.RAIN_AMOUNT_PROFILES:
        environment.apply_weather_controls(stage, rain_amount=amount)
        assert value(stage, wet, "inputs:metallic") == 0
        assert value(stage, wet, "inputs:roughness") >= .30
        wetness.append(value(stage, wet, "inputs:opacity"))
        emission = value(stage, rain, "inputs:emissiveColor")
        assert emission[0] == emission[1] == emission[2]
        assert emission[0] < .02
    assert wetness == sorted(wetness)


def test_weather_atmosphere_writes_stay_in_session_edit_context(monkeypatch):
    from contextlib import contextmanager
    depth = 0
    @contextmanager
    def edit_context(stage):
        nonlocal depth
        depth += 1
        try:
            yield
        finally:
            depth -= 1
    def check(*args):
        assert depth > 0, "Root-layer edits lose to existing session overrides"
    monkeypatch.setattr(environment, "_session_edit_context", edit_context)
    for name in ("_configure_fog_background", "_configure_rain_atmosphere",
                 "_configure_render_product_fog", "_configure_atmosphere_overlay"):
        monkeypatch.setattr(environment, name, check)
    stage = FakeStage()
    environment.apply_environment_mode(stage, "rain")
    environment.apply_weather_controls(stage, rain_amount="heavy")


def test_rain_grades_also_change_sky_visibility_and_background():
    stage = FakeStage()
    environment.apply_environment_mode(stage, "rain")

    expected = {
        "light": (588.0, 420.0),
        "medium": (540.0, 240.0),
        "heavy": (492.0, 110.0),
        "torrential": (444.0, 45.0),
    }
    for amount, (sky_intensity, visibility_m) in expected.items():
        environment.apply_weather_controls(stage, rain_amount=amount)
        assert value(stage, environment.DAY_SKY_PATH, "inputs:intensity") == pytest.approx(
            sky_intensity
        )
        assert value(
            stage,
            environment.ENVIRONMENT_ROOT_PATH,
            "smartPort:rainVisibilityMeters",
        ) == visibility_m
        assert value(stage, "/Render/Viewport", "omni:rtx:background:source:type") == "texture"


def test_all_rain_grades_keep_bright_ambient_fill_and_requested_lights():
    stage = FakeStage()
    environment.apply_environment_mode(stage, "rain")
    for amount in environment.RAIN_AMOUNT_PROFILES:
        environment.apply_weather_controls(stage, rain_amount=amount)
        assert value(stage, environment.DAY_SKY_PATH, "inputs:intensity") >= 400
        assert value(stage, environment.DAY_SKY_PATH, "inputs:exposure") == -.08
        assert min(value(stage, environment.DAY_SKY_PATH, "inputs:color")) >= .78
        assert value(stage, environment.DAY_SUN_PATH, "inputs:intensity") >= 300
        assert environment.RAIN_BACKGROUND_TEXTURE_LUMINANCE[amount] >= .50
        assert value(stage, environment.NIGHT_STREET_LIGHTS_PATH, "visibility") == "inherited"
        assert value(stage, environment.RTG_LED_NIGHT_ROOT_PATHS[0], "visibility") == "inherited"
        assert all(value(stage, path, "visibility") == "invisible"
                   for path in environment.RTG_LED_NIGHT_ROOT_PATHS[1:])


def test_puddles_splashes_and_mist_are_disabled():
    source = (SCRIPT_DIR / "smart_port_environment.py").read_text(encoding="utf-8")
    assert "PointInstancer" not in source
    assert "ContainerPuddleMaterial" not in source
    assert "refresh_task_bay_surface_effects" not in source
    assert environment.RAIN_CAMERA_FOLLOW_GRID_M == 10.0


def test_task_bay_rain_and_adaptive_fps_governor_are_removed():
    source = (SCRIPT_DIR / "smart_port_environment.py").read_text(encoding="utf-8")
    assert "TaskBayRoofRain" not in source
    assert "task_bay_roof" not in source
    assert "decide_rain_quality" not in source
    assert "update_rain_fps_quality" not in source
    assert "RAIN_TARGET_FPS" not in source


def test_fog_mode_hides_rain_geometry_and_uses_overcast_background():
    stage = FakeStage()

    assert environment.apply_environment_mode(stage, "fog") == "fog"
    assert value(stage, environment.RAIN_EFFECT_PATH, "visibility") == "invisible"
    assert value(stage, environment.DAY_SKY_PATH, "inputs:intensity") == pytest.approx(405.6)
    assert value(stage, "/Render/Viewport", "omni:rtx:background:source:type") == "texture"
    assert value(
        stage,
        "/Render/Viewport",
        "omni:rtx:background:source:texture:luminanceScale",
    ) == environment.FOG_BACKGROUND_TEXTURE_LUMINANCE


def test_rtg_twin_hud_does_not_duplicate_environment_buttons():
    source = (SCRIPT_DIR / "rtg_twin_state_receiver.py").read_text(encoding="utf-8")
    assert 'self._apply_environment_mode("day")' not in source
    assert 'self._apply_environment_mode("night")' not in source
    # Five HUD/panel materials plus the remote-mode signal-link and truck panel helpers.
    assert source.count('CreateInput("emissiveColor"') == 7


def test_standalone_3d_panel_materials_are_self_illuminated():
    for name in (
        "rtg_device_location_panel.py",
        "rtg_device_info_popup.py",
        "rtg_container_info_popup.py",
    ):
        source = (SCRIPT_DIR / name).read_text(encoding="utf-8")
        assert 'CreateInput("emissiveColor"' in source
