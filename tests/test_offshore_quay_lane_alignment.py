import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TRAFFIC_BUILDER = ROOT / "omniverse" / "scripts" / "build_quay_yard_traffic_layout.py"
QUAY_BUILDER = ROOT / "omniverse" / "scripts" / "build_offshore_quay_layout.py"
TRAFFIC_SCENE = ROOT / "omniverse" / "scenes" / "quay_yard_traffic_layout.usda"
QUAY_SCENE = ROOT / "omniverse" / "scenes" / "offshore_quay_layout.usda"


EXPECTED_CENTERS = (-5.375, 126.05, 256.30, 386.55)


def _tuple_constant(text: str, name: str) -> tuple[float, ...]:
    match = re.search(rf"^{name}\s*=\s*\(([^)]+)\)", text, re.MULTILINE)
    assert match
    return tuple(float(value.strip()) for value in match.group(1).split(",") if value.strip())


def test_quay_causeways_share_the_yard_approach_centerlines():
    traffic = TRAFFIC_BUILDER.read_text(encoding="utf-8")
    quay = QUAY_BUILDER.read_text(encoding="utf-8")
    assert _tuple_constant(traffic, "BLOCK_APPROACH_LANE_CENTERS_Y") == EXPECTED_CENTERS
    assert _tuple_constant(quay, "CAUSEWAY_CENTER_Y_M") == EXPECTED_CENTERS


def test_generated_causeways_overlap_the_yard_roads_at_the_shoreline():
    traffic = TRAFFIC_SCENE.read_text(encoding="utf-8")
    quay = QUAY_SCENE.read_text(encoding="utf-8")
    assert 'custom double[] smartPort:causewayCentersY = [-5.375, 126.05, 256.3, 386.55]' in quay
    assert "access causeways X=-221.5..-101.5" in quay
    assert "(-101.75, -9.075, 0.108)" in traffic

    for index, center_y in enumerate(EXPECTED_CENTERS, start=1):
        pattern = (
            rf'def Cube "Causeway_{index:02d}".*?'
            r'double3 xformOp:translate = \(([^,]+), ([^,]+), ([^)]+)\)'
        )
        match = re.search(pattern, quay, re.DOTALL)
        assert match
        position = tuple(float(value) for value in match.groups())
        assert position == pytest.approx((-161.5, center_y, 0.04))


def test_shore_landings_match_causeway_width_after_lane_marking_removal():
    traffic = TRAFFIC_SCENE.read_text(encoding="utf-8")
    for center_y in EXPECTED_CENTERS:
        lower_surface = center_y - 4.5
        upper_surface = center_y + 4.5
        assert f"(-101.75, {lower_surface:g}, 0.11)" in traffic
        assert f"(-85.875, {upper_surface:g}, 0.11)" in traffic


def test_offshore_quay_decks_match_the_traffic_road_color():
    builder = QUAY_BUILDER.read_text(encoding="utf-8")
    quay = QUAY_SCENE.read_text(encoding="utf-8")
    traffic_builder = TRAFFIC_BUILDER.read_text(encoding="utf-8")
    road_color = "(0.018, 0.035, 0.06)"
    assert f"CONCRETE = Gf.Vec3f{road_color}" in builder
    assert f'mesh_usda(f"{{PREFIX}}ROAD_SURFACE", {road_color}' in traffic_builder
    for name in ("OffshoreQuayPlatform", "Causeway_01", "Causeway_04"):
        pattern = (
            rf'def Cube "{name}".*?'
            r'color3f\[\] primvars:displayColor = \[\(0\.018, 0\.035, 0\.06\)\]'
        )
        assert re.search(pattern, quay, re.DOTALL)


def test_quay_has_two_longitudinal_lanes_without_yellow_center_dashes():
    builder = QUAY_BUILDER.read_text(encoding="utf-8")
    quay = QUAY_SCENE.read_text(encoding="utf-8")
    assert "WATERSIDE_TRAFFIC_LANE_CENTER_X_M = -238.0" in builder
    assert "LANDSIDE_TRAFFIC_LANE_CENTER_X_M = -225.0" in builder
    assert quay.count('def Cube "WatersideLane_CenterDash_') == 0
    assert quay.count('def Cube "LandsideLane_CenterDash_') == 0
    for name in (
        "WatersideLane_LeftEdge",
        "WatersideLane_RightEdge",
        "LandsideLane_LeftEdge",
        "LandsideLane_RightEdge",
    ):
        assert f'def Cube "{name}"' in quay


def test_causeway_edges_merge_with_the_yard_cross_road_edges():
    builder = QUAY_BUILDER.read_text(encoding="utf-8")
    quay = QUAY_SCENE.read_text(encoding="utf-8")
    assert "CAUSEWAY_EDGE_LINE_OFFSET_M = 3.7" in builder
    assert "CAUSEWAY_EDGE_LINE_WIDTH_M = 0.18" in builder
    assert "causeway_length - 2.0 * CAUSEWAY_SHORE_OVERLAP_M" in builder
    for center_y in EXPECTED_CENTERS:
        for edge_y in (center_y - 3.7, center_y + 3.7):
            assert f"double3 xformOp:translate = (-161.5, {edge_y:g}, 0.205)" in quay
    causeway_markings = re.findall(
        r'def Cube "Causeway_\d+_(?:Left|Right)Edge".*?'
        r'color3f\[\] primvars:displayColor = \[(\([^\]]+\))\].*?'
        r'float3 xformOp:scale = \(([^,]+), ([^,]+), ([^)]+)\)',
        quay,
        flags=re.DOTALL,
    )
    assert len(causeway_markings) == 8
    assert all(color == "(0.88, 0.9, 0.87)" for color, _x, y, _z in causeway_markings)
    assert all(float(y) == 0.18 for _color, _x, y, _z in causeway_markings)


def test_four_additional_sts_cranes_are_evenly_distributed_on_the_quay():
    builder = QUAY_BUILDER.read_text(encoding="utf-8")
    quay = QUAY_SCENE.read_text(encoding="utf-8")
    assert "ADDITIONAL_STS_CRANE_CENTERS_Y_M = (55.0, 130.0, 370.0, 445.0)" in builder
    assert quay.count('def Xform "SMART_PORT_ADDITIONAL_STS_CRANE_') == 4
    assert quay.count("smartPort:offshoreQuayAdditional = 1") == 4
    assert quay.count("STS_Crane_Static_Visual.usdc@</STSCrane>") == 4
    assert '"CODEx_STS_Crane0"' in builder
    assert '"SMART_PORT_quay_sts_crane_0"' in builder
