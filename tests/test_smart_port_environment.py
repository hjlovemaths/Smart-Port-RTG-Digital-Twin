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
    assert value(stage, "/Render/Viewport", "omni:rtx:background:source:type") == "domeLight"
    assert value(stage, environment.DAY_SKY_PATH, "visibility") == "inherited"
    assert value(
        stage,
        "/Render/Viewport",
        "omni:rtx:background:source:texture:luminanceScale",
    ) == 1.0
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


def test_rain_mode_shows_single_batched_rain_effect_and_turns_on_all_yard_lights():
    stage = FakeStage()

    assert environment.apply_environment_mode(stage, "rain") == "rain"
    assert value(stage, environment.RAIN_EFFECT_PATH, "visibility") == "inherited"
    assert value(stage, environment.DAY_SKY_PATH, "inputs:intensity") == pytest.approx(319.8)
    assert value(stage, environment.NIGHT_STREET_LIGHTS_PATH, "visibility") == "inherited"
    assert all(
        value(stage, path, "visibility") == "inherited"
        for path in environment.RTG_LED_NIGHT_ROOT_PATHS
    )


def test_rain_animation_can_advance_without_timeline_playback():
    stage = FakeStage()
    environment.apply_environment_mode(stage, "rain")
    assert environment.update_weather_animation(stage, 0.5) is True
    translation = value(stage, environment.RAIN_LAYER_PHASE_PATHS[0][0], "xformOp:translate")
    assert translation[0] >= 0.0
    assert value(stage, environment.AIR_RAIN_EFFECT_PATH, "xformOp:translate") == (
        0.0,
        0.0,
        0.0,
    )
    assert translation[0] > 1.5
    assert translation[1] > 0.4
    assert translation[2] > -(
        environment.RAIN_EMITTER_MIN_Z_M
        - environment.RAIN_STREAK_MAX_LENGTHS_M[0]
        - environment.RAIN_SURFACE_CLIP_Z_M
    )
    assert value(stage, environment.RAIN_LAYER_PHASE_PATHS[0][1], "xformOp:translate") != translation


def test_rain_animation_is_throttled_to_a_low_cost_24_hz_update_rate():
    stage = FakeStage()
    environment.apply_environment_mode(stage, "rain")
    environment._RAIN_LAST_ANIMATION_UPDATE.pop(id(stage), None)
    assert environment.update_weather_animation(stage, 1.0) is True
    first = value(
        stage,
        environment.RAIN_LAYER_PHASE_PATHS[0][0],
        "xformOp:translate",
    )
    assert environment.update_weather_animation(stage, 1.01) is True
    assert value(
        stage,
        environment.RAIN_LAYER_PHASE_PATHS[0][0],
        "xformOp:translate",
    ) == first
    assert environment.update_weather_animation(stage, 1.05) is True
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
    assert '"light": (2.50, 0.08, 0.08)' in source
    assert '"medium": (1.15, 0.34, 0.30)' in source
    assert '"heavy": (0.48, 1.45, 1.25)' in source
    assert "FOG_AMOUNT_BACKGROUND_COLORS" in source


def test_rain_profiles_change_both_layer_count_and_streak_visibility():
    source = (SCRIPT_DIR / "smart_port_environment.py").read_text(encoding="utf-8")
    assert '"visible_layers": 1, "opacity": 0.20' in source
    assert '"visible_layers": 2, "opacity": 0.40' in source
    assert '"visible_layers": 3, "opacity": 0.68' in source
    assert '"visible_layers": 4, "opacity": 0.92' in source
    assert 'smartPort:visibleLayerCount' in source
    assert 'inputs:opacity' in source


def test_rain_grades_also_change_sky_visibility_and_background():
    stage = FakeStage()
    environment.apply_environment_mode(stage, "rain")

    expected = {
        "light": (409.5, 650.0, (0.34, 0.41, 0.46)),
        "medium": (319.8, 360.0, (0.24, 0.31, 0.36)),
        "heavy": (226.2, 180.0, (0.15, 0.21, 0.25)),
        "torrential": (132.6, 78.0, (0.075, 0.11, 0.14)),
    }
    for amount, (sky_intensity, visibility_m, background) in expected.items():
        environment.apply_weather_controls(stage, rain_amount=amount)
        assert value(stage, environment.DAY_SKY_PATH, "inputs:intensity") == pytest.approx(
            sky_intensity
        )
        assert value(
            stage,
            environment.ENVIRONMENT_ROOT_PATH,
            "smartPort:rainVisibilityMeters",
        ) == visibility_m
        assert value(
            stage,
            "/Render/Viewport",
            "omni:rtx:background:source:color",
        ) == background


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
    assert value(stage, "/Render/Viewport", "omni:rtx:background:source:type") == "color"
    assert value(stage, "/Render/Viewport", "omni:rtx:background:source:color") == (
        0.52,
        0.58,
        0.61,
    )


def test_rtg_twin_hud_does_not_duplicate_environment_buttons():
    source = (SCRIPT_DIR / "rtg_twin_state_receiver.py").read_text(encoding="utf-8")
    assert 'self._apply_environment_mode("day")' not in source
    assert 'self._apply_environment_mode("night")' not in source
    assert source.count('CreateInput("emissiveColor"') == 5


def test_standalone_3d_panel_materials_are_self_illuminated():
    for name in (
        "rtg_device_location_panel.py",
        "rtg_device_info_popup.py",
        "rtg_container_info_popup.py",
    ):
        source = (SCRIPT_DIR / name).read_text(encoding="utf-8")
        assert 'CreateInput("emissiveColor"' in source
