"""Kit main-thread RTG adapter for services/rtg_plc_zmq_service.py.

Run this file in Script Editor to connect. It starts read-only; ctr_sw_set=1
from the external client claims control only when other RTG drivers are idle.
No ZMQ or third-party package is needed inside Kit.
"""
from __future__ import annotations

import builtins
import json
from pathlib import Path
import queue
import select
import socket
import sys
import threading
import time


def _root():
    for candidate in (Path(__file__).resolve().parent, Path.cwd() / "omniverse/scripts",
                      Path("D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts")):
        if (candidate / "rtg_plc_protocol.py").is_file():
            return candidate.parent.parent
    raise RuntimeError("Cannot locate Smart Port project scripts")


ROOT = _root()
for path in (ROOT / "omniverse/scripts", ROOT / "omniverse/extensions/smartport.agent_panel"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
from rtg_plc_protocol import PlcConfig, PlcEngine

OWNER_FLAG = "_SMART_PORT_RTG_PLC_CONTROL_ACTIVE"
DRIVER_KEY = "_smart_port_rtg_plc_scene_driver"
MAX_FRAME = 65536


class SceneLink:
    """Socket thread only moves JSON. It never reads a stage or writes USD."""
    def __init__(self, port):
        self.port = port
        self.incoming = queue.Queue(128)
        self.outgoing = queue.Queue(256)
        self.connected = False
        self.epoch = 0
        self.error = None
        self.stop_event = threading.Event()
        self.reset_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="rtg-plc-scene-link", daemon=True)

    def emit(self, packet):
        if not self.connected:
            return
        try:
            self.outgoing.put_nowait((self.epoch, packet))
        except queue.Full:
            self.connected = False
            self.reset_event.set()
            raise RuntimeError("Scene link output queue full")

    def _run(self):
        while not self.stop_event.is_set():
            self.reset_event.clear()
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1) as client:
                    client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    client.setblocking(False)
                    self.epoch += 1
                    epoch = self.epoch
                    rx = bytearray()
                    tx = bytearray(b'{"type":"hello","role":"smartport-rtg-kit"}\n')
                    welcomed = False
                    while not self.stop_event.is_set() and not self.reset_event.is_set():
                        readable, writable, _ = select.select([client], [client] if tx else [], [], .01)
                        if readable:
                            data = client.recv(MAX_FRAME)
                            if not data:
                                break
                            rx.extend(data)
                            if len(rx) > MAX_FRAME:
                                raise ValueError("oversized scene frame")
                            while b"\n" in rx:
                                line, _, remainder = rx.partition(b"\n")
                                rx = bytearray(remainder)
                                packet = json.loads(line)
                                if not welcomed:
                                    if packet.get("type") != "welcome":
                                        raise ValueError("expected service welcome")
                                    welcomed = True
                                    self.connected = True
                                    self.error = None
                                self.incoming.put_nowait((epoch, packet))
                        if writable:
                            sent = client.send(tx)
                            del tx[:sent]
                        for _ in range(32):
                            try:
                                item_epoch, packet = self.outgoing.get_nowait()
                            except queue.Empty:
                                break
                            if item_epoch == epoch:
                                payload = (json.dumps(packet, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
                                if len(tx) + len(payload) > MAX_FRAME:
                                    raise ValueError("oversized scene send queue")
                                tx.extend(payload)
            except (OSError, ValueError, queue.Full, AttributeError) as exc:
                self.error = str(exc)
            finally:
                self.connected = False
            self.stop_event.wait(.25)

    def close(self):
        self.connected = False
        self.stop_event.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=1.5)


class PlcSceneDriver:
    def __init__(self, port=48720):
        self.link = SceneLink(port)
        self.subscription = None
        self.stage = self.controller = self.engine = self.guard_runtime = None
        self.config = None
        self.epoch = 0
        self.last_tick = time.monotonic()
        self.next_state = 0.0
        self.last_error = None
        self.interaction = None

    def _arm(self, active):
        # Cargo physics continues holding/settling after control-off or loss of
        # transport. Other axis writers must wait until physics is disabled.
        setattr(builtins, OWNER_FLAG, bool(active or (self.interaction and self.interaction.enabled)))

    def _guard(self):
        self.guard_runtime._guard_manual("rtg")
        for runtime in tuple(getattr(builtins, "_smart_port_agent_scene_runtimes", ())):
            if runtime is not self.guard_runtime and runtime.stage == self.stage and runtime.motion:
                raise RuntimeError("Agent RTG motion owns the axes; stop it before PLC control")

    def _bind(self, stage):
        if self.stage == stage and self.controller is not None:
            return
        if self.stage is not None and self.stage != stage:
            # Invalidate the transport epoch as well as the control latch.
            # Otherwise an old queued control-enable could arm the new stage.
            self.link.connected = False
            self.link.reset_event.set()
        self.disarm("stage_changed")
        if self.interaction is not None:
            self.interaction.close()
            self.interaction = None
        self._arm(False)
        self.engine = self.controller = None
        self.stage = stage
        if self.guard_runtime is not None:
            self.guard_runtime.bind(stage)
        if stage is None:
            return
        import rtg_live_control as live
        if not all(stage.GetPrimAtPath(path) for path in (live.GANTRY_PATH, live.TROLLEY_PATH, live.HOIST_PATH)):
            return
        self.controller = live.RTGController(stage)
        from rtg_plc_interaction import PlcInteraction
        self.interaction = PlcInteraction(stage, self.controller)
        if self.config is not None:
            self._make_engine()

    def _make_engine(self):
        import rtg_live_control as live
        estopped = bool(self.engine and self.engine.estopped)
        self.disarm("scene_reconnected")
        self.engine = PlcEngine(self.controller, (live.GANTRY_SOFT_LIMITS, live.TROLLEY_SOFT_LIMITS,
                                                live.HOIST_SOFT_LIMITS), self.config,
                                guard=self._guard, on_arm=self._arm, interaction=self.interaction)
        if estopped:
            # Reconnecting the transport must not clear a latched digital stop.
            self.engine.apply({"match": "UI", "cmd": "estop", "value": None})

    def start(self):
        import omni.kit.app
        from smartport.agent_panel.scene_runtime import SceneRuntime
        if not getattr(SceneRuntime, "supports_plc_ownership", False):
            raise RuntimeError("Restart Kit to load the updated ZMQ PLC ownership checks before starting this adapter")
        self.guard_runtime = SceneRuntime(ROOT)
        self.guard_runtime.plc_driver_owner = True
        self.link.thread.start()
        self.subscription = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
            self._on_update, name="SmartPortRtgPlcSceneDriver")
        return self

    def disarm(self, reason="local_stop"):
        if self.engine is not None:
            self.engine.stop(reason, disarm=True)
        self._arm(False)

    def update(self, stage, dt):
        """Deterministic main-thread entry point, also used by USD integration tests."""
        self._bind(stage)
        connected = self.link.connected
        if not connected or self.epoch != self.link.epoch:
            self.disarm("scene_link_disconnected")
        if self.engine is not None:
            # Settle the previous velocity over the elapsed frame before new
            # commands change it. Never apply a fresh velocity to past time.
            self.engine.tick(dt, connected=connected and self.epoch == self.link.epoch)
        for _ in range(64):
            try:
                epoch, packet = self.link.incoming.get_nowait()
            except queue.Empty:
                break
            if epoch != self.link.epoch or not connected:
                continue
            if packet.get("type") == "welcome":
                self.config = PlcConfig(**packet["motion"])
                self.epoch = epoch
                if self.controller is not None:
                    self._make_engine()
            elif packet.get("type") == "command":
                try:
                    if self.engine is None:
                        raise ValueError("Open smart_port.usda before sending RTG commands")
                    if time.time_ns() // 1_000_000 > packet["expires_ms"]:
                        raise ValueError("expired command was discarded")
                    result = self.engine.apply(packet["message"])
                except (ValueError, RuntimeError, KeyError) as exc:
                    self.last_error = str(exc)
                    if self.engine:
                        self.engine.stop("command_rejected")
                    message = packet.get("message", {})
                    result = {"accepted": False, "exp_desc": str(exc), "cmd": message.get("cmd"),
                              "command_time": message.get("time")}
                self.link.emit({"type": "result", "id": packet.get("id"), "result": result})
        now = time.monotonic()
        if now >= self.next_state:
            self.next_state = now + .05
            if self.engine is not None and self.epoch == self.link.epoch:
                self.link.emit({"type": "state", "snapshot": self.engine.snapshot()})
            else:
                self.link.emit({"type": "unavailable"})

    def _on_update(self, event):
        import omni.usd
        now = time.monotonic()
        dt, self.last_tick = now - self.last_tick, now
        try:
            self.update(omni.usd.get_context().get_stage(), dt)
        except Exception as exc:
            self.disarm("driver_error")
            if self.last_error != str(exc):
                print(f"[RTG PLC] {exc}")
            self.last_error = str(exc)
            self.link.emit({"type": "unavailable"})

    def close(self):
        self.disarm("driver_stopped")
        self.subscription = None
        if self.interaction is not None:
            self.interaction.close()
        self._arm(False)
        self.link.close()
        if self.guard_runtime is not None:
            self.guard_runtime.close()


