from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SMART_PORT = ROOT / "omniverse" / "scenes" / "smart_port.usda"
GROUND = ROOT / "omniverse" / "scenes" / "ground_color_override.usda"


def test_ground_override_is_referenced_once():
    main = SMART_PORT.read_text(encoding="utf-8")
    reference = "@./ground_color_override.usda@"
    assert main.count(reference) == 1


def test_ground_override_matches_reference_palette_and_stays_lightweight():
    text = GROUND.read_text(encoding="utf-8")
    assert text.startswith("#usda 1.0")
    assert 'colorReference = "same low-reflection asphalt palette as the traffic road surface"' in text
    assert "primvars:displayColor = [(0.018, 0.035, 0.06)]" in text
    assert 'prepend apiSchemas = ["MaterialBindingAPI"]' in text
    assert 'def Material "RoadSurface"' in text
    assert "color3f inputs:specularColor = (0, 0, 0)" in text
    assert "float inputs:roughness = 1" in text
    assert len(re.findall(r'^\s*def Mesh "', text, flags=re.MULTILINE)) == 1
    assert 'subdivisionScheme = "none"' in text


def test_ground_override_covers_yard_below_markings():
    text = GROUND.read_text(encoding="utf-8")
    point_block = re.search(r"point3f\[\] points = \[(.*?)\]", text, flags=re.DOTALL)
    assert point_block is not None
    points = re.findall(
        r"\((-?\d+(?:\.\d+)?), (-?\d+(?:\.\d+)?), (-?\d+(?:\.\d+)?)\)",
        point_block.group(1),
    )
    assert len(points) == 4
    xs = [float(x) for x, _, _ in points]
    ys = [float(y) for _, y, _ in points]
    zs = [float(z) for _, _, z in points]
    assert min(xs) == -101.75
    assert max(xs) == 108.75
    assert min(ys) == -10.25
    assert max(ys) == 493.075
    assert set(zs) == {0.106}
