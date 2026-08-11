import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCENE = ROOT / "omniverse" / "scenes" / "smart_port.usda"
OVERLAY = ROOT / "omniverse" / "scenes" / "yard_expansion_prep.usda"
BUILDER = ROOT / "omniverse" / "scripts" / "build_yard_expansion_prep.py"


def test_expansion_prep_layer_is_strongest_scene_sublayer():
    text = SCENE.read_text(encoding="utf-8")
    assert text.count("@./yard_expansion_prep.usda@") == 1
    assert text.index("@./yard_expansion_prep.usda@") < text.index(
        "@./rtg_virtual_cameras.usda@"
    )


def test_ai_console_is_relocated_as_one_complete_component_set():
    text = OVERLAY.read_text(encoding="utf-8")
    moved_components = re.findall(
        r'over "(SMART_PORT_(?:control_center|roof_solar_panel|smart_control_center|smart_tower|tower)_[^"]+)"\s*\{.*?xformOp:translate',
        text,
        flags=re.DOTALL,
    )
    assert len(moved_components) >= 40
    assert 'over "SMART_PORT_smart_control_center_base_white"' in text
    assert "double3 xformOp:translate = (138, 470, 0.78)" in text
    assert "float3 xformOp:rotateXYZ = (0, 0, 90)" in text
    assert 'over "SMART_PORT_smart_tower_white_shaft"' in text
    assert "double3 xformOp:translate = (135.6, 470, 7.1)" in text
    assert "custom double yard:aiConsoleRotation = 90" in text


def test_old_markings_are_replaced_by_seven_parallel_yard_columns():
    text = OVERLAY.read_text(encoding="utf-8")
    for root_name in (
        "SMART_PORT_UNIFIED_SIDEYARD_cyan_lane_lines",
        "SMART_PORT_UNIFIED_SIDEYARD_white_slot_separators",
        "SMART_PORT_UNIFIED_SIDEYARD_yellow_boundaries",
        "SMART_PORT_UNIFIED_SIDEYARD_light_grey_40ft_slots",
        "YARD_LAYOUT_1C4C_ROOT",
    ):
        pattern = rf'over "{root_name}"\s*\(\s*active = false'
        assert re.search(pattern, text)

    assert 'custom string yard:preservedCorridor = "1C-4C; X=-10..10"' in text
    assert 'def Xform "SMART_PORT_REBUILT_YARD_LAYOUT"' in text
    assert (
        "custom double[] yard:columnCentersX = [-60, -30, 0, 30, 60, 90]" in text
    )
    assert "custom double yard:laneWidth = 10" in text
    assert "custom double yard:bayPitch = 2.675" in text
    assert "custom double yard:firstBayStartY = -0.5" in text
    assert "custom double yard:lastBayEndY = 446.225" in text
    assert 'def Mesh "SMART_PORT_CLEAN_BAY_MARKINGS"' in text
    assert 'def Mesh "SMART_PORT_CLEAN_BAY_NUMBER_BACKINGS"' in text
    assert 'def Mesh "SMART_PORT_CLEAN_BAY_NUMBER_DIGITS"' in text
    assert 'def Mesh "SMART_PORT_TERMINAL_BLOCK_LABEL_BACKINGS"' in text
    assert 'def Mesh "SMART_PORT_TERMINAL_BLOCK_LABEL_GLYPHS"' in text
    assert "custom int yard:terminalBlockLabelCount = 48" in text
    assert (
        'custom string yard:terminalBlockLabelRule = '
        '"one block label on the first and last bay of every yard column"'
    ) in text
    assert 'def Mesh "SMART_PORT_UNIFIED_SIDEYARD_white_slot_separators"' not in text
    assert 'def Mesh "SMART_PORT_UNIFIED_SIDEYARD_light_grey_40ft_slots"' not in text
    assert 'def Mesh "SMART_PORT_UNIFIED_SIDEYARD_yellow_boundaries"' not in text
    assert "SMART_PORT_REBUILT_1C4C_YELLOW_SAFETY_LINES" not in text

    point_blocks = re.findall(r"point3f\[\] points = \[(.*?)\]", text, re.DOTALL)
    assert len(point_blocks) == 5
    for block in point_blocks:
        coordinates = [
            tuple(float(value) for value in match)
            for match in re.findall(
                r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)",
                block,
            )
        ]
        x_values = [point[0] for point in coordinates]
        assert x_values
        assert min(x_values) >= -70.0001
        assert max(x_values) <= 100.0276
        assert {round(point[2], 6) for point in coordinates}.issubset(
            {0.145, 0.149, 0.151}
        )

    count_blocks = re.findall(r"int\[\] faceVertexCounts = \[(.*?)\]", text, re.DOTALL)
    assert len(count_blocks) == 5
    for block in count_blocks:
        assert set(re.findall(r"\d+", block)) == {"4"}
    assert "0.012" not in text
    assert "0.129" not in text
    assert "0.141" not in text


