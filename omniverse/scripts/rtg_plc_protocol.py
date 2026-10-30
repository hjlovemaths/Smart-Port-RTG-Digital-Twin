"""PLC document command semantics, independent of ZMQ, Kit and USD.

The controller implements get_positions, set_*_safe and spreader-size methods.
All calls to this class must run on the controller's owning (Kit main) thread.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass


def number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def choice(value, allowed, name="value"):
    if isinstance(value, bool) or not isinstance(value, int) or value not in allowed:
        raise ValueError(f"{name} must be one of {sorted(allowed)}")
    return value


@dataclass(frozen=True)
class PlcConfig:
    watchdog_s: float = 0.6
    max_dt: float = 0.1
    gantry_speed_mps: float = 3.0
    trolley_speed_mps: float = 3.0
    hoist_speed_mps: float = 1.0
    slow_speed_mps: float = 0.3
    hoist_reference_m: float = 18.0
    rope_pos_mode: str = "rope_length"
    hoist_positive_up: bool = True
    spreader_yaw_limit_deg: float = 5.0
    spreader_shift_limit_m: float = 0.3
    spreader_yaw_speed_deg_s: float = 1.0
    spreader_shift_speed_mps: float = 0.05
    spreader_jog_timeout_s: float = 10.0

    def __post_init__(self):
        for name in ("watchdog_s", "max_dt", "gantry_speed_mps", "trolley_speed_mps",
                     "hoist_speed_mps", "slow_speed_mps", "hoist_reference_m",
                     "spreader_yaw_limit_deg", "spreader_shift_limit_m", "spreader_yaw_speed_deg_s",
                     "spreader_shift_speed_mps", "spreader_jog_timeout_s"):
            if number(getattr(self, name), name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.rope_pos_mode not in {"rope_length", "height"}:
            raise ValueError("rope_pos_mode must be rope_length or height")
        if not isinstance(self.hoist_positive_up, bool):
            raise ValueError("hoist_positive_up must be boolean")
        if not .005 <= self.max_dt <= .1 or not self.max_dt <= self.watchdog_s <= 5:
            raise ValueError("max_dt must be 0.005..0.1 seconds; watchdog_s must be max_dt..5 seconds")
        if self.spreader_yaw_limit_deg > 15 or self.spreader_shift_limit_m > 1 or self.spreader_jog_timeout_s > 30:
            raise ValueError("spreader limits must be <=15 degrees, <=1 metre and jog timeout <=30 seconds")


class PlcEngine:
    def __init__(self, controller, limits, config=None, guard=None, on_arm=None, clock=time.monotonic, interaction=None):
        self.controller = controller
        self.limits = tuple(tuple(pair) for pair in limits)
        self.config = config or PlcConfig()
        self.guard = guard or (lambda: None)
        self.on_arm = on_arm or (lambda active: None)
        self.clock = clock
        self.interaction = interaction
        self.armed = False
        self.requested = [0.0, 0.0, 0.0]  # gantry, trolley, height; metres/second
        self.actual = [0.0, 0.0, 0.0]
        self._integrated = None
        self._last_positions = tuple(controller.get_positions())
        self.last_motion = None
        self._axis_motion_at = [None, None, None]
        self.gantry_handle_vel = 0.0  # RCMS: positive left, negative right
        self.estopped = False
        self.stop_reason = "control_off"
        self.left = self.right = 0
        self.gantry_mode = 3
        self.flags = {"tool_on_state": 0, "lock_state": 0, "unlock_state": 1,
                      "big_car_clamp_status": 0, "test_delay_state": 0,
                      "estop_state": 1, "comm_test": 0, "auto_correct_st": 0,
                      "correct_ctrl_state_fb": 0, "bypass_fb": 0,
                      "land_bypass_fb": 0, "spreader_bypass_fb": 0}
        self.last_command = None
        self.tick_dt_s = 0.0
        self.integration_dt_s = 0.0
        self.trim_requested = [0.0, 0.0]  # yaw deg/s, sideshift m/s; positive = left
        self.trim_actual = [0.0, 0.0]
        self.trim_target = [None, None]
        self.trim_deadline = [None, None]
        if interaction is not None and interaction.carried:
            self.flags.update(lock_state=1, unlock_state=0)

    def stop(self, reason, disarm=False):
        self.requested = [0.0, 0.0, 0.0]
        self.actual = [0.0, 0.0, 0.0]
        self.last_motion = None
        self._axis_motion_at = [None, None, None]
        self.gantry_handle_vel = 0.0
        self._integrated = None
        self.trim_requested = [0.0, 0.0]
        self.trim_actual = [0.0, 0.0]
        self.trim_target = [None, None]
        self.trim_deadline = [None, None]
        self.stop_reason = reason
        if disarm:
            self.armed = False
            self.on_arm(False)

    def _speeds(self, values, names, caps):
        if not isinstance(values, list) or len(values) != len(names):
            raise ValueError(f"value must contain {len(names)} speeds")
        result = [number(value, name) for value, name in zip(values, names)]
        for value, name, cap in zip(result, names, caps):
            if abs(value) > cap:
                raise ValueError(f"{name} exceeds +/-{cap} m/s")
        return result

    def _control_required(self):
        if self.estopped:
            raise ValueError("digital emergency stop is latched; send reset_one_key=null then ctr_sw_set=1")
        if not self.armed:
            raise ValueError("control is off; send ctr_sw_set=1 first")
        self.guard()
        if self.interaction is not None and self.interaction.enabled:
            self.interaction.backend.check()

    def _trim_pose(self):
        getter = getattr(self.controller, "get_spreader_trim", None)
        return tuple(getter()) if callable(getter) else (0.0, 0.0)

    def _require_trim(self):
        if not callable(getattr(self.controller, "set_spreader_trim", None)):
            raise ValueError("spreader trim geometry is unavailable; reload the updated RTG controller")

    def _validate_trim_actions(self, codes):
        for code in codes:
            if number(code, "spreader action") not in (10, 11, 20, 21):
                raise ValueError("spreader action must be 10/11/20/21")
        if any(code in (11, 21) for code in codes):
            self._require_trim()
            self._control_required()

    def _set_trim_action(self, index, code, lease):
        speed = (self.config.spreader_yaw_speed_deg_s, self.config.spreader_shift_speed_mps)[index]
        self.trim_requested[index] = speed if code == 11 else -speed if code == 21 else 0.0
        self.trim_target[index] = None
        self.trim_deadline[index] = self.clock() + lease if self.trim_requested[index] else None
        self.stop_reason = "" if any(self.trim_requested) or any(self.requested) else "commanded_stop"

    def _tick_trim(self, dt, now):
        before = self._trim_pose()
        pose = list(before)
        limits = (self.config.spreader_yaw_limit_deg, self.config.spreader_shift_limit_m)
        for index in range(2):
            if self.trim_deadline[index] is not None and now > self.trim_deadline[index]:
                self.trim_requested[index] = 0.0
                self.trim_target[index] = self.trim_deadline[index] = None
                self.stop_reason = "spreader_command_timeout"
            speed = self.trim_requested[index] if self.armed else 0.0
            if not speed or not dt:
                continue
            target = pose[index] + speed * dt
            if self.trim_target[index] is not None and (target - self.trim_target[index]) * speed >= 0:
                target = self.trim_target[index]
                self.trim_target[index] = None
                self.trim_requested[index] = 0.0
                self.trim_deadline[index] = None
            bounded = max(-limits[index], min(limits[index], target))
            if bounded != target or abs(bounded) >= limits[index]:
                self.trim_requested[index] = 0.0
                self.trim_target[index] = self.trim_deadline[index] = None
                self.stop_reason = "spreader_limit"
            pose[index] = bounded
        if pose != list(before):
            if self.interaction is not None and self.interaction.enabled:
                accepted = self.interaction.move_trim(pose)
                if tuple(accepted) != tuple(pose):
                    self.trim_requested = [0., 0.]
                    self.trim_target = self.trim_deadline = [None, None]
                    self.stop_reason = "collision_stop"
            else:
                self.controller.set_spreader_trim(*pose)
        after = self._trim_pose()
        self.trim_actual = [(b - a) / dt if dt else 0.0 for a, b in zip(before, after)]

    def _velocity(self, gantry, trolley, hoist, axes=(0, 1, 2), gantry_handle=None):
        values = [gantry, trolley, hoist if self.config.hoist_positive_up else -hoist]
        updated = list(self.requested)
        for index in axes:
            updated[index] = values[index]
        if any(updated) or gantry_handle:
            if self.estopped:
                raise ValueError("digital emergency stop is latched; send reset_one_key=null then ctr_sw_set=1")
            if not self.armed:
                raise ValueError("control is off; send ctr_sw_set=1 first")
            self.guard()
        self.requested = updated
        if self._integrated is None:
            self._integrated = list(self.controller.get_positions())
        now = self.clock()
        for index in axes:
            self._axis_motion_at[index] = now if updated[index] else None
        self.last_motion = max((stamp for stamp in self._axis_motion_at if stamp is not None), default=None)
        if 0 in axes:
            self.gantry_handle_vel = -gantry if gantry_handle is None else gantry_handle
        self.stop_reason = "" if any(self.requested) else "commanded_stop"

    def apply(self, message):
        if not isinstance(message, dict):
            raise ValueError("command must be a JSON object")
        if message.get("match") not in {"CTR", "UI", "CALI", "custom"}:
            raise ValueError("match must be CTR, UI, CALI or custom")
        if message.get("CRC", "0") not in (0, "0") or isinstance(message.get("CRC"), bool):
            raise ValueError("only placeholder CRC=0 is defined by the supplied protocol")
        raw_cmd, value = message.get("cmd"), message.get("value")
        cmd = raw_cmd.strip() if isinstance(raw_cmd, str) else raw_cmd
        if not isinstance(cmd, str):
            raise ValueError("cmd must be a string")
        config = self.config
        if cmd == "ctr_sw_set":
            choice(value, {0, 1})
            if value:
                if self.estopped:
                    raise ValueError("digital emergency stop is latched; send reset_one_key=null first")
                self.guard()
                if not self.armed:
                    self.stop("control_enabled")
                    self.armed = True
                    self.on_arm(True)
            else:
                self.stop("control_off", disarm=True)
        elif cmd == "vel":
            trolley, hoist = self._speeds(value, ("trolley", "hoist"),
                                          (config.trolley_speed_mps, config.hoist_speed_mps))
            self._velocity(0, trolley, hoist, axes=(1, 2))
        elif cmd == "bigcarvel":
            handle, first, second = self._speeds(value, ("gantry handle", "gantry drive 1", "gantry drive 2"),
                                                  (config.gantry_speed_mps,) * 3)
            if not math.isclose(first, second, rel_tol=0, abs_tol=1e-6):
                raise ValueError("unequal gantry drive speeds are unsupported by this single-axis RTG; use equal drive 1/2 speeds")
            # RCMS positive means left. The scene's left direction decreases
            # its continuous gantry coordinate; the handle is not a third drive.
            self._velocity(-first, 0, 0, axes=(0,), gantry_handle=handle)
        elif cmd == "bigcarstop":
            if value is not None:
                raise ValueError("bigcarstop.value must be null")
            self._velocity(0, 0, 0, axes=(0,))
        elif cmd == "heartbeat":
            choice(value, {1})  # heartbeat never arms or renews axis velocities
        elif cmd == "sim_physics":
            choice(value, {0, 1})
            self._control_required()
            if self.interaction is None:
                raise ValueError("reload the updated Kit PLC scene adapter before sim_physics")
            if any(self.requested) or any(self.actual) or any(self.trim_requested) or any(self.trim_actual):
                raise ValueError("stop all axes and spreader trim before changing sim_physics")
            self.interaction.set_enabled(bool(value))
            self.on_arm(self.armed)
            self.flags.update(lock_state=int(bool(self.interaction.carried)), unlock_state=int(not self.interaction.carried))
        elif cmd in {"tool_action", "tool_move"}:
            choice(value, {10, 11, 20, 21})
            self._validate_trim_actions([value])
            self._set_trim_action(0 if cmd == "tool_action" else 1, value, config.spreader_jog_timeout_s)
        elif cmd == "tool_reset":
            choice(value, {11, 21})
            self._require_trim()
            self._control_required()
            index = 0 if value == 11 else 1
            current = self._trim_pose()[index]
            self._set_trim_action(index, 21 if current > 0 else 11, config.spreader_jog_timeout_s)
            self.trim_target[index] = 0.0
            if abs(current) < 1e-9:
                self._set_trim_action(index, 10, config.spreader_jog_timeout_s)
        elif cmd == "move":
            if not isinstance(value, list) or len(value) != 6:
                raise ValueError("move.value must contain [trolley, hoist, rotation, micro-move, gantry1, gantry2]")
            trolley, hoist = self._speeds(value[:2], ("trolley", "hoist"),
                                         (config.trolley_speed_mps, config.hoist_speed_mps))
            # Validate every field before changing any of the five axes.
            self._validate_trim_actions(value[2:4])
            first, second = self._speeds(value[4:], ("gantry drive 1", "gantry drive 2"),
                                         (config.gantry_speed_mps,) * 2)
            if not math.isclose(first, second, rel_tol=0, abs_tol=1e-6):
                raise ValueError("unequal gantry drive speeds are unsupported by this single-axis RTG")
            self._velocity(-first, trolley, hoist)
            for index, code in enumerate(value[2:4]):
                self._set_trim_action(index, code, config.watchdog_s)
        elif cmd == "estop":
            if value is not None:
                raise ValueError("estop.value must be null")
            self.estopped = True
            self.flags["estop_state"] = 0
            self.stop("digital_estop", disarm=True)
        elif cmd == "comm_test":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2147483647:
                raise ValueError("comm_test.value must be an integer in 0..2147483647")
            self.flags["comm_test"] = value
        elif cmd in {"autocorrection", "bigcar_correction"}:
            choice(value, {0, 1})
            field = "auto_correct_st" if cmd == "autocorrection" else "correct_ctrl_state_fb"
            self.flags[field] = value  # virtual enable only; no yaw/differential-drive model
        elif cmd == "manual_mc":
            choice(value, {0, 1})
            if value:
                # This simulator already uses remote manual control. Selecting
                # it stops previous velocities without silently arming control.
                self.stop("manual_mode_selected")
        elif cmd == "set_bypass":
            choice(value, {10, 11, 20, 21, 30, 31})
            field = {1: "bypass_fb", 2: "land_bypass_fb", 3: "spreader_bypass_fb"}[value // 10]
            self.flags[field] = 0 if value % 10 == 0 else 1 - self.flags[field]
        elif cmd == "set_ctrl_cmd":
            if not isinstance(value, dict):
                raise ValueError("set_ctrl_cmd.value must be an object")
            switch = choice(value.get("switch"), {0, 1}, "switch")
            for field in ("toolAction", "midLock"):
                choice(value.get(field, 0), {0}, field)  # no tilt/midlock model on this RTG
            speeds = self._speeds(value.get("speed", [0, 0]), ("trolley", "hoist"),
                                  (config.trolley_speed_mps, config.hoist_speed_mps))
            gantry = number(value.get("bigCarSpeed", 0), "bigCarSpeed")
            if abs(gantry) > config.gantry_speed_mps:
                raise ValueError("bigCarSpeed exceeds configured speed limit")
            self._velocity(gantry if switch else 0, speeds[0] if not switch else 0,
                           speeds[1] if not switch else 0)
        elif cmd in {"tool_size", "tool_sw_set", "wheel_clamp", "lock"}:
            choice(value, {1, 2} if cmd == "tool_size" else {0, 1})
            self._control_required()
            if cmd == "tool_size":
                size = 20 if value == 1 else 40
                if self.interaction is not None and self.interaction.carried:
                    raise ValueError("cannot change spreader size with a locked container")
                if any(self.requested) or any(self.actual) or any(self.trim_requested) or any(self.trim_actual):
                    raise ValueError("stop axis motion before changing spreader size")
                actual = self.controller.set_spreader_size(size, smooth=False)
                if actual is None or abs(actual - size) > 0.05:
                    raise ValueError("spreader-size geometry is unavailable")
            elif cmd == "tool_sw_set":
                self.flags["tool_on_state"] = value
            elif cmd == "wheel_clamp":
                self.flags["big_car_clamp_status"] = value
            else:
                if self.interaction is not None and self.interaction.enabled:
                    if any(self.requested) or any(self.actual) or any(self.trim_requested) or any(self.trim_actual):
                        raise ValueError("stop all axes and spreader trim before lock/unlock")
                    if value and not self.flags["tool_on_state"]:
                        raise ValueError("enable the spreader pump with tool_sw_set=1 before locking")
                    self.interaction.set_lock(bool(value))
                self.flags.update(lock_state=value, unlock_state=1 - value)
        elif cmd in {"set_big_car_left", "set_big_car_right"}:
            choice(value, {0, 1, 2})
            if cmd.endswith("left"):
                self.left = value
            else:
                self.right = value
        elif cmd == "set_big_car_move_mode":
            self.gantry_mode = choice(value, {1, 2, 3})
        elif cmd == "plc_test_delay":
            self.flags["test_delay_state"] = choice(value, {0, 1})
        elif cmd == "reset_one_key":
            if value is not None:
                raise ValueError("reset_one_key.value must be null")
            self.estopped = False
            self.flags["estop_state"] = 1
            self.stop("reset", disarm=True)
        else:
            raise ValueError(f"unsupported RTG command: {cmd}; no action was applied")
        self.last_command = {"cmd": cmd, "time": message.get("time")}
        virtual = cmd in {"autocorrection", "bigcar_correction", "set_bypass", "tool_sw_set", "lock"}
        if cmd == "lock" and self.interaction is not None and self.interaction.enabled:
            virtual = False
        return {"status": 2,
                "state_desc": "virtual PLC state recorded; no hardware mechanism simulated" if virtual else "digital RTG command applied",
                "effect": "virtual_state" if virtual else "digital_scene",
                "accepted": True, "cmd": raw_cmd, "command_time": message.get("time")}

    def tick(self, dt, connected=True):
        if not connected:
            self.stop("scene_link_disconnected", disarm=True)
            if self.interaction is not None:
                self.interaction.tick(min(max(dt, 0), .1))
            return
        sample_dt = max(0.0, number(dt, "dt"))
        self.tick_dt_s = sample_dt
        self.integration_dt_s = 0.0
        if self.armed:
            try:
                self.guard()
            except Exception:
                self.stop("control_conflict", disarm=True)
                raise
        now = self.clock()
        expired = [index for index, stamp in enumerate(self._axis_motion_at)
                   if stamp is not None and now - stamp > self.config.watchdog_s]
        for index in expired:
            self.requested[index] = 0.0
            self._axis_motion_at[index] = None
            if index == 0:
                self.gantry_handle_vel = 0.0
        if expired:
            self.last_motion = max((stamp for stamp in self._axis_motion_at if stamp is not None), default=None)
            self.stop_reason = "command_timeout"
        if sample_dt > self.config.watchdog_s:
            self.stop("frame_stall")
        self._tick_trim(sample_dt, now)
        before = self.controller.get_positions()
        speeds = list(self.requested)
        gantry = speeds[0]
        side = self.left if gantry < 0 else self.right
        if self.flags["big_car_clamp_status"] or self.gantry_mode == 1 or side == 1:
            speeds[0] = 0.0
        elif self.gantry_mode == 2 or side == 2:
            speeds[0] = max(-self.config.slow_speed_mps, min(self.config.slow_speed_mps, gantry))
        if sample_dt > 0 and any(speeds) and self.armed:
            # max_dt bounds each integration step, not the whole frame.
            # Discarding the rest of a slow/minimized frame reduced real speed.
            remaining = sample_dt
            while remaining > 1e-9:
                step = min(remaining, self.config.max_dt)
                for index, method in enumerate(("set_gantry_safe", "set_trolley_safe", "set_hoist_safe")):
                    if speeds[index]:
                        # Preserve continuous targets across the trolley's flat
                        # truck-stop calibration instead of inverse-mapping it.
                        target = self._integrated[index] + speeds[index] * step
                        target = max(self.limits[index][0], min(self.limits[index][1], target))
                        if self.interaction is not None and self.interaction.enabled:
                            applied = self.interaction.move_axis(index, target)
                            if abs(applied - target) > 1e-8:
                                self.stop_reason = "collision_stop"
                                self.requested[index] = speeds[index] = 0.0
                                self._axis_motion_at[index] = None
                            self._integrated[index] = applied
                        else:
                            getattr(self.controller, method)(target)
                            self._integrated[index] = target
                remaining -= step
            self.integration_dt_s = sample_dt
        after = self.controller.get_positions()
        self.actual = [(b - a) / sample_dt if sample_dt > 0 else 0.0
                       for a, b in zip(self._last_positions, after)]
        self._last_positions = tuple(after)
        if self.interaction is not None:
            self.interaction.tick(min(sample_dt, .1))

    def snapshot(self):
        gantry, trolley, height = self.controller.get_positions()
        g_vel, t_vel, h_vel = self.actual
        rope_length = self.config.rope_pos_mode == "rope_length"
        size = self.controller.current_spreader_size_ft()
        content = dict(self.flags, ctr_on_state=int(self.armed), ctr_mod_remote=int(self.armed),
                       ctr_mod_local=0, ctr_mod_auto=0, ctr_mod_manual=int(self.armed),
                       big_car_pos=gantry, big_car_vel=g_vel, little_car_pos=trolley,
                       little_car_vel=t_vel, little_car_vel_cb=t_vel,
                       rope_pos=self.config.hoist_reference_m - height if rope_length else height,
                       rope_vel=-h_vel if rope_length else h_vel,
                       rope_vel_cb=-h_vel if rope_length else h_vel,
                       spreader_up_status=int(h_vel > 0), spreader_down_status=int(h_vel < 0),
                       little_car_forward_cmd_state=int(t_vel > 0), little_car_back_cmd_state=int(t_vel < 0),
                       rope_up_cmd_state=int(h_vel > 0), rope_down_cmd_state=int(h_vel < 0),
                       big_car_move_left=int(g_vel < 0), big_car_move_right=int(g_vel > 0),
                       big_car_move_left_state=self.left, big_car_move_right_state=self.right,
                       big_car_left_forbidden=int(self.left == 1 or self.gantry_mode == 1),
                       big_car_right_forbidden=int(self.right == 1 or self.gantry_mode == 1),
                       big_car_left_limit=int(self.left == 2 or self.gantry_mode == 2),
                       big_car_right_limit=int(self.right == 2 or self.gantry_mode == 2),
                       big_car_brake_status=int(g_vel != 0), tool_20f=int(size is not None and abs(size - 20) < .05),
                       tool_40f=int(size is not None and abs(size - 40) < .05), tool_45f=0,
                       little_car_handle_value=self.requested[1], spreader_handle_value=self.requested[2],
                       big_car_handle_value=self.requested[0], total_height=round(self.config.hoist_reference_m * 100),
                       PLC_long_time=time.time_ns() // 1_000_000, CRC=0)
        for index, names in enumerate((("big_car_low_limit", "big_car_high_limit"),
                                       ("little_car_back_limit_state", "little_car_forward_limit_state"),
                                       ("rope_down_limit_state", "rope_up_limit_state"))):
            pos = (gantry, trolley, height)[index]
            content[names[0]] = int(pos <= self.limits[index][0] + 1e-5)
            content[names[1]] = int(pos >= self.limits[index][1] - 1e-5)
        # RCMS reads these names from YsPLCMonitorNewModel/PLCContent. Keep the
        # original Word-protocol keys for existing clients, but publish RCMS
        # engineering values directly. lifting_pos is height, NOT rope length.
        content.update(
            bigcar_elec_distance=gantry, bigcar_engine_distance=gantry,
            bigcar1_vel_fb=-g_vel, bigcar2_vel_fb=-g_vel,
            bigcar_handle_vel=self.gantry_handle_vel,
            smallcar_pos=trolley, smallcar_vel=t_vel,
            lifting_pos=height, lifting_vel=h_vel,
            tool_l_rotate=int(self.trim_requested[0] > 0),
            tool_r_rotate=int(self.trim_requested[0] < 0),
            to_mid_state=int(abs(self._trim_pose()[0]) < 1e-6),
            ctr_off_state=int(not self.armed),
            sprdp_on_state=self.flags["tool_on_state"],
            sprdp_off_state=1 - self.flags["tool_on_state"],
            bigcar_left_state=int(g_vel < 0), bigcar_right_state=int(g_vel > 0),
            smallcar_front_state=int(t_vel > 0), smallcar_back_state=int(t_vel < 0),
            lifting_up_state=int(h_vel > 0), lifting_down_state=int(h_vel < 0),
            limit_smallcar_back=content["little_car_back_limit_state"],
            limit_smallcar_front=content["little_car_forward_limit_state"],
            limit_lifting_down=content["rope_down_limit_state"],
            limit_lifting_up=content["rope_up_limit_state"],
            not_allow_bigcar_left=int(bool(content["big_car_left_forbidden"] or
                                          self.flags["big_car_clamp_status"] or content["big_car_low_limit"])),
            not_allow_bigcar_right=int(bool(content["big_car_right_forbidden"] or
                                           self.flags["big_car_clamp_status"] or content["big_car_high_limit"])),
        )
        result = {"content": content, "digital_twin": {"scene_connected": True, "simulated": True,
                "telemetry_schema": "rcms_rtg_with_word_protocol_aliases",
                "gantry_distance_reference": "single simulated gantry work-centre encoder, metres from 1C/001 start",
                "lifting_pos_reference": "spreader engineering height in metres; positive velocity is up",
                "rcms_gantry_positive_direction": "left; decreases the scene gantry distance",
                "hoist_height_m": height, "rope_pos_mode": self.config.rope_pos_mode,
                "hoist_positive_up": self.config.hoist_positive_up, "stop_reason": self.stop_reason,
                "tick_dt_s": self.tick_dt_s, "integration_dt_s": self.integration_dt_s,
                "last_command": self.last_command,
                "spreader_trim": {"yaw_deg": self._trim_pose()[0], "shift_m": self._trim_pose()[1],
                    "yaw_velocity_deg_s": self.trim_actual[0], "shift_velocity_mps": self.trim_actual[1],
                    "yaw_returning": self.trim_target[0] is not None,
                    "shift_returning": self.trim_target[1] is not None,
                    "yaw_limit_deg": self.config.spreader_yaw_limit_deg,
                    "shift_limit_m": self.config.spreader_shift_limit_m,
                    "shift_zero": abs(self._trim_pose()[1]) < 1e-6},
                "virtual_fields": ["lock_state", "unlock_state", "tool_on_state", "big_car_clamp_status",
                                   "sprdp_on_state", "sprdp_off_state", "estop_state", "auto_correct_st",
                                   "correct_ctrl_state_fb", "bypass_fb", "land_bypass_fb", "spreader_bypass_fb"],
                "unsupported_fields": ["weight", "drop_state", "tool_45f", "tool_action", "tool_mid_lock", "beam_tilt_action"]}}
        if self.interaction is not None:
            physics = self.interaction.snapshot()
            result["digital_twin"]["physics"] = physics
            if physics["enabled"]:
                result["digital_twin"]["virtual_fields"] = [name for name in result["digital_twin"]["virtual_fields"]
                                                            if name not in {"lock_state", "unlock_state"}]
                result["digital_twin"]["unsupported_fields"].remove("drop_state")
                contact = physics["contact"]
                content.update(lock_state=int(physics["locked"]), unlock_state=int(not physics["locked"]),
                               drop_state=int(physics["landed"] or bool(contact and all(contact["feet"]))),
                               collision_state=int(physics["collision"] is not None))
        return result
