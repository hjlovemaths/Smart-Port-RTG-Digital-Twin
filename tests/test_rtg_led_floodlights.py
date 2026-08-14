from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMART_PORT = ROOT / "omniverse" / "scenes" / "smart_port.usda"
LAYER = ROOT / "omniverse" / "scenes" / "rtg_led_floodlights.usda"
SOURCE_ASSET = ROOT / "omniverse" / "assets" / "resource" / "led" / "LED_Floodlight.usdz"
RTG_ASSET = ROOT / "omniverse" / "assets" / "resource" / "led" / "RTG_LED_Floodlight.usdc"
ASSET_BUILDER = ROOT / "omniverse" / "scripts" / "build_rtg_led_asset.py"


def test_led_replacement_layer_is_composed_and_assets_exist():
    smart_port = SMART_PORT.read_text(encoding="utf-8")
    assert smart_port.count("@./rtg_led_floodlights.usda@") == 1
    assert SOURCE_ASSET.is_file()
    assert RTG_ASSET.is_file()
    assert ASSET_BUILDER.is_file()
    assert LAYER.is_file()


def test_led_asset_builder_keeps_housing_visible_under_rtg_underside():
    builder = ASSET_BUILDER.read_text(encoding="utf-8")
    assert 'Gf.Vec3f(18.0, 26.0, 6.0)' in builder
    assert 'LED_FACE_SCALE_X = 1.04' in builder
    assert 'LED_FACE_SCALE_Y = 2.0' in builder
    assert 'Gf.Vec3f(0.16, 0.18, 0.20)' in builder
    assert 'Gf.Vec3f(0.42, 0.45, 0.48)' in builder
    assert 'Gf.Vec3f(0.82, 0.88, 0.92)' in builder
    assert 'BODY_EMISSIVE = Gf.Vec3f(0.025, 0.040, 0.070)' in builder
    assert 'LED_BASE_EMISSIVE = Gf.Vec3f(0.060, 0.090, 0.130)' in builder
    assert 'GLASS_EMISSIVE = Gf.Vec3f(0.120, 0.180, 0.240)' in builder


def test_each_rtg_has_four_leds_on_each_upper_long_girder():
    text = LAYER.read_text(encoding="utf-8")
    reference = "prepend references = @../assets/resource/led/RTG_LED_Floodlight.usdc@"
    assert text.count('def Xform "SMART_PORT_RTG_LED_MODELS"') == 3
    assert text.count("custom int lampsPerBeam = 4") == 3
    assert text.count("custom int totalLampCount = 8") == 3
    assert text.count(reference) == 24
    assert "instanceable = true" not in text
    assert 'def Xform "SMART_PORT_RTG_LED_VISIBILITY_MOUNTS"' not in text
    assert 'def Cube "Housing"' not in text
    assert 'def Cube "Lens"' not in text
    assert 'def Cube "BeamBracket"' not in text
    for side in ("Near", "Far"):
        for index in range(1, 5):
            assert text.count(f'def Xform "{side}_{index:02d}"') == 3


def test_led_rows_are_aimed_down_and_inward_from_both_sides():
    text = LAYER.read_text(encoding="utf-8")
    assert text.count("xformOp:rotateXYZ = (55, 0, 0)") == 12
    assert text.count("xformOp:rotateXYZ = (-125, 180, 0)") == 12
    assert text.count("xformOp:rotateXYZ = (20, 0, 0)") == 12
    assert text.count("xformOp:rotateXYZ = (-160, 180, 0)") == 12
    assert text.count(
        'uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"]'
    ) == 24
    assert 'custom string aimDirection = "Near and far rows converge on the ground beneath the RTG center"' in text


def test_source_brackets_and_screws_contact_the_upper_long_girders():
    text = LAYER.read_text(encoding="utf-8")
    assert text.count("custom double girderContactHeight = 7.73") == 3
    assert text.count('custom string attachment = "Source U bracket and screw bar fixed to both upper long girders"') == 3
    assert text.count('custom string mountHardware = "The LED_Floodlight source asset supplies the visible bracket and screws"') == 3
    assert text.count(", 7.33)") == 24
    assert text.count(", 7.53)") == 24


def test_dynamic_leds_use_gantry_local_beam_coordinates():
    text = LAYER.read_text(encoding="utf-8")
    dynamic = text.split('over Xform "RTG_PRIMARY_DYNAMIC"', 1)[1].split(
        'over Xform "RTG_STATIC_LEFT"', 1
    )[0]
    assert dynamic.count(", -7.65, 7.33)") == 4
    assert dynamic.count(", -2.35, 7.33)") == 4
    assert ", 8.9275, 8.08)" not in dynamic
    assert ", 14.2275, 8.08)" not in dynamic


def test_old_single_side_floodlights_are_fully_disabled():
    text = LAYER.read_text(encoding="utf-8")
    assert text.count("active = false") == 45
    for part in ("glass", "housing", "bracket"):
        for index in range(5):
            assert f"under_beam_floodlight_{part}_{index}" in text


def test_night_led_visuals_use_one_shadow_free_downlight_per_led():
    text = LAYER.read_text(encoding="utf-8")
    assert text.count('def Xform "SMART_PORT_RTG_LED_NIGHT_LIGHTS"') == 3
    assert text.count('token visibility = "invisible"') == 3
    assert text.count("custom int effectiveLightCount = 8") == 3
    assert 'def Mesh "LampGlow"' not in text
    assert "SmartPortRtgLedNightGlow" not in text
    assert text.count('def DiskLight "') == 24
    assert text.count('_Downlight"') == 24
    assert text.count('prepend apiSchemas = ["ShapingAPI", "ShadowAPI"]') == 24
    assert text.count("bool inputs:shadow:enable = false") == 24
    assert text.count("float inputs:intensity = 60000") == 24
    assert text.count("float inputs:exposure = 2") == 24
    assert text.count("float inputs:radius = 0.24") == 24
    assert text.count("float inputs:shaping:cone:angle = 70") == 24
    assert text.count("float inputs:shaping:cone:softness = 0.65") == 24


def test_physical_led_models_stay_visible_while_only_lights_start_hidden():
    text = LAYER.read_text(encoding="utf-8")
    assert text.count('token visibility = "invisible"') == 3
    assert text.count('def Xform "SMART_PORT_RTG_LED_MODELS"') == 3
