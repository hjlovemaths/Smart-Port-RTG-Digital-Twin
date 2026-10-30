"""Independent work-view switches and background render gating, without Kit."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "omniverse/scripts"))
from rtg_work_views import RtgWorkViews, VIEW_DEFAULTS, VIEW_NAMES, VIEW_SETTINGS_ROOT


@pytest.fixture
def service():
    view = RtgWorkViews.__new__(RtgWorkViews)
    view.view_enabled = dict(VIEW_DEFAULTS)
    view.view_panels = {name: SimpleNamespace(visible=enabled) for name, enabled in VIEW_DEFAULTS.items()}
    view.view_widgets = {name: SimpleNamespace(viewport_api=SimpleNamespace(updates_enabled=False)) for name in VIEW_NAMES}
    view.view_models = {}
    saved = {}
    view.settings = SimpleNamespace(set=lambda key, value: saved.update({key: value}))
    view.saved = saved
    view.empty_label = SimpleNamespace(visible=False)
    view.window = SimpleNamespace(docked=True, selected_in_dock=True, visible=True)
    view.refreshes = []
    view.update_cameras = lambda dt: view.refreshes.append(dt)
    view._ensure_view_widget = lambda name: None
    view._selected_changed(True)
    return view


def test_third_view_starts_off_and_each_switch_only_controls_its_own_render(service):
    assert service.view_enabled == {"Top": True, "Side": True, "Overview": False}
    service.set_view_enabled("Side", False)
    assert service.view_widgets["Top"].viewport_api.updates_enabled
    assert not service.view_widgets["Side"].viewport_api.updates_enabled
    assert not service.view_panels["Side"].visible
    assert service.saved[VIEW_SETTINGS_ROOT + "/showSide"] is False
    service.set_view_enabled("Overview", True)
    assert service.view_widgets["Overview"].viewport_api.updates_enabled
    assert service.view_panels["Overview"].visible
    assert service.refreshes == [0]


def test_background_tab_does_not_resume_render_when_enabling_a_view(service):
    service.window.selected_in_dock = False
    service._selected_changed(False)
    service.set_view_enabled("Overview", True)
    assert not any(widget.viewport_api.updates_enabled for widget in service.view_widgets.values())
    service.set_view_enabled("Side", False)
    service.window.selected_in_dock = True
    service._selected_changed(True)
    assert service.view_widgets["Top"].viewport_api.updates_enabled
    assert service.view_widgets["Overview"].viewport_api.updates_enabled
    assert not service.view_widgets["Side"].viewport_api.updates_enabled


def test_all_views_can_be_off_and_reopened_independently(service):
    for name in VIEW_NAMES:
        service.set_view_enabled(name, False)
    assert service.empty_label.visible
    assert not any(widget.viewport_api.updates_enabled for widget in service.view_widgets.values())
    service.set_view_enabled("Side", True)
    assert not service.empty_label.visible
    assert service.view_enabled == {"Top": False, "Side": True, "Overview": False}


def test_unknown_view_cannot_change_preferences(service):
    before = dict(service.view_enabled)
    with pytest.raises(ValueError, match="Unknown work view"):
        service.set_view_enabled("Missing", True)
    assert service.view_enabled == before
    assert not service.saved
