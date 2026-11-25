"""Same-origin browser controls; all Kit model access stays on its main thread."""
import asyncio
import importlib
import math
import queue
import sys
import threading
import time
from collections import deque
from urllib.parse import urlparse

from . import agent_api
from . import sts_statistics
from .live_camera import install_camera_routes


IECS_AGENT_TOOLS = frozenset({
    "get_current_tasks", "get_equipment_status", "get_bay_inventory",
    "get_yard_inventory_summary", "list_viewpoints", "set_viewport_view",
})
IECS_AGENT_PROMPT = (
    "你是智慧港口 IECS 作业工作台的调度助手。\n"
    f"{agent_api.REPLY_STYLE}\n"
    "询问订单、载箱、等待、报错、"
    "真实 IECS 接入或当前进度时，先用 get_current_tasks 读取场景实时状态，重点查看 iecs "
    "及 external 字段。依据回报区分接单、完工和放行。用户要求看当前作业或指定集卡时，"
    "使用 set_viewport_view，指定集卡默认用车外跟随。用户指定第几条 IECS 任务或 IECS 订单号时，"
    "直接使用 set_viewport_view 的 target=iecs_order 和 order_index 或 order_id。"
    "不要改用当前任务或源箱位，依据返回的 focus_label 和 vehicle_visible 说明镜头所见。"
    "操作范围为查询和切换视角。仅在用户请求派单、设备控制或流程启停时，简短提示在工作台操作。"
)


