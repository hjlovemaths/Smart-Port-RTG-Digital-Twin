from __future__ import annotations

import builtins
import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "omniverse/scripts"))
sys.path.insert(0, str(ROOT / "omniverse/extensions/smartport.agent_panel"))

from smartport.agent_panel import agent_api, project_interfaces, runtime_catalog, scene_runtime, sts_statistics
from smartport.agent_panel.dataset_analysis import import_file


def test_excel_import_uses_actual_cells_when_dimension_is_a1():
    from io import BytesIO
    from zipfile import ZipFile

    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    book.active.sheet_state = "hidden"
    sheet = book.create_sheet("明细")
    sheet.append(["编号", "效率", "备注"])
    sheet.append(["0944", 28.53])
    sheet.append(["1122", 32, "复核"])
    raw = BytesIO()
    book.save(raw)
    modified = BytesIO()
    with ZipFile(raw) as original, ZipFile(modified, "w") as output:
        for name in original.namelist():
            content = original.read(name)
            if name == "xl/worksheets/sheet2.xml":
                assert b'ref="A1:C3"' in content
                content = content.replace(b'ref="A1:C3"', b'ref="A1"')
            output.writestr(name, content)
    dataset = import_file("效率.xlsx", modified.getvalue())
    result = dataset.analyze({"dataset_id": dataset.id, "title": "明细",
                              "sql": "SELECT 编号, 效率, 备注 FROM data ORDER BY 效率"})
    assert result["rows"] == [("0944", 28.53, None), ("1122", 32, "复核")]


def test_analyze_unrelated_datasets_through_runtime(runtime):
    dataset = import_file("门店销售.csv", "门店,月份,销售额,成本\n001,10,20,12\n001,10,18,3\n002,10,8,10\n002,11,100,0".encode("gb18030"))
    runtime.dataset = dataset
    schema = runtime.execute(None, "describe_dataset", {})
    assert schema["fields"][0] == {"name": "门店", "type": "TEXT", "nulls": 0}
    args = {"dataset_id": schema["dataset_id"], "format": "chart", "x": "门店", "y": "利润", "title": "十月门店利润",
            "sql": "SELECT 门店, sum(销售额-成本) AS 利润 FROM data WHERE 月份=10 GROUP BY 门店 ORDER BY 利润 DESC"}
    result = runtime.execute(None, "analyze_dataset", args)
    assert result["result_preview"] == [("001", 23), ("002", -2)]
    assert [p["value"] for p in runtime.sts_analysis_report["series"]] == [23, -2]

    runtime.dataset = import_file("监测.jsonl", '{"探头":"A","读数":12}\n{"探头":"B","读数":null}\n{"探头":"C","读数":14}'.encode())
    with pytest.raises(ValueError, match="切换"):
        runtime.execute(None, "analyze_dataset", args)
    schema = runtime.execute(None, "describe_dataset", {})
    assert schema["fields"][1]["nulls"] == 1
    summary = runtime.execute(None, "analyze_dataset", {"dataset_id": schema["dataset_id"], "title": "最高两项",
        "sql": "WITH ranked AS (SELECT 探头, 读数 FROM data WHERE 读数 IS NOT NULL) SELECT * FROM ranked ORDER BY 读数 DESC LIMIT 2"})
    assert summary["result_preview"] == [("C", 14), ("A", 12)]
    assert runtime.sts_analysis_report["format"] == "table"
    bad_sql = ["DELETE FROM data", "SELECT name FROM sqlite_master", "SELECT 1", "WITH a AS (SELECT 1) DELETE FROM data",
               "SELECT * FROM data; SELECT * FROM data", "SELECT randomblob(1000) FROM data"]
    for sql in bad_sql:
        with pytest.raises(ValueError):
            runtime.execute(None, "analyze_dataset", {"dataset_id": schema["dataset_id"], "title": "检查", "sql": sql})
    assert runtime.sts_analysis_report["title"] == "最高两项"
    aggregate = runtime.execute(None, "analyze_dataset", {"dataset_id": schema["dataset_id"], "title": "记录数",
        "sql": "SELECT count(*) FROM data"})
    assert aggregate["result_preview"] == [(3,)]


