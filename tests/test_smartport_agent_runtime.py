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

from smartport.agent_panel import agent_api, project_interfaces, runtime_catalog, scene_runtime


@pytest.mark.parametrize("name,args", [
    ("set_viewport_view", {"target": "bay"}),
    ("set_viewport_view", {"target": "sts"}),
    ("set_viewport_view", {"target": "rtg", "follow": "yes"}),
    ("switch_viewport_camera", {}),
    ("switch_viewport_camera", {"camera_path": "/World/Cam", "camera_number": 1}),
    ("switch_viewport_camera", {"camera_path": "file:/secrets"}),
    ("set_sts_axis_positions", {"crane_id": "STS_08", "gantry_m": 1}),
    ("set_sts_axis_positions", {"crane_id": "STS_04"}),
    ("set_sts_axis_positions", {"crane_id": "STS_04", "hoist_m": float("nan")}),
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


def test_equipment_task_and_multi_position_capture_preserve_confirmation_boundary():
    assert runtime_catalog.needs_approval("set_sts_axis_positions", {})
    assert runtime_catalog.needs_approval("control_sts_task", {"action": "start_unload"})
    assert runtime_catalog.needs_approval("capture_lidar", {"mode": "positions"})
    assert not runtime_catalog.needs_approval("get_current_tasks", {})
    assert not runtime_catalog.needs_approval("set_viewport_view", {"target": "rtg"})
    assert not runtime_catalog.needs_approval("capture_lidar", {"mode": "yard"})


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
    assert result["interface_count"] == 37
    assert any(item["name"] == "get_current_tasks" for item in result["interfaces"])


def test_documented_interface_names_and_machine_schemas_match_the_live_catalog():
    import json
    documentation = (ROOT / "omniverse/docs/AGENT_INTERFACES.md").read_text(encoding="utf-8")
    machine = json.loads((ROOT / "omniverse/extensions/smartport.agent_panel/docs/agent_interfaces.json").read_text(encoding="utf-8"))
    assert machine["tools"] == list(agent_api.TOOLS)
    for item in agent_api.TOOLS:
        assert "`" + item["function"]["name"] + "`" in documentation


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
