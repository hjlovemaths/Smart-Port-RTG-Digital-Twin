"""Real TCP/ZMQ sockets, paired with the actual Kit adapter update method."""
import builtins
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import pytest

zmq = pytest.importorskip("zmq")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(ROOT / "omniverse/scripts"))
from rtg_plc_zmq_service import PlcZmqService
from rtg_plc_scene_driver import PlcSceneDriver, OWNER_FLAG
from test_rtg_plc_protocol import Controller, message


@pytest.fixture
def rig(monkeypatch):
    context = zmq.Context()
    service = PlcZmqService({"bind_host": "127.0.0.1", "command_port": 0,
                             "state_port": 0, "scene_port": 0,
                             "motion": {"watchdog_s": .2}, "scene_stale_s": .25}, context)
    errors = []
    def run():
        try: service.run()
        except Exception as exc: errors.append(exc)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert service.ready.wait(3)
    assert service.error is None
    publisher, subscriber = context.socket(zmq.PUB), context.socket(zmq.SUB)
    subscriber.setsockopt(zmq.SUBSCRIBE, b"")
    for endpoint in (publisher, subscriber): endpoint.setsockopt(zmq.LINGER, 0)
    publisher.connect(f"tcp://127.0.0.1:{service.command_port}")
    subscriber.connect(f"tcp://127.0.0.1:{service.state_port}")
    monkeypatch.setattr(builtins, OWNER_FLAG, False, raising=False)
    monkeypatch.setitem(sys.modules, "rtg_live_control", SimpleNamespace(
        GANTRY_SOFT_LIMITS=(0, 500), TROLLEY_SOFT_LIMITS=(0, 18.06), HOIST_SOFT_LIMITS=(.5, 18)))
    driver = PlcSceneDriver(service.scene_port)
    driver.stage = object()
    driver.controller = Controller()
    driver.guard_runtime = SimpleNamespace(_guard_manual=lambda *a: None, close=lambda: None)
    rig = SimpleNamespace(service=service, publisher=publisher, subscriber=subscriber, driver=driver)
    rig.last_update = time.monotonic()
    try:
        time.sleep(.15)  # permit subscription propagation, bounded to test setup
        yield rig
    finally:
        driver.close()
        service.stop_event.set()
        thread.join(3)
        publisher.close()
        subscriber.close()
        context.term()
    assert not errors
    assert not thread.is_alive()


def until(rig, predicate, timeout=3, update=True):
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        if update:
            now = time.monotonic()
            rig.driver.update(rig.driver.stage, now - rig.last_update)
            rig.last_update = now
        while rig.subscriber.poll(0):
            packet = rig.subscriber.recv_json()
            seen.append(packet)
            if predicate(packet): return packet
        time.sleep(.005)
    raise AssertionError(f"No matching response; last packets: {seen[-3:]}")


def connect(rig):
    rig.driver.link.thread.start()
    until(rig, lambda p: p["msg_type"] == 1)


def send(rig, cmd, value, multipart=False):
    payload = message(cmd, value)
    payload["time"] = str(time.time_ns())
    raw = json.dumps(payload).encode()
    rig.publisher.send_multipart([b"RTG_220", raw] if multipart else [raw])
    return until(rig, lambda p: isinstance(p["content"], dict)
                 and p["content"].get("command_time") == payload["time"])


def test_zmq_spreader_rotation_sideshift_and_return_publish_real_motion(rig):
    connect(rig)
    send(rig, "ctr_sw_set", 1)
    reply = send(rig, "tool_action", 11)
    assert reply["msg_type"] == 2 and reply["content"]["effect"] == "digital_scene"
    until(rig, lambda p: p["msg_type"] == 1 and p["digital_twin"]["spreader_trim"]["yaw_deg"] > .02)
    send(rig, "tool_move", 21)
    packet = until(rig, lambda p: p["msg_type"] == 1 and
                   p["digital_twin"]["spreader_trim"]["shift_m"] < -.002)
    assert packet["content"]["tool_l_rotate"] == 1
    send(rig, "tool_action", 10)
    send(rig, "tool_move", 20)
    send(rig, "tool_reset", 11)
    send(rig, "tool_reset", 21)
    until(rig, lambda p: p["msg_type"] == 1 and
          p["digital_twin"]["spreader_trim"]["yaw_deg"] == 0 and
          p["digital_twin"]["spreader_trim"]["shift_m"] == 0)


def test_no_scene_never_publishes_fabricated_parameters_and_rejects_commands(rig):
    packet = until(rig, lambda p: p["msg_type"] == 3, update=False)
    assert packet["digital_twin"]["scene_connected"] is False
    packet = send(rig, "vel", [.5, 0])
    assert packet["msg_type"] == 3
    assert not packet["content"]["accepted"]
    assert rig.driver.controller.positions == [100, 5, 10]


