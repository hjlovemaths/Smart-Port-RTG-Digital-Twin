"""Engineering-height to optical zoom mapping; independent of Kit."""
import math
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "omniverse/scripts"))
from rtg_work_views import TOP_WIDE_FOCAL_MM, top_zoom_focal_for_height


def test_zoom_reference_heights_and_interpolated_positions():
    assert top_zoom_focal_for_height(15) == pytest.approx(TOP_WIDE_FOCAL_MM)
    assert top_zoom_focal_for_height(10) == 24
    assert top_zoom_focal_for_height(5) == 40
    assert top_zoom_focal_for_height(7.5) == 32
    assert top_zoom_focal_for_height(-10) == 48
    assert top_zoom_focal_for_height(100) == 9


def test_lowering_continuously_increases_focal_length_without_threshold_jumps():
    values = [top_zoom_focal_for_height(h / 10) for h in range(5, 181)]
    assert all(a >= b for a, b in zip(values, values[1:]))
    for height in (5, 10, 15):
        assert abs(top_zoom_focal_for_height(height - .001) - top_zoom_focal_for_height(height + .001)) < .01


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_invalid_height_does_not_produce_invalid_camera_lens(value):
    with pytest.raises(ValueError):
        top_zoom_focal_for_height(value)
