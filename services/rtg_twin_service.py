"""
Standalone RTG twin service.

This service replaces the WPF page as the runtime data bridge:

    PLC ZMQ + Task ZMQ
        -> cached latest PLC pose + cached current task
        -> newline-delimited TwinState JSON on 127.0.0.1:48700
        -> Omniverse rtg_twin_state_receiver.py
        -> RTGController drives the primary RTG

The Omniverse receiver is a TCP client, so this service is intentionally a TCP
server using the same TwinState JSON schema that the WPF bridge publishes.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import socket
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

try:
    import zmq
except ImportError:  # pragma: no cover - handled at runtime for deployment clarity
    zmq = None


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "rtg_twin_service.config.json"
SCHEMA = "smart-port-rtg-twin-state/v1"
BAY_MAP_PATTERN = re.compile(r"^\d{6}$")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_json_maybe(value: Any) -> Any:
    """Parse nested JSON strings used by RCMS wrapper messages."""
    current = value
    for _ in range(3):
        if isinstance(current, (bytes, bytearray)):
            current = current.decode("utf-8", errors="replace")
        if isinstance(current, str):
            text = current.strip()
            if not text:
                return None
            try:
                current = json.loads(text)
                continue
            except json.JSONDecodeError:
                return current
        return current
    return current


def finite_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def text_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def bay_map_layers(bay_map: Any) -> list[int]:
    text = text_value(bay_map) or ""
    if text and not BAY_MAP_PATTERN.fullmatch(text):
        return []
    return [int(ch) if ch.isdigit() else 0 for ch in text]


def invalid_task_bay_map(task: Optional[Dict[str, Any]]) -> Optional[tuple[str, str]]:
    """Return the first malformed map field, while allowing an omitted map."""

    if not task:
        return None
    for key in ("bay_map", "target_bay_map", "targetBayMap"):
        value = text_value(task.get(key))
        if value is not None and not BAY_MAP_PATTERN.fullmatch(value):
            return key, value
    return None


def is_task_active(task: Optional[Dict[str, Any]]) -> bool:
    if not task:
        return False
    if int_value(task.get("task_state"), 0) == 0 and int_value(task.get("task_type"), 0) == 0:
        return False
    return any(
        text_value(task.get(key))
        for key in ("task_id", "task_startpos", "task_endpos", "task_cntrno", "bay_map")
    )


def unwrap_plc_message(message: Any) -> Optional[Dict[str, Any]]:
    data = parse_json_maybe(message)
    if not isinstance(data, dict):
        return None

    # RCMS PLC wrapper: { msg_type: 1, content: "{...plc...}" }
    if "msg_type" in data and "content" in data:
        if int_value(data.get("msg_type"), 0) != 1:
            return None
        content = parse_json_maybe(data.get("content"))
        return content if isinstance(content, dict) else None

    # Raw PLC model.
    if (
        "current_bay_num" in data
        or "smallcar_pos" in data
        or "lifting_pos" in data
        or "work_mode" in data
        or "workMode" in data
    ):
        return data
    return None


def unwrap_core_message(message: Any) -> tuple[str, Optional[Dict[str, Any]]]:
    data = parse_json_maybe(message)
    if not isinstance(data, dict):
        return "", None

    cmd_id = text_value(data.get("cmd_id")) or ""
    content = parse_json_maybe(data.get("content")) if "content" in data else data
    return cmd_id, content if isinstance(content, dict) else None


def is_task_complete_runinfo(cmd_id: str, content: Optional[Dict[str, Any]]) -> bool:
    if cmd_id != "cmd_rcms_runinfo" or not content:
        return False
    desc = _runinfo_description_value(content) or ""
    return "本关任务完成" in desc or "任务完成" in desc


def _runinfo_description_value(content: Dict[str, Any]) -> Optional[str]:
    for key in ("msg_descption", "msg_description", "msg_descptionn"):
        value = text_value(content.get(key))
        if value:
            return value
    return None


def _runinfo_state_value(content: Dict[str, Any]) -> int:
    for key in ("run_state", "runState", "msg_type", "msgType"):
        if key in content:
            return int_value(content.get(key), 0)
    return 0


def _runinfo_timespan_value(content: Dict[str, Any]) -> Optional[str]:
    for key in ("happentime", "timespan", "timeSpan", "msg_timespan", "msgTimeSpan"):
        value = text_value(content.get(key))
        if value:
            return value
    return None


@dataclass
class ServiceConfig:
    rtg_name: str
    publish_host: str
    publish_port: int
    publish_hz: float
    plc_enabled: bool
    plc_host: str
    plc_port: int
    core_enabled: bool
    core_host: str
    core_port: int
    plc_stale_seconds: float
    task_stale_seconds: float

    @staticmethod
    def load(path: Path) -> "ServiceConfig":
        raw = json.loads(path.read_text(encoding="utf-8"))
        publish = raw.get("publish") or {}
        plc = raw.get("plc") or {}
        core = raw.get("core") or {}
        timeouts = raw.get("timeouts") or {}
        return ServiceConfig(
            rtg_name=text_value(raw.get("rtgName")) or "RTG_220",
            publish_host=text_value(publish.get("host")) or "127.0.0.1",
            publish_port=int_value(publish.get("port"), 48700),
            publish_hz=max(1.0, finite_float(publish.get("hz"), 10.0)),
            plc_enabled=bool(plc.get("enabled", True)),
            plc_host=text_value(plc.get("host")) or "127.0.0.1",
            plc_port=int_value(plc.get("port"), 5516),
            core_enabled=bool(core.get("enabled", True)),
            core_host=text_value(core.get("host")) or "127.0.0.1",
            core_port=int_value(core.get("port"), 7065),
            plc_stale_seconds=max(0.0, finite_float(timeouts.get("plcStaleSeconds"), 3.0)),
            task_stale_seconds=max(0.0, finite_float(timeouts.get("taskStaleSeconds"), 1800.0)),
        )


class TwinStateCache:
    def __init__(self, rtg_name: str, plc_stale_seconds: float, task_stale_seconds: float):
        self.rtg_name = rtg_name
        self.plc_stale_seconds = plc_stale_seconds
        self.task_stale_seconds = task_stale_seconds
        self._lock = threading.Lock()
        self._plc: Optional[Dict[str, Any]] = None
        self._plc_at: Optional[float] = None
        self._task: Optional[Dict[str, Any]] = None
        self._task_at: Optional[float] = None
        self._heartbeat: Optional[Dict[str, Any]] = None
        self._heartbeat_at: Optional[float] = None
        self._last_runinfo: Optional[str] = None
        self._last_runinfo_at: Optional[float] = None
        self._last_runinfo_timespan: Optional[str] = None
        self._last_runinfo_state: Optional[Any] = None

    def update_plc(self, plc_model: Dict[str, Any]) -> None:
        with self._lock:
            # PLC pose and heartbeat fields may arrive as separate messages.
            # Merge them so a heartbeat-only frame does not erase the latest
            # position, and a pose-only frame does not erase work_mode.
            merged = dict(self._plc or {})
            merged.update(plc_model)
            self._plc = merged
            self._plc_at = time.time()

    def update_heartbeat(self, heartbeat_model: Optional[Dict[str, Any]]) -> None:
        if not heartbeat_model:
            return
        with self._lock:
            self._heartbeat = dict(heartbeat_model)
            self._heartbeat_at = time.time()

    def update_task(self, task_model: Optional[Dict[str, Any]]) -> bool:
        invalid_map = invalid_task_bay_map(task_model)
        if invalid_map is not None:
            field, value = invalid_map
            task_id = text_value((task_model or {}).get("task_id")) or "<unknown>"
            logging.warning(
                "rejecting task=%s: %s=%r is invalid; expected exactly six digits "
                "(one height digit for each yard row)",
                task_id,
                field,
                value,
            )
            return False
        with self._lock:
            if is_task_active(task_model):
                self._task = dict(task_model or {})
                self._task_at = time.time()
            else:
                self._task = None
                self._task_at = None
        return True

    def clear_task(self) -> None:
        with self._lock:
            self._task = None
            self._task_at = None

    def complete_task(self) -> bool:
        """Publish one durable terminal task instead of dropping its identity."""

        with self._lock:
            if self._task is None:
                return False
            self._task = dict(self._task)
            self._task["_twin_operation_state"] = "COMPLETED"
            self._task["_twin_completed_at"] = utc_now_iso()
            self._task_at = time.time()
            return True

    def update_runinfo(self, content: Optional[Dict[str, Any]]) -> None:
        """Store the latest runinfo description for the HUD overlay."""
        if not content:
            return
        desc = _runinfo_description_value(content) or ""
        if not desc:
            return
        run_state = _runinfo_state_value(content)
        timespan = _runinfo_timespan_value(content)
        with self._lock:
            is_new = (
                self._last_runinfo != desc
                or self._last_runinfo_timespan != timespan
                or self._last_runinfo_state != run_state
            )
            self._last_runinfo = desc
            self._last_runinfo_timespan = timespan
            self._last_runinfo_state = run_state
            self._last_runinfo_at = time.time()
        if is_new:
            logging.info("runinfo: %s", desc)

    def build_state(self) -> Dict[str, Any]:
        with self._lock:
            plc = dict(self._plc) if self._plc else None
            task = dict(self._task) if self._task else None
            heartbeat = dict(self._heartbeat) if self._heartbeat else None
            plc_at = self._plc_at
            task_at = self._task_at

        now = time.time()
        plc_age = None if plc_at is None else max(0.0, now - plc_at)
        task_age = None if task_at is None else max(0.0, now - task_at)
        plc_stale = plc_age is None or (
            self.plc_stale_seconds > 0 and plc_age > self.plc_stale_seconds
        )
        task_stale = task_age is not None and (
            self.task_stale_seconds > 0 and task_age > self.task_stale_seconds
        )

        if task_stale:
            task = None

        return {
            "schema": SCHEMA,
            "timestampUtc": utc_now_iso(),
            "source": "RTG.Twin.Service",
            "rtgName": self.rtg_name,
            "workMode": self._heartbeat_work_mode(heartbeat, plc),
            "heartbeat": self._build_heartbeat(heartbeat, plc),
            "pose": self._build_pose(plc, heartbeat, plc_age, plc_stale),
            "task": self._build_task(task, task_age),
            "runinfo": self._build_runinfo(),
        }

    def _build_heartbeat(
        self,
        heartbeat: Optional[Dict[str, Any]],
        plc: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Expose the RCMS service flags to consumers such as the WPF client."""
        source = heartbeat or plc or {}
        return {
            "rtgState": int_value(source.get("rtg_state", source.get("rtgState")), 0),
            "ctrlOn": int_value(source.get("ctrl_on", source.get("ctrlOn")), 0),
            "workMode": self._heartbeat_work_mode(heartbeat, plc),
            "commState": int_value(source.get("comm_state", source.get("commState")), 0),
            "taskState": int_value(source.get("task_state", source.get("taskState")), 0),
            "iecsServer": int_value(source.get("iecs_server", source.get("iecsServer")), 0),
            "ctrlServer": int_value(source.get("ctrl_server", source.get("ctrlServer")), 0),
            "plcServer": int_value(source.get("plc_server", source.get("plcServer")), 0),
            "gdServer": int_value(source.get("gd_server", source.get("gdServer")), 0),
            "viServer": int_value(source.get("vi_server", source.get("viServer")), 0),
            "sfServer": int_value(source.get("sf_server", source.get("sfServer")), 0),
        }

    def _build_pose(
        self,
        plc: Optional[Dict[str, Any]],
        heartbeat: Optional[Dict[str, Any]],
        plc_age: Optional[float],
        plc_stale: bool,
    ) -> Optional[Dict[str, Any]]:
        if plc is None:
            return None
        return {
            "currentField": text_value(plc.get("current_field_num")),
            "currentBay": int_value(plc.get("current_bay_num"), 0),
            "bigcarElecDistance": finite_float(plc.get("bigcar_elec_distance"), 0.0),
            "bigcarEngineDistance": finite_float(plc.get("bigcar_engine_distance"), 0.0),
            "trolleyMeter": finite_float(plc.get("smallcar_pos"), 0.0),
            "hoistMeter": finite_float(plc.get("lifting_pos"), 0.0),
            "lockState": int_value(plc.get("lock_state"), 0),
            "unlockState": int_value(plc.get("unlock_state"), 0),
            "spreaderRow": int_value(plc.get("spreader_row"), 0),
            "tool20f": int_value(plc.get("tool_20f"), 0),
            "tool40f": int_value(plc.get("tool_40f"), 0),
            "workMode": self._heartbeat_work_mode(heartbeat, plc),
            "plcAgeSeconds": round(plc_age, 3) if plc_age is not None else None,
            "plcStale": plc_stale,
        }

    @staticmethod
    def _heartbeat_work_mode(
        heartbeat: Optional[Dict[str, Any]],
        plc: Optional[Dict[str, Any]],
    ) -> int:
        # RCMS heartbeat work mode: 0 unknown, 1 automatic, 2 assisted,
        # 3 remote manual, 4 local, 5 maintenance.
        for source in (heartbeat, plc):
            if not source:
                continue
            for key in ("work_mode", "workMode"):
                if source.get(key) is not None:
                    return int_value(source.get(key), 0)
        return 0

    def _build_task(
        self,
        task: Optional[Dict[str, Any]],
        task_age: Optional[float],
    ) -> Optional[Dict[str, Any]]:
        if task is None:
            return None
        bay_map = text_value(task.get("bay_map"))
        bay_slot_heights = parse_json_maybe(
            task.get("bay_slot_heights")
            or task.get("baySlotHeights")
        )
        target_bay_map = text_value(
            task.get("target_bay_map")
            or task.get("targetBayMap")
        )
        return {
            "taskId": text_value(task.get("task_id")),
            "taskState": int_value(task.get("task_state"), 0),
            "taskType": int_value(task.get("task_type"), 0),
            "workType": text_value(task.get("task_work_type")),
            "startPos": text_value(task.get("task_startpos")),
            "endPos": text_value(task.get("task_endpos")),
            "containerNo": text_value(task.get("task_cntrno")),
            "containerType": text_value(task.get("cntr_type")),
            "containerHeight": text_value(task.get("cntr_height")),
            "containerSize": int_value(task.get("cntr_size"), 0),
            "spreaderPos": text_value(task.get("spreader_pos")),
            "carType": text_value(task.get("car_type")),
            "carNo": text_value(task.get("car_no")),
            "bayMap": bay_map,
            "targetBayMap": target_bay_map,
            "bayMapLayers": bay_map_layers(bay_map),
            "baySlotHeights": bay_slot_heights,
            "operationState": text_value(task.get("_twin_operation_state")),
            "completedAtUtc": text_value(task.get("_twin_completed_at")),
            "taskAgeSeconds": round(task_age, 3) if task_age is not None else None,
        }

    def _build_runinfo(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            message = self._last_runinfo
            at_time = self._last_runinfo_at
            timespan = self._last_runinfo_timespan
            run_state = self._last_runinfo_state
        if message is None:
            return None
        return {
            "message": message,
            "timespan": timespan,
            "runState": run_state,
            "receivedAtUtc": (
                datetime.fromtimestamp(at_time, tz=timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z")
                if at_time is not None
                else None
            ),
        }


class TcpTwinStatePublisher:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[socket.socket] = None
        self._latest_payload: Optional[bytes] = None

    def start(self) -> None:
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self.host, self.port))
        self._server.listen()
        self._server.settimeout(1.0)
        self._thread = threading.Thread(target=self._accept_loop, name="TwinStateTcpPublisher", daemon=True)
        self._thread.start()
        logging.info("TwinState publisher listening on %s:%s", self.host, self.port)

    def stop(self) -> None:
        self._stop.set()
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass
        with self._lock:
            clients = list(self._clients)
            self._clients.clear()
        for client in clients:
            try:
                client.close()
            except OSError:
                pass

    def publish(self, state: Dict[str, Any]) -> None:
        payload = (json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        self._latest_payload = payload
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.sendall(payload)
            except OSError:
                self._remove_client(client)

    def _accept_loop(self) -> None:
        assert self._server is not None
        while not self._stop.is_set():
            try:
                client, address = self._server.accept()
                client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                with self._lock:
                    self._clients.append(client)
                logging.info("Omniverse receiver connected from %s:%s", *address)
                if self._latest_payload:
                    try:
                        client.sendall(self._latest_payload)
                    except OSError:
                        self._remove_client(client)
            except socket.timeout:
                continue
            except OSError:
                if not self._stop.is_set():
                    logging.exception("TCP accept loop failed")
                break

    def _remove_client(self, client: socket.socket) -> None:
        with self._lock:
            if client in self._clients:
                self._clients.remove(client)
        try:
            client.close()
        except OSError:
            pass


class ZmqSubscriber:
    def __init__(self, name: str, host: str, port: int, handler):
        self.name = name
        self.host = host
        self.port = port
        self.handler = handler
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if zmq is None:
            raise RuntimeError("pyzmq is not installed. Install with: pip install -r services/requirements.txt")
        self._thread = threading.Thread(target=self._run, name=f"ZmqSubscriber-{self.name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        assert zmq is not None
        url = f"tcp://{self.host}:{self.port}"
        context = zmq.Context.instance()
        while not self._stop.is_set():
            socket_obj = context.socket(zmq.SUB)
            socket_obj.setsockopt_string(zmq.SUBSCRIBE, "")
            socket_obj.setsockopt(zmq.RCVTIMEO, 1000)
            try:
                socket_obj.connect(url)
                logging.info("%s subscriber connected to %s", self.name, url)
                while not self._stop.is_set():
                    try:
                        parts = socket_obj.recv_multipart()
                    except zmq.Again:
                        continue
                    if not parts:
                        continue
                    self.handler(parts[-1])
            except Exception:
                logging.exception("%s subscriber error; reconnecting in 2s", self.name)
                time.sleep(2)
            finally:
                socket_obj.close(0)


class RtgTwinService:
    def __init__(
        self,
        config: ServiceConfig,
        *,
        print_plc_heartbeat: bool = False,
        print_core_heartbeat: bool = False,
    ):
        self.config = config
        self.print_plc_heartbeat = print_plc_heartbeat
        self.print_core_heartbeat = print_core_heartbeat
        self.cache = TwinStateCache(
            config.rtg_name,
            config.plc_stale_seconds,
            config.task_stale_seconds,
        )
        self.publisher = TcpTwinStatePublisher(config.publish_host, config.publish_port)
        self.subscribers: list[ZmqSubscriber] = []
        self._stop = threading.Event()
        self._last_log_at = 0.0

    def start(self) -> None:
        self.publisher.start()
        if self.config.plc_enabled:
            self.subscribers.append(
                ZmqSubscriber("PLC", self.config.plc_host, self.config.plc_port, self._handle_plc_message)
            )
        if self.config.core_enabled:
            self.subscribers.append(
                ZmqSubscriber("TASK", self.config.core_host, self.config.core_port, self._handle_core_message)
            )
        for subscriber in self.subscribers:
            subscriber.start()

    def stop(self) -> None:
        self._stop.set()
        for subscriber in self.subscribers:
            subscriber.stop()
        self.publisher.stop()

    def run_forever(self) -> None:
        self.start()
        interval = 1.0 / self.config.publish_hz
        try:
            while not self._stop.is_set():
                state = self.cache.build_state()
                self.publisher.publish(state)
                self._log_state_summary(state)
                time.sleep(interval)
        except KeyboardInterrupt:
            logging.info("Stopping by Ctrl+C")
        finally:
            self.stop()

    def _handle_plc_message(self, raw: bytes) -> None:
        plc = unwrap_plc_message(raw)
        if plc:
            if self.print_plc_heartbeat:
                logging.info(
                    "PLC heartbeat parsed: %s",
                    json.dumps(plc, ensure_ascii=False, separators=(",", ":")),
                )
            self.cache.update_plc(plc)
        elif self.print_plc_heartbeat:
            logging.warning(
                "PLC message received but could not parse: %s",
                raw.decode("utf-8", errors="replace")[:2000],
            )

    def _handle_core_message(self, raw: bytes) -> None:
        cmd_id, content = unwrap_core_message(raw)
        if cmd_id == "cmd_rcms_taskinfo":
            self.cache.update_task(content)
        elif cmd_id == "cmd_rcms_runinfo":
            self.cache.update_runinfo(content)
            if is_task_complete_runinfo(cmd_id, content):
                self.cache.complete_task()
        elif cmd_id == "cmd_rcms_heartbeat":
            if self.print_core_heartbeat:
                logging.info(
                    "RCMS heartbeat parsed: %s",
                    json.dumps(content, ensure_ascii=False, separators=(",", ":")),
                )
            self.cache.update_heartbeat(content)

    def _log_state_summary(self, state: Dict[str, Any]) -> None:
        now = time.time()
        if now - self._last_log_at < 2.0:
            return
        self._last_log_at = now
        pose = state.get("pose") or {}
        task = state.get("task") or {}
        logging.info(
            "publish rtg=%s field=%s bay=%s trolley=%s hoist=%s lock=%s workMode=%s task=%s/%s bayMap=%s stale=%s",
            state.get("rtgName"),
            pose.get("currentField"),
            pose.get("currentBay"),
            pose.get("trolleyMeter"),
            pose.get("hoistMeter"),
            pose.get("lockState"),
            pose.get("workMode", state.get("workMode")),
            task.get("taskId"),
            task.get("workType"),
            task.get("bayMap"),
            pose.get("plcStale"),
        )


def load_test_json(path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    data = parse_json_maybe(Path(path).read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Smart Port RTG Twin standalone bridge service")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to service JSON config")
    parser.add_argument("--plc-json", help="Optional PLC wrapper/raw JSON file for offline testing")
    parser.add_argument("--task-json", help="Optional task wrapper/raw JSON file for offline testing")
    parser.add_argument("--once", action="store_true", help="Print one TwinState JSON and exit")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    parser.add_argument(
        "--print-plc-heartbeat",
        action="store_true",
        help="Print every parsed PLC heartbeat JSON (diagnostic; can be noisy).",
    )
    parser.add_argument(
        "--print-core-heartbeat",
        action="store_true",
        help="Print every parsed RCMS heartbeat JSON from the core/task endpoint (diagnostic; can be noisy).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    config = ServiceConfig.load(Path(args.config))
    service = RtgTwinService(
        config,
        print_plc_heartbeat=args.print_plc_heartbeat,
        print_core_heartbeat=args.print_core_heartbeat,
    )

    plc_raw = load_test_json(args.plc_json)
    if plc_raw:
        plc = unwrap_plc_message(plc_raw) or plc_raw
        service.cache.update_plc(plc)
    task_raw = load_test_json(args.task_json)
    if task_raw:
        cmd_id, task = unwrap_core_message(task_raw)
        service.cache.update_task(task if task else task_raw)

    if args.once:
        print(json.dumps(service.cache.build_state(), ensure_ascii=False, indent=2))
        return 0

    service.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