def test_dataset_pie_calculates_shares_and_rejects_negative_values(runtime):
    runtime.dataset = import_file("counts.csv", b"category,amount\nA,3\nB,1\nC,0")
    schema = runtime.execute(None, "describe_dataset", {})
    args = {"dataset_id": schema["dataset_id"], "title": "构成", "format": "pie", "x": "category", "y": "total",
            "sql": "SELECT category, sum(amount) AS total FROM data GROUP BY category ORDER BY category"}
    result = runtime.execute(None, "analyze_dataset", args)
    assert result["report"]["format"] == "pie"
    assert [p["share"] for p in runtime.sts_analysis_report["series"]] == [0.75, 0.25, 0]
    runtime.execute(None, "analyze_dataset", {**args, "format": "chart", "title": "对比"})
    assert [report["format"] for report in runtime.sts_analysis_report["reports"]] == ["pie", "chart"]
    assert [report["title"] for report in runtime.sts_analysis_report["reports"]] == ["构成", "对比"]
    with pytest.raises(ValueError, match="非负"):
        runtime.execute(None, "analyze_dataset", {**args, "sql": "SELECT category, -amount AS total FROM data"})


def test_dataset_computes_before_display_limit_and_can_explore_without_opening(runtime):
    data = import_file("counts.json", ('{"columns":["index"],"rows":[' +
                       ",".join(f"[{i}]" for i in range(222)) + ']}').encode())
    runtime.dataset = data
    base = {"dataset_id": data.id, "title": "记录"}
    preview = runtime.execute(None, "analyze_dataset", {**base, "sql": "SELECT SUM([index]) FROM data", "format": "preview"})
    assert preview["result_preview"] == [(sum(range(222)),)] and runtime.sts_analysis_report is None
    result = runtime.execute(None, "analyze_dataset", {**base, "format": "line", "x": "i", "y": "s",
        "sql": "SELECT [index] AS i, sum([index]) OVER (ORDER BY [index]) AS s FROM data ORDER BY i"})
    assert result["truncated"] and result["displayed_rows"] == 200 and len(result["result_preview"]) == 20
    assert runtime.sts_analysis_report["series"][-1]["value"] == sum(range(200))


@pytest.mark.parametrize("live_running,sim_running,expected_hidden", [
    (False, False, True),
    (True, False, False),
    (False, True, False),
])
def test_idle_cleanup_only_hides_stale_task_panel_when_both_sources_stopped(
    monkeypatch, live_running, sim_running, expected_hidden
):
    import types
    from contextlib import nullcontext

    prim = SimpleNamespace(hidden=False)
    stage = SimpleNamespace(GetSessionLayer=lambda: object(), GetPrimAtPath=lambda path: prim)
    usd = SimpleNamespace(EditContext=lambda *_: nullcontext())
    geom = SimpleNamespace(Imageable=lambda item: SimpleNamespace(
        MakeInvisible=lambda: setattr(item, "hidden", True)))
    pxr = types.ModuleType("pxr")
    pxr.Usd, pxr.UsdGeom = usd, geom
    monkeypatch.setitem(sys.modules, "pxr", pxr)
    monkeypatch.setattr(builtins, "_smart_port_rtg_panel_facing_driver",
                        SimpleNamespace(_subscription=object()) if live_running else None,
                        raising=False)
    monkeypatch.setattr(project_interfaces, "simulation_status", lambda _module: {"running": sim_running})
    monkeypatch.setattr(project_interfaces, "existing_module", lambda _name: None)
    project_interfaces._clear_idle_task_panel(stage)
    assert prim.hidden is expected_hidden


