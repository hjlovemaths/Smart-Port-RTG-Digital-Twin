from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "omniverse" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import build_weather_effects as weather  # noqa: E402


def test_weather_layer_is_sublayered_into_smart_port():
    stage_source = (ROOT / "omniverse" / "scenes" / "smart_port.usda").read_text(
        encoding="utf-8"
    )
    assert "@./weather_effects.usda@" in stage_source


def test_rain_uses_batched_streak_meshes_and_starts_hidden():
    source = weather.build_usda()
    assert source.count('def Mesh "') == 13
    assert 'def Xform "Rain"' in source
    assert 'uniform token[] xformOpOrder = ["xformOp:translate"]' in source
    assert source.count('double3 xformOp:translate = (0, 0, 0)') == 12
    assert source.count("custom int smartPort:phaseCount = 3") == 4
    assert 'token visibility = "invisible"' in source
    assert f"custom int smartPort:rainDropCount = {weather.RAIN_DROP_COUNT}" in source


def test_ground_splashes_are_not_authored():
    assert weather.SPLASH_LAYER_COUNTS == ()
    source = weather.build_usda()
    assert 'def Mesh "SplashLayer' not in source
    assert "RainSplashMaterial" not in source


def test_ground_mist_is_not_authored():
    assert weather.MIST_LAYER_COUNTS == ()
    source = weather.build_usda()
    assert 'def Mesh "MistLayer' not in source
    assert "RainMistMaterial" not in source


def test_container_puddle_material_is_not_authored():
    source = weather.build_usda()
    assert "ContainerPuddleMaterial" not in source


def test_each_rain_drop_is_a_crossed_pair_of_camera_visible_ribbons():
    assert len(weather.rain_streak_quads()) == weather.RAIN_DROP_COUNT * 2


def test_rain_uses_three_runtime_phases_without_root_motion_below_ground():
    source = weather.build_usda()
    assert "xformOp:translate.timeSamples" not in source
    assert source.count('def Mesh "Phase') == 12
    assert "reset before their longest streak crosses Z=0.15 m" in source


def test_each_grade_stays_above_surface_at_its_max_runtime_translation():
    all_quads = weather.rain_streak_quads()
    quad_cursor = 0
    for drop_count, profile in zip(
        weather.RAIN_LAYER_DROP_COUNTS,
        weather.RAIN_LAYER_STREAK_PROFILES,
    ):
        layer_quads = all_quads[quad_cursor : quad_cursor + drop_count * 2]
        quad_cursor += drop_count * 2
        lowest_authored_z = min(point[2] for quad in layer_quads for point in quad)
        max_fall = weather.RAIN_Z_RANGE[0] - profile[0][1] - 0.15
        assert lowest_authored_z - max_fall >= 0.15 - 1e-9


def test_rain_layers_use_cumulative_density_steps_for_visible_amount_difference():
    assert weather.RAIN_LAYER_DROP_COUNTS == (100, 250, 500, 1150)
    source = weather.build_usda()
    assert 'smartPort:rainLayerDropCounts = "Light=100; Moderate=350 cumulative; Heavy=850 cumulative; Torrential=2000 cumulative"' in source


def test_ground_impacts_are_disabled_for_performance():
    source = weather.build_usda()
    assert 'def Xform "GroundRain"' not in source
    assert "puddles, splashes, and impact mist are disabled" in source


def test_rain_grades_use_distinct_but_thin_streak_lengths():
    assert weather.RAIN_LAYER_STREAK_PROFILES[0][0] == (0.35, 0.65)
    assert weather.RAIN_LAYER_STREAK_PROFILES[-1][0] == (2.50, 4.20)
    assert weather.RAIN_LAYER_STREAK_PROFILES[-1][1] == (0.030, 0.055)
    source = weather.build_usda()
    assert 'smartPort:streakLengthMeters = "0.35-0.65"' in source
    assert 'smartPort:streakLengthMeters = "2.5-4.2"' in source


def test_wet_sheen_covers_longitudinal_and_cross_roads_only():
    assert len(weather.wet_road_quads()) == len(weather.ROAD_X_RANGES) + len(
        weather.CROSS_ROAD_CENTERS_Y
    )