class IECSConversation:
    """Session-local UI state for the existing Agent worker."""

    def __init__(self):
        self.events = queue.SimpleQueue()
        self.endpoint = agent_api.DEFAULT_ENDPOINT
        self.model = agent_api.DEFAULT_MODEL
        self.api_key = ""
        self.clear()

    def clear(self):
        self.history = [{"role": "system", "content": IECS_AGENT_PROMPT}]
        self.chat = deque(maxlen=48)
        self.busy = False
        self.status = "就绪"

    def collect(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event["kind"]
            if kind == "complete":
                self.history = event["history"]
                self.chat.append({"speaker": "Agent", "text": event["answer"]})
                self.status, self.busy = "就绪", False
            elif kind == "failed":
                self.status, self.busy = "Agent 回复失败，请检查 Settings 中的模型配置后重试", False
            elif kind in ("status", "tool_start"):
                self.status = "正在处理港区操作…" if kind == "tool_start" else "正在等待 Agent 回复…"

    def configure(self, data):
        endpoint, model, key = data.get("endpoint"), data.get("model"), data.get("api_key", "")
        if not all(isinstance(value, str) and len(value) <= 4096 for value in (endpoint, model, key)):
            raise ValueError("请填写有效的模型 URL、名称和 API Key")
        endpoint, model, key = endpoint.strip(), model.strip(), key.strip()
        endpoint = agent_api.validate_iecs_endpoint(endpoint)
        if not model or not model.isprintable() or (key and not key.isascii()) or (key and not key.isprintable()):
            raise ValueError("请填写有效的模型名称和 API Key")
        previous = urlparse(self.endpoint)
        updated = urlparse(endpoint)
        if (previous.hostname, previous.port or 443) != (updated.hostname, updated.port or 443):
            # Keep credentials and conversation context with the original provider.
            self.api_key = ""
            self.clear()
        self.endpoint = endpoint
        self.model = model
        if key:
            self.api_key = key

    def handle(self, panel, action, data, active):
        if not active():
            raise ValueError("会话已失效，请重新登录")
        self.collect()
        if action in ("send", "clear", "settings") and self.busy:
            raise ValueError("请等待当前回复完成")
        if action == "clear":
            self.clear()
        elif action == "settings":
            self.configure(data)
        elif action == "send":
            text = data.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 8192:
                raise ValueError("请输入 1 至 8192 字的内容")
            if not self.api_key:
                raise ValueError("请在 Settings 中配置 Agent 模型和 API Key")
            text = text.strip()
            history = self.history + [{"role": "user", "content": text}]
            self.chat.append({"speaker": "你", "text": text})
            self.status, self.busy = "正在连接 Agent…", True
            threading.Thread(
                target=panel._run_agent_turn,
                args=(self.endpoint, self.model, self.api_key, history),
                kwargs={
                    "emit": self.events.put, "tool_names": IECS_AGENT_TOOLS, "active": active,
                    "open_endpoint": agent_api.open_iecs_endpoint,
                },
                name="SmartPortIECSAgentRequest",
                daemon=True,
            ).start()
        return {
            "chat": list(self.chat),
            "busy": self.busy,
            "configured": bool(self.api_key),
            "status": self.status if self.api_key else "请在 Settings 中配置 Agent 模型和 API Key",
            "endpoint": self.endpoint,
            "model": self.model,
            "key_configured": bool(self.api_key),
            "accepted": action == "send",
        }


def validate_iecs_navigation(payload):
    allowed={'action','orbit_x','orbit_y','pan_x','pan_y','zoom'}
    if set(payload)-allowed or payload.get('action','move') not in ('move','reset','state'):
        raise ValueError('IECS 视角仅支持环绕、平移、缩放和复位')
    return payload


class AgentWebBridge:
    def __init__(self):
        from .viewport_navigation import ViewportNavigation
        self.navigation = ViewportNavigation()
        self.commands = queue.Queue()
        self.installed = False
        self.next_attempt = 0
        self.closed = False
        self.camera_preview = None
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
        from starlette.responses import FileResponse,HTMLResponse,Response
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
        import rtg_lidar_mounts
        import sts_lidar_mounts
        import webrtc_auth
        importlib.reload(rtg_lidar_mounts)
        importlib.reload(sts_lidar_mounts)
        importlib.reload(webrtc_auth).install_auth(app)
        prefix = "/api/smartport-agent"
        app.router.routes[:] = [r for r in app.router.routes
                                if not getattr(r, "path", "").startswith((prefix,'/api/truck-driver/','/api/rtg-driver/','/api/sts-driver/','/api/iecs/','/api/sim/'))
                                and getattr(r,'path','') not in ('/api/transfer-camera/state','/driver','/rtg-driver','/sts-driver','/iecs','/sim','/api/session/initial-view')
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

        async def container_at(payload: dict):
            return await execute("container-at", payload)

        async def inspect_at(payload: dict):
            return await execute("inspect-at", payload)

        async def device_map():
            return await execute("device-map", {})

        async def device_view(payload: dict):
            return await execute("device-view", payload)

        app.add_api_route(prefix + "/device-map", device_map, methods=["GET"])
        app.add_api_route(prefix + "/device-view", device_view, methods=["POST"])

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
        install_camera_routes(app, prefix, lambda: self.camera_preview if not self.closed else None, execute)
        sts_statistics.install_routes(app, prefix, execute)
        from . import dataset_analysis
        dataset_analysis.install_routes(app, prefix, execute)
        app.add_api_route('/api/transfer-camera/state',cinema_state,methods=['GET'])
        async def iecs_state():
            return await execute('iecs_state',{})
        async def iecs_command(payload: dict):
            return await execute('iecs_command',payload)
        async def iecs_external_command(payload: dict):
            return await execute('iecs_external_command',payload)
        async def iecs_navigate(payload: dict):
            try:validate_iecs_navigation(payload)
            except ValueError as exc:raise HTTPException(400,str(exc)) from exc
            return await execute('navigate',payload)
        async def iecs_inspect_at(payload: dict):
            return await execute("device-at",payload)
        async def iecs_events(before: int = 0):
            if before<0:raise HTTPException(400,'无效事件游标')
            return await execute('iecs_events',{'before':before or None})
        async def iecs_messages(message_id: str = '', direction: str = '', heartbeat: bool = False):
            if direction not in ('','received','sent'):
                raise HTTPException(400,'无效的报文方向')
            return await execute('iecs_messages',{'message_id':message_id,'direction':direction,'heartbeat':heartbeat})
        async def iecs_agent_state(request: Request):
            return await iecs_agent(request, "state", {})
        async def iecs_agent_command(request: Request, action: str, payload: dict):
            if action not in ("send", "clear", "settings"):
                raise HTTPException(404, "未知的 IECS Agent 操作")
            return await iecs_agent(request, action, payload)
        async def iecs_agent(request, action, payload):
            session = request.scope.get("smartport_session")
            if not session or session.get("role") not in ("iecs", "admin"):
                raise HTTPException(403, "需要 IECS 作业权限")
            conversation = session.get("iecs_agent")
            if conversation is None:
                conversation = session["iecs_agent"] = IECSConversation()
            return await execute("iecs_agent", {
                "conversation": conversation, "action": action, "data": payload,
                "active": lambda: session["expires"] > time.time(),
            })
        app.add_api_route('/api/iecs/state',iecs_state,methods=['GET'])
        app.add_api_route('/api/iecs/command',iecs_command,methods=['POST'])
        app.add_api_route('/api/iecs/external/command',iecs_external_command,methods=['POST'])
        app.add_api_route('/api/iecs/external/messages',iecs_messages,methods=['GET'])
        app.add_api_route('/api/iecs/navigate',iecs_navigate,methods=['POST'])
        app.add_api_route('/api/iecs/device-map',device_map,methods=['GET'])
        app.add_api_route('/api/iecs/device-view',device_view,methods=['POST'])
        app.add_api_route('/api/iecs/inspect-at',iecs_inspect_at,methods=['POST'])
        app.add_api_route('/api/iecs/events',iecs_events,methods=['GET'])
        app.add_api_route('/api/iecs/agent/state',iecs_agent_state,methods=['GET'])
        app.add_api_route('/api/iecs/agent/{action}',iecs_agent_command,methods=['POST'])

        async def sim_state(equipment: str = "rtg"):
            return await execute("sim_state", {"equipment": equipment})

        async def sim_command(payload: dict):
            return await execute("sim_command", payload)

        async def sim_pointcloud_download(capture_id: str, extension: str):
            from .sim_pointcloud import download

            path = download(Path(scripts).parents[1], capture_id, extension)
            if path is None:
                raise HTTPException(404, "找不到这份点云文件。")
            options = {"media_type": "application/octet-stream"} if extension == "preview" else {"filename": path.name}
            return FileResponse(path, headers={"Cache-Control": "private, no-store"}, **options)

        app.add_api_route("/api/sim/state", sim_state, methods=["GET"])
        app.add_api_route("/api/sim/command", sim_command, methods=["POST"])
        app.add_api_route("/api/sim/pointcloud/{capture_id}/{extension}", sim_pointcloud_download, methods=["GET"])

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
        def driver_frame(role,attribute):
            async def endpoint(request:Request):
                import builtins
                session=request.scope.get('smartport_session')
                if not session or session.get('role')!=role:raise HTTPException(403,'仅对应设备驾驶员可查看画面')
                client_id=request.query_params.get('client_id','')
                if not re.fullmatch(r'[A-Za-z0-9_-]{16,80}',client_id):raise HTTPException(400,'驾驶页面标识无效')
                owner=request.scope['smartport_session_token']+':'+client_id
                driver=getattr(builtins,attribute,None)
                if not driver or driver.closed or driver.owner!=owner:raise HTTPException(404,'驾驶会话已结束')
                encoder=getattr(getattr(driver,'private_view',None),'encoder',None)
                frame=encoder.jpeg_frame if encoder and encoder.transport=='jpeg' else None
                if not frame:raise HTTPException(503,'画面尚未就绪')
                if not getattr(encoder,'jpeg_served',False):
                    encoder.jpeg_served=True
                    print(f'[Driver stream {encoder.signal_port}] Serving JPEG frames over HTTP ({len(frame)} bytes)',flush=True)
                return Response(frame,media_type='image/jpeg',headers={'Cache-Control':'no-store'})
            return endpoint
        for path,role,attribute in (
                ('truck-driver','driver','_smartport_truck_driver'),
                ('rtg-driver','rtg_driver','_smartport_rtg_driver'),
                ('sts-driver','sts_driver','_smartport_sts_driver')):
            app.add_api_route('/api/'+path+'/frame',driver_frame(role,attribute),methods=['GET'])
        import rtg_webrtc_viewer
        build_viewer_page=importlib.reload(rtg_webrtc_viewer).build_viewer_page

        async def driver_page(request:Request):
            from omni.services.livestream.webrtc import extension as web_client
            root=Path(web_client.__file__).parent/'web';backup=root/'index.smartport-original.html'
            stock=(backup if backup.exists() else root/'index.html').read_text(encoding='utf-8')
            return HTMLResponse(build_viewer_page(stock),headers={'Cache-Control':'no-store'})
        app.router.routes.insert(0,Route('/driver',driver_page,methods=['GET']))
        app.router.routes.insert(0,Route('/rtg-driver',driver_page,methods=['GET']))
        app.router.routes.insert(0,Route('/sts-driver',driver_page,methods=['GET']))
        app.router.routes.insert(0,Route('/iecs',driver_page,methods=['GET']))
        app.router.routes.insert(0,Route('/sim',driver_page,methods=['GET']))
        # Project assets precede the vendor static mount; no installed files
        # need changing to make the new driving page available.
        for file in (Path(scripts).parent/'web').glob('smartport-*'):
            if file.suffix not in ('.js','.css'):continue
            def asset_endpoint(file=file):
                async def response(request):return FileResponse(file,headers={'Cache-Control':'no-store'})
                return response
            app.router.routes.insert(0,Route('/static/'+file.name,asset_endpoint(),methods=['GET','HEAD']))
        for name, endpoint in (("draft", draft), ("send", send), ("settings", settings), ("zoom", zoom), ("navigate", navigate), ("container-at", container_at), ("inspect-at", inspect_at), ("view", view), ("clear", clear), ("approval", approval),('cinema',cinema)):
            app.add_api_route(prefix + "/" + name, endpoint, methods=["POST"])
        self.installed = True

    def tick(self, panel):
        self.install()
        self.camera_preview = getattr(getattr(panel, "_runtime", None), "camera_preview", None)
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
                import carb.settings
                import importlib
                import large_vessel_layout

                importlib.reload(large_vessel_layout).ensure_live_layout(stage)
                # The SIM housings are rendered in the WebRTC scene. Keep
                # them out of the separate Isaac RTX sensor authoring flow.
                if carb.settings.get_settings().get("/smartport/webrtc/browserAgentOnly"):
                    from rtg_lidar_mounts import install_sim_housings
                    from sts_lidar_mounts import STS_MOUNTS

                    install_sim_housings(stage)
                    install_sim_housings(stage, STS_MOUNTS, housing_scale=.46)
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
                elif command["action"] in ("container-at", "inspect-at", "device-at"):
                    self._query_scene(command)
                    continue
                elif command["action"] == "device-map":
                    from .device_locations import device_map_state
                    stage, viewport = self.navigation._context()
                    command["result"] = device_map_state(stage, viewport.time)
                elif command["action"] == "device-view":
                    command["result"] = self._focus_map_device(panel, command["payload"])
                else:
                    command["result"] = self.execute(panel, command["action"], command["payload"])
            except Exception as exc:
                command["error"] = str(exc)
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

    def _focus_map_device(self, panel, payload):
        from .device_locations import device_for_hit
        from .scene_runtime import SceneRuntime

        device_id = payload.get("id")
        if set(payload) != {"id"} or not isinstance(device_id, str) or not device_id.startswith("/World/") or len(device_id) > 240:
            raise ValueError("无效的地图设备标识")
        stage, viewport = self.navigation._context()
        device = device_for_hit(stage, device_id, viewport.time)
        if not device or device["id"] != device_id:
            raise ValueError("所选设备已离开当前场景")
        runtime = getattr(panel, "_runtime", None)
        if runtime is None:
            runtime = panel._runtime = SceneRuntime(panel._project_root, panel._ui_events.put)
        runtime.bind(stage)
        return runtime.set_device_view(device, viewport)

    def _query_scene(self, command):
        from .device_locations import device_for_hit

        action = command["action"]
        x, y = (command["payload"].get(key) for key in ("x", "y"))
        if any(type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value) for value in (x, y)):
            raise ValueError("无效的港区画面坐标")
        stage, viewport = self.navigation._context()
        width, height = viewport.resolution
        if width <= 0 or height <= 0:
            raise ValueError("港区画面尚未就绪")
        pixel = (min(width - 1, int(x * width)), min(height - 1, int(y * height)))

        def completed(prim_path, *_args):
            if command["cancelled"]:
                return
            try:
                rows, device = [], None
                if prim_path and viewport.stage == stage:
                    path = str(prim_path)
                    if action != "device-at":
                        from rtg_container_info_popup import container_info_for_hit
                        rows = container_info_for_hit(stage, path)
                    if not rows and action != "container-at":
                        device = device_for_hit(stage, path, viewport.time)
                command["result"] = {"rows": rows}
                if action != "container-at":
                    command["result"]["device"] = device
            except Exception as exc:
                command["error"] = str(exc)
            finally:
                command["done"].set()

        viewport.request_query(pixel, completed)

    @staticmethod
    def execute(panel, action, payload):
        from . import agent_api
        if action in {"sim_state", "sim_command"}:
            import omni.usd
            from . import sim_workbench
            from .scene_runtime import SceneRuntime

            stage = omni.usd.get_context().get_stage()
            if not stage:
                raise ValueError("港区场景尚未打开。")
            owner = getattr(panel, "_runtime", None)
            if owner is None:
                owner = panel._runtime = SceneRuntime(panel._project_root, panel._ui_events.put)
            owner.bind(stage)
            if action == "sim_command":
                return sim_workbench.command(owner, payload)
            return sim_workbench.state(owner, payload.get("equipment", "rtg"))
        if action=='iecs_agent':
            return payload['conversation'].handle(panel,payload['action'],payload['data'],payload['active'])
        if action in {'iecs_state','iecs_command','iecs_events','iecs_external_command','iecs_messages'}:
            import omni.usd
            from .scene_runtime import SceneRuntime, iecs_runtime
            from .project_interfaces import _iecs, external_iecs
            stage=omni.usd.get_context().get_stage()
            if not stage:raise ValueError('港区场景尚未打开')
            focus = None
            if action in {'iecs_command','iecs_external_command'}:
                owner=getattr(panel,'_runtime',None)
                if owner is None:
                    owner=panel._runtime=SceneRuntime(panel._project_root,panel._ui_events.put)
                owner.bind(stage)
                if action=='iecs_external_command':
                    external_iecs(owner,payload)
                else:
                    args=agent_api.validate_tool_arguments('control_iecs',payload)
                    result = _iecs(owner,args,web=True)
                    if args["action"] == "focus" and result.get("order_id"):
                        focus = result
            runtime=iecs_runtime(stage)
            if action=='iecs_events':
                return {'events':runtime.recent_history(payload['before']) if runtime else []}
            if action=='iecs_messages':
                traffic=runtime.external.transport.traffic if runtime and runtime.external else None
                if payload['message_id']:
                    return {'message':traffic.read(payload['message_id']) if traffic else None}
                return {'messages':traffic.read(direction=payload['direction'],telemetry=payload['heartbeat']) if traffic else []}
            if runtime is None:
                return {"running": False}
            state = {"running": True, **runtime.cached}
            if focus is not None:
                state["focus"] = focus
            return state
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
        if action == "live-camera-close":
            runtime = getattr(panel, "_runtime", None)
            preview = runtime.camera_preview if runtime else None
            if preview and payload.get("revision") == preview.revision:
                return panel._execute_tool("control_camera_display", {"display": preview.display, "action": "stop"})
            return {"ok": True}
        if action in {"sts-statistics-data", "sts-statistics-control"}:
            report = getattr(getattr(panel, "_runtime", None), "sts_statistics", None)
            if action == "sts-statistics-data":
                return report if report else {"visible": False}
            arguments = dict(payload)
            revision = arguments.pop("revision", None)
            if arguments.get("action") == "close" and revision and (not report or revision != report["revision"]):
                return {"visible": bool(report)}
            return panel._execute_tool("control_sts_statistics", arguments)
        if action == "dataset-import":
            if panel._busy:
                raise ValueError("Agent 正在处理提问，请在回答结束后导入")
            if getattr(panel, "_runtime", None) is None:
                from .scene_runtime import SceneRuntime
                panel._runtime = SceneRuntime(panel._project_root, panel._ui_events.put)
            runtime = panel._runtime
            if runtime.dataset:
                runtime.dataset.db.close()
            runtime.dataset = payload["dataset"]
            runtime.sts_analysis_report = None
            runtime.dataset_reports.clear()
            return {"source": runtime.dataset.name, "rows": runtime.dataset.count}
        if action in {"sts-analysis-data", "sts-analysis-close"}:
            runtime = getattr(panel, "_runtime", None)
            analysis = getattr(runtime, "sts_analysis_report", None)
            if action == "sts-analysis-data":
                return analysis or {"visible": False}
            if analysis and payload.get("revision") == analysis["revision"]:
                runtime.sts_analysis_report = None
                runtime.dataset_reports.clear()
                return {"visible": False}
            return {"visible": bool(analysis)}
        if action == "state":
            if payload.get("native_input") is True:
                # Expire automatically when the browser card stops polling.
                panel._web_input_lease_until = time.monotonic() + 5
            overlay = getattr(panel, "_overlay", None)
            runtime = getattr(panel, "_runtime", None)
            preview = getattr(runtime, "camera_preview", None)
            report = getattr(runtime, "sts_statistics", None)
            analysis = getattr(runtime, "sts_analysis_report", None)
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
                    "live_camera": preview.state() if preview else None,
                    "sts_statistics": report["revision"] if report else None,
                    "sts_analysis": analysis["revision"] if analysis else None,
                    "dataset": runtime.dataset.name if getattr(runtime, "dataset", None) else None,
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