@pytest.mark.parametrize("filename", ["D:/scripts/rtg_driver.py", r"D:\scripts\rtg_driver.py"])
def test_motion_module_lookup_handles_launcher_aliases_without_path_allocations(monkeypatch, filename):
    module = SimpleNamespace(__file__=filename)
    monkeypatch.setattr(scene_runtime, "sys", SimpleNamespace(modules={"launcher_alias": module}))

    def no_paths(*args):
        raise AssertionError("Module lookup must not allocate Path objects in motion frames")

    monkeypatch.setattr(scene_runtime, "Path", no_paths)
    assert scene_runtime.existing_module("rtg_driver") is module
    assert scene_runtime.existing_module("different_driver") is None


@pytest.mark.parametrize("name,args", [
    ("set_viewport_view", {"target": "bay"}),
    ("set_viewport_view", {"target": "sts"}),
    ("set_viewport_view", {"target": "truck"}),
    ("set_viewport_view", {"target": "truck", "truck_number": 21}),
    ("set_viewport_view", {"target": "rtg", "view": "cabin"}),
    ("set_viewport_view", {"target": "rtg", "follow": "yes"}),
    ("switch_viewport_camera", {}),
    ("switch_viewport_camera", {"camera_path": "/World/Cam", "camera_number": 1}),
    ("switch_viewport_camera", {"camera_path": "file:/secrets"}),
    ("set_sts_axis_positions", {"crane_id": "STS_08", "gantry_m": 1}),
    ("set_sts_axis_positions", {"crane_id": "STS_04"}),
    ("set_sts_axis_positions", {"crane_id": "STS_04", "hoist_m": float("nan")}),
    ("set_sts_axis_positions", {"crane_id": "STS_04", "hoist_distance_m": 5}),
    ("set_sts_axis_positions", {"crane_id": "STS_04", "hoist_m": 5, "hoist_distance_m": 2, "hoist_direction": "lower"}),
    ("set_sts_axis_positions", {"crane_id": "STS_04", "boom_deg": 50, "park_for_luffing": True}),
    ("control_iecs", {"action": "submit"}),
    ("control_iecs", {"action": "speed", "speed": 100}),
    ("submit_rtg_task", {"work_type": "SWITCH", "start_pos": "3C5562"}),
    ("rtg_slot_operation", {"action": "move", "bay_id": "3C2", "row": 0}),
    ("capture_lidar", {"mode": "yard", "output_stem": "../outside"}),
    ("capture_lidar", {"mode": "yard", "output_dir": "D:/outside"}),
    ("capture_lidar", {"mode": "positions", "output_frame": "sensor"}),
    ("control_lidar", {"action": "start", "mode": "yard", "variant": "OS1"}),
    ("control_gamepad", {"action": "position_for_pick"}),
    ("set_rtg_axis_positions", {"duration_s": 2}),
    ("set_viewport_view", {"target": "port", "view": "under_trolley"}),
    ("set_viewport_view", {"target": "trolley", "view": "under_trolley", "elevation_deg": 45}),
    ("set_viewport_view", {"target": "trolley", "view": "under_trolley", "distance_m": 10}),
    ("set_viewport_view", {"target": "trolley", "field_of_view_deg": 141}),
    ("set_viewport_view", {"target": "trolley", "field_of_view_deg": float("nan")}),
])
def test_runtime_boundary_rejects_ambiguous_or_invalid_commands(name, args):
    with pytest.raises(ValueError):
        agent_api.validate_tool_arguments(name, args)


def test_view_crane_and_duration_arguments_normalize_without_losing_targets():
    assert agent_api.validate_tool_arguments("set_viewport_view", {"target": "BAY", "bay_id": "4c41", "view": "TOP"}) == {"target": "bay", "bay_id": "4C/041", "view": "top"}
    assert agent_api.validate_tool_arguments("set_sts_axis_positions", {"crane_id": "sts4", "gantry_m": 2}) == {"crane_id": "STS_04", "gantry_m": 2.0}
    assert agent_api.validate_tool_arguments("move_rtg_to_bay", {"bay_id": "4C3", "duration_s": 4})["duration_s"] == 4


