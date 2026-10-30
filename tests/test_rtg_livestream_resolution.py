"""WebRTC presets must advertise resize support and retain requested sizes."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "omniverse/scripts/rtg_viewport_only_livestream.py"
spec = importlib.util.spec_from_file_location("rtg_livestream_resolution_test", SCRIPT)
stream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stream)


@pytest.fixture
def settings(monkeypatch):
    values = {}
    carb = ModuleType("carb")
    settings_module = ModuleType("carb.settings")
    settings_module.get_settings = lambda: SimpleNamespace(set=values.__setitem__)
    carb.settings = settings_module
    monkeypatch.setitem(sys.modules, "carb", carb)
    monkeypatch.setitem(sys.modules, "carb.settings", settings_module)
    return values


def test_stream_accepts_browser_resize_and_os_clamped_frames(settings):
    stream._configure_livestream(width=2560, height=1440)
    assert settings["/exts/omni.kit.livestream.app/primaryStream/allowDynamicResize"] is True
    assert settings["/app/window/width"] == 2560
    assert settings["/app/window/height"] == 1440


def test_fixed_size_stream_can_explicitly_opt_out(settings):
    stream._configure_livestream(allow_dynamic_resize=False)
    assert settings["/exts/omni.kit.livestream.app/primaryStream/allowDynamicResize"] is False


def test_live_fix_only_changes_resize_capability(settings):
    stream.enable_livestream_dynamic_resize()
    assert settings == {"/exts/omni.kit.livestream.app/primaryStream/allowDynamicResize": True}


@pytest.mark.parametrize("preset,size", [("1080p", (1920, 1080)), ("2k", (2560, 1440))])
def test_presets_request_their_named_resolution(monkeypatch, preset, size):
    monkeypatch.setattr(stream, "start_viewport_only_livestream", lambda **kwargs: kwargs)
    result = getattr(stream, "start_viewport_only_livestream_" + preset)(hide_kit_ui=False)
    assert (result["width"], result["height"]) == size
    assert result["hide_kit_ui"] is False


def test_qhd_preset_preserves_explicit_overrides(monkeypatch):
    monkeypatch.setattr(stream, "start_viewport_only_livestream", lambda **kwargs: kwargs)
    result = stream.start_viewport_only_livestream_2k(width=1280, height=720, target_fps=30)
    assert (result["width"], result["height"], result["target_fps"]) == (1280, 720, 30)
