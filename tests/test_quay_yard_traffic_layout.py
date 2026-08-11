from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SMART_PORT = ROOT / "omniverse" / "scenes" / "smart_port.usda"
TRAFFIC = ROOT / "omniverse" / "scenes" / "quay_yard_traffic_layout.usda"
PREFIX = "SMART_PORT_QUAY_YARD_TRAFFIC_"


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
    assert mesh_count == 12
    assert text.count('subdivisionScheme = "none"') == mesh_count
    assert text.count('primvars:displayColor:interpolation = "constant"') == mesh_count
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
    assert "Twelve merged meshes" in text
    assert "no labels, collision, shadows, or runtime vehicles" in text


def test_lane_edges_stay_white_and_center_dashes_are_yellow():
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
    assert "(117.89," not in white_mesh.group(1)
    assert "(117.89," in yellow_mesh.group(1)


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
    assert min(ys) >= -10.25
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
    assert "(-101.75, 492.7, 0.145)" in text
    assert "(150, 499.9, 0.145)" in text


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
