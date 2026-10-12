from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SMART_PORT = ROOT / "omniverse" / "scenes" / "smart_port.usda"
TRAFFIC = ROOT / "omniverse" / "scenes" / "quay_yard_traffic_layout.usda"
PREFIX = "SMART_PORT_QUAY_YARD_TRAFFIC_"
STREET_LIGHT_ASSET = ROOT / "omniverse" / "assets" / "resource" / "streetLight" / "Intersection_Street_Light.usdc"


def test_traffic_sublayer_is_referenced_without_touching_runtime_layers():
    main = SMART_PORT.read_text(encoding="utf-8")
    assert "@./quay_yard_traffic_layout.usda@" in main
    assert main.count("@./quay_yard_traffic_layout.usda@") == 1
    assert "rtg_twin_state_receiver" not in main


def test_traffic_layer_is_scoped_and_lightweight():
    text = TRAFFIC.read_text(encoding="utf-8")
    assert text.startswith("#usda 1.0")
    assert "metersPerUnit = 1" in text
    assert 'upAxis = "Z"' in text
    assert PREFIX + "LAYOUT" in text
    mesh_count = len(re.findall(r'^\s*def Mesh "', text, flags=re.MULTILINE))
    assert mesh_count == 13
    assert text.count('subdivisionScheme = "none"') == mesh_count
    # Twelve traffic meshes use display colors; the night lamp mesh uses a
    # bound emissive material instead.
    assert text.count('primvars:displayColor:interpolation = "constant"') == 12
    assert "X=-101.75..150.00; Y=-10.25..500.00" in text
    assert "longShoreline=positiveX" in text
    assert "Four straight quay-to-yard lanes use the clear approach space before bay 001 of 1C, 2C, 3C, and 4C" in text
    assert "connected by the long-shore road" in text
    assert "a full-width terminal cross-road" in text
    assert "waterside green tree belt and a yard-side road" in text
    assert "far-right former narrow lane is a green tree belt beside the outer continuous road" in text
    assert "five inter-yard gaps are traffic lanes" in text
    assert "two groups of ten waiting bays" in text
    assert "Y=439.025..499.000" not in text
    assert "Physics" not in text
    assert "Twelve traffic meshes plus 90 unique instanceable three-head street lights" in text
    assert "one merged night lamp-glow mesh" in text
    assert "no labels, collision, or runtime vehicles" in text
    assert 'prepend apiSchemas = ["MaterialBindingAPI"]' in text
    assert 'def Material "RoadSurface"' in text
    assert "color3f inputs:specularColor = (0, 0, 0)" in text
    assert "float inputs:roughness = 1" in text
    assert "rel material:binding = </World/SMART_PORT_QUAY_YARD_TRAFFIC_LAYOUT/Looks/RoadSurface>" in text


def test_every_true_crossroad_vertex_has_one_instanceable_street_light():
    text = TRAFFIC.read_text(encoding="utf-8")
    reference = "prepend references = @../assets/resource/streetLight/Intersection_Street_Light.usdc@"
    assert STREET_LIGHT_ASSET.is_file()
    assert PREFIX + "INTERSECTION_STREET_LIGHTS" in text
    assert "custom int intersectionCount = 24" in text
    assert "custom int streetLightCount = 90" in text
    assert "custom int headsPerStreetLight = 3" in text
    assert "custom int yardParkingLightCount = 66" in text
    assert "custom int requestedYardParkingLightCount = 84" in text
    assert "custom int mergedCrossroadCount = 18" in text
    assert "One side of every yard strip; 1C-3C:4 at both ends plus 2 middle positions" in text
    assert "custom double streetLightHeightMeters = 6.3305" in text
    assert text.count(reference) == 90
    assert text.count("instanceable = true") == 90
    assert text.count("float3 xformOp:scale = (1.65, 1.65, 0.5475)") == 90

    translations = {
        tuple(float(value) for value in match)
        for match in re.findall(
            r"xformOp:translate = \((-?\d+(?:\.\d+)?), (-?\d+(?:\.\d+)?), (-?\d+(?:\.\d+)?)\)",
            text,
        )
    }
    road_ranges = (
        (-85.875, -70.0),
        (-50.0, -40.0),
        (-20.0, -10.0),
        (10.0, 20.0),
        (40.0, 50.0),
        (70.0, 80.0),
    )
    cross_centers = (-5.375, 126.05, 256.30, 386.55)
    expected_intersections = {
        (round(x_max, 4), round(center_y + 3.7, 4), 0.112)
        for _x_min, x_max in road_ranges
        for center_y in cross_centers
    }
    assert expected_intersections.issubset(translations)
    # Three yard endpoints coincide with the corresponding crossroad lamps;
    # the set therefore contains 15 unique x=-70 translations (14 yard + 4
    # intersection placements minus 3 coincident endpoints).
    assert sum(1 for x, _y, _z in translations if x == -70.0) == 15
    for x in (-40.0, -10.0, 20.0, 50.0, 80.0):
        assert sum(1 for tx, _y, _z in translations if tx == x) == 15
    assert all(x != 102.0 for x, _y, _z in translations)


