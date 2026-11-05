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
        from starlette.requests import Request
        from starlette.responses import FileResponse,HTMLResponse
        from starlette.routing import Route
        import json
        import re
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
                                if not getattr(r, "path", "").startswith((prefix,'/api/truck-driver/','/api/rtg-driver/','/api/sts-driver/'))
                                and getattr(r,'path','') not in ('/api/transfer-camera/state','/driver','/rtg-driver','/sts-driver','/api/session/initial-view')
                                and not getattr(r,'path','').startswith('/static/smartport-')]

        async def execute(action, payload):
            if self.closed:
                raise HTTPException(503, "Agent is restarting")
            command = {"action": action, "payload": payload, "done": threading.Event(), "cancelled": False}
            self.commands.put(command)
            if not await asyncio.to_thread(command["done"].wait, 45 if action in ('driver_acquire','rtg_driver_acquire','sts_driver_acquire') else 5):
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

        async def cinema_state():
            return await execute('cinema_state',{})

        async def cinema(payload:dict):
            return await execute('cinema',payload)

        app.add_api_route(prefix + "/state", state, methods=["GET"])
        app.add_api_route(prefix + "/work-mode", work_mode, methods=["GET"])
        app.add_api_route('/api/transfer-camera/state',cinema_state,methods=['GET'])
        async def initial_view(request:Request):
            session=request.scope.get('smartport_session')
            if not session or session.get('role')!='admin':raise HTTPException(403,'仅管理员可初始化管理视口')
            if session.get('initial_view_pending'):
                result=await execute('admin_home',{})
                session['initial_view_pending']=False
                return result
            return {'ok':True}
        app.add_api_route('/api/session/initial-view',initial_view,methods=['POST'])
        def driver_endpoint(action, role='driver', action_prefix='driver_'):
            async def endpoint(request:Request):
                session=request.scope.get('smartport_session')
                if not session or session.get('role')!=role:raise HTTPException(403,'仅对应设备驾驶员可操作')
                body=await request.body()
                if len(body)>4096:raise HTTPException(400,'驾驶指令过长')
                try:data=json.loads(body) if body else {}
                except (ValueError,UnicodeDecodeError):raise HTTPException(400,'无效驾驶指令')
                if not isinstance(data,dict):raise HTTPException(400,'驾驶指令须为对象')
                client_id=data.pop('client_id',request.query_params.get('client_id',''))
                if not isinstance(client_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}',client_id):
                    raise HTTPException(400,'缺少有效驾驶页面标识，请重新进入驾驶页面')
                owner=request.scope['smartport_session_token']+':'+client_id
                return await execute(action_prefix+action,dict(owner=owner,session=session,data=data))
            return endpoint
        for action in ('acquire','input','release','state'):
            app.add_api_route('/api/truck-driver/'+action,driver_endpoint(action),methods=['GET'] if action=='state' else ['POST'])
            app.add_api_route('/api/rtg-driver/'+action,driver_endpoint(action,'rtg_driver','rtg_driver_'),methods=['GET'] if action=='state' else ['POST'])
            app.add_api_route('/api/sts-driver/'+action,driver_endpoint(action,'sts_driver','sts_driver_'),methods=['GET'] if action=='state' else ['POST'])
        async def driver_page(request:Request):
            from omni.services.livestream.webrtc import extension as web_client
            from rtg_webrtc_viewer import build_viewer_page
            root=Path(web_client.__file__).parent/'web';backup=root/'index.smartport-original.html'
            stock=(backup if backup.exists() else root/'index.html').read_text(encoding='utf-8')
            return HTMLResponse(build_viewer_page(stock),headers={'Cache-Control':'no-store'})
        app.router.routes.insert(0,Route('/driver',driver_page,methods=['GET']))
        app.router.routes.insert(0,Route('/rtg-driver',driver_page,methods=['GET']))
        app.router.routes.insert(0,Route('/sts-driver',driver_page,methods=['GET']))
        # Project assets precede the vendor static mount; no installed files
        # need changing to make the new driving page available.
        for file in (Path(scripts).parent/'web').glob('smartport-*'):
            if file.suffix not in ('.js','.css'):continue
            def asset_endpoint(file=file):
                async def response(request):return FileResponse(file,headers={'Cache-Control':'no-store'})
                return response
            app.router.routes.insert(0,Route('/static/'+file.name,asset_endpoint(),methods=['GET','HEAD']))
        for name, endpoint in (("draft", draft), ("send", send), ("settings", settings), ("zoom", zoom), ("navigate", navigate), ("view", view), ("clear", clear), ("approval", approval),('cinema',cinema)):
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
                    runtime=getattr(panel,'_runtime',None)
                    if runtime is not None and payload.get('action','move') in ('move','reset'):
                        runtime.pause_transfer_camera('manual')
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
        driver_module=sys.modules.get('truck_driver_runtime')
        driver=driver_module.active_driver() if driver_module else None
        if driver:
            if driver.stage!=stage:driver.close()
            else:
                now=time.monotonic();previous=getattr(self,'_driver_last_tick',now)
                self._driver_last_tick=now
                try:driver.tick(min(.1,max(0.,now-previous)))
                except Exception:
                    import logging
                    driver.close();logging.getLogger(__name__).exception('Truck driver stopped')
        else:self._driver_last_tick=time.monotonic()
        rtg_module=sys.modules.get('rtg_driver_runtime')
        rtg_driver=rtg_module.active_rtg_driver() if rtg_module else None
        if rtg_driver:
            if rtg_driver.stage!=stage:rtg_driver.close()
            else:
                now=time.monotonic();previous=getattr(self,'_rtg_driver_last_tick',now)
                self._rtg_driver_last_tick=now
                try:rtg_driver.tick(min(.1,max(0.,now-previous)))
                except Exception:
                    import logging
                    rtg_driver.close();logging.getLogger(__name__).exception('RTG driver stopped')
        else:self._rtg_driver_last_tick=time.monotonic()
        sts_module=sys.modules.get('sts_driver_runtime')
        sts_driver=sts_module.active_sts_driver() if sts_module else None
        if sts_driver:
            if sts_driver.stage!=stage:sts_driver.close()
            else:
                now=time.monotonic();previous=getattr(self,'_sts_driver_last_tick',now);self._sts_driver_last_tick=now
                try:sts_driver.tick(min(.1,max(0.,now-previous)))
                except Exception:
                    import logging
                    sts_driver.close();logging.getLogger(__name__).exception('STS driver stopped')
        else:self._sts_driver_last_tick=time.monotonic()

    @staticmethod
    def execute(panel, action, payload):
        from . import agent_api
        if action=='admin_home':
            import omni.usd
            from omni.kit.viewport.utility import get_viewport_from_window_name,get_active_viewport
            import rtg_viewport_only_livestream as stream
            from pxr import Usd
            stage=omni.usd.get_context().get_stage()
            info=stream._ensure_rtg_follow_camera(stage)
            if not info:raise ValueError('RTG初始视角尚未就绪')
            path,transform,center=info;target=stream._rtg_follow_target_world(stage)
            with Usd.EditContext(stage,stage.GetSessionLayer()):
                transform.Set(stream._look_at_camera_matrix(stream._default_follow_eye(target),target));center.Set(target)
            runtime=getattr(panel,'_runtime',None)
            if runtime:
                runtime.follow=runtime.follow_viewport=None;runtime.pause_transfer_camera('manual')
            viewport=get_viewport_from_window_name('Viewport') or get_active_viewport()
            if viewport is None:raise ValueError('管理员视口尚未就绪')
            viewport.camera_path=path
            stream._start_rtg_camera_follow()
            return {'ok':True,'camera_path':path}
        if action.startswith('sts_driver_'):
            import omni.usd
            from sts_driver_runtime import execute_sts_driver
            return execute_sts_driver(panel,omni.usd.get_context().get_stage(),action[11:],payload['owner'],payload['session'],payload['data'])
        if action.startswith('rtg_driver_'):
            import omni.usd
            from rtg_driver_runtime import execute_rtg_driver
            return execute_rtg_driver(panel,omni.usd.get_context().get_stage(),action[11:],
                                      payload['owner'],payload['session'],payload['data'])
        if action.startswith('driver_'):
            import omni.usd
            from omni.kit.viewport.utility import get_active_viewport
            from truck_driver_runtime import execute_driver
            return execute_driver(panel,omni.usd.get_context().get_stage(),get_active_viewport(),action[7:],
                                  payload['owner'],payload['session'],payload['data'])
        if action=='cinema_state':
            runtime=getattr(panel,'_runtime',None)
            return runtime.transfer_camera_state() if runtime is not None else {'status':'idle','visible':False}
        if action=='cinema':return panel._execute_tool('control_transfer_camera',payload)
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
                approval={"id":pending['web_id'],"name":pending.get('name'),"arguments":pending.get('arguments',{}),"summary":pending.get("summary")}
            return {"chat_count":len(getattr(panel,"_chat_lines",[])),"chat":[{"speaker":who,"text":text} for who,text in getattr(panel,"_chat_lines",[])][-48:],
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
