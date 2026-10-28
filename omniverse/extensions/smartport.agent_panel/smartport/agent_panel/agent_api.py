"""OpenAI-compatible Chat Completions helpers for the Smart Port Agent panel.

This module has no Omniverse dependencies so request construction and the local
tool allowlist can be validated outside Kit.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from . import runtime_catalog


DEFAULT_ENDPOINT = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-flash"
MAX_HISTORY_MESSAGES = 48


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any] | None = None,
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


_MODE = {"type": "string", "enum": ["day", "night", "rain", "fog"]}
_RAIN = {
    "type": "string",
    "enum": ["light", "medium", "heavy", "torrential"],
}
_FOG = {"type": "string", "enum": ["light", "medium", "heavy"]}
_ANCHOR = {"type": "string", "enum": ["start", "center", "end"]}
_NUMBER = {"type": "number"}

TOOLS: tuple[dict[str, Any], ...] = (
    _tool(
        "get_scene_status",
        "Read the active USD stage, current environment/time, and primary RTG positions.",
    ),
    _tool(
        "get_bay_inventory",
        "Read a yard bay's container inventory and box area map: six row heights, "
        "tier occupancy, container numbers and sizes. Resolves 40 ft stacks covering "
        "20 ft bays. Use this for questions like 'What is the box area map on 4C41 Bay?'. "
        "Returns source mode and snapshot time; does not change the scene.",
        {"bay_id": {"type": "string", "description": "Bay such as 4C41, 4C/041 or 3C/002"}},
        ("bay_id",),
    ),
    _tool(
        "get_yard_inventory_summary",
        "Read recorded container counts and occupied bay maps for the yard or one "
        "block. Distinguishes live and simulation sources; does not change the scene.",
        {"block": {"type": "string", "enum": ["1C", "2C", "3C", "4C"]}},
    ),
    _tool(
        "set_environment",
        "Set the Smart Port scene environment preset to day, night, rain, or fog.",
        {"mode": _MODE},
        ("mode",),
    ),
    _tool(
        "set_time_of_day",
        "Set the Smart Port 24-hour lighting clock. Hour must be between 0 and 24.",
        {"hour": {"type": "number", "minimum": 0, "maximum": 24}},
        ("hour",),
    ),
    _tool(
        "set_weather",
        "Set wind speed and/or rain and fog grades in the active Smart Port scene.",
        {
            "wind_speed_mps": {"type": "number", "minimum": 0, "maximum": 20},
            "rain_amount": _RAIN,
            "fog_amount": _FOG,
        },
    ),
    _tool(
        "move_rtg_to_bay",
        "Move the primary RTG in the local USD scene to a yard bay, such as 1C/017.",
        {"bay_id": {"type": "string"}, "anchor": _ANCHOR, "duration_s": {"type": "number", "minimum": 0.2, "maximum": 120}},
        ("bay_id",),
    ),
    _tool(
        "set_rtg_axis_positions",
        "Set one or more primary RTG axes in the local USD scene using soft limits.",
        {
            "gantry_m": _NUMBER,
            "trolley_m": _NUMBER,
            "hoist_m": _NUMBER,
            "duration_s": {"type": "number", "minimum": 0.2, "maximum": 120},
        },
    ),
) + runtime_catalog.TOOLS
_TOOL_NAMES = {item["function"]["name"] for item in TOOLS}


def validate_endpoint(endpoint: str) -> str:
    value = str(endpoint or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("API URL must be a complete http:// or https:// URL")
    host = (parsed.hostname or "").lower()
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and host not in local_hosts:
        raise ValueError("Remote API endpoints must use HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("Put credentials in the API key field, not in the URL")
    return value


def is_loopback_endpoint(endpoint: str) -> bool:
    parsed = urlparse(str(endpoint or "").strip())
    return (parsed.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}


def build_request_payload(
    model: str, messages: list[dict[str, Any]]
) -> dict[str, Any]:
    model_name = str(model or "").strip()
    if not model_name:
        raise ValueError("Enter an API model name")
    if not messages:
        raise ValueError("Conversation is empty")
    bounded_messages = messages
    if len(messages) > MAX_HISTORY_MESSAGES:
        system = messages[:1] if messages[0].get("role") == "system" else []
        remaining = messages[1:] if system else messages
        keep_count = MAX_HISTORY_MESSAGES - len(system)
        start = max(0, len(remaining) - keep_count)
        # Do not start in the middle of an assistant tool-call/result exchange.
        next_user = next(
            (index for index in range(start, len(remaining)) if remaining[index].get("role") == "user"),
            None,
        )
        if next_user is not None:
            start = next_user
        bounded_messages = system + remaining[start:]
    return {
        "model": model_name,
        "messages": bounded_messages,
        "tools": list(TOOLS),
        "tool_choice": "auto",
    }


def parse_tool_call(raw_call: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    function = raw_call.get("function")
    if not isinstance(function, Mapping):
        raise ValueError("Agent returned an invalid tool call")
    name = str(function.get("name") or "")
    if name not in _TOOL_NAMES:
        raise ValueError(f"Agent requested an unavailable tool: {name or '(empty)'}")
    raw_arguments = function.get("arguments", "{}")
    try:
        arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
    except json.JSONDecodeError as exc:
        raise ValueError(f"Agent returned invalid arguments for {name}") from exc
    if not isinstance(arguments, dict):
        raise ValueError(f"Arguments for {name} must be a JSON object")
    return name, validate_tool_arguments(name, arguments)


def normalize_bay_id(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "").upper())
    match = re.fullmatch(r"([1-4]C)[/-]?(\d{1,3})", text)
    if match is None or int(match.group(2)) == 0:
        raise ValueError("bay_id must look like 4C41, 4C/041, or 3C/002")
    return f"{match.group(1)}/{int(match.group(2)):03d}"


def _validate_runtime_arguments(name: str, args: dict[str, Any]) -> dict[str, Any]:
    schema = runtime_catalog.SCHEMAS[name]
    properties = schema["properties"]
    unknown = set(args) - properties.keys()
    if unknown:
        raise ValueError(f"Unexpected argument(s) for {name}: {', '.join(sorted(unknown))}")
    missing = set(schema["required"]) - args.keys()
    if missing:
        raise ValueError(f"Missing argument(s) for {name}: {', '.join(sorted(missing))}")
    result = {}
    for key, value in args.items():
        prop = properties[key]
        kind = prop["type"]
        if kind in {"number", "integer"}:
            if isinstance(value, bool) or not isinstance(value, (float, int)):
                raise ValueError(f"{key} must be a number")
            value = _finite_number(value, key)
            if kind == "integer":
                if not value.is_integer():
                    raise ValueError(f"{key} must be an integer")
                value = int(value)
            if value < prop.get("minimum", -math.inf) or value > prop.get("maximum", math.inf):
                raise ValueError(f"{key} is outside its allowed range")
        elif kind == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false")
        elif kind == "string":
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key} must be a non-empty string")
            value = value.strip()
            if len(value) > prop.get("maxLength", 512):
                raise ValueError(f"{key} is too long")
            if "pattern" in prop and re.fullmatch(prop["pattern"], value) is None:
                raise ValueError(f"Invalid {key} format")
            if "enum" in prop:
                value = next((item for item in prop["enum"] if item.casefold() == value.casefold()), value)
        if "enum" in prop and value not in prop["enum"]:
            raise ValueError(f"{key} must be one of: {', '.join(map(str, prop['enum']))}")
        if key == "bay_id":
            value = normalize_bay_id(value)
        if key == "crane_id":
            match = re.fullmatch(r"(?:STS[_ -]?)?0?([1-7])", value.upper())
            if not match:
                raise ValueError("crane_id must be STS_01 through STS_07")
            value = f"STS_{int(match.group(1)):02d}"
        if key.endswith("_path") and (not value.startswith("/") or ".." in value or ":" in value):
            raise ValueError(f"{key} must be an absolute USD prim path")
        result[key] = value
    action = result.get("action")
    required = set()
    if name == "set_viewport_view":
        if result["target"] == "trolley" and "view" not in result:
            result["view"] = "under_trolley"
        if result.get("view") == "under_trolley":
            if result["target"] not in {"rtg", "trolley", "spreader"}:
                raise ValueError("under_trolley is an RTG trolley-mounted camera preset")
            if set(result) & {"azimuth_deg", "elevation_deg", "distance_m"}:
                raise ValueError("under_trolley looks vertically down from a fixed mount; adjust field_of_view_deg instead")
            result.setdefault("follow", True)
        if result["target"] == "bay":
            required.add("bay_id")
        if result["target"] == "sts":
            required.add("crane_id")
    if name == "switch_viewport_camera" and len(result) != 1:
        raise ValueError("Specify exactly one camera_path or camera_number")
    if name == "set_sts_axis_positions":
        axes = set(result) & {"gantry_m", "trolley_m", "hoist_m", "boom_deg"}
        if not axes and not result.get("park_for_luffing"):
            raise ValueError("Specify at least one STS axis or park_for_luffing=true")
        if axes and result.get("park_for_luffing"):
            raise ValueError("Park and boom movement must be separate commands")
    if name == "control_iecs":
        if action == "submit": required.add("flow")
        if action == "speed": required.add("speed")
    if name == "control_sea_motion":
        required.update({"strength": {"strength"}, "quality": {"smooth"}, "hold": {"ship_path"}, "release": {"ship_path"}}.get(action, set()))
    if name == "show_alarm" and action == "show": required.add("message")
    if name == "control_gamepad" and action == "position_for_pick": required.update(("block", "bay", "row", "tier"))
    if name == "capture_lidar" and result["mode"] in {"batches", "positions"} and result.get("output_frame", "world") != "world":
        raise ValueError("Batched and multi-position captures use world coordinates")
    if name == "control_lidar" and result.get("mode", "yard") == "yard" and "variant" in result:
        raise ValueError("variant is supported by fleet modes; yard mode uses its config parameter")
    if name == "rtg_slot_operation" and action in {"target", "alignment", "move", "lower", "lift", "attach", "pick_demo", "calibrate"}:
        required.update(("bay_id", "row"))
    if name == "rtg_slot_operation" and action in {"scene_rows", "scene_target", "move_scene_row"}:
        required.add("bay_id")
        if action != "scene_rows": required.add("visual_row_from_right")
    if name == "submit_rtg_task":
        if result["work_type"] == "LOAD": required.add("end_pos")
        elif result["work_type"] == "UNLOAD": required.add("start_pos")
        else: required.update(("start_pos", "end_pos"))
        for key in ("start_pos", "end_pos"):
            if key in result and re.fullmatch(r"[1-4][cC][0-9/\-]{1,8}", result[key]) is None:
                raise ValueError(f"{key} must be a yard task position")
    missing = required - result.keys()
    if missing:
        raise ValueError(f"Missing argument(s) for {name}: {', '.join(sorted(missing))}")
    return result


def validate_tool_arguments(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Apply strict local validation before any tool reaches Kit/USD."""

    if name not in _TOOL_NAMES:
        raise ValueError(f"Agent requested an unavailable tool: {name or '(empty)'}")
    args = dict(arguments)
    if name in runtime_catalog.SCHEMAS:
        return _validate_runtime_arguments(name, args)
    schemas = {
        "get_scene_status": set(),
        "get_bay_inventory": {"bay_id"},
        "get_yard_inventory_summary": {"block"},
        "set_environment": {"mode"},
        "set_time_of_day": {"hour"},
        "set_weather": {"wind_speed_mps", "rain_amount", "fog_amount"},
        "move_rtg_to_bay": {"bay_id", "anchor", "duration_s"},
        "set_rtg_axis_positions": {"gantry_m", "trolley_m", "hoist_m", "duration_s"},
    }
    unknown = set(args) - schemas[name]
    if unknown:
        raise ValueError(f"Unexpected argument(s) for {name}: {', '.join(sorted(unknown))}")

    if name == "get_scene_status":
        return {}
    if name == "get_bay_inventory":
        return {"bay_id": normalize_bay_id(args.get("bay_id"))}
    if name == "get_yard_inventory_summary":
        if "block" not in args:
            return {}
        block = str(args["block"]).strip().upper()
        if block not in {"1C", "2C", "3C", "4C"}:
            raise ValueError("block must be 1C, 2C, 3C, or 4C")
        return {"block": block}
    if name == "set_environment":
        mode = str(args.get("mode", "")).strip().lower()
        if mode not in {"day", "night", "rain", "fog"}:
            raise ValueError("mode must be day, night, rain, or fog")
        return {"mode": mode}
    if name == "set_time_of_day":
        hour = _finite_number(args.get("hour"), "hour")
        if not 0 <= hour <= 24:
            raise ValueError("hour must be between 0 and 24")
        return {"hour": hour}
    if name == "set_weather":
        result: dict[str, Any] = {}
        if "wind_speed_mps" in args:
            wind = _finite_number(args["wind_speed_mps"], "wind_speed_mps")
            if not 0 <= wind <= 20:
                raise ValueError("wind_speed_mps must be between 0 and 20")
            result["wind_speed_mps"] = wind
        if "rain_amount" in args:
            rain = str(args["rain_amount"]).strip().lower()
            if rain not in {"light", "medium", "heavy", "torrential"}:
                raise ValueError("rain_amount must be light, medium, heavy, or torrential")
            result["rain_amount"] = rain
        if "fog_amount" in args:
            fog = str(args["fog_amount"]).strip().lower()
            if fog not in {"light", "medium", "heavy"}:
                raise ValueError("fog_amount must be light, medium, or heavy")
            result["fog_amount"] = fog
        if not result:
            raise ValueError("Set at least one weather value")
        return result
    if name == "move_rtg_to_bay":
        bay_id = normalize_bay_id(args.get("bay_id"))
        anchor = str(args.get("anchor", "center")).strip().lower()
        if anchor not in {"start", "center", "end"}:
            raise ValueError("anchor must be start, center, or end")
        result = {"bay_id": bay_id, "anchor": anchor}
        if "duration_s" in args:
            result["duration_s"] = _duration(args["duration_s"])
        return result
    if name == "set_rtg_axis_positions":
        if not (set(args) & {"gantry_m", "trolley_m", "hoist_m"}):
            raise ValueError("Provide at least one of gantry_m, trolley_m, or hoist_m")
        return {key: _duration(value) if key == "duration_s" else _finite_number(value, key) for key, value in args.items()}
    raise ValueError(f"No validator for tool: {name}")


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _duration(value: Any) -> float:
    number = _finite_number(value, "duration_s")
    if not 0.2 <= number <= 120:
        raise ValueError("duration_s must be between 0.2 and 120 seconds")
    return number