def test_under_trolley_preset_defaults_to_a_following_vertical_camera():
    assert agent_api.validate_tool_arguments("set_viewport_view", {"target": "TROLLEY"}) == {
        "target": "trolley", "view": "under_trolley", "follow": True,
    }
    assert agent_api.validate_tool_arguments("set_viewport_view", {
        "target": "rtg", "view": "UNDER_TROLLEY", "field_of_view_deg": 130, "follow": False,
    }) == {"target": "rtg", "view": "under_trolley", "field_of_view_deg": 130.0, "follow": False}


def test_every_advertised_runtime_tool_has_an_explicit_dispatch_adapter():
    assert set(runtime_catalog.SCHEMAS) == scene_runtime.CORE_TOOLS | project_interfaces.HANDLED_TOOLS
    assert len(agent_api.TOOLS) == len({item["function"]["name"] for item in agent_api.TOOLS})


def test_work_views_dispatch_uses_fixed_display_api_without_equipment_control(runtime, monkeypatch):
    calls = []
    module = SimpleNamespace(
        start_rtg_work_views=lambda stage: calls.append(("start", stage)) or {"running": True},
        stop_rtg_work_views=lambda: calls.append(("stop", None)),
        get_rtg_work_views_status=lambda: {"running": False})
    monkeypatch.setattr(project_interfaces, "load", lambda name: module if name == "rtg_work_views" else pytest.fail(name))
    monkeypatch.setattr(runtime, "_guard_manual", lambda *_: pytest.fail("Camera display must not acquire equipment"))
    assert runtime.execute(None, "control_camera_display", {"display": "WORK_VIEWS", "action": "START"}) == {"running": True}
    assert calls == [("start", None)]
    runtime.execute(None, "control_camera_display", {"display": "work_views", "action": "status"})
    assert len(calls) == 1
    runtime.execute(None, "control_camera_display", {"display": "work_views", "action": "stop"})
    assert calls[-1] == ("stop", None)


def test_agent_opens_configured_rtsp_only_in_web_preview(runtime, monkeypatch):
    class Reader:
        def __init__(self, camera, on_frame, on_status, *, active):
            self.stopped = False

        def stop(self, *, wait):
            self.stopped = True

    cameras = {1: SimpleNamespace(location="吊具角一", password="camera-secret"),
               5: SimpleNamespace(location="前横梁", password="camera-secret")}
    module = SimpleNamespace(camera_config_path=lambda: "cameras.json", _read_camera_config=lambda _: cameras,
                             RtgRtspReader=Reader, SPREADER_CAMERA_GRID_LAYOUT=((1, 2), (4, 3)))
    monkeypatch.setattr(project_interfaces, "load", lambda name: module)
    name = "control_camera_display"
    popup = {"display": "rtsp_popup"}
    assert runtime.execute(None, name, {**popup, "action": "status"})["configured_cameras"][1] == {
        "number": 5, "location": "前横梁"}
    result = runtime.execute(None, name, {**popup, "action": "start", "camera_number": 5})
    assert result["cameras"] == [{"number": 5, "location": "前横梁", "configured": True}]
    preview = runtime.camera_preview
    with pytest.raises(ValueError, match="未配置"):
        runtime.execute(None, name, {**popup, "action": "start", "camera_number": 6})
    assert runtime.camera_preview is preview and not preview.feeds[5]._reader.stopped
    grid = {"display": "spreader_rtsp_grid"}
    with pytest.raises(ValueError, match="requires rtsp_popup"):
        runtime.execute(None, name, {**grid, "action": "start", "camera_number": 1})
    result = runtime.execute(None, name, {**grid, "action": "start"})
    assert [tile["number"] for tile in result["cameras"]] == [1, 2, 4, 3]
    assert preview.feeds[5]._reader.stopped and "camera-secret" not in repr(result)
    runtime.execute(None, name, {**grid, "action": "stop"})
    assert runtime.camera_preview is None