def test_street_lights_exclude_terminal_boundary_and_outer_t_junctions():
    text = TRAFFIC.read_text(encoding="utf-8")
    street_lights = re.search(
        rf'def Xform "{PREFIX}INTERSECTION_STREET_LIGHTS"\s*\{{(.*?)\n\s*\}}\n\n\s*def Xform "{PREFIX}INTERSECTION_NIGHT_LIGHTS"',
        text,
        flags=re.DOTALL,
    )
    assert street_lights
    section = street_lights.group(1)
    assert section.count("def Xform \"Road_") == 24
    assert section.count("def Xform \"Yard_") == 66
    assert section.count("float3 xformOp:rotateXYZ = (0, 0, 180)") == 24


def test_night_street_lighting_is_hidden_by_default_and_performance_bounded():
    text = TRAFFIC.read_text(encoding="utf-8")
    assert PREFIX + "INTERSECTION_NIGHT_LIGHTS" in text
    assert 'token visibility = "invisible"' in text
    assert "custom int effectiveLightCount = 270" in text
    assert "custom int emissiveLampHeadCount = 270" in text
    assert "custom int downlightsPerPole = 3" in text
    assert text.count('def DiskLight "') == 270
    assert text.count('_Downlight"') == 270
    assert text.count('prepend apiSchemas = ["ShapingAPI", "ShadowAPI"]') == 270
    assert text.count("bool inputs:shadow:enable = false") == 270
    assert text.count("float inputs:intensity = 70000") == 270
    assert text.count("float inputs:exposure = 2.2") == 270
    assert text.count("float inputs:radius = 0.24") == 270
    assert text.count("float inputs:shaping:cone:angle = 76") == 270
    assert text.count("float inputs:shaping:cone:softness = 0.68") == 270
    assert text.count("custom double2 smartPort:targetGroundXY") == 270
    assert "All yard, crossroad, and three-head DiskLights share one intensity" in text
    assert "yard lamps map to their adjacent bay-area code" in text
    assert "always cover both adjacent bay codes" in text
    assert text.count('uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ"]') >= 270
    assert "float3 xformOp:rotateXYZ = (" in text
    assert 'def Mesh "LampHeadGlow"' in text
    assert 'def Mesh "GroundPool' not in text
    assert 'def Material "GroundPool' not in text
    assert "color3f inputs:emissiveColor = (30, 18, 7)" in text


def test_yard_street_lights_map_to_their_adjacent_bay_area_codes():
    text = TRAFFIC.read_text(encoding="utf-8")
    targets = re.findall(
        r'def DiskLight "Yard_(\dC)_Side_(\d+)_Light_(\d+)_Head_(\d+)_Downlight".*?'
        r'custom double2 smartPort:targetGroundXY = \((-?\d+(?:\.\d+)?), (-?\d+(?:\.\d+)?)\)',
        text,
        flags=re.DOTALL,
    )
    assert len(targets) == 66 * 3

    pole_x_by_side = (-70.0, -40.0, -10.0, 20.0, 50.0, 80.0)
    right_offsets = (6.85, 11.25, 15.65)
    left_offsets = (-23.15, -18.75, -14.35)
    block_specs = {
        "1C": (-0.5, 46),
        "2C": (129.75, 46),
        "3C": (260.0, 46),
        "4C": (390.25, 21),
    }
    bay_pitch = 2.475 + 0.20

    for block, side, light, head, target_x, target_y in targets:
        side_index = int(side)
        light_index = int(light)
        if side_index == 1:
            target_offsets = right_offsets
        elif light_index % 2 == 1:
            target_offsets = (left_offsets[2], right_offsets[1], left_offsets[0])
        else:
            target_offsets = (right_offsets[0], right_offsets[2], left_offsets[1])
        expected_x = pole_x_by_side[side_index - 1] + target_offsets[int(head) - 1]
        assert abs(float(target_x) - expected_x) < 0.0001

        block_start_y, slot_count = block_specs[block]
        first_bay_center_y = block_start_y + 2.475 * 0.5
        slot_index = round((float(target_y) - first_bay_center_y) / bay_pitch)
        assert 0 <= slot_index < slot_count
        assert abs(float(target_y) - (first_bay_center_y + slot_index * bay_pitch)) < 0.0001


