"""Browser text must preserve Unicode and never expose the in-memory key."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "omniverse/extensions/smartport.agent_panel"))
from smartport.agent_panel.web_bridge import AgentWebBridge


class Model:
    def __init__(self, value=""):
        self.value = value

    def get_value_as_string(self):
        return self.value

    def set_value(self, value):
        self.value = value


@pytest.fixture
def panel():
    return SimpleNamespace(_busy=False, _last_status="Ready", _input_model=Model(),
                           _endpoint_model=Model("https://api.deepseek.com/chat/completions"),
                           _model_model=Model("deepseek-flash"), _key_model=Model("private-test-key"),
                           _save_settings=lambda: None)


def test_unicode_draft_reaches_kit_model_unchanged(panel):
    text = "查询当前任务：4C/041，吊具🚢"
    AgentWebBridge.execute(panel, "draft", {"text": text})
    assert panel._input_model.value == text


def test_state_never_returns_api_key(panel):
    state = AgentWebBridge.execute(panel, "state", {})
    assert state["key_configured"] is True
    assert "private-test-key" not in repr(state)


def test_only_browser_native_input_poll_renews_input_lease(panel):
    AgentWebBridge.execute(panel, "state", {})
    assert not hasattr(panel, "_web_input_lease_until")
    AgentWebBridge.execute(panel, "state", {"native_input": True})
    assert panel._web_input_lease_until > 0


def test_blank_key_preserves_session_credential(panel):
    AgentWebBridge.execute(panel, "settings", {"endpoint": panel._endpoint_model.value,
                                              "model": "another-model", "api_key": ""})
    assert panel._key_model.value == "private-test-key"
    assert panel._model_model.value == "another-model"


def test_busy_request_cannot_be_overwritten_from_browser(panel):
    panel._busy = True
    panel._input_model.value = "existing draft"
    with pytest.raises(ValueError):
        AgentWebBridge.execute(panel, "send", {"text": "new request"})
    assert panel._input_model.value == "existing draft"


def test_invalid_endpoint_does_not_partially_change_settings(panel):
    with pytest.raises(ValueError):
        AgentWebBridge.execute(panel, "settings", {"endpoint": "http://external.invalid/",
                                                  "model": "changed", "api_key": "changed"})
    assert panel._model_model.value == "deepseek-flash"
    assert panel._key_model.value == "private-test-key"
