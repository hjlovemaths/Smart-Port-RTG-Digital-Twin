"""Jump-host relay server for RTG PLC, task, and runinfo messages.

Run this on the jump host.  The jump host subscribes to the equipment-side ZMQ
publishers, then opens a TCP server.  The local PC actively connects to this
server and pulls newline-delimited JSON records.

This direction is useful when the jump host cannot actively connect back to the
local PC.

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
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

try:
    import zmq
except ImportError:  # pragma: no cover
    zmq = None


SCHEMA = "smart-port-rtg-forwarded-message/v1"
DEFAULT_LISTEN_HOST = "0.0.0.0"
DEFAULT_LISTEN_PORT = 48800
DEFAULT_PLC_HOST = "10.142.20.1"
DEFAULT_PLC_PORT = 5515
DEFAULT_CORE_HOST = "10.142.20.1"
DEFAULT_CORE_PORT = 7065


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def raw_text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


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


def unwrap_plc_message(message: Any) -> Optional[dict[str, Any]]:
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
    if "current_bay_num" in data or "smallcar_pos" in data or "lifting_pos" in data:
        return data
    return None


def unwrap_core_message(message: Any) -> tuple[str, Optional[dict[str, Any]]]:
    data = parse_json_maybe(message)
    if not isinstance(data, dict):
        return "", None

    cmd_id = text_value(data.get("cmd_id")) or ""
    content = parse_json_maybe(data.get("content")) if "content" in data else data
    return cmd_id, content if isinstance(content, dict) else None


class TcpBroadcastServer:
    def __init__(self, host: str, port: int, stop_event: threading.Event) -> None:
        self.host = host
        self.port = port
        self.stop_event = stop_event
        self.thread = threading.Thread(target=self._run, name="TcpBroadcastServer", daemon=True)
        self._server: Optional[socket.socket] = None
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        with self._lock:
            clients = list(self._clients)
            self._clients.clear()
        for client in clients:
            try:
                client.close()
            except OSError:
                pass
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass

    def publish(self, record: dict[str, Any]) -> None:
        line = compact_json(record).encode("utf-8") + b"\n"
        dead: list[socket.socket] = []
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.sendall(line)
            except OSError:
                dead.append(client)
        if dead:
            with self._lock:
                for client in dead:
                    if client in self._clients:
                        self._clients.remove(client)
                    try:
                        client.close()
                    except OSError:
                        pass

    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def _run(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            self._server = server
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen()
            server.settimeout(1.0)
            logging.info("jump relay listening on %s:%s", self.host, self.port)
            while not self.stop_event.is_set():
                try:
                    client, address = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if not self.stop_event.is_set():
                        logging.exception("relay accept failed")
                    return
                client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                with self._lock:
                    self._clients.append(client)
                logging.info("local collector connected from %s", address)


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
        self.thread = threading.Thread(target=self._run, name=f"ZmqStreamSubscriber-{name}", daemon=True)

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


class JumpRelayServer:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.stop_event = threading.Event()
        self.counts = {"plc": 0, "task": 0, "runinfo": 0, "core_other": 0, "unknown": 0}
        self.relay = TcpBroadcastServer(args.listen_host, args.listen_port, self.stop_event)
        topic = args.topic.encode("utf-8")
        self.subscribers = [
            ZmqStreamSubscriber("PLC", args.plc_host, args.plc_port, topic, self._handle_plc, self.stop_event),
            ZmqStreamSubscriber("CORE", args.core_host, args.core_port, topic, self._handle_core, self.stop_event),
        ]

    def run_forever(self) -> None:
        self.relay.start()
        for subscriber in self.subscribers:
            subscriber.start()
        logging.info(
            "relay source PLC tcp://%s:%s CORE tcp://%s:%s",
            self.args.plc_host,
            self.args.plc_port,
            self.args.core_host,
            self.args.core_port,
        )
        try:
            while not self.stop_event.wait(5.0):
                logging.info("counts=%s localClients=%s", self.counts, self.relay.client_count())
        except KeyboardInterrupt:
            logging.info("stopping by Ctrl+C")
        finally:
            self.stop()

    def stop(self) -> None:
        self.stop_event.set()
        self.relay.stop()

    def _base_record(self, stream: str, raw: bytes, parts: list[bytes]) -> dict[str, Any]:
        record: dict[str, Any] = {
            "schema": SCHEMA,
            "sentAtUtc": utc_now_iso(),
            "stream": stream,
            "source": "jump_relay_server",
            "partCount": len(parts),
        }
        if self.args.include_raw:
            record["raw"] = raw_text(raw)
        return record

    def _publish(self, record: dict[str, Any]) -> None:
        self.relay.publish(record)
        if self.args.echo:
            print(compact_json(record), flush=True)

    def _handle_plc(self, _name: str, raw: bytes, parts: list[bytes]) -> None:
        plc = unwrap_plc_message(raw)
        if plc is None:
            record = self._base_record("unknown", raw, parts)
            record["warning"] = "PLC message could not be unwrapped"
            record["parsed"] = parse_json_maybe(raw)
            self.counts["unknown"] += 1
            self._publish(record)
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
        self._publish(record)

    def _handle_core(self, _name: str, raw: bytes, parts: list[bytes]) -> None:
        cmd_id, content = unwrap_core_message(raw)
        stream = self._classify_core(cmd_id)
        record = self._base_record(stream, raw, parts)
        record["cmdId"] = cmd_id
        record["parsed"] = content
        record["summary"] = self._core_summary(cmd_id, content)
        self.counts[stream] = self.counts.get(stream, 0) + 1
        self._publish(record)

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
    parser = argparse.ArgumentParser(description="Serve jump-host RTG messages for local PC pull mode.")
    parser.add_argument("--listen-host", default=DEFAULT_LISTEN_HOST)
    parser.add_argument("--listen-port", type=int, default=DEFAULT_LISTEN_PORT)
    parser.add_argument("--plc-host", default=DEFAULT_PLC_HOST)
    parser.add_argument("--plc-port", type=int, default=DEFAULT_PLC_PORT)
    parser.add_argument("--core-host", default=DEFAULT_CORE_HOST)
    parser.add_argument("--core-port", type=int, default=DEFAULT_CORE_PORT)
    parser.add_argument("--topic", default="", help="Optional ZMQ SUB topic; empty means subscribe all.")
    parser.add_argument("--include-raw", action="store_true")
    parser.add_argument("--echo", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    server = JumpRelayServer(args)

    def request_stop(_signum: int, _frame: Any) -> None:
        server.stop()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    server.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