def test_traffic_lane_markings_are_omitted_but_bay_marks_stay_in_yard_layer():
    text = TRAFFIC.read_text(encoding="utf-8")
    white_mesh = re.search(
        rf'def Mesh "{PREFIX}WHITE_LANE_MARKINGS"\s*\{{(.*?)(?=\n\s*def Mesh ")',
        text,
        flags=re.DOTALL,
    )
    yellow_mesh = re.search(
        rf'def Mesh "{PREFIX}YELLOW_LANE_CENTERLINES"\s*\{{(.*?)(?=\n\s*def Mesh ")',
        text,
        flags=re.DOTALL,
    )
    assert white_mesh and yellow_mesh
    assert "[(0.88, 0.9, 0.87)]" in white_mesh.group(1)
    assert "[(0.96, 0.76, 0.04)]" in yellow_mesh.group(1)
    assert "faceVertexCounts = []" in white_mesh.group(1)
    assert "point3f[] points = [\n\n            ]" in white_mesh.group(1)
    assert "faceVertexCounts = []" in yellow_mesh.group(1)
    assert "point3f[] points = [\n\n            ]" in yellow_mesh.group(1)


def test_traffic_layer_braces_and_coordinates_are_well_formed():
    text = TRAFFIC.read_text(encoding="utf-8")
    assert text.count("{") == text.count("}")
    assert text.count("[") == text.count("]")
    point_blocks = re.findall(r"point3f\[\] points = \[(.*?)\]", text, flags=re.DOTALL)
    points = re.findall(
        r"\((-?\d+(?:\.\d+)?), (-?\d+(?:\.\d+)?), (-?\d+(?:\.\d+)?)\)",
        "\n".join(point_blocks),
    )
    assert points
    xs = [float(x) for x, _, _ in points]
    ys = [float(y) for _, y, _ in points]
    assert min(xs) >= -101.75
    assert max(xs) <= 150
    # The first-row lamp lenses extend 0.46 m beyond the road-layer boundary.
    assert min(ys) >= -10.75
    assert max(ys) <= 500


def test_four_quay_lanes_reach_the_full_yard_width_before_each_bay_001():
    text = TRAFFIC.read_text(encoding="utf-8")
    for center_y in (-5.375, 126.05, 256.30, 386.55):
        lower_edge = center_y - 3.7
        formatted = f"{lower_edge:.4f}".rstrip("0").rstrip(".")
        assert f"(-101.75, {formatted}, 0.108)" in text
        assert f"(110.5, {formatted}, 0.108)" in text


def test_four_quay_lane_landings_expand_only_through_the_coastal_green_belt():
    text = TRAFFIC.read_text(encoding="utf-8")
    for center_y in (-5.375, 126.05, 256.30, 386.55):
        lower_edge = f"{center_y - 4.5:g}"
        upper_edge = f"{center_y + 4.5:g}"
        assert f"(-101.75, {lower_edge}, 0.11)" in text
        assert f"(-85.875, {upper_edge}, 0.11)" in text
        assert f"(-70, {lower_edge}, 0.11)" not in text


def test_terminal_cross_road_connects_every_longitudinal_lane_to_the_apron():
    text = TRAFFIC.read_text(encoding="utf-8")
    assert "(-101.75, 492.6, 0.11)" in text
    assert "(150, 500, 0.11)" in text
    white_mesh = re.search(
        rf'def Mesh "{PREFIX}WHITE_LANE_MARKINGS"\s*\{{(.*?)(?=\n\s*def Mesh ")',
        text,
        flags=re.DOTALL,
    )
    assert white_mesh
    assert "492.7, 0.145" not in white_mesh.group(1)
    assert "499.9, 0.145" not in white_mesh.group(1)


