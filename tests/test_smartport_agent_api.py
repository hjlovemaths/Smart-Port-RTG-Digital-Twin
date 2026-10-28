from __future__ import annotations

import sys
from pathlib import Path

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
