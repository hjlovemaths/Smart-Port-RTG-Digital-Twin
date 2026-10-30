"""Example client for the project's RTG PLC-compatible ZMQ service."""
import argparse
import json
import sys
import time

import zmq


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--command-port", type=int, default=5515)
    parser.add_argument("--state-port", type=int, default=5516)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--watch", action="store_true")
    group.add_argument("--send", help="One protocol JSON command; print its result")
    group.add_argument("--physics", choices=("on", "off"), help="Send sim_physics=1/0; requires control enabled and axes stopped")
    group.add_argument("--demo", action="store_true", help="Arm, drive for a duration, then stop/disarm")
    parser.add_argument("--trolley", type=float, default=None)
    parser.add_argument("--hoist", type=float, default=0)
    parser.add_argument("--gantry", type=float, default=0)
    parser.add_argument("--seconds", type=float, default=2)
    args = parser.parse_args()
    if not 0 < args.seconds <= 30:
        parser.error("--seconds must be 0..30")
    if args.gantry and (args.hoist or args.trolley):
        parser.error("gantry mode is exclusive; do not combine it with trolley/hoist speeds")
    if args.trolley is None:
        args.trolley = 0 if args.gantry or args.hoist else .5
    with zmq.Context() as context:
        publisher = context.socket(zmq.PUB)
        subscriber = context.socket(zmq.SUB)
        for endpoint in (publisher, subscriber):
            endpoint.setsockopt(zmq.LINGER, 250)
        subscriber.setsockopt(zmq.SUBSCRIBE, b"")
        subscriber.connect(f"tcp://{args.host}:{args.state_port}")
        publisher.connect(f"tcp://{args.host}:{args.command_port}")
        latest_state = None

        def receive():
            nonlocal latest_state
            reply = subscriber.recv_json()
            if reply.get("msg_type") == 1:
                latest_state = reply
            return reply

        def pose(packet):
            content = packet["content"]
            return {"gantry_m": content["bigcar_elec_distance"], "trolley_m": content["smallcar_pos"],
                    "hoist_m": content["lifting_pos"]}

        def wait_state(predicate=lambda packet: True, timeout=3):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if subscriber.poll(100):
                    packet = receive()
                    if packet.get("msg_type") == 1 and predicate(packet):
                        return packet
            raise RuntimeError("No fresh scene parameters; check that the Kit adapter is running")

        def command(cmd, value, match="CTR"):
            payload = {"match": match, "cmd": cmd, "value": value,
                       "CRC": "0", "time": str(time.time_ns() // 1_000_000)}
            publisher.send_json(payload)
            return payload

        def result(payload):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if subscriber.poll(100):
                    reply = receive()
                    content = reply.get("content", {})
                    if isinstance(content, dict) and content.get("command_time") == payload["time"] and content.get("cmd") == payload["cmd"]:
                        print(json.dumps(reply, ensure_ascii=False))
                        if not content.get("accepted"):
                            raise RuntimeError(content.get("exp_desc", "command rejected"))
                        return reply
            raise RuntimeError("No command acknowledgement; check both ports and the Kit scene adapter")

        try:
            # PUB/SUB needs time to propagate subscriptions (the slow-joiner rule).
            time.sleep(.35)
            if args.watch:
                print("--watch 仅订阅参数，不发送运动指令。", file=sys.stderr)
                while True:
                    if subscriber.poll(1000):
                        print(json.dumps(receive(), ensure_ascii=False))
                return
            if args.send:
                payload = json.loads(args.send)
                payload.setdefault("time", str(time.time_ns() // 1_000_000))
                publisher.send_json(payload)
                result(payload)
                return
            if args.physics:
                result(command("sim_physics", int(args.physics == "on")))
                return
            initial = pose(wait_state())
            print("运动前位置: " + json.dumps(initial, ensure_ascii=False))
            try:
                result(command("ctr_sw_set", 1))
                deadline = time.monotonic() + args.seconds
                next_send = next_display = time.monotonic()
                pending = set()
                while time.monotonic() < deadline:
                    now = time.monotonic()
                    if now >= next_send:
                        if args.gantry:
                            payload = command("set_ctrl_cmd", {"switch": 1, "speed": [0, 0],
                                              "bigCarSpeed": args.gantry, "toolAction": 0, "midLock": 0})
                        else:
                            payload = command("vel", [args.trolley, args.hoist])
                        pending.add((payload["cmd"], payload["time"]))
                        next_send = now + .1
                    # Receive replies without holding up the velocity heartbeat.
                    timeout_ms = max(1, min(50, int((min(next_send, deadline) - now) * 1000)))
                    if subscriber.poll(timeout_ms):
                        reply = receive()
                        content = reply.get("content", {})
                        if isinstance(content, dict):
                            key = (content.get("cmd"), content.get("command_time"))
                            if key in pending:
                                pending.remove(key)
                                if not content.get("accepted"):
                                    raise RuntimeError(content.get("exp_desc", "command rejected"))
                            elif reply.get("msg_type") == 3 and reply.get("digital_twin", {}).get("scene_connected") is False:
                                raise RuntimeError(content.get("exp_desc", "scene disconnected"))
                    if latest_state is not None and now >= next_display:
                        next_display = now + .5
                        print("当前位置: " + json.dumps(pose(latest_state), ensure_ascii=False))
            finally:
                # The watchdog is a fallback; always issue an explicit stop too.
                original_error = sys.exc_info()[1]
                for attempt in range(3):
                    command("vel", [0, 0])
                    stopped = command("ctr_sw_set", 0)
                    try:
                        result(stopped)
                        break
                    except RuntimeError as stop_error:
                        if attempt < 2:
                            time.sleep(.25)
                        elif original_error is None:
                            raise
                        else:
                            print("停止确认失败: " + str(stop_error), file=sys.stderr)
            final = pose(wait_state(lambda packet: packet["content"].get("ctr_on_state") == 0 and
                                    packet["content"].get("PLC_long_time", 0) >= int(stopped["time"])))
            delta = {axis: round(final[axis] - initial[axis], 4) for axis in initial}
            print("运动后位置: " + json.dumps(final, ensure_ascii=False))
            print("实际位移（米）: " + json.dumps(delta, ensure_ascii=False))
            if all(abs(distance) < .001 for distance in delta.values()):
                raise RuntimeError("Commands were accepted but the RTG did not move; inspect limits and control state")
        except KeyboardInterrupt:
            pass
        finally:
            publisher.close()
            subscriber.close()


if __name__ == "__main__":
    main()
