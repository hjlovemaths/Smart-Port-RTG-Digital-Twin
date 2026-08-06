"""Receive PLC, task, and runinfo messages from the jump-host network.

This is a diagnostic/local-ingress script.  It does not drive Omniverse and it
does not publish TwinState.  Use it first to verify that the local machine can
see the same ZMQ messages that the jump host can subscribe to.

Typical usage:

    python services/rtg_message_receiver.py --jump-host 10.142.20.1

By default:
  - PLC messages are subscribed from tcp://<jump-host>:5515
  - task + runinfo core messages are subscribed from tcp://<jump-host>:7065

Every received message is printed as one JSON line and optionally written to a
JSONL log directory.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

try:
    import zmq
except ImportError:  # pragma: no cover - runtime deployment hint
    zmq = None


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.append(str(SCRIPT_DIR))

from rtg_twin_service import (  # noqa: E402
    parse_json_maybe,
    text_value,
    unwrap_core_message,
    unwrap_plc_message,
)


DEFAULT_JUMP_HOST = "10.142.20.1"
DEFAULT_PLC_PORT = 5515
DEFAULT_CORE_PORT = 7065


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def raw_text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class ReceiverConfig:
    jump_host: str
    plc_host: str
    plc_port: int
    core_host: str
    core_port: int
    subscribe_topic: bytes
    log_dir: Optional[Path]
    print_raw: bool
    print_parsed: bool


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


class ZmqStreamSubscriber:
    def __init__(
        self,
        name: str,
        host: str,
        port: int,
        topic: bytes,
        handler: Callable[[str, bytes, list[bytes]], None],
        stop_event: threading.Event,
    ) -> None:
        self.name = name
        self.host = host
        self.port = port
        self.topic = topic
        self.handler = handler
        self.stop_event = stop_event
        self.thread = threading.Thread(
            target=self._run, name=f"ZmqStreamSubscriber-{name}", daemon=True
        )

    def start(self) -> None:
        if zmq is None:
            raise RuntimeError(
                "pyzmq is not installed. Install it with: pip install -r services/requirements.txt"
            )
        self.thread.start()

    def _run(self) -> None:
        assert zmq is not None
        url = f"tcp://{self.host}:{self.port}"
        context = zmq.Context.instance()
        while not self.stop_event.is_set():
            socket_obj = context.socket(zmq.SUB)
            socket_obj.setsockopt(zmq.SUBSCRIBE, self.topic)
            socket_obj.setsockopt(zmq.RCVTIMEO, 1000)
            try:
                socket_obj.connect(url)
                logging.info("%s connected to %s", self.name, url)
                while not self.stop_event.is_set():
                    try:
                        parts = socket_obj.recv_multipart()
                    except zmq.Again:
                        continue
                    if not parts:
                        continue
                    self.handler(self.name, parts[-1], parts)
            except Exception:
                logging.exception("%s subscriber error; reconnecting in 2s", self.name)
                time.sleep(2.0)
            finally:
                socket_obj.close(0)


class LocalMessageReceiver:
    def __init__(self, config: ReceiverConfig) -> None:
        self.config = config
        self.stop_event = threading.Event()
        self.writer = JsonlWriter(config.log_dir)
        self.counts = {"plc": 0, "task": 0, "runinfo": 0, "core_other": 0, "unknown": 0}
        self.subscribers = [
            ZmqStreamSubscriber(
                "PLC",
                config.plc_host,
                config.plc_port,
                config.subscribe_topic,
                self._handle_plc,
                self.stop_event,
            ),
            ZmqStreamSubscriber(
                "CORE",
                config.core_host,
                config.core_port,
                config.subscribe_topic,
                self._handle_core,
                self.stop_event,
            ),
        ]

    def run_forever(self) -> None:
        for subscriber in self.subscribers:
            subscriber.start()

        logging.info(
            "receiving PLC tcp://%s:%s, TASK/RUNINFO tcp://%s:%s",
            self.config.plc_host,
            self.config.plc_port,
            self.config.core_host,
            self.config.core_port,
        )
        try:
            while not self.stop_event.wait(5.0):
                logging.info("counts %s", self.counts)
        except KeyboardInterrupt:
            logging.info("stopping by Ctrl+C")
        finally:
            self.stop()

    def stop(self) -> None:
        self.stop_event.set()
        self.writer.close()

    def _base_record(self, stream: str, raw: bytes, parts: list[bytes]) -> dict[str, Any]:
        record: dict[str, Any] = {
            "receivedAtUtc": utc_now_iso(),
            "stream": stream,
            "partCount": len(parts),
        }
        if self.config.print_raw:
            record["raw"] = raw_text(raw)
        return record

    def _emit(self, stream: str, record: dict[str, Any]) -> None:
        self.writer.write(stream, record)
        print(compact_json(record), flush=True)

    def _handle_plc(self, _name: str, raw: bytes, parts: list[bytes]) -> None:
        plc = unwrap_plc_message(raw)
        record = self._base_record("plc", raw, parts)
        if plc is not None:
            self.counts["plc"] += 1
            if self.config.print_parsed:
                record["parsed"] = plc
            record["summary"] = {
                "currentField": plc.get("current_field_num"),
                "currentBay": plc.get("current_bay_num"),
                "bigcarElecDistance": plc.get("bigcar_elec_distance"),
                "trolleyMeter": plc.get("smallcar_pos"),
                "hoistMeter": plc.get("lifting_pos"),
                "lockState": plc.get("lock_state"),
            }
            self._emit("plc", record)
            return

        self.counts["unknown"] += 1
        parsed = parse_json_maybe(raw)
        if self.config.print_parsed:
            record["parsed"] = parsed
        record["warning"] = "PLC message could not be unwrapped"
        self._emit("unknown", record)

    def _handle_core(self, _name: str, raw: bytes, parts: list[bytes]) -> None:
        cmd_id, content = unwrap_core_message(raw)
        stream = self._classify_core(cmd_id)
        self.counts[stream] = self.counts.get(stream, 0) + 1

        record = self._base_record(stream, raw, parts)
        record["cmdId"] = cmd_id
        if self.config.print_parsed:
            record["parsed"] = content
        record["summary"] = self._core_summary(cmd_id, content)
        self._emit(stream, record)

    @staticmethod
    def _classify_core(cmd_id: str) -> str:
        if cmd_id == "cmd_rcms_taskinfo":
            return "task"
        if cmd_id == "cmd_rcms_runinfo":
            return "runinfo"
        return "core_other"

    @staticmethod
    def _core_summary(cmd_id: str, content: Optional[dict[str, Any]]) -> dict[str, Any]:
        if not content:
            return {"cmdId": cmd_id}
        if cmd_id == "cmd_rcms_taskinfo":
            return {
                "taskId": content.get("task_id"),
                "taskState": content.get("task_state"),
                "taskType": content.get("task_type"),
                "workType": content.get("task_work_type"),
                "startPos": content.get("task_startpos"),
                "endPos": content.get("task_endpos"),
                "containerNo": content.get("task_cntrno"),
                "bayMap": content.get("bay_map"),
                "carNo": content.get("car_no"),
            }
        if cmd_id == "cmd_rcms_runinfo":
            return {
                "taskId": content.get("task_id"),
                "runState": content.get("run_state"),
                "happenTime": content.get("happentime"),
                "msgDescription": text_value(content.get("msg_descption"))
                or text_value(content.get("msg_description"))
                or text_value(content.get("msg_descptionn")),
                "exceptionCode": content.get("exception_code"),
            }
        return {"cmdId": cmd_id, "keys": sorted(content.keys())[:20]}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Subscribe local machine to PLC, task, and runinfo ZMQ messages."
    )
    parser.add_argument(
        "--jump-host",
        default=DEFAULT_JUMP_HOST,
        help="Jump-host/source IP used for both PLC and core messages.",
    )
    parser.add_argument("--plc-host", help="Override PLC host. Defaults to --jump-host.")
    parser.add_argument(
        "--plc-port", type=int, default=DEFAULT_PLC_PORT, help="PLC ZMQ SUB port."
    )
    parser.add_argument(
        "--core-host", help="Override task/runinfo core host. Defaults to --jump-host."
    )
    parser.add_argument(
        "--core-port",
        type=int,
        default=DEFAULT_CORE_PORT,
        help="Task/runinfo core ZMQ SUB port.",
    )
    parser.add_argument(
        "--topic",
        default="",
        help="Optional ZMQ SUB topic. Default subscribes to all topics.",
    )
    parser.add_argument(
        "--log-dir",
        default=str(SCRIPT_DIR / "logs" / "rtg_messages"),
        help="Directory for plc.jsonl/task.jsonl/runinfo.jsonl. Use empty string to disable.",
    )
    parser.add_argument(
        "--no-raw",
        action="store_true",
        help="Do not include raw message text in printed/logged JSON.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print/log only summaries, not the full parsed message body.",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable debug-level logging."
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    log_dir = Path(args.log_dir) if args.log_dir else None
    config = ReceiverConfig(
        jump_host=args.jump_host,
        plc_host=args.plc_host or args.jump_host,
        plc_port=args.plc_port,
        core_host=args.core_host or args.jump_host,
        core_port=args.core_port,
        subscribe_topic=args.topic.encode("utf-8"),
        log_dir=log_dir,
        print_raw=not args.no_raw,
        print_parsed=not args.summary_only,
    )
    receiver = LocalMessageReceiver(config)

    def request_stop(_signum: int, _frame: Any) -> None:
        receiver.stop()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    receiver.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
