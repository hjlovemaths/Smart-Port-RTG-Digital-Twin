import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "omniverse" / "scripts"))
from preview_rain_effects import summarize_samples


def test_frame_generation_is_reported_separately_from_native_fps():
    result = summarize_samples([.025] * 100, [40] * 10, [160] * 10)
    assert result["app_average_fps"] == pytest.approx(40)
    assert result["displayed_median_fps"] == 160
    assert result["meets_sampled_displayed_150_fps"]
    assert not result["meets_native_150_fps"]


def test_frame_generation_dropouts_are_not_hidden():
    result = summarize_samples([.025] * 100, [40] * 10, [160] * 9 + [40])
    assert result["displayed_min_sampled_fps"] == 40
    assert not result["meets_sampled_displayed_150_fps"]


def test_empty_benchmark_does_not_report_a_pass():
    with pytest.raises(RuntimeError):
        summarize_samples([], [])
