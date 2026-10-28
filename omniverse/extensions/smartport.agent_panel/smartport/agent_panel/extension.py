"""Dockable Kit chat panel backed by an OpenAI-compatible tool-calling API."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import carb
import omni.ext
import omni.kit.app
import omni.kit.menu.utils
import omni.ui as ui
import omni.usd
from omni.kit.menu.utils import MenuItemDescription

from . import agent_api, runtime_catalog


_EXTENSION_ID = "smartport.agent_panel"
_SETTING_ROOT = "/exts/smartport.agent_panel"
_WINDOW_NAME = "Smart Port Agent"
_SYSTEM_PROMPT = """You are the scene assistant for Smart Port RTG Digital Twin.
默认使用简体中文回答，包括用户用英文提问时；只有用户明确要求其他语言时才切换。回答简洁易懂。保留接口名称、参数名、设备编号、贝位编号和单位的原始写法。工具调用结果中的技术标识保持原样，说明和操作结果使用中文。
Use the provided tools to query the current USD scene, set day/night/rain/fog, set the time from 0 to 24 hours, adjust weather, and move the primary RTG in the current USD scene.
You also have viewport/camera controls, smooth RTG motion, independent STS crane axes with boom interlocks, current task queries, STS and RTG simulation tasks, IECS scheduling, live twin visualization, sea motion, camera/info panels, gamepad mode, slot picking, physics, LiDAR captures, and local streaming. Use get_agent_capabilities to discover the complete current catalog instead of claiming a capability is missing. For view requests use set_viewport_view (target, view, optional bay_id/crane_id/follow) or switch_viewport_camera. To look straight down at the spreader and container tops from below the trolley platform, use target=trolley, view=under_trolley. This wide-angle preset follows the trolley by default and must not be replaced with the external top view. To widen it, increase field_of_view_deg (default 110, maximum 140); read list_viewpoints for the active preset when needed. For current tasks call get_current_tasks. For shore-crane motion use set_sts_axis_positions and query get_equipment_status for its current limits. Ask which crane if unspecified. Treat motion/job status 'running' or 'moving' as started, never completed; poll get_operation_status or get_equipment_status for actual arrival. Respect active task ownership, soft limits and STS boom interlocks. Do not stop/reset a competing task unless the user has requested that. RTX sensing/physics depend on the installed runtime; report a missing dependency clearly.
For bay inventory, container distribution, row heights, or a box area map, call get_bay_inventory. For yard/block inventory totals or occupied bays, call get_yard_inventory_summary. A compact bay such as 4C41 means 4C/041. Report the returned logical bay and any 40 ft coverage relationship, the six-digit bay_map in R1-to-R6 order, row heights, and container counts. Use the returned source mode and timestamp; do not present a simulation or cached snapshot as verified current real-world stock, and do not call unrecorded bays empty when coverage is unknown. Do not claim inventory is unavailable without trying the inventory tools.
Only query the scene when the user explicitly asks about its current state. Only call modifying tools when the user explicitly asks to change the scene or equipment positions. Ask for clarification when the target position is unclear; do not guess.
All RTG position operations only edit the visualization in the current USD session layer. They never send commands to PLC, RCMS, or real equipment. Never claim that real equipment has moved.
Only use capabilities in the tool allowlist. Do not execute or generate arbitrary Python, shell commands, file operations, network requests, or PLC controls."""
_APPROVAL_TIMEOUT_SECONDS = 120.0
_TOOL_TIMEOUT_SECONDS = 30.0
_API_TIMEOUT_SECONDS = 90.0
_MOVEMENT_TOOLS = {"move_rtg_to_bay", "set_rtg_axis_positions"}
_WELCOME_MESSAGE = "你好，我可以切换视角、移动 RTG 和岸桥、查看当前任务与箱区库存，也可以操作项目中的仿真功能。"


def _conversation_text_style() -> dict[str, Any]:
    """Use Kit's startup CJK atlas; a per-widget font loses its custom glyphs."""
    return {"font_size": 16}


