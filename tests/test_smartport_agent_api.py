from __future__ import annotations

import socket
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


EXTENSION_ROOT = (
    Path(__file__).resolve().parents[1]
    / "omniverse"
    / "extensions"
    / "smartport.agent_panel"
)
if str(EXTENSION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXTENSION_ROOT))

from smartport.agent_panel import agent_api, runtime_catalog
from smartport.agent_panel.web_bridge import IECSConversation, IECS_AGENT_TOOLS


def test_request_payload_retains_system_prompt_and_attaches_only_allowlisted_tools():
    messages = [{"role": "system", "content": "system"}]
    for i in range(30):
        messages.extend(
            [
                {"role": "user", "content": str(i)},
                {"role": "assistant", "content": "answer"},
            ]
        )

    payload = agent_api.build_request_payload("test-model", messages)

    assert payload["messages"][0] == messages[0]
    assert len(payload["messages"]) <= agent_api.MAX_HISTORY_MESSAGES
    assert payload["messages"][1]["role"] == "user"
    assert {tool["function"]["name"] for tool in payload["tools"]} == {
        "get_scene_status",
        "get_bay_inventory",
        "get_yard_inventory_summary",
        "set_environment",
        "set_time_of_day",
        "set_weather",
        "move_rtg_to_bay",
        "set_rtg_axis_positions",
    } | set(runtime_catalog.SCHEMAS)


def test_remote_api_must_use_https_but_local_development_can_use_http():
    assert agent_api.validate_endpoint("https://agent.example/v1/chat/completions")
    assert agent_api.validate_endpoint("http://127.0.0.1:9000/v1/chat/completions")
    assert agent_api.is_loopback_endpoint("http://localhost:9000/v1/chat/completions")
    assert not agent_api.is_loopback_endpoint("https://agent.example/v1/chat/completions")
    with pytest.raises(ValueError, match="HTTPS"):
        agent_api.validate_endpoint("http://agent.example/v1/chat/completions")


def test_tool_calls_are_allowlisted_and_arguments_are_validated_before_kit():
    name, args = agent_api.parse_tool_call(
        {
            "id": "call_1",
            "function": {
                "name": "move_rtg_to_bay",
                "arguments": '{"bay_id":"4c-40","anchor":"center"}',
            },
        }
    )
    assert name == "move_rtg_to_bay"
    assert args == {"bay_id": "4C/040", "anchor": "center"}

    with pytest.raises(ValueError, match="unavailable tool"):
        agent_api.validate_tool_arguments("execute_python", {"code": "pass"})
    with pytest.raises(ValueError, match="between 0 and 24"):
        agent_api.validate_tool_arguments("set_time_of_day", {"hour": 25})
    with pytest.raises(ValueError, match="wind_speed_mps"):
        agent_api.validate_tool_arguments("set_weather", {"wind_speed_mps": 30})
    with pytest.raises(ValueError, match="Unexpected argument"):
        agent_api.validate_tool_arguments("set_environment", {"mode": "night", "code": "x"})


def test_inventory_tools_accept_compact_bays_but_no_arbitrary_source_paths():
    assert agent_api.validate_tool_arguments("get_bay_inventory", {"bay_id": "4c41"}) == {"bay_id": "4C/041"}
    assert agent_api.validate_tool_arguments("get_yard_inventory_summary", {"block": "4c"}) == {"block": "4C"}
    assert agent_api.validate_tool_arguments("get_yard_inventory_summary", {}) == {}
    with pytest.raises(ValueError, match="Unexpected argument"):
        agent_api.validate_tool_arguments("get_bay_inventory", {"bay_id": "4C41", "path": "secrets.json"})
    with pytest.raises(ValueError, match="bay_id"):
        agent_api.validate_tool_arguments("get_bay_inventory", {"bay_id": "4C41123"})
    with pytest.raises(ValueError, match="block"):
        agent_api.validate_tool_arguments("get_yard_inventory_summary", {"block": "5C"})