def test_real_command_movement_readback_watchdog_and_multipart_stop(rig):
    connect(rig)
    assert send(rig, "ctr_sw_set", 1)["content"]["accepted"]
    assert getattr(builtins, OWNER_FLAG)
    assert send(rig, "vel", [.5, .1])["content"]["accepted"]
    packet = until(rig, lambda p: p["msg_type"] == 1 and p["content"]["little_car_pos"] > 5)
    # PUB samples are asynchronous; compare with actual controller reads,
    # rather than the newer pose at the instant this subscriber consumes it.
    pose = (packet["content"]["bigcar_elec_distance"], packet["content"]["smallcar_pos"],
            packet["content"]["lifting_pos"])
    assert packet["content"]["bigcar_elec_distance"] == packet["content"]["bigcar_engine_distance"]
    assert pose in rig.driver.controller.reads
    assert packet["content"]["little_car_vel"] > 0
    packet = until(rig, lambda p: p["msg_type"] == 1 and
                   p["digital_twin"]["stop_reason"] == "command_timeout")
    assert packet["content"]["little_car_vel"] == 0
    before = list(rig.driver.controller.positions)
    assert send(rig, "ctr_sw_set", 0, multipart=True)["content"]["accepted"]
    assert not getattr(builtins, OWNER_FLAG)
    assert rig.driver.controller.positions == before


def test_bad_command_produces_error_without_killing_parameter_stream(rig):
    connect(rig)
    send(rig, "ctr_sw_set", 1)
    packet = send(rig, "beam_tilt_action", 1)
    assert packet["msg_type"] == 3 and "unsupported" in packet["content"]["exp_desc"]
    assert until(rig, lambda p: p["msg_type"] == 1)["content"]["big_car_pos"] == 100
    rig.publisher.send(b"not JSON")
    until(rig, lambda p: p["msg_type"] == 3)
    assert send(rig, "ctr_sw_set", 0)["content"]["accepted"]


def test_rcms_bigcarvel_direction_feedback_stop_and_control_guard_over_real_zmq(rig):
    connect(rig)
    denied = send(rig, "bigcarvel", [2, 2, 2])
    assert not denied["content"]["accepted"] and "control is off" in denied["content"]["exp_desc"]
    send(rig, "ctr_sw_set", 1)
    assert send(rig, "bigcarvel", [2, 2, 2])["content"]["accepted"]
    packet = until(rig, lambda p: p["msg_type"] == 1 and p["content"]["bigcar_elec_distance"] < 100)
    assert packet["content"]["bigcar1_vel_fb"] == pytest.approx(2, abs=.01)
    assert packet["content"]["bigcar_left_state"] == 1
    assert send(rig, "bigcarstop", None)["content"]["accepted"]
    until(rig, lambda p: p["msg_type"] == 1 and p["content"]["bigcar1_vel_fb"] == 0)
    stopped = tuple(rig.driver.controller.positions)
    assert send(rig, "heartbeat", 1)["content"]["accepted"]
    assert tuple(rig.driver.controller.positions) == stopped


def test_latest_combined_move_and_estop_reconnect_require_explicit_reset(rig):
    connect(rig)
    send(rig, "ctr_sw_set", 1)
    reply = send(rig, "move", [.5, .25, 10.0, 20.0, .4, .4])
    assert reply["content"]["accepted"]
    packet = until(rig, lambda p: p["msg_type"] == 1 and p["content"]["smallcar_pos"] > 5)
    assert packet["content"]["bigcar_elec_distance"] < 100
    assert packet["content"]["lifting_pos"] > 10
    assert send(rig, "estop", None)["content"]["accepted"]
    old_epoch = rig.driver.link.epoch
    rig.driver.link.reset_event.set()
    packet = until(rig, lambda p: p["msg_type"] == 1 and rig.driver.link.epoch > old_epoch)
    assert packet["content"]["estop_state"] == 0
    denied = send(rig, "ctr_sw_set", 1)
    assert denied["content"]["accepted"] is False
    assert "emergency stop" in denied["content"]["exp_desc"]
    assert send(rig, "reset_one_key", None)["content"]["accepted"]
    assert not rig.driver.engine.armed
    assert send(rig, "ctr_sw_set", 1)["content"]["accepted"]


def test_stalled_scene_rejects_commands_instead_of_replaying_them(rig):
    connect(rig)
    until(rig, lambda p: p["msg_type"] == 3 and not p.get("digital_twin", {}).get("scene_connected", True),
          update=False)
    payload = message("ctr_sw_set", 1)
    rig.publisher.send_json(payload)
    packet = until(rig, lambda p: p["msg_type"] == 3 and p["content"].get("cmd") == "ctr_sw_set", update=False)
    assert not packet["content"]["accepted"]
    for _ in range(20):
        rig.driver.update(rig.driver.stage, .01)
        time.sleep(.005)
    assert not rig.driver.engine.armed


def test_expired_scene_command_and_new_connection_cannot_resume_motion(rig):
    connect(rig)
    rig.driver.link.incoming.put((rig.driver.link.epoch, {"type": "command", "id": 999,
        "expires_ms": 0, "message": message("ctr_sw_set", 1)}))
    packet = until(rig, lambda p: p["msg_type"] == 3 and p["content"].get("request_id") == 999)
    assert "expired" in packet["content"]["exp_desc"]
    send(rig, "ctr_sw_set", 1)
    send(rig, "vel", [1, 0])
    rig.driver.link.reset_event.set()
    old_epoch = rig.driver.link.epoch
    until(rig, lambda p: p["msg_type"] == 1 and rig.driver.link.epoch > old_epoch)
    assert not rig.driver.engine.armed
    assert not getattr(builtins, OWNER_FLAG)


@pytest.mark.parametrize("config", [{"command_port": 5515, "state_port": 5515},
    {"command_port": True}, {"publish_hz": float("nan")}, {"scene_stale_s": 0},
    {"motion": {"watchdog_s": 0}}])
def test_invalid_service_configuration_fails_before_binding(config):
    with pytest.raises(ValueError): PlcZmqService(config)