def test_equipment_task_and_multi_position_capture_preserve_confirmation_boundary():
    assert runtime_catalog.needs_approval("set_sts_axis_positions", {})
    assert runtime_catalog.needs_approval("control_sts_task", {"action": "start_unload"})
    assert runtime_catalog.needs_approval("capture_lidar", {"mode": "positions"})
    assert not runtime_catalog.needs_approval("get_current_tasks", {})
    assert not runtime_catalog.needs_approval("set_viewport_view", {"target": "rtg"})
    assert not runtime_catalog.needs_approval("capture_lidar", {"mode": "yard"})


@pytest.mark.parametrize('source_size,current_size',[(20,40),(40,20)])
def test_standalone_pick_checks_actual_source_size_before_descent(runtime,monkeypatch,tmp_path,source_size,current_size):
    from smartport.agent_panel import yard_queries
    calls=[]
    fake=SimpleNamespace(
        slot_target=lambda **kwargs:SimpleNamespace(slot=SimpleNamespace(container_size_ft=source_size)),
        lower_to_slot=lambda **kwargs:calls.append(kwargs) or {'status':'started'},
        move_to_slot=lambda **kwargs:None,lift_from_slot=lambda **kwargs:None)
    monkeypatch.setattr(project_interfaces,'load',lambda _name:fake)
    monkeypatch.setattr(runtime,'_guard_manual',lambda *_:None)
    monkeypatch.setattr(yard_queries,'read_inventory_snapshot',lambda *_:(None,{'kind':'inventory_json_snapshot','inventory_file':str(tmp_path/'stock.json')}))
    monkeypatch.setitem(sys.modules,'rtg_transfer_motion',SimpleNamespace(read_spreader_size_ft=lambda _stage:current_size))
    args={'action':'lower','bay_id':'3C/001','row':2,'tier':1}
    with pytest.raises(ValueError,match=f'源箱实际为{source_size}尺'):
        project_interfaces.execute_project_interface(runtime,'rtg_slot_operation',args)
    assert not calls
    monkeypatch.setitem(sys.modules,'rtg_transfer_motion',SimpleNamespace(read_spreader_size_ft=lambda _stage:source_size))
    assert project_interfaces.execute_project_interface(runtime,'rtg_slot_operation',args)['status']=='started'
    assert len(calls)==1


@pytest.fixture
def runtime(monkeypatch):
    for name in ("_smart_port_sts_panel", "_smart_port_sts_controller_pool", "_iecs_runtime", "_smart_port_rtg_panel_facing_driver", "_SMART_PORT_RTG_REMOTE_MANUAL_ACTIVE"):
        monkeypatch.setattr(builtins, name, None, raising=False)
    runtime = scene_runtime.SceneRuntime(ROOT)
    yield runtime
    runtime.close()


def install_rtg(monkeypatch):
    class Controller:
        def __init__(self, stage): self.stage = stage
        def get_positions(self): return self.stage.positions["gantry_m"], self.stage.positions["trolley_m"], self.stage.positions["hoist_m"]
        def set_gantry_safe(self, value): self.stage.positions["gantry_m"] = value
        def set_trolley_safe(self, value): self.stage.positions["trolley_m"] = value
        def set_hoist_safe(self, value): self.stage.positions["hoist_m"] = value
    module = SimpleNamespace(RTGController=Controller, GANTRY_SOFT_LIMITS=(1, 450), TROLLEY_SOFT_LIMITS=(0, 18.06), HOIST_SOFT_LIMITS=(0.5, 18))
    monkeypatch.setitem(sys.modules, "rtg_live_control", module)
    return SimpleNamespace(positions={"gantry_m": 10.0, "trolley_m": 2.0, "hoist_m": 5.0})