class SmartPortAgentPanelExtension(omni.ext.IExt):
    def on_startup(self, _ext_id: str) -> None:
        self._settings = carb.settings.get_settings()
        self._project_root = self._find_project_root()
        self._scripts_dir = self._project_root / "omniverse" / "scripts"
        if str(self._scripts_dir) not in sys.path:
            sys.path.insert(0, str(self._scripts_dir))

        self._ui_events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._kit_commands: queue.Queue[dict[str, Any]] = queue.Queue()
        self._history: list[dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_PROMPT}
        ]
        self._chat_lines: list[tuple[str, str]] = [
            ("Agent", _WELCOME_MESSAGE)
        ]
        self._busy = False
        self._shutting_down = False
        self._runtime = None
        self._window: ui.Window | None = None
        self._menu_items: list[MenuItemDescription] = []
        self._chat_scroll: Any = None
        self._chat_content: Any = None
        self._chat_log: Any = None
        self._status_label: Any = None
        self._input_field: Any = None
        self._endpoint_field: Any = None
        self._model_field: Any = None
        self._key_field: Any = None

        self._update_subscription = (
            omni.kit.app.get_app()
            .get_update_event_stream()
            .create_subscription_to_pop(
                self._on_update, name="smartport.agent_panel.update"
            )
        )
        self._menu_items = [
            MenuItemDescription(name=_WINDOW_NAME, onclick_fn=self._toggle_window)
        ]
        omni.kit.menu.utils.add_menu_items(self._menu_items, "Window")
        asyncio.ensure_future(self._show_panel_after_startup())
        carb.log_info(f"[{_EXTENSION_ID}] Started for project {self._project_root}")

    def on_shutdown(self) -> None:
        self._shutting_down = True
        if self._runtime is not None:
            self._runtime.close()
            self._runtime = None
        if self._menu_items:
            omni.kit.menu.utils.remove_menu_items(self._menu_items, "Window")
            self._menu_items = []
        if self._window is not None:
            self._window.destroy()
            self._window = None
        self._update_subscription = None
        self._clear_ui_references()

    @staticmethod
    def _find_project_root() -> Path:
        here = Path(__file__).resolve()
        for parent in here.parents:
            if (parent / "omniverse" / "scripts" / "smart_port_environment.py").is_file():
                return parent
            if (parent / "scripts" / "smart_port_environment.py").is_file():
                return parent.parent
        raise RuntimeError(
            "Cannot find the project root containing omniverse/scripts/smart_port_environment.py"
        )

    async def _show_panel_after_startup(self) -> None:
        for _ in range(8):
            await omni.kit.app.get_app().next_update_async()
        if self._shutting_down:
            return
        self._create_window()
        if self._window is not None:
            self._window.visible = True
            self._window.deferred_dock_in("Stage")

    def _toggle_window(self) -> None:
        self._create_window()
        if self._window is not None:
            self._window.visible = not self._window.visible

    def _create_window(self) -> None:
        if self._window is not None:
            return
        self._window = ui.Window(
            _WINDOW_NAME,
            width=430,
            height=760,
            dockPreference=ui.DockPreference.RIGHT_TOP,
            visible=True,
        )
        with self._window.frame:
            with ui.VStack(spacing=7, style={"margin": 10}):
                ui.Label("SMART PORT / AGENT", height=26)
                ui.Label("API Settings", height=20)
                ui.Label("Chat Completions URL", height=18)
                endpoint_model = ui.SimpleStringModel(
                    self._read_setting("endpoint", agent_api.DEFAULT_ENDPOINT)
                )
                self._endpoint_field = ui.StringField(model=endpoint_model, height=26)
                ui.Label("Model", height=18)
                model_model = ui.SimpleStringModel(
                    self._read_setting("model", agent_api.DEFAULT_MODEL)
                )
                self._model_field = ui.StringField(model=model_model, height=26)
                ui.Label("API Key (current session only)", height=18)
                key_model = ui.SimpleStringModel(
                    os.environ.get("SMARTPORT_AGENT_API_KEY")
                    or os.environ.get("DEEPSEEK_API_KEY", "")
                )
                self._key_field = ui.StringField(
                    model=key_model, height=26, password_mode=True
                )

                with ui.HStack(height=28, spacing=6):
                    ui.Button("Save URL / Model", clicked_fn=self._save_settings)
                    ui.Button("Clear Chat", clicked_fn=self._clear_conversation)
                self._status_label = ui.Label("Not connected", height=36, word_wrap=True)

                ui.Separator(height=5)
                ui.Label("Chat & Scene Controls", height=22)
                with ui.ScrollingFrame(height=ui.Fraction(1)) as self._chat_scroll:
                    with ui.VStack(spacing=4) as self._chat_content:
                        self._chat_log = ui.Label(
                            self._format_chat(),
                            word_wrap=True,
                            alignment=ui.Alignment.LEFT_TOP,
                            style=_conversation_text_style(),
                        )
                self._input_field = ui.StringField(
                    height=74, multiline=True, style=_conversation_text_style()
                )
                with ui.HStack(height=32, spacing=6):
                    ui.Button("Send", width=100, clicked_fn=self._send_message)
                    ui.Label("Equipment movement requires confirmation", word_wrap=True)

                ui.Separator(height=4)
                ui.Label(
                    "RTG controls affect the current USD scene only. No PLC commands are sent.",
                    height=32,
                    word_wrap=True,
                )

    def _clear_ui_references(self) -> None:
        self._chat_scroll = None
        self._chat_content = None
        self._chat_log = None
        self._status_label = None
        self._input_field = None
        self._endpoint_field = None
        self._model_field = None
        self._key_field = None

    def _read_setting(self, key: str, fallback: str) -> str:
        try:
            return self._settings.get_as_string(f"{_SETTING_ROOT}/{key}") or fallback
        except Exception:
            return fallback

    def _save_settings(self) -> None:
        endpoint = self._endpoint_field.model.get_value_as_string().strip()
        model = self._model_field.model.get_value_as_string().strip()
        try:
            agent_api.validate_endpoint(endpoint)
            if not model:
                raise ValueError("Enter a model name")
            self._settings.set(f"{_SETTING_ROOT}/endpoint", endpoint)
            self._settings.set(f"{_SETTING_ROOT}/model", model)
            self._set_status("URL / Model saved (API key not saved)")
        except Exception as exc:
            self._set_status(f"Configuration error: {exc}")

    def _clear_conversation(self) -> None:
        if self._busy:
            self._set_status("A request is still in progress")
            return
        self._history = [{"role": "system", "content": _SYSTEM_PROMPT}]
        self._chat_lines = [
            ("Agent", "对话已清空。你可以询问视角、设备、任务、库存、环境或传感器。")
        ]
        self._render_chat()
        self._set_status("Chat cleared")

    def _send_message(self) -> None:
        if self._busy:
            self._set_status("Please wait for the current response")
            return
        if self._input_field is None:
            return
        prompt = self._input_field.model.get_value_as_string().strip()
        if not prompt:
            return
        endpoint = self._endpoint_field.model.get_value_as_string().strip()
        model = self._model_field.model.get_value_as_string().strip()
        api_key = self._key_field.model.get_value_as_string().strip()
        if not api_key and not agent_api.is_loopback_endpoint(endpoint):
            self._set_status("Enter an API key or set DEEPSEEK_API_KEY before starting Kit")
            return
        try:
            agent_api.validate_endpoint(endpoint)
            agent_api.build_request_payload(
                model,
                self._history + [{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            self._set_status(f"Configuration error: {exc}")
            return

        self._settings.set(f"{_SETTING_ROOT}/endpoint", endpoint)
        self._settings.set(f"{_SETTING_ROOT}/model", model)
        self._history.append({"role": "user", "content": prompt})
        self._chat_lines.append(("You", prompt))
        self._render_chat()
        self._input_field.model.set_value("")
        self._busy = True
        self._set_status("Connecting to Agent API...")
        history = list(self._history)
        worker = threading.Thread(
            target=self._run_agent_turn,
            args=(endpoint, model, api_key, history),
            name="SmartPortAgentRequest",
            daemon=True,
        )
        worker.start()

    def _run_agent_turn(
        self,
        endpoint: str,
        model: str,
        api_key: str,
        messages: list[dict[str, Any]],
    ) -> None:
        try:
            for _round in range(6):
                if self._shutting_down:
                    return
                self._ui_events.put({"kind": "status", "text": "Waiting for Agent API..."})
                response = self._request_completion(endpoint, model, api_key, messages)
                choices = response.get("choices")
                if not isinstance(choices, list) or not choices:
                    raise RuntimeError("API response has no choices; check that the endpoint supports Chat Completions")
                assistant_message = choices[0].get("message")
                if not isinstance(assistant_message, dict):
                    raise RuntimeError("API response has no assistant message")
                messages.append(assistant_message)
                tool_calls = assistant_message.get("tool_calls") or []
                if not tool_calls:
                    answer = self._message_text(assistant_message.get("content"))
                    if not answer:
                        answer = str(assistant_message.get("refusal") or "The agent returned no text.")
                    self._ui_events.put(
                        {"kind": "complete", "answer": answer, "history": messages}
                    )
                    return

                if not isinstance(tool_calls, list):
                    raise RuntimeError("API returned invalid tool_calls")
                for raw_call in tool_calls:
                    if not isinstance(raw_call, dict):
                        continue
                    call_id = str(raw_call.get("id") or "")
                    if not call_id:
                        raise RuntimeError("API tool call is missing an id")
                    try:
                        name, arguments = agent_api.parse_tool_call(raw_call)
                        self._ui_events.put(
                            {"kind": "tool_start", "name": name, "arguments": arguments}
                        )
                        if runtime_catalog.needs_approval(name, arguments):
                            approved = self._request_operator_confirmation(name, arguments)
                            if not approved:
                                result: dict[str, Any] = {
                                    "ok": False,
                                    "cancelled": True,
                                    "message": "The operator cancelled this scene movement.",
                                }
                            else:
                                result = self._dispatch_kit_tool(name, arguments)
                        else:
                            result = self._dispatch_kit_tool(name, arguments)
                    except Exception as exc:
                        result = {"ok": False, "error": str(exc)}
                    raw_function = raw_call.get("function")
                    display_name = (
                        raw_function.get("name", "tool")
                        if isinstance(raw_function, dict)
                        else "tool"
                    )
                    self._ui_events.put(
                        {"kind": "tool_result", "name": display_name, "result": result}
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": json.dumps(result, ensure_ascii=False, default=str),
                        }
                    )
            raise RuntimeError("Agent tool-call round limit reached; this request has been stopped")
        except Exception as exc:
            self._ui_events.put({"kind": "failed", "error": str(exc)})

    @staticmethod
    def _message_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") in {"text", "output_text"}
            ]
            return "\n".join(part for part in parts if part).strip()
        return ""

    @staticmethod
    def _request_completion(
        endpoint: str,
        model: str,
        api_key: str,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = agent_api.build_request_payload(model, messages)
        request = urllib.request.Request(
            agent_api.validate_endpoint(endpoint),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        if api_key:
            request.add_header("Authorization", f"Bearer {api_key}")
        try:
            with urllib.request.urlopen(request, timeout=_API_TIMEOUT_SECONDS) as response:
                body = response.read(4 * 1024 * 1024 + 1)
            if len(body) > 4 * 1024 * 1024:
                raise RuntimeError("Agent API response exceeds the 4 MiB limit")
            result = json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", errors="replace")
            raise RuntimeError(f"Agent API HTTP {exc.code}: {detail[:1200]}") from None
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Unable to connect to Agent API: {exc.reason}") from None
        except TimeoutError:
            raise RuntimeError("Agent API request timed out") from None
        except json.JSONDecodeError as exc:
            raise RuntimeError("Agent API returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise RuntimeError("Agent API returned an invalid response format")
        return result

    def _request_operator_confirmation(
        self, name: str, arguments: dict[str, Any]
    ) -> bool:
        approval = {
            "kind": "approval",
            "name": name,
            "arguments": arguments,
            "decision": threading.Event(),
            "approved": False,
        }
        self._ui_events.put(approval)
        if not approval["decision"].wait(_APPROVAL_TIMEOUT_SECONDS):
            return False
        return bool(approval["approved"])

    def _dispatch_kit_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        command = {
            "name": name,
            "arguments": arguments,
            "completed": threading.Event(),
            "cancelled": False,
            "result": None,
            "error": None,
        }
        self._kit_commands.put(command)
        if not command["completed"].wait(_TOOL_TIMEOUT_SECONDS):
            command["cancelled"] = True
            raise TimeoutError("Kit timed out while processing the scene operation")
        if command["error"] is not None:
            raise RuntimeError(str(command["error"]))
        return {"ok": True, "result": command["result"]}

    def _on_update(self, _event: Any) -> None:
        if getattr(self, "_runtime", None) is not None:
            try:
                dt = _event.payload.get("dt", None) if _event is not None else None
                self._runtime.tick(omni.usd.get_context().get_stage(), dt)
            except Exception as exc:
                carb.log_error(f"[{_EXTENSION_ID}] Scene control stopped: {exc}")
                self._runtime.close()
                self._runtime = None
                self._ui_events.put({"kind": "status", "text": f"Scene control stopped: {exc}"})
        for _ in range(16):
            try:
                command = self._kit_commands.get_nowait()
            except queue.Empty:
                break
            try:
                if command["cancelled"] or self._shutting_down:
                    continue
                command["result"] = self._execute_tool(
                    command["name"], command["arguments"]
                )
            except Exception as exc:
                command["error"] = exc
            finally:
                command["completed"].set()

        for _ in range(32):
            try:
                event = self._ui_events.get_nowait()
            except queue.Empty:
                break
            self._handle_ui_event(event)

    def _execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        stage = omni.usd.get_context().get_stage()
        if stage is None and name != "get_agent_capabilities":
            raise RuntimeError("No USD stage is open")

        if name in runtime_catalog.SCHEMAS or name in _MOVEMENT_TOOLS:
            if getattr(self, "_runtime", None) is None:
                from .scene_runtime import SceneRuntime

                self._runtime = SceneRuntime(self._project_root, self._ui_events.put)
            return self._runtime.execute(stage, name, arguments)

        if name == "get_bay_inventory":
            from .yard_queries import query_bay

            return query_bay(stage, self._project_root, arguments["bay_id"])

        if name == "get_yard_inventory_summary":
            from .yard_queries import query_yard

            return query_yard(stage, self._project_root, arguments.get("block"))

        if name == "get_scene_status":
            from pxr import UsdPhysics
            import smart_port_environment as environment
            import smart_port_time_of_day as time_of_day
            import rtg_live_control as live_control

            root = stage.GetPrimAtPath(environment.ENVIRONMENT_ROOT_PATH)
            hour_attr = root.GetAttribute(time_of_day.TIME_ATTRIBUTE) if root else None
            hour = hour_attr.Get() if hour_attr else None
            positions: dict[str, float] | None = None
            try:
                values: list[float] = []
                for joint_path, converter in (
                    (live_control.GANTRY_JOINT_PATH, live_control.gantry_usd_to_actual),
                    (live_control.TROLLEY_JOINT_PATH, live_control.trolley_usd_to_actual),
                    (live_control.HOIST_JOINT_PATH, live_control.hoist_usd_to_actual),
                ):
                    joint = UsdPhysics.PrismaticJoint.Get(stage, joint_path)
                    if not joint:
                        raise RuntimeError(f"Missing joint: {joint_path}")
                    attr = UsdPhysics.DriveAPI.Get(
                        joint.GetPrim(), UsdPhysics.Tokens.linear
                    ).GetTargetPositionAttr()
                    values.append(float(converter(float(attr.Get() or 0.0))))
                positions = dict(zip(("gantry_m", "trolley_m", "hoist_m"), values))
            except Exception:
                positions = None
            return {
                "stage_url": omni.usd.get_context().get_stage_url() or "",
                "environment": environment.current_environment_mode(stage),
                "time_of_day_hours": float(hour) if hour is not None else None,
                "rtg_positions_m": positions,
                "rtg_control_is_visual_usd_only": True,
            }

        if name == "set_environment":
            import smart_port_environment as environment

            mode = environment.apply_environment_mode(stage, arguments["mode"])
            return {"mode": mode, "label": environment.environment_mode_label(mode)}

        if name == "set_time_of_day":
            import smart_port_time_of_day as time_of_day

            return time_of_day.apply_time(stage, arguments["hour"])

        if name == "set_weather":
            import smart_port_environment as environment

            return environment.apply_weather_controls(
                stage,
                wind_speed_mps=arguments.get("wind_speed_mps"),
                rain_amount=arguments.get("rain_amount"),
                fog_amount=arguments.get("fog_amount"),
            )

        raise ValueError(f"Unsupported Agent tool: {name}")

    def _handle_ui_event(self, event: dict[str, Any]) -> None:
        kind = event.get("kind")
        if kind == "status":
            self._set_status(str(event.get("text", "")))
        elif kind == "tool_start":
            self._append_chat(
                "Tool",
                f"Calling {event.get('name')}: "
                + json.dumps(event.get("arguments", {}), ensure_ascii=False),
            )
        elif kind == "tool_result":
            self._append_chat(
                "Tool Result",
                json.dumps(event.get("result", {}), ensure_ascii=False, default=str),
            )
        elif kind == "approval":
            self._append_chat(
                "Pending Approval",
                f"{event.get('name')}: "
                + json.dumps(event.get("arguments", {}), ensure_ascii=False)
                + "\nThis changes only the local digital twin or its demonstration tasks. No PLC commands are sent.",
            )
            self._append_approval_buttons(event)
            self._set_status("Waiting for scene operation approval")
        elif kind == "complete":
            self._history = list(event.get("history", self._history))
            self._append_chat("Agent", str(event.get("answer", "")))
            self._set_status("Ready")
            self._busy = False
        elif kind == "failed":
            self._append_chat("Agent Error", str(event.get("error", "Request failed")))
            self._set_status("Request failed")
            self._busy = False

    def _append_approval_buttons(self, approval: dict[str, Any]) -> None:
        if self._chat_content is None:
            approval["approved"] = False
            approval["decision"].set()
            return
        with self._chat_content:
            with ui.HStack(height=30, spacing=6):
                ui.Button(
                    "Confirm",
                    clicked_fn=lambda: self._resolve_approval(approval, True),
                )
                ui.Button(
                    "Cancel",
                    clicked_fn=lambda: self._resolve_approval(approval, False),
                )

    @staticmethod
    def _resolve_approval(approval: dict[str, Any], approved: bool) -> None:
        if not approval["decision"].is_set():
            approval["approved"] = approved
            approval["decision"].set()

    def _append_chat(self, speaker: str, text: str) -> None:
        self._chat_lines.append((speaker, text))
        if len(self._chat_lines) > 80:
            self._chat_lines = self._chat_lines[-80:]
        self._render_chat()

    def _format_chat(self) -> str:
        return "\n\n".join(f"{speaker}: {text}" for speaker, text in self._chat_lines)

    def _render_chat(self) -> None:
        if self._chat_log is not None:
            self._chat_log.text = self._format_chat()
        try:
            if self._chat_scroll is not None:
                self._chat_scroll.scroll_y = self._chat_scroll.scroll_y_max
        except (AttributeError, RuntimeError):
            pass

    def _set_status(self, text: str) -> None:
        if self._status_label is not None:
            self._status_label.text = text