def test_decorative_ground_agvs_and_light_poles_are_inactive():
    text = OVERLAY.read_text(encoding="utf-8")
    for prim_name in (
        "SMART_PORT_agv_body_4",
        "SMART_PORT_agv_cyan_sensor_panel_4",
        "SMART_PORT_SIDEYARD_left_agv_0",
        "SMART_PORT_SIDEYARD_right_agv_sensor_4",
        "SMART_PORT_smart_cyan_camera_0",
        "SMART_PORT_smart_cyan_camera_8",
        "SMART_PORT_smart_light_head_0",
        "SMART_PORT_smart_light_head_8",
        "SMART_PORT_smart_light_pole_2",
        "SMART_PORT_smart_light_pole_8",
        "SMART_PORT_floating_data_panel_0",
        "SMART_PORT_floating_data_label_4__USD_MESH",
        "SMART_PORT_floating_data_panel_post_3",
        "SMART_PORT_SIDEYARD_left_zone_label_1__USD_MESH",
        "SMART_PORT_SIDEYARD_right_zone_label_4__USD_MESH",
        "LIVE_internal_truck_lane_yellow_safety_line",
        "LIVE_RTG_leg_line_reference_x_5_001",
        "SMART_PORT_white_lane_dash_y16__6",
        "Y_WHEEL_ground_tire_track_x_6_001",
        "Y_WHEEL_ground_tire_track_x_6_009",
        "Y_WHEEL_ground_tire_track_x_6_08",
        "Y_WHEEL_ground_tire_track_x_6_002",
    ):
        pattern = rf'over "{prim_name}"\s*\(\s*active = false'
        assert re.search(pattern, text)


def test_builder_documents_parallel_future_yards_and_lane_separation():
    text = BUILDER.read_text(encoding="utf-8")
    assert "Six yards repeat the 1C-4C bay template" in text
    assert "YARD_X_MIN = -10.0" in text
    assert "YARD_X_MAX = 10.0" in text
    assert (
        "YARD_COLUMN_CENTERS_X = (-60.0, -30.0, 0.0, 30.0, 60.0, 90.0)" in text
    )
    assert "YARD_LANE_WIDTH = 10.0" in text
    assert "BAY_PITCH_Y = BAY_LENGTH_M + BAY_GAP_M" in text
    assert "SMART_PORT_CLEAN_BAY_MARKINGS" in text
    assert "YARD_LAYOUT_START_Y_M" in text
    assert "REBUILT_LABEL_DIGITS_NAME" in text
    assert '"C": ("top", "upper_left", "lower_left", "bottom")' in text
    assert "_author_terminal_block_labels(output_stage)" in text
    assert "GROUND_MARKING_PREFIXES" in text
    assert "AI_SOURCE_PIVOT = Gf.Vec2d(-18.0, 17.0)" in text
    assert "AI_TARGET_CENTER = Gf.Vec2d(138.0, 470.0)" in text
    assert "AI_ROTATION_DEGREES = 90.0" in text


def test_old_left_and_right_central_boundary_meshes_are_inactive():
    text = OVERLAY.read_text(encoding="utf-8")
    for root_name in (
        "YARD_LAYOUT_1C4C_INFILL_LEFT_CENTRAL_BOUNDARIES",
        "YARD_LAYOUT_1C4C_INFILL_RIGHT_CENTRAL_BOUNDARIES",
    ):
        assert re.search(
            rf'over "{root_name}"\s*\(\s*active = false',
            text,
        )


def test_rebuilt_bay_markings_only_have_the_required_right_outer_boundary():
    text = OVERLAY.read_text(encoding="utf-8")
    mesh = re.search(
        r'def Mesh "SMART_PORT_CLEAN_BAY_MARKINGS"\s*\{(.*?)(?=\n\s*def Mesh ")',
        text,
        flags=re.DOTALL,
    )
    assert mesh
    point_block = re.search(
        r"point3f\[\] points = \[(.*?)\]", mesh.group(1), flags=re.DOTALL
    )
    assert point_block
    points = [
        tuple(float(value) for value in match)
        for match in re.findall(
            r"\((-?\d+(?:\.\d+)?), (-?\d+(?:\.\d+)?), (-?\d+(?:\.\d+)?)\)",
            point_block.group(1),
        )
    ]
    quads = [points[index : index + 4] for index in range(0, len(points), 4)]
    assert quads
    longitudinal_quads = []
    for quad in quads:
        y_span = max(point[1] for point in quad) - min(point[1] for point in quad)
        if y_span > 0.0551:
            longitudinal_quads.append(quad)

    assert len(longitudinal_quads) == 4
    expected_y_ranges = ((-0.5, 122.35), (129.75, 252.6), (260.0, 382.85), (390.25, 446.225))
    actual_y_ranges = []
    for quad in longitudinal_quads:
        x_values = [point[0] for point in quad]
        assert min(x_values) == 99.9725
        assert max(x_values) == 100.0275
        actual_y_ranges.append(
            (min(point[1] for point in quad), max(point[1] for point in quad))
        )
    assert tuple(actual_y_ranges) == expected_y_ranges