def test_rtg_motion_is_continuous_and_cancellation_holds_the_current_pose(runtime, monkeypatch):
    stage = install_rtg(monkeypatch)
    result = runtime.execute(stage, "set_rtg_axis_positions", {"gantry_m": 20, "duration_s": 1})
    assert stage.positions["gantry_m"] == 10
    for _ in range(5): runtime.tick(stage, 0.1)
    assert 10 < stage.positions["gantry_m"] < 20
    assert stage.positions["trolley_m"] == 2 and stage.positions["hoist_m"] == 5
    held = dict(stage.positions)
    runtime.execute(stage, "cancel_operation", {"operation_id": result["operation_id"]})
    runtime.tick(stage, 0.1)
    assert stage.positions == held
    assert runtime.operations[result["operation_id"]]["status"] == "cancelled"


def test_invalid_multi_axis_command_is_atomic_and_motion_finishes_at_target(runtime, monkeypatch):
    stage = install_rtg(monkeypatch)
    with pytest.raises(ValueError, match="hoist_m"):
        runtime.execute(stage, "set_rtg_axis_positions", {"gantry_m": 20, "hoist_m": -3})
    assert stage.positions == {"gantry_m": 10, "trolley_m": 2, "hoist_m": 5}
    result = runtime.execute(stage, "set_rtg_axis_positions", {"gantry_m": 20, "hoist_m": 6, "duration_s": 0.2})
    runtime.tick(stage, 0.1)
    runtime.tick(stage, 0.1)
    assert stage.positions == {"gantry_m": 20, "trolley_m": 2, "hoist_m": 6}
    assert runtime.operations[result["operation_id"]]["status"] == "completed"


def test_active_automatic_task_blocks_manual_motion(runtime, monkeypatch):
    stage = install_rtg(monkeypatch)
    engine = SimpleNamespace(orders={"O1": {"status": "executing"}})
    monkeypatch.setattr(builtins, "_iecs_runtime", SimpleNamespace(stage=stage, closed=False, engine=engine))
    with pytest.raises(ValueError, match="IECS"):
        runtime.execute(stage, "set_rtg_axis_positions", {"trolley_m": 5})
    assert stage.positions["trolley_m"] == 2


def test_sts_task_pause_retains_its_target_and_resume_does_not_restart_a_stopped_task(runtime, monkeypatch):
    stage = object()
    runtime.bind(stage)
    controller = SimpleNamespace(targets={"hoist": 10}, stopped=False)
    task = SimpleNamespace(paused=False, c=controller)
    panel = SimpleNamespace(unload_task=task)
    monkeypatch.setattr(project_interfaces, "current_sts_panel", lambda _stage: panel)
    monkeypatch.setattr(project_interfaces, "current_tasks", lambda _stage: {"sts": {"paused": task.paused}})
    project_interfaces.execute_project_interface(runtime, "control_sts_task", {"action": "pause"})
    assert task.paused and controller.targets == {"hoist": 10}
    project_interfaces.execute_project_interface(runtime, "control_sts_task", {"action": "resume"})
    assert not task.paused
    task._agent_stop_cancelled_targets = True
    with pytest.raises(ValueError, match="Reset"):
        project_interfaces.execute_project_interface(runtime, "control_sts_task", {"action": "resume"})


def test_physics_target_adapter_fills_untouched_axes(runtime, monkeypatch):
    stage = install_rtg(monkeypatch)
    runtime.bind(stage)
    received = {}
    fake = SimpleNamespace(set_rtg_physics_targets=lambda **kwargs: received.update(kwargs) or received)
    monkeypatch.setattr(project_interfaces, "load", lambda name: fake)
    result = project_interfaces.execute_project_interface(runtime, "control_rtg_physics", {"action": "set_targets", "trolley_m": 3})
    assert result == {"stage": stage, "gantry_m": 10, "trolley_m": 3, "hoist_m": 5}


def test_capabilities_can_be_read_without_loading_or_starting_service_modules(runtime, monkeypatch):
    monkeypatch.setattr(project_interfaces, "load", lambda _name: pytest.fail("Capability listing must not load services"))
    result = runtime.execute(None, "get_agent_capabilities", {})
    assert result["interface_count"] == 51
    assert any(item["name"] == "get_current_tasks" for item in result["interfaces"])


