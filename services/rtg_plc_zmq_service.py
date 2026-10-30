"""Third-party PUB -> :5515 SUB; :5516 PUB -> third-party SUB.

Only the Kit adapter writes USD. This process owns networking and forwards
bounded, expiring commands over a loopback-only scene link (:48720).
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import logging
import os
from pathlib import Path
import socket
import sys
import threading
import time

try:
    import zmq
except ImportError:
    zmq = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "omniverse/scripts"))
from rtg_plc_protocol import PlcConfig

MAX_FRAME = 65536
DEFAULT_CONFIG = Path(__file__).with_suffix(".config.json")
LOG = logging.getLogger("rtg_plc_zmq")


def encode(packet):
    return (json.dumps(packet, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


class PlcZmqService:
    def __init__(self, config=None, context=None, ready_file=None):
        config = config or {}
        self.host = config.get("bind_host", "0.0.0.0")
        self.command_port = config.get("command_port", 5515)
        self.state_port = config.get("state_port", 5516)
        self.scene_port = config.get("scene_port", 48720)
        for port in (self.command_port, self.state_port, self.scene_port):
            if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
                raise ValueError("ports must be integers in 0..65535 (0 selects a test port)")
        if self.command_port and self.command_port == self.state_port:
            raise ValueError("command_port and state_port must differ")
        self.hz = config.get("publish_hz", 20)
        self.stale_s = config.get("scene_stale_s", 0.6)
        if isinstance(self.hz, bool) or not isinstance(self.hz, (int, float)) or not 1 <= self.hz <= 100:
            raise ValueError("publish_hz must be 1..100")
        if isinstance(self.stale_s, bool) or not isinstance(self.stale_s, (int, float)) or not .1 <= self.stale_s <= 5:
            raise ValueError("scene_stale_s must be 0.1..5 seconds")
        self.engine_config = PlcConfig(**config.get("motion", {}))
        self.context = context
        self._own_context = context is None
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.error = None
        self.ready_file = Path(ready_file) if ready_file else None
        self.latest = None
        self.latest_at = 0.0
        self.sequence = 0
        self.client = None
        self.handshake = False
        self.rx = bytearray()
        self.tx = bytearray()

    def envelope(self, msg_type, content, **extra):
        return {"match": "PLC", "time": str(time.time_ns() // 1_000_000),
                "msg_type": msg_type, "version": {"product": "smartport_rtg_plc_simulator",
                "caption": "RTG 数字孪生 PLC 协议服务", "version": "0.1.0", "backup": "digital scene only"},
                "content": content, **extra}

    def publish(self, kind, content, **extra):
        self.publisher.send_json(self.envelope(kind, content, **extra), flags=zmq.NOBLOCK)

    def failure(self, description, command=None):
        self.publish(3, {"exp_type": 2, "exp_desc": str(description), "accepted": False,
                         "cmd": command.get("cmd") if isinstance(command, dict) else None,
                         "command_time": command.get("time") if isinstance(command, dict) else None})

    def _disconnect(self):
        if self.client is not None:
            self.client.close()
        self.client = None
        self.handshake = False
        self.rx.clear()
        self.tx.clear()
        self.latest = None
        self.latest_at = 0.0

    def _accept(self):
        try:
            client, _ = self.listener.accept()
        except BlockingIOError:
            return
        if self.client is not None:
            client.close()  # one scene owner; never replace a live adapter implicitly
            return
        client.setblocking(False)
        client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.client = client

    def _queue(self, packet):
        payload = encode(packet)
        if len(self.tx) + len(payload) > MAX_FRAME:
            raise ValueError("scene output queue is full; reconnect required")
        self.tx.extend(payload)

    def _scene_packet(self, packet):
        if not isinstance(packet, dict):
            raise ValueError("scene packet must be an object")
        kind = packet.get("type")
        if not self.handshake:
            if kind != "hello" or packet.get("role") != "smartport-rtg-kit":
                raise ValueError("invalid scene handshake")
            self.handshake = True
            self._queue({"type": "welcome", "motion": asdict(self.engine_config)})
        elif kind == "state":
            snapshot = packet.get("snapshot")
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get("content"), dict):
                raise ValueError("invalid scene snapshot")
            self.latest, self.latest_at = snapshot, time.monotonic()
        elif kind == "result":
            result = packet["result"]
            if result.get("accepted"):
                self.publish(2, dict(result, request_id=packet.get("id")))
            else:
                self.publish(3, dict(result, exp_type=2, request_id=packet.get("id")))
        elif kind == "unavailable":
            self.latest = None
        else:
            raise ValueError("unknown scene packet")

    def _scene_io(self):
        if self.client is None:
            return
        try:
            try:
                data = self.client.recv(MAX_FRAME)
            except BlockingIOError:
                data = None
            if data == b"":
                self._disconnect()
                return
            if data:
                self.rx.extend(data)
                if len(self.rx) > MAX_FRAME:
                    raise ValueError("oversized scene frame")
                while b"\n" in self.rx:
                    line, _, remainder = self.rx.partition(b"\n")
                    self.rx = bytearray(remainder)
                    self._scene_packet(json.loads(line))
            if self.tx:
                try:
                    sent = self.client.send(self.tx)
                    del self.tx[:sent]
                except BlockingIOError:
                    pass
        except (OSError, ValueError, KeyError, TypeError) as exc:
            LOG.warning("Scene link disconnected: %s", exc)
            self._disconnect()

    def _command(self, frames):
        command = None
        try:
            if len(frames) not in (1, 2) or sum(map(len, frames)) > MAX_FRAME:
                raise ValueError("expected one JSON frame or [topic, JSON], maximum 64 KiB")
            command = json.loads(frames[-1].decode("utf-8"))
            if not isinstance(command, dict):
                raise ValueError("command must be a JSON object")
            # Do not replay anything queued while Kit is absent, stalled or changing stages.
            if not self.handshake or self.latest is None or time.monotonic() - self.latest_at > self.stale_s:
                raise ValueError("RTG scene is not connected/fresh; start the Kit adapter first")
            self.sequence += 1
            self._queue({"type": "command", "id": self.sequence, "message": command,
                         "expires_ms": time.time_ns() // 1_000_000 + round(self.stale_s * 1000)})
        except (ValueError, UnicodeError) as exc:
            self.failure(exc, command)

    def run(self):
        if zmq is None:
            raise RuntimeError("Install pyzmq: python -m pip install -r services/requirements.txt")
        self.context = self.context or zmq.Context()
        subscriber = self.context.socket(zmq.SUB)
        self.publisher = self.context.socket(zmq.PUB)
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            for endpoint in (subscriber, self.publisher):
                endpoint.setsockopt(zmq.LINGER, 0)
                endpoint.setsockopt(zmq.SNDHWM, 256)
                endpoint.setsockopt(zmq.RCVHWM, 256)
                endpoint.setsockopt(zmq.MAXMSGSIZE, MAX_FRAME)
            subscriber.setsockopt(zmq.SUBSCRIBE, b"")
            subscriber.bind(f"tcp://{self.host}:{self.command_port or '*'}")
            self.publisher.bind(f"tcp://{self.host}:{self.state_port or '*'}")
            self.command_port = int(subscriber.getsockopt(zmq.LAST_ENDPOINT).decode().rsplit(":", 1)[1])
            self.state_port = int(self.publisher.getsockopt(zmq.LAST_ENDPOINT).decode().rsplit(":", 1)[1])
            self.listener.bind(("127.0.0.1", self.scene_port))
            self.scene_port = self.listener.getsockname()[1]
            self.listener.listen(2)
            self.listener.setblocking(False)
            poller = zmq.Poller()
            poller.register(subscriber, zmq.POLLIN)
            next_publish = 0.0
            if self.ready_file is not None:
                # The Agent uses a unique file per child; publish only after all binds succeed.
                temporary = self.ready_file.with_suffix(".tmp")
                temporary.write_text(json.dumps({"pid": os.getpid(), "command_port": self.command_port,
                                                "state_port": self.state_port, "scene_port": self.scene_port}),
                                     encoding="utf-8")
                temporary.replace(self.ready_file)
            self.ready.set()
            LOG.info("Commands SUB :%s; states PUB :%s; Kit link 127.0.0.1:%s",
                     self.command_port, self.state_port, self.scene_port)
            while not self.stop_event.is_set():
                self._accept()
                self._scene_io()
                if subscriber in dict(poller.poll(5)):
                    for _ in range(64):
                        try:
                            self._command(subscriber.recv_multipart(zmq.NOBLOCK))
                        except zmq.Again:
                            break
                now = time.monotonic()
                if now >= next_publish:
                    next_publish = now + 1 / self.hz
                    if self.latest is not None and now - self.latest_at <= self.stale_s:
                        self.publish(1, self.latest["content"], digital_twin=self.latest["digital_twin"])
                    else:
                        self.publish(3, {"exp_type": 1, "exp_desc": "RTG scene unavailable or stale"},
                                     digital_twin={"scene_connected": False, "simulated": True})
        except Exception as exc:
            self.error = exc
            self.ready.set()
            raise
        finally:
            self._disconnect()
            self.listener.close()
            subscriber.close()
            self.publisher.close()
            if self._own_context:
                self.context.term()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--command-port", type=int)
    parser.add_argument("--state-port", type=int)
    parser.add_argument("--ready-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    if args.command_port is not None:
        config["command_port"] = args.command_port
    if args.state_port is not None:
        config["state_port"] = args.state_port
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    service = PlcZmqService(config, ready_file=args.ready_file)
    try:
        service.run()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        LOG.error("Service stopped: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
