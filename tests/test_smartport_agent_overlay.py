"""Keep viewport chat usable when docks or the main window are resized."""

from pathlib import Path
import sys

import pytest


EXTENSION_ROOT = Path(__file__).resolve().parents[1] / "omniverse/extensions/smartport.agent_panel"
if str(EXTENSION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXTENSION_ROOT))

from smartport.agent_panel.viewport_overlay import panel_layout, ViewportChatOverlay
from types import SimpleNamespace
import time


@pytest.mark.parametrize("width,height", [(1810, 750), (2560, 1440), (1280, 720), (800, 600), (480, 320)])
def test_cards_stay_in_viewport_without_overlapping(width, height):
    layout = panel_layout(width, height)
    ix, iy, iw, ih = layout["input"]
    ox, oy, ow, oh = layout["output"]
    for x, y, w, h in layout.values():
        assert 0 <= x < width and 0 <= y < height
        assert x + w <= width and y + h <= height
    assert ix + iw / 2 == pytest.approx(width / 2)
    assert ix + iw < ox or oy + oh < iy
    assert height - iy - ih == pytest.approx(width - ox - ow)


def test_wide_viewport_anchors_response_to_bottom_right():
    layout = panel_layout(1810, 750)
    _, iy, _, ih = layout["input"]
    _, oy, _, oh = layout["output"]
    assert iy + ih == pytest.approx(oy + oh)


def test_narrow_viewport_moves_response_above_input():
    layout = panel_layout(480, 320)
    assert layout["output"][1] + layout["output"][3] < layout["input"][1]


def test_minimized_viewport_produces_nonnegative_sizes():
    assert all(value >= 0 for rect in panel_layout(0, 0).values() for value in rect)


def test_browser_input_replaces_native_card_and_expiry_restores_fallback():
    panel = SimpleNamespace(_web_input_lease_until=time.monotonic()+5)
    overlay = ViewportChatOverlay(panel)
    overlay._input_card = SimpleNamespace(visible=True)
    overlay._input_placer = SimpleNamespace(visible=True)
    overlay._output_card = SimpleNamespace(visible=True)
    overlay._apply_input_visibility()
    assert not overlay._input_card.visible and not overlay._input_placer.visible
    assert overlay._output_card.visible
    panel._web_input_lease_until = 0
    overlay._apply_input_visibility()
    assert overlay._input_card.visible and overlay._input_placer.visible
    assert overlay._output_card.visible
