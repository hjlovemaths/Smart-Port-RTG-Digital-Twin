"""Lifecycle regression tests using child processes and real ephemeral sockets."""
import asyncio
import json
from pathlib import Path
import shutil
import socket
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "omniverse/scripts"))
sys.path.insert(0, str(ROOT / "omniverse/extensions/smartport.agent_panel"))
import rtg_plc_service_manager as lifecycle
from smartport.agent_panel import agent_api, plc_interface


@pytest.fixture
def manager(tmp_path, monkeypatch):
    pytest.importorskip("zmq")
    for filename in ("services/rtg_plc_zmq_service.py", "omniverse/scripts/rtg_plc_protocol.py"):
        target = tmp_path / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / filename, target)
    (tmp_path / "services/rtg_plc_zmq_service.config.json").write_text(json.dumps({
        "bind_host": "127.0.0.1", "command_port": 0, "state_port": 0, "scene_port": 0,
    }), encoding="utf-8")
    monkeypatch.setattr(lifecycle, "find_service_python", lambda _config: sys.executable)
    instance = lifecycle.PlcServiceManager(tmp_path)
    try:
        yield instance
    finally:
        instance._stop_owned()


def test_actual_child_binds_all_ports_reuses_pid_and_stops(manager):
    import zmq

    async def check():
        result = await manager.start()
        assert result["started"] and result["ready"]
        assert result["pid"] == manager.process.pid
        assert all(result["ports"].values())
        again = await manager.start()
        assert not again["started"] and again["pid"] == result["pid"]
        context = zmq.Context()
        subscriber = context.socket(zmq.SUB)
        subscriber.setsockopt(zmq.SUBSCRIBE, b"")
        subscriber.setsockopt(zmq.LINGER, 0)
        try:
            subscriber.connect("tcp://127.0.0.1:" + str(result["ports"]["state_port"]))
            assert subscriber.poll(2500)
            packet = subscriber.recv_json()
            assert packet["version"]["product"] == "smartport_rtg_plc_simulator"
            assert packet["msg_type"] == 3  # No Kit scene; never fabricate positions.
        finally:
            subscriber.close()
            context.term()
        child, ready_file = manager.process, manager.ready_file
        stopped = await manager.stop()
        assert child.poll() is not None
        assert not stopped["managed_running"] and not ready_file.exists()

    asyncio.run(check())


def test_port_conflict_reports_failure_and_leaves_external_listener_untouched(manager):
    with socket.socket() as external:
        external.bind(("127.0.0.1", 0))
        external.listen()
        config = manager.config()
        config["command_port"] = external.getsockname()[1]
        manager.config_path.write_text(json.dumps(config), encoding="utf-8")
        with pytest.raises(RuntimeError, match="connect"):
            asyncio.run(manager.start())
        assert not manager.status()["managed_running"]
        with socket.create_connection(external.getsockname(), timeout=1):
            accepted, _ = external.accept()
            accepted.close()
        asyncio.run(manager.stop())
        assert external.fileno() != -1


def test_cancelling_start_cleans_up_unready_child(manager):
    (manager.root / "services/rtg_plc_zmq_service.py").write_text("import time; time.sleep(30)", encoding="utf-8")

    async def check():
        task = asyncio.create_task(manager.start())
        for _ in range(100):
            if manager.process is not None:
                break
            await asyncio.sleep(.01)
        child = manager.process
        assert child is not None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert child.poll() is not None
        assert manager.ready_file is None

    asyncio.run(check())


def test_interpreter_probe_is_fixed_hidden_and_removes_kit_python_environment(tmp_path, monkeypatch):
    candidate = tmp_path / "python.exe"
    candidate.touch()
    monkeypatch.setenv("PYTHONHOME", "kit-home")
    monkeypatch.setenv("PYTHONPATH", "kit-libs")
    monkeypatch.delenv("SMARTPORT_PLC_PYTHON", raising=False)
    seen = []

    def probe(command, **kwargs):
        seen.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(lifecycle.subprocess, "run", probe)
    assert lifecycle.find_service_python({"service_python": str(candidate)}) == str(candidate)
    command, options = seen[0]
    assert command == [str(candidate), "-I", "-c", "import zmq"]
    assert "PYTHONHOME" not in options["env"] and "PYTHONPATH" not in options["env"]
    assert "shell" not in options
    if sys.platform == "win32":
        assert options["creationflags"] == lifecycle.subprocess.CREATE_NO_WINDOW


@pytest.mark.parametrize("args", [{"action": "exec"}, {"action": "start", "command": "anything"},
                                  {"action": "start", "python": "arbitrary.exe"}])
def test_agent_service_tool_does_not_accept_arbitrary_execution(args):
    with pytest.raises(ValueError):
        agent_api.validate_tool_arguments("control_plc_zmq", args)


