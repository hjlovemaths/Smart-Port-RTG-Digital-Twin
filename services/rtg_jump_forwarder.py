"""Jump-host forwarder for RTG PLC, task, and runinfo messages.

Run this script on the jump host, i.e. the machine that can subscribe to the
equipment-side ZMQ publishers.  It subscribes to:

  - PLC ZMQ stream
  - CORE ZMQ stream, which contains taskinfo and runinfo commands

Then it forwards normalized newline-delimited JSON records to the local PC.

Local PC side should run:

    python services/rtg_local_collector.py --listen-host 0.0.0.0 --listen-port 48800

Jump host side example:

    python services/rtg_jump_forwarder.py --local-host 你的本机IP --local-port 48800
"""

from __future__ import annotations

import argparse
import json
import logging
import queue
import signal
import socket
import sys
import threading
import time
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


SCHEMA = "smart-port-rtg-forwarded-message/v1"
DEFAULT_PLC_HOST = "10.142.20.1"
DEFAULT_CORE_HOST = "10.142.20.1"
DEFAULT_PLC_PORT = 5515
DEFAULT_CORE_PORT = 7065
DEFAULT_LOCAL_PORT = 48800


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def raw_text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


class TcpForwardClient:
    """Reconnecting TCP client that pushes JSONL records to the local PC."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        queue_size: int,
        stop_event: threading.Event,
    ) -> None:
        self.host = host
        self.port = port
        self.stop_event = stop_event
        self.records: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=queue_size)
        self.thread = threading.Thread(target=self._run, name="TcpForwardClient", daemon=True)
        self.dropped = 0

    def start(self) -> None:
        self.thread.start()

    def send(self, record: dict[str, Any]) -> None:
        try:
            self.records.put_nowait(record)
        except queue.Full:
            try:
                self.records.get_nowait()
            except queue.Empty:
                pass
            self.dropped += 1
            try:
                self.records.put_nowait(record)
            except queue.Full:
                self.dropped += 1

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._connect_and_flush()
            except Exception:
                logging.exception(
                    "forward connection to %s:%s failed; reconnecting in 2s",
                    self.host,
                    self.port,
                )
                time.sleep(2.0)

    def _connect_and_flush(self) -> None:
        with socket.create_connection((self.host, self.port), timeout=5.0) as sock:
            logging.info("connected to local collector tcp://%s:%s", self.host, self.port)
            sock.settimeout(5.0)
            while not self.stop_event.is_set():
                try:
                    record = self.records.get(timeout=1.0)
                except queue.Empty:
                    continue
                line = compact_json(record).encode("utf-8") + b"\n"
                sock.sendall(line)


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
            raise RuntimeError("pyzmq is not installed. Run: pip install pyzmq")
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
                logging.info("%s subscriber connected to %s", self.name, url)
                while not self.stop_event.is_set():
                    try:
                        parts = socket_obj.recv_multipart()
                    except zmq.Again:
                        continue
                    if parts:
                        self.handler(self.name, parts[-1], parts)
            except Exception:
                logging.exception("%s subscriber error; reconnecting in 2s", self.name)
                time.sleep(2.0)
            finally:
                socket_obj.close(0)


class JumpForwarder:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.stop_event = threading.Event()
        self.counts = {"plc": 0, "task": 0, "runinfo": 0, "core_other": 0, "unknown": 0}
        self.forward_client = TcpForwardClient(
            args.local_host,
            args.local_port,
            queue_size=args.queue_size,
            stop_event=self.stop_event,
        )
        topic = args.topic.encode("utf-8")
        self.subscribers = [
            ZmqStreamSubscriber(
                "PLC",
                args.plc_host,
                args.plc_port,
                topic,
                self._handle_plc,
                self.stop_event,
            ),
            ZmqStreamSubscriber(
                "CORE",
                args.core_host,
                args.core_port,
                topic,
                self._handle_core,
                self.stop_event,
            ),
        ]

    def run_forever(self) -> None:
        self.forward_client.start()
        for subscriber in self.subscribers:
            subscriber.start()
        logging.info(
            "forwarding PLC tcp://%s:%s and CORE tcp://%s:%s to %s:%s",
            self.args.plc_host,
            self.args.plc_port,
            self.args.core_host,
            self.args.core_port,
            self.args.local_host,
            self.args.local_port,
        )
        try:
            while not self.stop_event.wait(5.0):
                logging.info("counts=%s dropped=%s", self.counts, self.forward_client.dropped)
        except KeyboardInterrupt:
            logging.info("stopping by Ctrl+C")
        finally:
            self.stop()

    def stop(self) -> None:
        self.stop_event.set()

    def _base_record(self, stream: str, raw: bytes, parts: list[bytes]) -> dict[str, Any]:
        record: dict[str, Any] = {
            "schema": SCHEMA,
            "sentAtUtc": utc_now_iso(),
            "stream": stream,
            "source": "jump_forwarder",
            "partCount": len(parts),
        }
        if self.args.include_raw:
            record["raw"] = raw_text(raw)
        return record

    def _push(self, record: dict[str, Any]) -> None:
        self.forward_client.send(record)
        if self.args.echo:
            print(compact_json(record), flush=True)

    def _handle_plc(self, _name: str, raw: bytes, parts: list[bytes]) -> None:
        plc = unwrap_plc_message(raw)
        if plc is None:
            record = self._base_record("unknown", raw, parts)
            record["warning"] = "PLC message could not be unwrapped"
            record["parsed"] = parse_json_maybe(raw)
            self.counts["unknown"] += 1
            self._push(record)
            return

        record = self._base_record("plc", raw, parts)
        record["parsed"] = plc
        record["summary"] = {
            "currentField": plc.get("current_field_num"),
            "currentBay": plc.get("current_bay_num"),
            "bigcarElecDistance": plc.get("bigcar_elec_distance"),
            "trolleyMeter": plc.get("smallcar_pos"),
            "hoistMeter": plc.get("lifting_pos"),
            "lockState": plc.get("lock_state"),
        }
        self.counts["plc"] += 1
        self._push(record)

    def _handle_core(self, _name: str, raw: bytes, parts: list[bytes]) -> None:
        cmd_id, content = unwrap_core_message(raw)
        stream = self._classify_core(cmd_id)
        record = self._base_record(stream, raw, parts)
        record["cmdId"] = cmd_id
        record["parsed"] = content
        record["summary"] = self._core_summary(cmd_id, content)
        self.counts[stream] = self.counts.get(stream, 0) + 1
        self._push(record)

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
    parser = argparse.ArgumentParser(description="Forward RTG ZMQ messages from jump host to local PC.")
    parser.add_argument("--local-host", required=True, help="Local PC IP reachable from the jump host.")
    parser.add_argument("--local-port", type=int, default=DEFAULT_LOCAL_PORT)
    parser.add_argument("--plc-host", default=DEFAULT_PLC_HOST, help="PLC ZMQ host as seen from jump host.")
    parser.add_argument("--plc-port", type=int, default=DEFAULT_PLC_PORT)
    parser.add_argument("--core-host", default=DEFAULT_CORE_HOST, help="CORE ZMQ host as seen from jump host.")
    parser.add_argument("--core-port", type=int, default=DEFAULT_CORE_PORT)
    parser.add_argument("--topic", default="", help="Optional ZMQ SUB topic; empty means subscribe all.")
    parser.add_argument("--queue-size", type=int, default=5000)
    parser.add_argument("--include-raw", action="store_true", help="Forward raw message text too.")
    parser.add_argument("--echo", action="store_true", help="Also print forwarded JSON lines on jump host.")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    forwarder = JumpForwarder(args)

    def request_stop(_signum: int, _frame: Any) -> None:
        forwarder.stop()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    forwarder.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
