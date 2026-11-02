"""Same-origin browser controls; all Kit model access stays on its main thread."""
import asyncio
import queue
import sys
import threading
import time


class AgentWebBridge:
    def __init__(self):
        from .viewport_navigation import ViewportNavigation
        self.navigation = ViewportNavigation()
        self.commands = queue.Queue()
        self.installed = False
        self.next_attempt = 0
        self.closed = False
        self._rtg_visibility_stage = None

    def install(self):
        if self.installed or time.monotonic() < self.next_attempt:
            return
        self.next_attempt = time.monotonic() + 1
        if "omni.services.core.main" not in sys.modules:
            return
        from omni.services.core import main
        from fastapi import HTTPException
        import carb.settings

        # Camera model representations are editor helpers, not port equipment.
        carb.settings.get_settings().set("/app/viewport/createCameraModelRep", False)
        # Hide the editor's performance HUD, independently of the Agent cards.
        hud_settings = carb.settings.get_settings()
        hud_settings.set("/app/viewport/defaults/hud/visible", False)
        from omni.kit.viewport.window import get_viewport_window_instances
        hud_keys = []
        for window in get_viewport_window_instances():
            viewport = window.viewport_api
            hud_key = f"/persistent/app/viewport/{viewport.id}/hud/visible"
            hud_keys.append(hud_key)
            hud_settings.set(hud_key, False)
            for item in ("deviceMemory", "renderResolution"):
                hud_settings.set(f"/persistent/app/viewport/{viewport.id}/hud/{item}/visible", False)

        app = main.get_app()
        from pathlib import Path
        scripts = str(Path(__file__).resolve().parents[5] / "omniverse/scripts")
        if scripts not in sys.path: sys.path.insert(0,scripts)
        from webrtc_auth import install_auth
        install_auth(app)
        prefix = "/api/smartport-agent"
        app.router.routes[:] = [r for r in app.router.routes
                                if not getattr(r, "path", "").startswith(prefix)]

        async def execute(action, payload):
            if self.closed:
                raise HTTPException(503, "Agent is restarting")
            command = {"action": action, "payload": payload, "done": threading.Event(), "cancelled": False}
            self.commands.put(command)
            if not await asyncio.to_thread(command["done"].wait, 5):
                command["cancelled"] = True
                raise HTTPException(503, "Kit is busy; please retry")
            if "error" in command:
                raise HTTPException(400, command["error"])
            return command["result"]

        async def state(native_input: bool = False):
            result = await execute("state", {"native_input": native_input})
            result["viewport_hud_visible"] = any(bool(hud_settings.get(key)) for key in hud_keys)
            return result

        async def work_mode():
            return await execute("work_mode", {})

        async def draft(payload: dict):
            return await execute("draft", payload)

        async def send(payload: dict):
            return await execute("send", payload)

        async def settings(payload: dict):
            return await execute("settings", payload)

        async def zoom(payload: dict):
            return await execute("zoom", payload)

        async def navigate(payload: dict):
            return await execute("navigate", payload)

        async def clear(payload: dict):
            return await execute("clear", payload)

        async def approval(payload: dict):
            return await execute("approval", payload)

        async def view(payload: dict):
            return await execute("view", payload)

        app.add_api_route(prefix + "/state", state, methods=["GET"])
        app.add_api_route(prefix + "/work-mode", work_mode, methods=["GET"])
        for name, endpoint in (("draft", draft), ("send", send), ("settings", settings), ("zoom", zoom), ("navigate", navigate), ("view", view), ("clear", clear), ("approval", approval)):
            app.add_api_route(prefix + "/" + name, endpoint, methods=["POST"])
        self.installed = True

    def tick(self, panel):
        self.install()
        import omni.usd
        stage = omni.usd.get_context().get_stage()
        if stage is not None and stage is not self._rtg_visibility_stage:
            # Wait for the saved port stage to finish loading before applying
            # the renderer workaround. Never edit its source assets/layers.
            if stage.GetPrimAtPath("/World/PortAndRTG/SMART_PORT_STATIC_RTG_YARD_M60"):
                from .viewport_navigation import ensure_complete_rtg_instances
                ensure_complete_rtg_instances(stage)
                # Mount/reload the saved berth geometry without reopening the
                # stage, including the imported 6.6 structure layer.
                from pathlib import Path
                scripts = str(Path(__file__).resolve().parents[5] / 'omniverse' / 'scripts')
                if scripts not in sys.path:
                    sys.path.insert(0, scripts)
                import importlib
                import large_vessel_layout
                importlib.reload(large_vessel_layout).ensure_live_layout(stage)
                self._rtg_visibility_stage = stage
        for _ in range(8):
            try:
                command = self.commands.get_nowait()
            except queue.Empty:
                break
            if command["cancelled"]:
                continue
            try:
                if command["action"] in ("navigate", "zoom"):
                    payload = command["payload"]
                    if command["action"] == "zoom":
                        payload = {"zoom": payload.get("steps")}
                    command["result"] = self.navigation.execute(payload)
                    if payload.get("action", "move") in ("move", "reset"):
                        # Manual viewing takes over from the Agent's camera
                        # writer; the navigator retains the same moving target.
                        runtime = getattr(panel, "_runtime", None)
                        if runtime is not None:
                            runtime.follow = runtime.follow_viewport = None
                else:
                    command["result"] = self.execute(panel, command["action"], command["payload"])
            except Exception as exc:
                command["error"] = str(exc)
            finally:
                command["done"].set()
        try:
            self.navigation.tick()
        except RuntimeError:
            # A viewport can disappear while Kit is closing/replacing a stage.
            self.navigation.stage = self.navigation.viewport = None

    @staticmethod
    def execute(panel, action, payload):
        from . import agent_api
        if action == "work_mode":
            import omni.usd
            from .scene_runtime import rtg_work_mode
            return rtg_work_mode(omni.usd.get_context().get_stage())
        if action == "view":
            # Use the existing validated view adapter, without an LLM roundtrip.
            return panel._execute_tool("set_viewport_view", payload)
        if action == "clear":
            panel._clear_conversation()
            return {"ok":True}
        if action == "approval":
            pending=getattr(panel,"_pending_approval",None)
            if not pending or payload.get("id")!=pending.get("web_id") or type(payload.get("approved")) is not bool:
                raise ValueError("确认请求已失效")
            panel._resolve_approval(pending,payload['approved'])
            return {"ok":True}
        if action == "state":
            if payload.get("native_input") is True:
                # Expire automatically when the browser card stops polling.
                panel._web_input_lease_until = time.monotonic() + 5
            overlay = getattr(panel, "_overlay", None)
            pending=getattr(panel,"_pending_approval",None)
            approval=None
            if pending and not pending['decision'].is_set():
                import secrets
                pending.setdefault('web_id',secrets.token_urlsafe(16))
                approval={"id":pending['web_id'],"name":pending.get('name'),"arguments":pending.get('arguments',{})}
            return {"chat":[{"speaker":who,"text":text} for who,text in getattr(panel,"_chat_lines",[])][-48:],
                    "approval":approval,"busy": panel._busy, "status": panel._last_status,
                    "endpoint": panel._endpoint_model.get_value_as_string(),
                    "model": panel._model_model.get_value_as_string(),
                    "key_configured": bool(panel._key_model.get_value_as_string()),
                    "draft": panel._input_model.get_value_as_string(),
                    "native_input_visible": getattr(getattr(overlay, "_input_card", None), "visible", None)}
        if action in ("draft", "send"):
            text = payload.get("text")
            if not isinstance(text, str) or len(text) > 8192:
                raise ValueError("请输入不超过 8192 字的文本")
            if panel._busy:
                raise ValueError("请等待当前回复完成")
            if action == "send":
                if not text.strip():
                    raise ValueError("请输入内容")
                endpoint = panel._endpoint_model.get_value_as_string().strip()
                agent_api.validate_endpoint(endpoint)
                if not panel._key_model.get_value_as_string().strip() and not agent_api.is_loopback_endpoint(endpoint):
                    raise ValueError("请先在 Settings 填写 API Key")
            panel._input_model.set_value(text)
            if action == "send":
                panel._send_message()
            return {"ok": True, "accepted": action == "send" and panel._busy}
        if action == "settings":
            endpoint, model = payload.get("endpoint"), payload.get("model")
            key = payload.get("api_key", "")
            if not all(isinstance(v, str) for v in (endpoint, model, key)):
                raise ValueError("配置格式错误")
            agent_api.validate_endpoint(endpoint.strip())
            if not model.strip() or max(len(endpoint), len(model), len(key)) > 4096:
                raise ValueError("请填写有效的 URL 和模型")
            if panel._busy:
                raise ValueError("请等待当前回复完成再修改设置")
            panel._endpoint_model.set_value(endpoint.strip())
            panel._model_model.set_value(model.strip())
            if key:
                panel._key_model.set_value(key.strip())
            panel._save_settings()
            return {"ok": True}
        raise ValueError("Unknown browser action")