class FakeManager:
    ports = {"scene_port": 48720}

    def __init__(self):
        self.running = False
        self.starts = self.stops = 0

    def config(self):
        return self.ports

    def status(self):
        return {"ready": self.running, "managed_running": self.running}

    async def start(self):
        self.starts += 1
        started, self.running = not self.running, True
        return {"started": started}

    async def stop(self):
        self.stops += 1
        self.running = False


class FakeAdapter:
    def __init__(self):
        self.running = self.connected = False
        self.starts = self.stops = 0
        self.fail = False
        self.external = True
        self.manager = None
        self.physics = self.armed = False

    def get_rtg_plc_status(self):
        self.connected = self.running and (self.external or bool(self.manager and self.manager.running))
        return {"running": self.running, "connected": self.connected, "scene_ready": False,
                "scene_port": 48720, "armed": self.armed, "error": None, "physics_enabled": self.physics}

    def start_rtg_plc_driver(self, port):
        assert port == 48720
        if self.fail:
            raise RuntimeError("adapter unavailable")
        self.starts += 1
        self.running = self.connected = True

    def stop_rtg_plc_driver(self):
        self.stops += 1
        self.running = self.connected = False

    def set_rtg_plc_physics(self, enabled):
        self.physics = self.armed = enabled


def invoke(manager, adapter, action):
    adapter.manager = manager
    owner = SimpleNamespace(job=lambda _kind, coroutine: coroutine)
    modules = {"rtg_plc_service_manager": SimpleNamespace(get_plc_service_manager=lambda: manager),
               "rtg_plc_scene_driver": adapter}
    return plc_interface.control_plc_zmq(owner, {"action": action}, modules.__getitem__)


def test_agent_connect_only_reuses_external_service_and_reports_missing_scene():
    manager, adapter = FakeManager(), FakeAdapter()
    result = asyncio.run(invoke(manager, adapter, "connect"))
    assert adapter.starts == 1 and manager.starts == 0
    assert not result["ready_for_commands"] and not result["adapter"]["armed"]
    asyncio.run(invoke(manager, adapter, "start"))
    asyncio.run(invoke(manager, adapter, "start"))
    assert adapter.starts == 1
    asyncio.run(invoke(manager, adapter, "stop"))
    assert not adapter.running and not manager.running


def test_agent_does_not_launch_a_service_when_adapter_cannot_start():
    manager, adapter = FakeManager(), FakeAdapter()
    adapter.fail = True
    with pytest.raises(RuntimeError, match="adapter unavailable"):
        asyncio.run(invoke(manager, adapter, "start"))
    assert manager.starts == manager.stops == 0 and not manager.running


def test_agent_preserves_existing_service_when_adapter_cannot_start():
    manager, adapter = FakeManager(), FakeAdapter()
    manager.running = True
    adapter.fail = True
    with pytest.raises(RuntimeError):
        asyncio.run(invoke(manager, adapter, "start"))
    assert manager.running and manager.stops == 0


def test_agent_status_is_read_only_and_disconnect_leaves_service_running():
    manager, adapter = FakeManager(), FakeAdapter()
    manager.running = adapter.running = adapter.connected = True
    result = invoke(manager, adapter, "status")
    assert result["service"]["managed_running"]
    assert adapter.starts == adapter.stops == manager.starts == manager.stops == 0
    invoke(manager, adapter, "disconnect")
    assert manager.running and not adapter.running


def test_agent_start_reuses_connected_external_service_without_duplicate_bind():
    manager, adapter = FakeManager(), FakeAdapter()
    result = asyncio.run(invoke(manager, adapter, "start"))
    assert manager.starts == manager.stops == 0
    assert result["service"]["ready"] and result["service"]["externally_managed"]
    assert not result["service"]["managed_ready"]
    assert not result["adapter"]["armed"]


def test_agent_launches_service_when_no_compatible_service_connects():
    manager, adapter = FakeManager(), FakeAdapter()
    adapter.external = False
    result = asyncio.run(invoke(manager, adapter, "start"))
    assert manager.starts == 1 and adapter.starts == 1
    assert result["service"]["managed_running"] and result["adapter"]["connected"]


def test_agent_physics_has_explicit_allowlisted_actions_without_starting_other_physics():
    manager, adapter = FakeManager(), FakeAdapter()
    adapter.running = True
    for action, expected in (("physics_on", True), ("physics_off", False)):
        validated = agent_api.validate_tool_arguments("control_plc_zmq", {"action": action})
        assert validated["action"] == action
        result = invoke(manager, adapter, action)
        assert result["adapter"]["physics_enabled"] is expected
    assert manager.starts == manager.stops == adapter.starts == adapter.stops == 0