def test_iecs_view_selects_one_order_from_the_workbench():
    validate = agent_api.validate_tool_arguments
    assert validate("set_viewport_view", {"target": "iecs_order", "order_index": 4}) == {
        "target": "iecs_order", "order_index": 4, "follow": True,
    }
    assert validate("control_iecs", {"action": "focus", "order_id": "iecs-00004"})["order_id"] == "IECS-00004"
    with pytest.raises(ValueError):
        validate("set_viewport_view", {"target": "iecs_order", "order_index": 0})
    with pytest.raises(ValueError):
        validate("set_viewport_view", {"target": "iecs_order", "order_index": 4, "order_id": "IECS-00004"})
    with pytest.raises(ValueError):
        validate("set_viewport_view", {"target": "task", "order_index": 4})
    with pytest.raises(ValueError):
        validate("control_iecs", {"action": "reset", "order_index": 4})


def test_iecs_chat_uses_scoped_tools_and_session_history():
    finished = threading.Event()
    observed = {}

    def run_turn(endpoint, model, key, messages, *, emit, tool_names, active, open_endpoint):
        observed["active"] = active()
        observed["endpoint"] = endpoint
        observed["key"] = key
        observed["opener"] = open_endpoint
        observed["payload"] = agent_api.build_request_payload(model, messages, tool_names=tool_names)
        emit({"kind": "complete", "answer": "当前没有等待车辆。", "history": messages})
        finished.set()

    panel = SimpleNamespace(_run_agent_turn=run_turn)
    first = IECSConversation()
    second = IECSConversation()
    prompt = "现在有多少辆集卡在等待？"

    def active():
        return True

    settings = {"endpoint": "https://agent.example/v1/chat/completions", "model": "iecs-model", "api_key": "test-key"}
    assert not first.handle(panel, "state", {}, active)["configured"]
    assert first.handle(panel, "settings", settings, active)["key_configured"]
    assert first.handle(panel, "settings", {**settings, "api_key": ""}, active)["key_configured"]
    submitted = first.handle(panel, "send", {"text": prompt}, active)
    assert submitted["accepted"] and submitted["busy"]
    assert finished.wait(2)
    assert observed["active"]
    assert observed["endpoint"] == settings["endpoint"]
    assert observed["key"] == settings["api_key"]
    assert observed["opener"] is agent_api.open_iecs_endpoint
    assert observed["payload"]["model"] == settings["model"]
    assert agent_api.REPLY_STYLE in observed["payload"]["messages"][0]["content"]
    assert {tool["function"]["name"] for tool in observed["payload"]["tools"]} == IECS_AGENT_TOOLS
    assert {"get_current_tasks", "set_viewport_view"} <= IECS_AGENT_TOOLS
    assert not {"control_iecs", "set_rtg_axis_positions"} & IECS_AGENT_TOOLS
    state = first.handle(panel, "state", {}, active)
    assert state["chat"] == [{"speaker": "你", "text": prompt}, {"speaker": "Agent", "text": "当前没有等待车辆。"}]
    assert not state["busy"]
    assert "test-key" not in str(state)
    other_state = second.handle(panel, "state", {}, active)
    assert other_state["chat"] == []
    assert not other_state["key_configured"]
    assert other_state["endpoint"] == agent_api.DEFAULT_ENDPOINT
    changed = first.handle(panel, "settings", {**settings, "endpoint": "https://other.example/v1", "api_key": ""}, active)
    assert changed["chat"] == []
    assert not changed["key_configured"]
    with pytest.raises(ValueError, match="会话已失效"):
        first.handle(panel, "state", {}, lambda: False)


@pytest.mark.parametrize("endpoint", [
    "http://127.0.0.1:11434/v1/chat/completions",
    "https://localhost./v1/chat/completions",
    "https://10.1.2.3/v1/chat/completions",
    "https://[::ffff:127.0.0.1]/v1/chat/completions",
])
def test_iecs_model_configuration_requires_public_tls(endpoint):
    with pytest.raises(ValueError, match="公网 HTTPS"):
        agent_api.validate_iecs_endpoint(endpoint)


def test_iecs_model_connects_only_to_the_checked_dns_result(monkeypatch):
    dns_address = "1.1.1.1"
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (dns_address, 443))
    ])
    connected = []
    sentinel = object()

    def connect(address, timeout, source_address):
        connected.append(address)
        return sentinel

    monkeypatch.setattr(socket, "create_connection", connect)
    assert agent_api._connect_to_public_address(("model.example", 443), 1, None) is sentinel
    assert connected == [("1.1.1.1", 443)]
    dns_address = "192.168.10.12"
    with pytest.raises(OSError, match="public addresses"):
        agent_api._connect_to_public_address(("model.example", 443), 1, None)
    assert len(connected) == 1