def test_documented_interface_names_and_machine_schemas_match_the_live_catalog():
    import json
    documentation = (ROOT / "omniverse/docs/AGENT_INTERFACES.md").read_text(encoding="utf-8")
    machine = json.loads((ROOT / "omniverse/extensions/smartport.agent_panel/docs/agent_interfaces.json").read_text(encoding="utf-8"))
    assert machine["tools"] == list(agent_api.TOOLS)
    for item in agent_api.TOOLS:
        assert "`" + item["function"]["name"] + "`" in documentation


@pytest.fixture
def sts_report(tmp_path):
    import json

    source = tmp_path / sts_statistics.SNAPSHOT
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({
        "columns": ["桥吊编号", "开始时间", "任务用时(s)", "任务类型", "等集卡时间（s）", "司机工号"],
        "start": "2026-11-24T08:00:00+08:00",
        "end": "2026-11-25T08:00:00+08:00",
        "missing_replies": 3,
        "rows": [
            ["H101", "08:10", "60", "卸船-DSCH", "0", "test-operator"],
            ["H101", "08:11", 120, "卸船-DSCH", "20", "test-operator"],
            ["H105", "08:12", "300", "卸船-DSCH", "/", "test-operator"],
            ["H105", "08:13", "", "卸船-DSCH", "NaN", "test-operator"],
            ["H105", "08:14", 900, "装船-LOAD", 50, "test-operator"],
        ],
    }), encoding="utf-8")
    return scene_runtime.SceneRuntime(tmp_path)


def test_agent_analyzes_sts_rows_without_changing_the_open_panel(sts_report):
    sts_report.execute(None, "control_sts_statistics", {"action": "query", "crane": "H101"})
    visible_report = sts_report.sts_statistics
    name, args = agent_api.parse_tool_call({"function": {
        "name": "analyze_sts_statistics", "arguments": '{"task_type":"DSCH","top_n":2}',
    }})
    result = sts_report.execute(None, name, args)
    assert result["overall"] == {
        "row_count": 4, "valid_count": 3, "invalid_count": 1, "mean_seconds": 160,
        "median_seconds": 120, "min_seconds": 60, "max_seconds": 300,
    }
    assert result["by_crane"]["H101"]["mean_seconds"] == 90
    assert result["by_crane"]["H105"]["valid_count"] == 1
    assert list(result["by_task_type"]) == ["DSCH"]
    assert result["longest"] == [
        {"crane": "H105", "task_type": "DSCH", "seconds": 300, "start": "08:12"},
        {"crane": "H101", "task_type": "DSCH", "seconds": 120, "start": "08:11"},
    ]
    assert result["source"] == "日志回放"
    assert result["start"] == "2026-11-24T08:00:00+08:00"
    assert result["snapshot_missing_replies"] == 3
    assert sts_report.sts_statistics is visible_report
    assert "test-operator" not in repr(result)


def test_agent_preserves_zero_waits_and_reports_empty_sts_filters(sts_report):
    wait = sts_report.execute(None, "analyze_sts_statistics",
                              {"crane": "H101", "metric": "truck_wait", "top_n": 0})
    assert wait["overall"]["valid_count"] == 2
    assert wait["overall"]["min_seconds"] == 0
    assert wait["overall"]["mean_seconds"] == 10
    assert wait["longest"] == []
    empty = sts_report.execute(None, "analyze_sts_statistics", {"crane": "H101", "task_type": "LOAD"})
    assert empty["overall"]["row_count"] == 0 and empty["overall"]["mean_seconds"] is None
    assert empty["covered_cranes"] == ["H101", "H105"]
    assert sts_report.sts_statistics is None
    with pytest.raises(ValueError, match="贝扫用时"):
        sts_report.execute(None, "analyze_sts_statistics", {"metric": "bay_scan"})


