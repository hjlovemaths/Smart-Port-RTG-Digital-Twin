"""Local pull collector for RTG messages from the jump host.

Run this on the local PC when the jump host cannot actively connect back to the
local PC.  The local PC connects to the jump host relay server and receives
newline-delimited JSON records.

Jump host:

    python services/rtg_jump_relay_server.py --listen-host 0.0.0.0 --listen-port 48800

Local PC:

    python services/rtg_local_pull_collector.py --jump-host 10.10.10.25 --jump-port 48800 --publish-twin-state
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


DEFAULT_JUMP_HOST = "10.10.10.25"
DEFAULT_JUMP_PORT = 48800
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
                handle = (self.log_dir / f"{stream}.jsonl").open("a", encoding="utf-8")
                self._files[stream] = handle
            handle.write(compact_json(record) + "\n")
            handle.flush()


class JumpPullClient:
    def __init__(self, host: str, port: int, handler, stop_event: threading.Event) -> None:
        self.host = host
        self.port = port
        self.handler = handler
        self.stop_event = stop_event
        self.thread = threading.Thread(target=self._run, name="JumpPullClient", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._connect_and_read()
            except Exception:
                logging.exception("connection to jump relay failed; reconnecting in 2s")
                time.sleep(2.0)

    def _connect_and_read(self) -> None:
        with socket.create_connection((self.host, self.port), timeout=5.0) as sock:
            logging.info("connected to jump relay tcp://%s:%s", self.host, self.port)
            sock.settimeout(1.0)
            buffer = b""
            while not self.stop_event.is_set():
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    logging.warning("jump relay closed connection")
                    return
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    self._handle_line(line)

    def _handle_line(self, line: bytes) -> None:
        if not line.strip():
            return
        try:
            record = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            logging.warning("bad JSON from jump relay: %r", line[:200])
            return
        if isinstance(record, dict):
            self.handler(record)


class LocalPullCollector:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.stop_event = threading.Event()
        self.cache = TwinStateCache(args.rtg_name, args.plc_stale_seconds, args.task_stale_seconds)
        self.writer = JsonlWriter(Path(args.log_dir) if args.log_dir else None)
        self.pull_client = JumpPullClient(args.jump_host, args.jump_port, self._handle_record, self.stop_event)
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
        self.pull_client.start()
        if self.publisher:
            self.publisher.start()
            logging.info("TwinState publisher enabled on %s:%s", self.args.twin_host, self.args.twin_port)
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

        if self.args.echo or stream in ("task", "runinfo"):
            print(compact_json(record), flush=True)

    def _log_twin_state(self, state: dict[str, Any]) -> None:
        now = time.time()
        if now - self._last_log_at < 2.0:
            return
        self._last_log_at = now
        pose = state.get("pose") or {}
        task = state.get("task") or {}
        logging.info(
            "counts=%s twin field=%s bay=%s trolley=%s hoist=%s task=%s/%s bayMap=%s",
            self.counts,
            pose.get("currentField"),
            pose.get("currentBay"),
            pose.get("trolleyMeter"),
            pose.get("hoistMeter"),
            task.get("taskId"),
            task.get("workType"),
            task.get("bayMap"),
        )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pull forwarded RTG messages from jump host to local PC.")
    parser.add_argument("--jump-host", default=DEFAULT_JUMP_HOST)
    parser.add_argument("--jump-port", type=int, default=DEFAULT_JUMP_PORT)
    parser.add_argument("--rtg-name", default="RTG_220")
    parser.add_argument(
        "--log-dir",
        default=str(SCRIPT_DIR / "logs" / "pulled_messages"),
        help="Directory for plc.jsonl/task.jsonl/runinfo.jsonl. Empty disables logging.",
    )
    parser.add_argument("--echo", action="store_true", help="Print every forwarded record.")
    parser.add_argument("--publish-twin-state", action="store_true")
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
    collector = LocalPullCollector(args)

    def request_stop(_signum: int, _frame: Any) -> None:
        collector.stop()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    collector.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