def start_rtg_plc_driver(port=48720):
    stop_rtg_plc_driver()
    driver = PlcSceneDriver(port).start()
    setattr(builtins, DRIVER_KEY, driver)
    print(f"[RTG PLC] Connecting to 127.0.0.1:{port}; control starts OFF")
    return driver


def stop_rtg_plc_driver():
    driver = getattr(builtins, DRIVER_KEY, None)
    if driver is not None:
        driver.close()
        setattr(builtins, DRIVER_KEY, None)


def get_rtg_plc_status():
    driver = getattr(builtins, DRIVER_KEY, None)
    return {"running": driver is not None and driver.subscription is not None,
            "connected": bool(driver and driver.link.connected),
            "scene_ready": bool(driver and driver.engine is not None and driver.link.connected),
            "scene_port": driver.link.port if driver else None,
            "armed": bool(driver and driver.engine and driver.engine.armed),
            "physics_enabled": bool(driver and driver.interaction and driver.interaction.enabled),
            "carried_container": driver.interaction.carried if driver and driver.interaction else None,
            "error": driver.last_error or driver.link.error if driver else None}


def set_rtg_plc_physics(enabled):
    """Kit-main-thread entry point for the Agent's explicit physics request.

    Uses the same command validation and ownership checks as incoming ZMQ.
    Claims only local control; never sends velocity or real PLC commands.
    """
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    driver = getattr(builtins, DRIVER_KEY, None)
    if not driver or not driver.link.connected or driver.engine is None or driver.epoch != driver.link.epoch:
        raise RuntimeError("先启动或连接 ZMQ 服务，等待 Kit 适配器 scene_ready 后再开启物理模式。")
    engine = driver.engine
    if not enabled and (driver.interaction is None or not driver.interaction.enabled):
        return get_rtg_plc_status()
    was_armed = engine.armed
    try:
        if not was_armed:
            engine.apply({"match": "CTR", "cmd": "ctr_sw_set", "value": 1})
        engine.apply({"match": "CTR", "cmd": "sim_physics", "value": int(enabled)})
    except BaseException:
        if not was_armed:
            engine.stop("physics_enable_failed", disarm=True)
        raise
    if not enabled and not was_armed:
        engine.stop("control_off", disarm=True)
    return get_rtg_plc_status()


if __name__ == "__main__":
    start_rtg_plc_driver()