def test_agent_groups_report_columns_into_table_and_chart(sts_report):
    by_crane = sts_report.execute(None, "query_sts_data",
                                  {"group_by": "桥吊编号", "operation": "count", "report_format": "chart"})
    assert [(point["label"], point["value"]) for point in by_crane["points"]] == [
        ("H105", 3), ("H101", 2),
    ]
    assert sts_report.sts_analysis_report["format"] == "chart"
    assert sts_report.sts_analysis_report["unit"] == "条"
    waits = sts_report.execute(None, "query_sts_data",
                               {"group_by": "桥吊编号", "operation": "mean", "measure": "等集卡时间(s)",
                                "filter_field": "任务类型", "filter_value": "DSCH", "report_format": "table"})
    assert waits["matched_rows"] == 4
    assert waits["points"][0]["value"] == 10
    assert waits["points"][1]["value"] is None
    assert sts_report.sts_analysis_report["rows"][1][3] == 0
    assert sts_report.sts_analysis_report["format"] == "table"


def test_idle_simulator_subscription_is_reported_running_before_first_task():
    module = SimpleNamespace(_simulation=SimpleNamespace(_subscription=object()), get_simulation_status=lambda: {"running": False, "state": "IDLE"})
    assert scene_runtime.simulation_status(module) == {"running": True, "state": "IDLE"}


def test_async_sensor_failure_is_reported_with_the_operation_id(runtime):
    import asyncio

    async def run():
        async def fail():
            raise RuntimeError("RTX sensor dependency missing")
        stage = object()
        runtime.bind(stage)
        operation = runtime.job("lidar_yard", fail())
        await asyncio.sleep(0)
        result = runtime.execute(stage, "get_operation_status", {"operation_id": operation["operation_id"]})
        assert result["status"] == "failed"
        assert result["error"] == "RTX sensor dependency missing"
    asyncio.run(run())


def test_async_capture_can_be_cancelled_without_a_completed_result(runtime):
    import asyncio

    async def run():
        async def capture():
            await asyncio.Event().wait()
        stage = object()
        runtime.bind(stage)
        operation = runtime.job("lidar_yard", capture())
        await asyncio.sleep(0)
        runtime.execute(stage, "cancel_operation", {"operation_id": operation["operation_id"]})
        await asyncio.sleep(0)
        result = runtime.execute(stage, "get_operation_status", {"operation_id": operation["operation_id"]})
        assert result["status"] == "cancelled"
        assert "result" not in result
    asyncio.run(run())


def test_slot_pick_owns_rtg_axes_until_its_controller_is_stopped(runtime, monkeypatch):
    stage = install_rtg(monkeypatch)
    module = SimpleNamespace(_PICK_SEQUENCE_SUBSCRIPTIONS={id(stage): object()},
                             _PICK_SEQUENCE_STATES={id(stage): {"phase": "lowering", "bay_id": "4C/040"}})
    monkeypatch.setitem(sys.modules, "rtg_pick_place_controller", module)
    with pytest.raises(ValueError, match="slot-pick"):
        runtime.execute(stage, "set_rtg_axis_positions", {"trolley_m": 6})
    status = scene_runtime.auxiliary_rtg_motion(stage)
    assert status["slot_pick"]["state"]["phase"] == "lowering"
    assert stage.positions["trolley_m"] == 2


def test_stop_all_lidar_stops_both_the_yard_pair_and_the_fleet(runtime, monkeypatch):
    runtime.bind(object())
    stopped = []
    yard = SimpleNamespace(stop_rtg_yard_lidar=lambda: stopped.append("yard"))
    fleet = SimpleNamespace(stop_rtg_lidar_fleet=lambda: stopped.append("fleet"))
    monkeypatch.setattr(project_interfaces, "load", lambda name: yard if name == "rtg_yard_lidar" else fleet)
    project_interfaces.execute_project_interface(runtime, "control_lidar", {"action": "stop", "mode": "all"})
    assert set(stopped) == {"yard", "fleet"}
