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
    assert mesh_count <= 12
    assert mesh_count == 5
    assert text.count('subdivisionScheme = "none"') == mesh_count
    assert text.count('primvars:displayColor:interpolation = "constant"') == mesh_count
    assert "X=108.75..150.00; Y=120.00..420.00" in text
    assert "longShoreline=positiveX" in text
    assert "two groups of ten waiting bays" in text
    assert "Y=439.025..499.000" not in text
    assert "Physics" not in text
    assert "no labels, collision, shadows, or runtime vehicles" in text


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
    assert min(xs) >= 101
    assert max(xs) <= 150
    assert min(ys) >= 120
    assert max(ys) <= 420
