import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "omniverse" / "scenes" / "smart_port.usda"
LAYER = ROOT / "omniverse" / "scenes" / "ship_traffic_animation.usda"
BUILDER = ROOT / "omniverse" / "scripts" / "build_ship_traffic_animation.py"


def test_outbound_ship_animation_layer_is_loaded_once():
    text = MAIN.read_text(encoding="utf-8")
    assert text.count("@./ship_traffic_animation.usda@") == 1


def test_outbound_ship_uses_the_same_model_and_scale_as_the_berth_ships():
    text = LAYER.read_text(encoding="utf-8")
    assert "Cargo_Ship_09_variant_b.usdz" in text
    assert "float3 xformOp:scale = (0.03883929, 0.027641999, 0.043764994)" in text


def test_outbound_route_travels_about_one_kilometre_and_disappears():
    text = LAYER.read_text(encoding="utf-8")
    match = re.search(r"custom double ship:routeLengthMeters = ([\d.]+)", text)
    assert match
    assert 1000.0 <= float(match.group(1)) <= 1100.0
    assert "double3 xformOp:translate.timeSamples" in text
    assert "900: \"invisible\"" in text
    assert "ROUTE = (" in BUILDER.read_text(encoding="utf-8")
