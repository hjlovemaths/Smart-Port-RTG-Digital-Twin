"""Viewport chat overlay backed by an OpenAI-compatible tool-calling API."""

from __future__ import annotations

import asyncio
import builtins
import json
import os
import queue
import sys
import threading
import time
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
from .viewport_overlay import ViewportChatOverlay
from .web_bridge import AgentWebBridge


_EXTENSION_ID = "smartport.agent_panel"
_SETTING_ROOT = "/exts/smartport.agent_panel"
_WINDOW_NAME = "Smart Port Agent"
_SYSTEM_PROMPT = """You are the scene assistant for Smart Port RTG Digital Twin.
默认使用简体中文回答，包括用户用英文提问时；只有用户明确要求其他语言时才切换。回答简洁易懂。保留接口名称、参数名、设备编号、贝位编号和单位的原始写法。工具调用结果中的技术标识保持原样，说明和操作结果使用中文。
默认只直接回答用户所问的结果，通常一句话，必要时最多两句；用户明确要求详情或同时询问多项时再展开。不主动附带依据、来源、时间戳、坐标、任务编号、工作模式、库存模式等未被问到的字段。不在结尾追问或推荐其他查询。使用纯文本，不使用 Markdown 加粗标记、标题或项目列表。只有结果未知、数据过期、仅为模拟/上次记录、或操作失败时，才补充一句必要说明，不能为了简短把不确定数据说成实时事实。
例如用户问“rtg所在贝位是哪里”，只回答“RTG_220 当前所在贝位为 2C/090。”；用户问工作模式时只回答“当前为维修模式。”。设备编号和结果必须来自本次工具查询，不能照抄示例。
Use the provided tools to query the current USD scene, set day/night/rain/fog, set the time from 0 to 24 hours, adjust weather, and move the primary RTG in the current USD scene.
You also have viewport/camera controls, smooth RTG motion, independent STS crane axes with boom interlocks, current task queries, STS and RTG simulation tasks, IECS scheduling, live twin visualization, sea motion, camera/info panels, gamepad mode, slot picking, physics, LiDAR captures, and local streaming. Use get_agent_capabilities to discover the complete current catalog instead of claiming a capability is missing. For view requests use set_viewport_view (target, view, optional bay_id/crane_id/follow) or switch_viewport_camera. To look straight down at the spreader and container tops from below the trolley platform, use target=trolley, view=under_trolley. This wide-angle preset follows the trolley by default and must not be replaced with the external top view. To widen it, increase field_of_view_deg (default 110, maximum 140); read list_viewpoints for the active preset when needed. For current tasks call get_current_tasks. For shore-crane motion use set_sts_axis_positions and query get_equipment_status for its current limits. Ask which crane if unspecified. Treat motion/job status 'running' or 'moving' as started, never completed; poll get_operation_status or get_equipment_status for actual arrival. Respect active task ownership, soft limits and STS boom interlocks. Do not stop/reset a competing task unless the user has requested that. RTX sensing/physics depend on the installed runtime; report a missing dependency clearly.
For bay inventory, container distribution, row heights, or a box area map, call get_bay_inventory. For yard/block inventory totals or occupied bays, call get_yard_inventory_summary. A compact bay such as 4C41 means 4C/041. Report only the requested parts of the returned logical bay, any relevant 40 ft coverage relationship, six-digit bay_map in R1-to-R6 order, row heights, or counts. Use source mode and timestamp internally to assess reliability; include them only when requested or necessary to qualify simulation/stale/unknown data. Do not present simulation or cached stock as verified current real-world stock, or call unrecorded bays empty when coverage is unknown. Do not claim inventory is unavailable without trying the inventory tools.
Only query the scene when the user explicitly asks about its current state. Only call modifying tools when the user explicitly asks to change the scene or equipment positions. Ask for clarification when the target position is unclear; do not guess.
For RTG device work mode, query get_equipment_status(equipment="rtg") and read work_mode from the received TwinState workMode. 1=自动模式, 2=辅助模式, 3=远程手动, 4=本地模式, 5=维修模式, 0/invalid=未知. A running TwinState receiver does not imply automatic mode. Only report a current real mode when is_current is true; otherwise distinguish the last snapshot, stale/stopped receiver or simulation. Local gamepad/Agent/PLC control ownership is not the device workMode.
For requests to start/query/stop the ZMQ PLC simulator, use control_plc_zmq. start launches the network service and connects the Kit adapter, while connect attaches to an existing external service. Poll get_operation_status until completed and report adapter.scene_ready separately from service readiness. Never claim startup enabled control or moved the RTG. If the scene is missing RTG nodes, ask the user to open smart_port.usda; do not replace their open scene. This is different from control_live_twin, which receives TwinState updates.
For ZMQ plus physical simulation, collision, landing or twistlock picking, first start/connect control_plc_zmq and wait for scene_ready, then call control_plc_zmq(action="physics_on"). This explicitly arms local scene control and enables the cargo PhysX mode, without moving any axis. Check adapter.physics_enabled and armed afterwards. Do not substitute the legacy control_rtg_physics.activate_physics or press Play. physics_off disables cargo physics after landing and unlocking. If an existing service is connected, reuse it instead of trying to bind the ports again.
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
        self._approval_frame: Any = None
        self._pending_approval: dict[str, Any] | None = None
        self._approval_ui_dirty = False
        self._status_label: Any = None
        self._input_field: Any = None
        self._endpoint_field: Any = None
        self._model_field: Any = None
        self._key_field: Any = None
        self._endpoint_model = ui.SimpleStringModel(
            self._read_setting("endpoint", agent_api.DEFAULT_ENDPOINT)
        )
        self._model_model = ui.SimpleStringModel(
            self._read_setting("model", agent_api.DEFAULT_MODEL)
        )
        self._key_model = ui.SimpleStringModel(
            getattr(builtins, "_smart_port_agent_session_key", None)
            or os.environ.get("SMARTPORT_AGENT_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY", "")
        )
        self._input_model = ui.SimpleStringModel("")
        self._last_status = "Ready"
        self._overlay = ViewportChatOverlay(self)
        self._web_bridge = AgentWebBridge()

        self._update_subscription = (
            omni.kit.app.get_app()
            .get_update_event_stream()
            .create_subscription_to_pop(
                self._on_update, name="smartport.agent_panel.update"
            )
        )
        self._menu_items = [
            MenuItemDescription(name=_WINDOW_NAME, onclick_fn=self._toggle_window),
            MenuItemDescription(name="RTG Work Views", onclick_fn=self._show_work_views),
        ]
        omni.kit.menu.utils.add_menu_items(self._menu_items, "Window")
        asyncio.ensure_future(self._show_panel_after_startup())
        carb.log_info(f"[{_EXTENSION_ID}] Started for project {self._project_root}")

    def _show_work_views(self):
        try:
            import rtg_work_views
            rtg_work_views.start_rtg_work_views()
        except Exception as exc:
            carb.log_error(f"[{_EXTENSION_ID}] RTG Work Views: {exc}")

    def on_shutdown(self) -> None:
        self._shutting_down = True
        key_model = getattr(self, "_key_model", None)
        if key_model is not None:
            builtins._smart_port_agent_session_key = key_model.get_value_as_string()
        if getattr(self, "_web_bridge", None) is not None:
            self._web_bridge.closed = True
        self._cancel_pending_approval("panel_closed")
        if self._runtime is not None:
            self._runtime.close()
            self._runtime = None
        if self._menu_items:
            omni.kit.menu.utils.remove_menu_items(self._menu_items, "Window")
            self._menu_items = []
        self._clear_ui_references()
        self._overlay.destroy()
        if self._window is not None:
            self._window.destroy()
            self._window = None
        self._update_subscription = None

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
        self._overlay.update()

    def _toggle_window(self) -> None:
        self._overlay.set_visible(not self._overlay.visible)

    def _show_settings(self) -> None:
        self._create_window()
        self._window.visible = True

    def _create_window(self) -> None:
        if self._window is not None:
            return
        self._window = ui.Window(
            _WINDOW_NAME + " Settings",
            width=430,
            height=330,
            visible=False,
        )
        self._build_window_content()

    def _build_window_content(self) -> None:
        with self._window.frame:
            with ui.VStack(
                name="agent_settings_content", spacing=7,
                style={"VStack::agent_settings_content": {"margin": 10}},
            ):
                ui.Label("API Settings", height=20)
                ui.Label("Chat Completions URL", height=18)
                self._endpoint_field = ui.StringField(model=self._endpoint_model, height=26)
                ui.Label("Model", height=18)
                self._model_field = ui.StringField(model=self._model_model, height=26)
                ui.Label("API Key (current session only)", height=18)
                self._key_field = ui.StringField(
                    model=self._key_model, height=26, password_mode=True
                )

                with ui.HStack(height=28, spacing=6):
                    ui.Button("Save URL / Model", clicked_fn=self._save_settings)
                    ui.Button("Close", clicked_fn=lambda: setattr(self._window, "visible", False))

                ui.Separator(height=4)
                ui.Label(
                    "RTG controls affect the current USD scene only. No PLC commands are sent.",
                    height=32,
                    word_wrap=True,
                )

    def _clear_ui_references(self) -> None:
        self._cancel_pending_approval("panel_closed")
        self._approval_frame = None
        self._approval_ui_dirty = False
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
        endpoint = self._endpoint_model.get_value_as_string().strip()
        model = self._model_model.get_value_as_string().strip()
        try:
            agent_api.validate_endpoint(endpoint)
            if not model:
                raise ValueError("Enter a model name")
            self._settings.set(f"{_SETTING_ROOT}/endpoint", endpoint)
            self._settings.set(f"{_SETTING_ROOT}/model", model)
            # Keep the credential only in this Kit process across hot reloads.
            builtins._smart_port_agent_session_key = self._key_model.get_value_as_string()
            self._set_status("URL / Model saved (API key not saved)")
        except Exception as exc:
            self._set_status(f"Configuration error: {exc}")

    def _clear_conversation(self) -> None:
        if self._busy:
            self._set_status("A request is still in progress")
            return
        self._cancel_pending_approval("chat_cleared")
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
        endpoint = self._endpoint_model.get_value_as_string().strip()
        model = self._model_model.get_value_as_string().strip()
        api_key = self._key_model.get_value_as_string().strip()
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
                    if self._shutting_down:
                        return
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
            "lock": threading.Lock(),
            "expires_at": time.monotonic() + _APPROVAL_TIMEOUT_SECONDS,
        }
        self._ui_events.put(approval)
        if not approval["decision"].wait(_APPROVAL_TIMEOUT_SECONDS):
            self._decide_approval(approval, False, "timeout")
        # Only the Kit main thread may remove widgets. Also notify it when the
        # worker times out, including requests that have not been displayed yet.
        self._ui_events.put({"kind": "approval_finished", "approval": approval})
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
        if getattr(self, "_web_bridge", None) is not None:
            self._web_bridge.tick(self)
        self._overlay.update()
        if getattr(self, "_chat_scroll_pending", False):
            self._chat_scroll_pending = False
            if self._chat_scroll is not None:
                self._chat_scroll.scroll_y = self._chat_scroll.scroll_y_max
        approval = self._pending_approval
        if approval is not None:
            hidden = not self._overlay.visible
            if approval["decision"].is_set() or time.monotonic() >= approval["expires_at"] or hidden:
                self._resolve_approval(approval, False, "panel_closed" if hidden else "timeout")
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
        # UI click handlers may hide controls, but must never destroy their
        # container while omni.ui is dispatching that click. Rebuild only here,
        # after the UI event has returned, once for the final pending request.
        self._rebuild_approval_controls()

    def _execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        stage = omni.usd.get_context().get_stage()
        if stage is None and name not in {"get_agent_capabilities", "control_plc_zmq", "get_operation_status", "cancel_operation"}:
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
        elif kind in ("tool_start", "tool_result"):
            # Keep tool details in the model history, out of the conversation UI.
            return
        elif kind == "approval":
            self._append_approval_buttons(event)
        elif kind == "approval_finished":
            approval = event["approval"]
            if self._pending_approval is approval:
                self._resolve_approval(approval, False, "timeout")
        elif kind == "complete":
            self._cancel_pending_approval("request_finished")
            self._history = list(event.get("history", self._history))
            self._append_chat("Agent", str(event.get("answer", "")))
            self._set_status("Ready")
            self._busy = False
        elif kind == "failed":
            self._cancel_pending_approval("request_finished")
            self._append_chat("Agent Error", str(event.get("error", "Request failed")))
            self._set_status("Request failed")
            self._busy = False

    def _append_approval_buttons(self, approval: dict[str, Any]) -> None:
        if approval["decision"].is_set():
            return
        if time.monotonic() >= approval["expires_at"]:
            self._decide_approval(approval, False, "timeout")
            return
        if self._approval_frame is None or self._shutting_down:
            self._decide_approval(approval, False, "panel_closed")
            return
        if self._pending_approval is approval:
            return
        self._cancel_pending_approval("replaced")
        self._pending_approval = approval
        self._append_chat(
            "Pending Approval",
            f"{approval.get('name')}: "
            + json.dumps(approval.get("arguments", {}), ensure_ascii=False)
            + "\nThis changes only the local digital twin or its demonstration tasks. No PLC commands are sent.",
        )
        self._approval_ui_dirty = True
        self._set_status("Waiting for scene operation approval")

    @staticmethod
    def _decide_approval(approval: dict[str, Any], approved: bool, reason: str) -> None:
        with approval["lock"]:
            if approval["decision"].is_set():
                return
            if time.monotonic() >= approval["expires_at"]:
                approved, reason = False, "timeout"
            approval["approved"] = approved
            approval["reason"] = reason
            approval["decision"].set()

    def _resolve_approval(self, approval: dict[str, Any], approved: bool, reason: str | None = None) -> None:
        self._decide_approval(approval, approved, reason or ("confirmed" if approved else "cancelled"))
        # A late callback from an older request must never clear a newer one.
        if self._pending_approval is not approval:
            return
        self._pending_approval = None
        self._clear_approval_controls()
        resolution = approval.get("reason")
        text = ("已确认，正在执行。" if approval["approved"] else
                "确认请求已超时，未执行。" if resolution == "timeout" else "已取消，未执行。")
        self._append_chat("Approval", text)
        self._set_status("Scene operation approved" if approval["approved"] else "Scene operation cancelled")

    def _clear_approval_controls(self) -> None:
        if self._approval_frame is not None:
            self._approval_frame.visible = False
            self._approval_frame.enabled = False
            self._approval_frame.height = ui.Pixel(0)
            self._approval_ui_dirty = True

    def _rebuild_approval_controls(self) -> None:
        if not self._approval_ui_dirty:
            return
        self._approval_ui_dirty = False
        frame = self._approval_frame
        if frame is None:
            return
        frame.clear()
        approval = self._pending_approval
        if approval is None or approval["decision"].is_set():
            return
        frame.enabled = True
        frame.visible = True
        frame.height = ui.Pixel(58)
        with frame:
            with ui.VStack(spacing=4):
                ui.Label("Confirm scene operation", height=20)
                with ui.HStack(height=30, spacing=6):
                    ui.Button("Confirm", clicked_fn=lambda: self._resolve_approval(approval, True))
                    ui.Button("Cancel", clicked_fn=lambda: self._resolve_approval(approval, False))

    def _cancel_pending_approval(self, reason: str) -> None:
        if self._pending_approval is not None:
            self._resolve_approval(self._pending_approval, False, reason)
        else:
            self._clear_approval_controls()

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
        # New wrapped text gets its final height on the next layout pass.
        self._chat_scroll_pending = True
        try:
            if self._chat_scroll is not None:
                self._chat_scroll.scroll_y = self._chat_scroll.scroll_y_max
        except (AttributeError, RuntimeError):
            pass

    def _set_status(self, text: str) -> None:
        self._last_status = text
        if self._status_label is not None:
            self._status_label.text = text
            self._status_label.tooltip = text