def test_green_belts_fill_the_terminal_corners_up_to_the_road_surface():
    text = TRAFFIC.read_text(encoding="utf-8")
    for x_min, x_max in ((-101.75, -85.875), (105.25, 110.5)):
        assert f"({x_min:g}, 392.05, 0.111)" in text
        assert f"({x_max:g}, 492.6, 0.111)" in text
    assert "(-85.875, 490.8, 0.111)" not in text
    assert "(105.25, 490.8, 0.111)" not in text


def test_interblock_roads_fit_the_expanded_gaps_without_covering_bays():
    block_ranges = (
        (-0.5, 122.35),
        (129.75, 252.60),
        (260.00, 382.85),
        (390.25, 446.225),
    )
    centers = (126.05, 256.30, 386.55)
    for previous, following, center_y in zip(
        block_ranges, block_ranges[1:], centers
    ):
        assert round(center_y - 3.7, 3) == previous[1]
        assert round(center_y + 3.7, 3) == following[0]


def test_coastal_strip_and_inter_yard_gaps_are_longitudinal_roads():
    text = TRAFFIC.read_text(encoding="utf-8")
    road_ranges = (
        (-85.875, -70.0),
        (-50.0, -40.0),
        (-20.0, -10.0),
        (10.0, 20.0),
        (40.0, 50.0),
        (70.0, 80.0),
    )
    for x_min, x_max in road_ranges:
        assert f"({x_min:g}, -10.25, 0.109)" in text
        assert f"({x_max:g}, 500, 0.109)" in text


def test_other_half_of_former_coastal_yard_is_a_tree_belt():
    text = TRAFFIC.read_text(encoding="utf-8")
    assert PREFIX + "COASTAL_GREEN_BELT" in text
    assert PREFIX + "COASTAL_TREE_TRUNKS" in text
    assert PREFIX + "COASTAL_TREE_CROWNS" in text
    assert "(-101.75, 0.125, 0.111)" in text
    assert "(-85.875, 120.55, 0.111)" in text
    assert "color3f[] primvars:displayColor = [(0.06, 0.34, 0.11)]" in text


def test_far_right_green_belt_replaces_the_former_narrow_lane():
    text = TRAFFIC.read_text(encoding="utf-8")
    assert PREFIX + "RIGHT_EDGE_GREEN_BELT" in text
    assert PREFIX + "RIGHT_EDGE_TREE_TRUNKS" in text
    assert PREFIX + "RIGHT_EDGE_TREE_CROWNS" in text
    assert "(105.25, 0.125, 0.111)" in text
    assert "(110.5, 120.55, 0.111)" in text
    assert "(100, 0.125, 0.111)" not in text
    assert "(107.695," in text
    assert "(102.445," not in text
    assert "(105.25, -10.25, 0.109)" not in text
    assert "(110.5, 500, 0.109)" not in text


def test_white_lane_markings_stop_at_all_yard_road_intersections():
    text = TRAFFIC.read_text(encoding="utf-8")
    mesh = re.search(
        rf'def Mesh "{PREFIX}WHITE_LANE_MARKINGS"\s*\{{(.*?)(?=\n\s*def Mesh ")',
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
    yard_roads = (
        (-85.875, -70.0),
        (-50.0, -40.0),
        (-20.0, -10.0),
        (10.0, 20.0),
        (40.0, 50.0),
        (70.0, 80.0),
    )
    cross_centers = (-5.375, 126.05, 256.30, 386.55, 496.30)
    for quad in quads:
        x_min = min(point[0] for point in quad)
        x_max = max(point[0] for point in quad)
        y_min = min(point[1] for point in quad)
        y_max = max(point[1] for point in quad)
        for road_x_min, road_x_max in yard_roads:
            for center_y in cross_centers:
                overlaps_intersection = (
                    x_max > road_x_min
                    and x_min < road_x_max
                    and y_max > center_y - 3.7
                    and y_min < center_y + 3.7
                )
                assert not overlaps_intersection
