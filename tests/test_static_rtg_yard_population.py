import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "omniverse" / "scenes" / "smart_port.usda"
LAYER = ROOT / "omniverse" / "scenes" / "static_rtg_yard_population.usda"
BUILDER = ROOT / "omniverse" / "scripts" / "build_static_rtg_yard_population.py"


def test_static_rtg_population_layer_is_loaded_once():
    text = MAIN.read_text(encoding="utf-8")
    assert text.count("@./static_rtg_yard_population.usda@") == 1


def test_original_static_rtgs_are_hidden_in_favor_of_animated_references():
    text = LAYER.read_text(encoding="utf-8")
    for name in ("RTG_STATIC_LEFT", "RTG_STATIC_RIGHT"):
        pattern = rf'over "{name}".*?token visibility = "invisible"'
        assert re.search(pattern, text, flags=re.DOTALL)


def test_five_yards_receive_instanceable_static_visual_rtgs_at_distinct_bays():
    text = LAYER.read_text(encoding="utf-8")
    expected = (
        ("SMART_PORT_STATIC_RTG_YARD_M60", -44.0, "1C", 17, 22.1375),
        ("SMART_PORT_STATIC_RTG_YARD_M30", -23.0, "1C", 63, 83.6625),
        ("SMART_PORT_STATIC_RTG_YARD_P30", 19.0, "2C", 29, 168.4375),
        ("SMART_PORT_STATIC_RTG_YARD_P60", 40.0, "3C", 57, 336.1375),
        ("SMART_PORT_STATIC_RTG_YARD_P90", 61.0, "4C", 25, 423.5875),
    )
    for name, center_x, block, bay, center_y in expected:
        pattern = (
            rf'def Xform "{name}"\s*\(\s*instanceable = true\s*'
            rf'prepend references = @\.\./assets/rtg/RTG_Static_Visual\.usdc@'
            rf'</StaticRTG>\s*\).*?'
            rf'custom string rtg:role = "static_visual".*?'
            rf'custom bool rtg:runtimeControlled = 0.*?'
            rf'custom string rtg:yardBlock = "{block}".*?'
            rf'custom double rtg:yardColumnCenterX = {center_x + 1.2:g}'
        )
        assert re.search(pattern, text, flags=re.DOTALL)
        assert f"custom int rtg:bayNumber = {bay}" in text
        parking_match = re.search(
            rf'def Xform "{name}".*?custom double rtg:parkingCenterY = ([\d.]+)',
            text,
            flags=re.DOTALL,
        )
        assert parking_match
        assert abs(float(parking_match.group(1)) - center_y) < 1e-6


def test_builder_uses_all_six_parallel_yard_centers_without_cloning_dynamic_rtg():
    text = BUILDER.read_text(encoding="utf-8")
    assert "YARD_COLUMN_CENTERS_X" in text
    assert "center_x + RTG_LATERAL_ALIGNMENT_X_M for center_x in YARD_COLUMN_CENTERS_X" in text
    assert 'STATIC_TEMPLATE_NAME = "RTG_STATIC_LEFT"' in text
    assert 'STATIC_TEMPLATE_NAME = "RTG_PRIMARY_DYNAMIC"' not in text


def test_builder_packages_materials_with_the_static_rtg_prototype():
    text = BUILDER.read_text(encoding="utf-8")
    assert (
        'PROTOTYPE = ROOT / "omniverse" / "assets" / "rtg" '
        '/ "RTG_Static_Visual.usdc"'
    ) in text
    assert 'SOURCE_MATERIALS_PATH = "/World/_materials"' in text
    assert "Sdf.CopySpec(" in text
    assert "root.AddTranslateOp().Set(Gf.Vec3d(0.0))" in text
    assert "_parent_offset_for_target(" in text
    assert "_remap_prototype_connections(prototype_stage)" in text


def test_all_static_rtgs_receive_lightweight_visual_hoist_ropes():
    text = LAYER.read_text(encoding="utf-8")
    assert text.count('def BasisCurves "SMART_PORT_STATIC_RTG_') == 5
    assert text.count('custom token rtg:systemRole = "static_hoist_rope_visual"') == 5
    assert text.count("int[] curveVertexCounts = [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2]") == 5
    assert text.count("float[] widths = [0.036]") == 5


def test_static_spreaders_have_looping_pick_animation_without_splitting_trolley_platforms():
    builder = BUILDER.read_text(encoding="utf-8")
    assert 'ANIMATED_SPREADER_PATH = f"{PROTOTYPE_MODEL_PATH}/AnimatedSpreader"' in builder
    assert "ANIMATED_TROLLEY_PATH" not in builder
    assert "ANIMATION_CYCLE_FRAMES = 240" in builder
    assert "TROLLEY_START_OFFSET_M = 0.0" in builder
    assert "TROLLEY_END_OFFSET_M = 6.5" in builder
    assert "HOIST_TRAVEL_M = 3.5" in builder
    assert "points_attr.Set(rope_points(trolley_x, hoist_z), Usd.TimeCode(frame))" in builder
    assert "_freeze_prototype_at_start_frame(prototype_stage)" in builder
    assert "trolley_x / parent_x_scale" in builder
    assert "hoist_z / parent_z_scale" in builder


def test_five_outer_rail_festoon_loops_are_removed_from_every_rtg_model():
    text = LAYER.read_text(encoding="utf-8")
    builder = BUILDER.read_text(encoding="utf-8")
    assert (
        'OUTER_RAIL_FESTOON_PREFIX = '
        '"RTG_TOP_REDRAW_black_festoon_loop_from_outer_true_rail_"'
    ) in builder
    assert "OUTER_RAIL_FESTOON_COUNT = 5" in builder
    assert "_disable_outer_rail_festoon_loops(" in builder
    # These five loops exist only on the primary dynamic model.
    assert text.count("active = false") >= 5


def test_all_detached_red_festoon_blocks_are_removed_from_all_rtgs():
    text = LAYER.read_text(encoding="utf-8")
    builder = BUILDER.read_text(encoding="utf-8")
    assert "RED_FESTOON_BLOCK_NAMES" in builder
    assert "for index in range(0, 6)" in builder
    assert "_disable_red_festoon_blocks(" in builder
    # All six source clamps are disabled on each original RTG; cloned RTGs use
    # the self-contained prototype with the same inactive opinions.
    assert text.count("active = false") >= 20


def test_five_outer_rail_red_blocks_are_removed_from_all_rtgs():
    text = LAYER.read_text(encoding="utf-8")
    builder = BUILDER.read_text(encoding="utf-8")
    assert (
        '"RTG_TOP_REDRAW_red_brown_festoon_clamp_on_outer_rail_"'
        in builder
    )
    assert "OUTER_RAIL_RED_BLOCK_COUNT = 5" in builder
    assert "_disable_outer_rail_red_blocks(" in builder
    for index in range(5):
        assert f"red_brown_festoon_clamp_on_outer_rail_{index}" in text


def test_five_outer_rail_festoon_pins_are_removed_with_the_red_blocks():
    text = LAYER.read_text(encoding="utf-8")
    builder = BUILDER.read_text(encoding="utf-8")
    assert '"RTG_TOP_REDRAW_silver_festoon_pin_outer_"' in builder
    assert "OUTER_RAIL_FESTOON_PIN_COUNT = 5" in builder
    assert "_disable_outer_rail_festoon_pins(" in builder
    for index in range(5):
        assert f"silver_festoon_pin_outer_{index}" in text
