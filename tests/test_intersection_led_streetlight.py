from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "omniverse" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import build_intersection_led_streetlight as builder  # noqa: E402


def test_composite_asset_hides_old_heads_and_adds_three_new_led_heads():
    source = builder.build_usda()
    assert source.count('token visibility = "invisible"') == 3
    assert source.count('prepend references = @../led/LED_Floodlight.usdz@') == 3
    assert source.count('def Xform "LED_Floodlight_') == 3


def test_traffic_layout_references_requested_streetlight_asset():
    source = (ROOT / "omniverse" / "scripts" / "build_quay_yard_traffic_layout.py").read_text(
        encoding="utf-8"
    )
    assert "Intersection_Street_Light.usdc" in source
