"""Browser text must preserve Unicode and never expose the in-memory key."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "omniverse/extensions/smartport.agent_panel"))
from smartport.agent_panel.web_bridge import AgentWebBridge, validate_iecs_navigation


def test_iecs_navigation_only_accepts_camera_gestures():
    assert validate_iecs_navigation({"pan_x":15,"zoom":1.2}) == {"pan_x":15,"zoom":1.2}
    assert validate_iecs_navigation({"action":"reset"}) == {"action":"reset"}
    for payload in ({"action":"restore_visibility"},{"action":"snapshot"},{"model":"large_vessel"}):
        with pytest.raises(ValueError,match="IECS"):
            validate_iecs_navigation(payload)


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

def test_browser_response_contains_conversation_not_tool_history(panel):
    panel._chat_lines=[('You','你好'),('Agent','港区就绪')]
    result=AgentWebBridge.execute(panel,'state',{})
    assert result['chat']==[{'speaker':'You','text':'你好'},{'speaker':'Agent','text':'港区就绪'}]
    assert result['approval'] is None


def test_cinema_state_is_read_only_and_controls_use_the_allowlisted_tool(panel):
    assert AgentWebBridge.execute(panel,'cinema_state',{})=={'status':'idle','visible':False}
    panel._runtime=SimpleNamespace(transfer_camera_state=lambda:{'status':'paused','visible':True,'title':'岸桥取箱'})
    result=AgentWebBridge.execute(panel,'cinema_state',{})
    assert result['title']=='岸桥取箱' and 'private-test-key' not in repr(result)
    calls=[];panel._execute_tool=lambda name,args:calls.append((name,args)) or {'status':'running'}
    AgentWebBridge.execute(panel,'cinema',{'action':'resume'})
    assert calls==[('control_transfer_camera',{'action':'resume'})]


def test_live_camera_routes_check_role_and_preview_revision():
    import asyncio
    from smartport.agent_panel.live_camera import install_camera_routes

    fastapi = pytest.importorskip("fastapi")
    httpx = pytest.importorskip("httpx")
    app = fastapi.FastAPI()

    @app.middleware("http")
    async def test_session(request, call_next):
        request.scope["smartport_session"] = {"role": request.headers.get("test-role")}
        return await call_next(request)

    requests = []

    async def execute(action, payload):
        requests.append((action, payload))
        return {"ok": True}

    preview = SimpleNamespace(revision="selection", feeds={
        1: SimpleNamespace(frame=lambda: b"jpeg"), 2: SimpleNamespace(frame=lambda: None)})
    prefix = "/api/smartport-agent"
    install_camera_routes(app, prefix, lambda: preview, execute)

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            root = prefix + "/live-camera"
            admin = {"test-role": "admin"}
            viewer = {"test-role": "viewer"}
            assert (await client.get(root + "/selection/1")).status_code == 403
            assert (await client.get(root + "/selection/1", headers=viewer)).status_code == 403
            assert (await client.get(root + "/old/1", headers=admin)).status_code == 404
            assert (await client.get(root + "/selection/3", headers=admin)).status_code == 404
            assert (await client.get(root + "/selection/2", headers=admin)).status_code == 204
            response = await client.get(root + "/selection/1", headers=admin)
            assert response.content == b"jpeg"
            assert response.headers["Content-Type"] == "image/jpeg"
            assert response.headers["Cache-Control"] == "private, no-store"
            assert (await client.post(root + "/close", json={}, headers=viewer)).status_code == 403
            assert (await client.post(root + "/close", json={"revision": "selection"}, headers=admin)).status_code == 200
            assert requests == [("live-camera-close", {"revision": "selection"})]

    asyncio.run(check())


def test_sts_statistics_agent_and_browser_use_the_same_read_only_report(panel, tmp_path):
    import asyncio
    import json
    from smartport.agent_panel import sts_statistics
    from smartport.agent_panel.scene_runtime import SceneRuntime

    fastapi = pytest.importorskip("fastapi")
    httpx = pytest.importorskip("httpx")
    source = tmp_path / sts_statistics.SNAPSHOT
    source.parent.mkdir(parents=True)
    rows = [["H101", "test-container-1", "卸船-DSCH", "60"], ["H105", "test-container-2", "卸船-DSCH", "120"]]
    source.write_text(json.dumps({"columns": ["桥吊编号", "箱号", "任务类型", "任务用时(s)"], "rows": rows, "missing_replies": 3,
                                  "start": "2026-11-24T08:00:00+08:00", "end": "2026-11-25T08:00:00+08:00"}), encoding="utf-8")
    panel._runtime = SceneRuntime(tmp_path)
    panel._execute_tool = lambda name, args: panel._runtime.execute(None, name, args)
    tool = "control_sts_statistics"
    result = panel._execute_tool(tool, {"action": "open"})
    assert result["visible"] and result["crane"] == "ALL" and result["row_count"] == 2
    assert "rows" not in result and "test-container-1" not in repr(result)
    state = AgentWebBridge.execute(panel, "state", {})
    assert state["sts_statistics"] == result["revision"] and "test-container-1" not in repr(state)

    app = fastapi.FastAPI()

    @app.middleware("http")
    async def test_session(request, call_next):
        request.scope["smartport_session"] = {"role": request.headers.get("test-role")}
        return await call_next(request)

    async def execute(action, payload):
        return AgentWebBridge.execute(panel, action, payload)

    prefix = "/api/smartport-agent"
    sts_statistics.install_routes(app, prefix, execute)
    from smartport.agent_panel import dataset_analysis
    dataset_analysis.install_routes(app, prefix, execute)

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            path = prefix + "/sts-statistics"
            admin = {"test-role": "admin"}
            viewer = {"test-role": "viewer"}
            upload = prefix + "/dataset?name=temperatures.tsv"
            content = b"station\tcelsius\nA\t-4\nB\t12"
            assert (await client.post(upload, content=content, headers=viewer)).status_code == 403
            assert (await client.post(upload, content=content, headers=admin)).json()["rows"] == 2
            assert (await client.post(upload, content=b"bad\tbad\n1\t2", headers=admin)).status_code == 400
            schema = panel._execute_tool("describe_dataset", {})
            panel._execute_tool("analyze_dataset", {"dataset_id": schema["dataset_id"], "title": "Highest",
                                "sql": "SELECT station FROM data ORDER BY celsius DESC LIMIT 1"})
            result_table = (await client.get(prefix + "/sts-analysis", headers=admin)).json()
            assert result_table["rows"] == [["B"]] and result_table["source"] == "temperatures.tsv"
            await client.post(prefix + "/sts-analysis", json={"action": "close", "revision": result_table["revision"]}, headers=admin)
            assert (await client.get(path, headers=viewer)).status_code == 403
            assert (await client.post(path, json={"action": "close"}, headers=viewer)).status_code == 403
            report = await client.get(path, headers=admin)
            assert report.headers["cache-control"] == "private, no-store" and report.json()["rows"] == rows

            analysis_path = prefix + "/sts-analysis"
            analysis_tool = "analyze_sts_statistics"
            panel._execute_tool(analysis_tool, {"top_n": 1})
            assert AgentWebBridge.execute(panel, "state", {})["sts_analysis"] is None
            analyzed = panel._execute_tool(analysis_tool, {"report_view": "ranking", "top_n": 1})
            assert analyzed["report"]["format"] == "table"
            ranking = (await client.get(analysis_path, headers=admin)).json()
            assert ranking["rows"][0][:4] == [1, "H105", "卸船", 120]
            assert len(ranking["rows"]) == 1
            state = AgentWebBridge.execute(panel, "state", {})
            assert state["sts_analysis"] == ranking["revision"]
            assert state["sts_statistics"] == result["revision"]
            assert (await client.get(analysis_path, headers=viewer)).status_code == 403
            assert (await client.post(analysis_path, json={"action": "close", "revision": ranking["revision"]},
                                      headers=viewer)).status_code == 403
            panel._execute_tool(analysis_tool, {"report_view": "by_crane", "report_format": "chart", "task_type": "DSCH"})
            response = await client.get(analysis_path, headers=admin)
            assert response.headers["cache-control"] == "private, no-store"
            chart = response.json()
            assert chart["format"] == "chart"
            assert chart["scope"] == "全部桥吊 · 卸船"
            assert [(point["label"], point["value"]) for point in chart["series"]] == [("H101", 60), ("H105", 120)]
            stale = await client.post(analysis_path, json={"action": "close", "revision": ranking["revision"]}, headers=admin)
            assert stale.json()["visible"]
            closed = await client.post(analysis_path, json={"action": "close", "revision": chart["revision"]}, headers=admin)
            assert closed.json() == {"visible": False}
            assert (await client.get(path, headers=admin)).json()["rows"] == rows

            selected = await client.post(path, json={"action": "query", "crane": "H105"}, headers=admin)
            assert selected.json()["row_count"] == 1
            await client.post(path, json={"action": "close", "revision": result["revision"]}, headers=admin)
            assert (await client.get(path, headers=admin)).json()["rows"] == [rows[1]]
            reopened = panel._execute_tool(tool, {"action": "open"})
            assert reopened["crane"] == "ALL" and (await client.get(path, headers=admin)).json()["rows"] == rows
            await client.post(path, json={"action": "close", "revision": reopened["revision"]}, headers=admin)
            assert (await client.get(path, headers=admin)).json() == {"visible": False}

    asyncio.run(check())
