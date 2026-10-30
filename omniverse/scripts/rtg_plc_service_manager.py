"""Fixed local PLC service lifecycle, usable from Kit without importing pyzmq."""
from __future__ import annotations

import asyncio
import atexit
import builtins
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid


MANAGER_KEY = "_smart_port_rtg_plc_service_manager"
ROOT = Path(__file__).resolve().parents[2]


def child_environment():
    # Kit's Python environment must not be inherited by the external interpreter.
    return {key: value for key, value in os.environ.items()
            if key.upper() not in {"PYTHONHOME", "PYTHONPATH"}}


def process_options():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def find_service_python(config):
    configured = os.environ.get("SMARTPORT_PLC_PYTHON") or config.get("service_python")
    if configured:
        candidates = [str(configured)]
    else:
        candidates = [shutil.which("python"), sys.executable]
        executable = "python.exe" if os.name == "nt" else "python3"
        candidates += [str(Path(folder) / executable) for folder in os.get_exec_path()]
        candidates += [str(Path.home() / folder / executable) for folder in ("anaconda3", "miniconda3")]
    visited = set()
    for candidate in candidates:
        if not candidate or candidate in visited or not Path(candidate).is_file():
            continue
        visited.add(candidate)
        # Never attempt to start another Kit application as a Python probe.
        if Path(candidate).stem.lower() not in {"python", "python3", "pythonw"}:
            continue
        try:
            result = subprocess.run([candidate, "-I", "-c", "import zmq"],
                                    env=child_environment(), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=5, **process_options())
            if result.returncode == 0:
                return candidate
        except (OSError, subprocess.TimeoutExpired):
            continue
    raise RuntimeError("找不到安装了 pyzmq 的外部 Python。请在 services/rtg_plc_zmq_service.config.json "
                       "中配置 service_python 为 Python 的绝对路径，并用该 Python 安装 services/requirements.txt。")


class PlcServiceManager:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.config_path = self.root / "services/rtg_plc_zmq_service.config.json"
        self.process = None
        self.ready_file = None
        self.log_path = self.root / "outputs/plc_zmq/agent_service.log"
        self.lock = asyncio.Lock()
        self.python = None
        self.ports = None

    def config(self):
        return json.loads(self.config_path.read_text(encoding="utf-8-sig"))

    def status(self):
        running = self.process is not None and self.process.poll() is None
        ready = False
        if running and self.ready_file and self.ready_file.is_file():
            try:
                packet = json.loads(self.ready_file.read_text(encoding="utf-8"))
                ready = packet.get("pid") == self.process.pid
                if ready:
                    self.ports = {key: packet[key] for key in ("command_port", "state_port", "scene_port")}
            except (OSError, ValueError):
                pass
        return {"managed_running": running, "ready": ready,
                "pid": self.process.pid if running else None, "ports": self.ports,
                "python": self.python, "log_file": str(self.log_path),
                "scope": "Only the service launched by this Kit Agent is managed; external processes are never stopped."}

    def _stop_owned(self):
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        self.process = None
        if self.ready_file:
            self.ready_file.unlink(missing_ok=True)
            self.ready_file.with_suffix(".tmp").unlink(missing_ok=True)
        self.ready_file = None

    async def start(self):
        async with self.lock:
            if self.status()["ready"]:
                return {**self.status(), "started": False}
            self._stop_owned()
            config = self.config()
            # Probes run off the Kit main thread and cannot launch the service.
            self.python = await asyncio.to_thread(find_service_python, config)
            self.ports = {key: config.get(key, default) for key, default in
                          (("command_port", 5515), ("state_port", 5516), ("scene_port", 48720))}
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.ready_file = self.log_path.parent / ("agent-" + uuid.uuid4().hex + ".ready.json")
            try:
                with self.log_path.open("w", encoding="utf-8") as log:
                    self.process = subprocess.Popen(
                        [self.python, "-I", "-X", "utf8", "-u", str(self.root / "services/rtg_plc_zmq_service.py"),
                         "--config", str(self.config_path), "--ready-file", str(self.ready_file)],
                        cwd=str(self.root), env=child_environment(), stdin=subprocess.DEVNULL,
                        stdout=log, stderr=subprocess.STDOUT, **process_options())
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    if self.process.poll() is not None:
                        detail = self.log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
                        raise RuntimeError("ZMQ 服务启动失败。端口被外部服务占用时，请使用 connect 连接已有服务，"
                                           "或先在原终端停止它。日志：" + detail)
                    if self.status()["ready"]:
                        return {**self.status(), "started": True}
                    await asyncio.sleep(.05)
                raise RuntimeError("ZMQ 服务启动超时，见 " + str(self.log_path))
            except BaseException:
                self._stop_owned()
                raise

    async def stop(self):
        async with self.lock:
            self._stop_owned()
            return self.status()


def get_plc_service_manager():
    manager = getattr(builtins, MANAGER_KEY, None)
    if manager is None:
        manager = PlcServiceManager()
        setattr(builtins, MANAGER_KEY, manager)
        atexit.register(manager._stop_owned)
    return manager
