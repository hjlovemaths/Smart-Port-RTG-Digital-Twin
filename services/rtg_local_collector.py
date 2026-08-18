"""Local collector for messages forwarded from the jump host.

Run this script on the local PC.  It listens for newline-delimited JSON records
from ``rtg_jump_forwarder.py`` running on the jump host.

It can either just print/save the forwarded PLC/task/runinfo messages, or also
convert them to the TwinState JSON stream consumed by Omniverse:

    jump host rtg_jump_forwarder.py
        -> local PC rtg_local_collector.py :48800
        -> optional local TwinState server :48700
        -> Omniverse rtg_twin_state_receiver.py
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.append(str(SCRIPT_DIR))

from rtg_twin_service import (  # noqa: E402
    TcpTwinStatePublisher,
    TwinStateCache,
    is_task_complete_runinfo,
    parse_json_maybe,
)


DEFAULT_LISTEN_HOST = "0.0.0.0"
DEFAULT_LISTEN_PORT = 48800
DEFAULT_TWIN_HOST = "127.0.0.1"
DEFAULT_TWIN_PORT = 48700
DEFAULT_WPF_DEBUG_HOST = "127.0.0.1"
DEFAULT_WPF_DEBUG_PORT = 48701


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class JsonlWriter:
    def __init__(self, log_dir: Optional[Path]) -> None:
        self.log_dir = log_dir
        self._lock = threading.Lock()
        self._files: dict[str, Any] = {}
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        with self._lock:
            for handle in self._files.values():
                handle.close()
            self._files.clear()

    def write(self, stream: str, record: dict[str, Any]) -> None:
        if not self.log_dir:
            return
        with self._lock:
            handle = self._files.get(stream)
            if handle is None:
                path = self.log_dir / f"{stream}.jsonl"
                handle = path.open("a", encoding="utf-8")
                self._files[stream] = handle
            handle.write(compact_json(record) + "\n")
            handle.flush()


class ForwardedMessageServer:
    def __init__(self, host: str, port: int, handler, stop_event: threading.Event) -> None:
        self.host = host
        self.port = port
        self.handler = handler
        self.stop_event = stop_event
        self.thread = threading.Thread(target=self._run, name="ForwardedMessageServer", daemon=True)
        self._server: Optional[socket.socket] = None

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass

    def _run(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            self._server = server
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen()
            server.settimeout(1.0)
            logging.info("local collector listening on %s:%s", self.host, self.port)
            while not self.stop_event.is_set():
                try:
                    client, address = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if not self.stop_event.is_set():
                        logging.exception("collector accept failed")
                    return
                threading.Thread(
                    target=self._handle_client,
                    args=(client, address),
                    name=f"ForwardedClient-{address}",
                    daemon=True,
                ).start()

    def _handle_client(self, client: socket.socket, address: Any) -> None:
        logging.info("jump forwarder connected from %s", address)
        buffer = b""
        with client:
            client.settimeout(1.0)
            while not self.stop_event.is_set():
                try:
                    chunk = client.recv(65536)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    self._handle_line(line)
        logging.info("jump forwarder disconnected from %s", address)

    def _handle_line(self, line: bytes) -> None:
        if not line.strip():
            return
        try:
            record = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            logging.warning("bad forwarded JSON line: %r", line[:200])
            return
        if isinstance(record, dict):
            self.handler(record)


class LocalCollector:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.print_core_heartbeat = bool(getattr(args, "print_core_heartbeat", False))
        self.stop_event = threading.Event()
        self.cache = TwinStateCache(
            args.rtg_name,
            args.plc_stale_seconds,
            args.task_stale_seconds,
        )
        self.writer = JsonlWriter(Path(args.log_dir) if args.log_dir else None)
        self.server = ForwardedMessageServer(
            args.listen_host,
            args.listen_port,
            self._handle_record,
            self.stop_event,
        )
        self.publisher: Optional[TcpTwinStatePublisher] = None
        if args.publish_twin_state:
            self.publisher = TcpTwinStatePublisher(args.twin_host, args.twin_port)
        self.wpf_debug_publisher: Optional[TcpTwinStatePublisher] = None
        if args.publish_wpf_debug_state:
            self.wpf_debug_publisher = TcpTwinStatePublisher(
                args.wpf_debug_host, args.wpf_debug_port
            )
        self.counts = {"plc": 0, "task": 0, "runinfo": 0, "core_other": 0, "unknown": 0}
        self._last_log_at = 0.0

    def run_forever(self) -> None:
        self.server.start()
        if self.publisher:
            self.publisher.start()
            logging.info(
                "TwinState publisher enabled on %s:%s",
                self.args.twin_host,
                self.args.twin_port,
            )
        else:
            logging.info("TwinState publisher disabled")
        if self.wpf_debug_publisher:
            self.wpf_debug_publisher.start()
            logging.info(
                "WPF debug TwinState publisher enabled on %s:%s",
                self.args.wpf_debug_host,
                self.args.wpf_debug_port,
            )

        interval = 1.0 / max(1.0, float(self.args.publish_hz))
        try:
            while not self.stop_event.wait(interval):
                if self.publisher or self.wpf_debug_publisher:
                    state = self.cache.build_state()
                    if self.publisher:
                        self.publisher.publish(state)
                    if self.wpf_debug_publisher:
                        self.wpf_debug_publisher.publish(state)
                    self._log_twin_state(state)
        except KeyboardInterrupt:
            logging.info("stopping by Ctrl+C")
        finally:
            self.stop()

    def stop(self) -> None:
        self.stop_event.set()
        self.server.stop()
        self.writer.close()
        if self.publisher:
            self.publisher.stop()
        if self.wpf_debug_publisher:
            self.wpf_debug_publisher.stop()

    def _handle_record(self, record: dict[str, Any]) -> None:
        stream = str(record.get("stream") or "unknown")
        record = dict(record)
        record["receivedAtLocalUtc"] = utc_now_iso()
        self.counts[stream] = self.counts.get(stream, 0) + 1
        self.writer.write(stream, record)

        parsed = record.get("parsed")
        if parsed is None and record.get("raw") is not None:
            parsed = parse_json_maybe(record.get("raw"))

        if stream == "plc" and isinstance(parsed, dict):
            self.cache.update_plc(parsed)
        elif stream == "task" and isinstance(parsed, dict):
            self.cache.update_task(parsed)
        elif stream == "runinfo" and isinstance(parsed, dict):
            self.cache.update_runinfo(parsed)
            if is_task_complete_runinfo(str(record.get("cmdId") or ""), parsed):
                self.cache.complete_task()
        elif stream == "core_other" and str(record.get("cmdId") or "") == "cmd_rcms_heartbeat":
            if isinstance(parsed, dict):
                if self.print_core_heartbeat:
                    logging.info(
                        "RCMS heartbeat forwarded parsed: %s",
                        compact_json(parsed),
                    )
                self.cache.update_heartbeat(parsed)

        if self.args.echo:
            print(compact_json(record), flush=True)
        elif stream in ("task", "runinfo"):
            print(compact_json(record), flush=True)

    def _log_twin_state(self, state: dict[str, Any]) -> None:
        now = time.time()
        if now - self._last_log_at < 2.0:
            return
        self._last_log_at = now
        pose = state.get("pose") or {}
        task = state.get("task") or {}
        logging.info(
            "counts=%s twin field=%s bay=%s trolley=%s hoist=%s workMode=%s task=%s/%s bayMap=%s",
            self.counts,
            pose.get("currentField"),
            pose.get("currentBay"),
            pose.get("trolleyMeter"),
            pose.get("hoistMeter"),
            pose.get("workMode", state.get("workMode")),
            task.get("taskId"),
            task.get("workType"),
            task.get("bayMap"),
        )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Receive jump-host forwarded RTG messages on local PC."
    )
    parser.add_argument("--listen-host", default=DEFAULT_LISTEN_HOST)
    parser.add_argument("--listen-port", type=int, default=DEFAULT_LISTEN_PORT)
    parser.add_argument("--rtg-name", default="RTG_220")
    parser.add_argument(
        "--log-dir",
        default=str(SCRIPT_DIR / "logs" / "forwarded_messages"),
        help="Directory for plc.jsonl/task.jsonl/runinfo.jsonl. Empty disables logging.",
    )
    parser.add_argument("--echo", action="store_true", help="Print every forwarded record.")
    parser.add_argument(
        "--print-core-heartbeat",
        action="store_true",
        help="Print each forwarded parsed RCMS heartbeat JSON from core_other/cmd_rcms_heartbeat (diagnostic; can be noisy).",
    )
    parser.add_argument(
        "--publish-twin-state",
        action="store_true",
        help="Also publish TwinState JSON for Omniverse/WPF on the local PC.",
    )
    parser.add_argument("--twin-host", default=DEFAULT_TWIN_HOST)
    parser.add_argument("--twin-port", type=int, default=DEFAULT_TWIN_PORT)
    parser.add_argument(
        "--publish-wpf-debug-state",
        action="store_true",
        help="Also publish the same TwinState JSONL stream for local WPF debugging.",
    )
    parser.add_argument("--wpf-debug-host", default=DEFAULT_WPF_DEBUG_HOST)
    parser.add_argument("--wpf-debug-port", type=int, default=DEFAULT_WPF_DEBUG_PORT)
    parser.add_argument("--publish-hz", type=float, default=10.0)
    parser.add_argument("--plc-stale-seconds", type=float, default=3.0)
    parser.add_argument("--task-stale-seconds", type=float, default=1800.0)
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    collector = LocalCollector(args)

    def request_stop(_signum: int, _frame: Any) -> None:
        collector.stop()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    collector.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
