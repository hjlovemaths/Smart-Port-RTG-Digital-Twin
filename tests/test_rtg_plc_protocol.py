from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "omniverse/scripts"))
from rtg_plc_protocol import PlcConfig, PlcEngine


class Controller:
    def __init__(self):
        self.positions = [100., 5., 10.]
        self.size = 40
        self.reads = []
        self.trim = [0., 0.]

    def get_positions(self):
        self.reads.append(tuple(self.positions))
        return tuple(self.positions)

    def set_gantry_safe(self, value): self.positions[0] = max(0, min(500, value))
    def set_trolley_safe(self, value): self.positions[1] = max(0, min(18.06, value))
    def set_hoist_safe(self, value): self.positions[2] = max(.5, min(18, value))
    def current_spreader_size_ft(self): return self.size
    def get_spreader_trim(self): return tuple(self.trim)
    def set_spreader_trim(self, yaw, shift): self.trim = [yaw, shift]
    def set_spreader_size(self, size, smooth=False):
        self.size = size
        return size


def message(cmd, value):
    return {"match": "CTR", "cmd": cmd, "value": value, "CRC": "0", "time": "12345"}


@pytest.fixture
def engine():
    clock = [0.]
    instance = PlcEngine(Controller(), ((0, 500), (0, 18.06), (.5, 18)), clock=lambda: clock[0])
    instance.test_clock = clock
    return instance


def test_disabled_until_explicit_control_enable_and_disconnect_never_resumes(engine):
    with pytest.raises(ValueError, match="control is off"):
        engine.apply(message("vel", [.5, .2]))
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("vel", [.5, .2]))
    engine.tick(.1)
    assert engine.controller.positions == pytest.approx([100, 5.05, 10.02])
    engine.tick(.1, connected=False)
    engine.tick(.1, connected=True)
    assert not engine.armed
    assert engine.controller.positions == pytest.approx([100, 5.05, 10.02])


def test_legacy_gantry_switch_is_exclusive_and_rcms_stop_stops_gantry(engine):
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("vel", [1, .5]))
    engine.apply(message("set_ctrl_cmd", {"switch": 1, "speed": [2, 1], "bigCarSpeed": -.5}))
    engine.tick(.1)
    assert engine.controller.positions == pytest.approx([99.95, 5, 10])
    engine.apply(message("vel", [0, 0]))
    assert engine.requested[0] == -.5  # RCMS vel controls trolley/hoist only
    engine.apply(message("bigcarstop", None))
    engine.tick(.1)
    assert engine.controller.positions == pytest.approx([99.95, 5, 10])


def test_watchdog_is_renewed_only_by_speed_commands(engine):
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("vel", [1, 0]))
    engine.test_clock[0] = 1
    engine.apply(message("ctr_sw_set", 1))
    engine.tick(.1)
    assert engine.controller.positions[1] == 5
    assert engine.snapshot()["digital_twin"]["stop_reason"] == "command_timeout"


@pytest.mark.parametrize("cmd,value", [
    ("vel", [float("nan"), 0]), ("vel", [0, float("inf")]), ("vel", [True, 0]),
    ("vel", [4, 0]), ("vel", [0]), ("vel", "1,0"), ("vel", [0, 2]), ("vel", [2, 2]),
    ("set_ctrl_cmd", {"switch": True}), ("set_ctrl_cmd", {"switch": 2}),
    ("set_ctrl_cmd", {"switch": 1, "bigCarSpeed": 4}),
    ("set_ctrl_cmd", {"switch": 0, "speed": [.5, 0], "toolAction": 11}),
    ("tool_size", 3), ("beam_tilt_action", 1), ("cali_plc_offset", [1, 2]),
    ("tool_action", 12), ("ctr_sw_set", True), ("set_big_car_left", -1),
])
def test_bad_and_unmodelled_commands_reject_without_partial_updates(engine, cmd, value):
    engine.apply(message("ctr_sw_set", 1))
    before = dict(engine.flags)
    with pytest.raises(ValueError):
        engine.apply(message(cmd, value))
    assert engine.requested == [0, 0, 0]
    assert engine.flags == before
    assert engine.controller.positions == [100, 5, 10]


def test_soft_limits_clamps_dt_and_reports_real_height_and_rope_length(engine):
    engine.controller.positions[2] = 17.99
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("vel", [0, 1]))
    engine.tick(2)  # no two-second jump after a stalled frame
    assert engine.controller.positions[2] == 17.99
    assert engine.snapshot()["digital_twin"]["stop_reason"] == "frame_stall"
    engine.apply(message("vel", [0, 1]))
    engine.tick(.1)
    state = engine.snapshot()
    assert engine.controller.positions[2] == 18
    assert state["content"]["rope_pos"] == 0
    assert state["content"]["rope_up_limit_state"] == 1
    assert state["digital_twin"]["hoist_height_m"] == 18
    engine.apply(message("vel", [0, -1]))
    engine.tick(.1)
    assert engine.snapshot()["content"]["rope_pos"] == pytest.approx(.1)
    assert engine.snapshot()["content"]["rope_vel"] > 0


def test_slow_frames_use_substeps_without_discarding_elapsed_motion(engine):
    engine.apply(message("ctr_sw_set", 1))
    for _ in range(5):
        engine.apply(message("set_ctrl_cmd", {"switch": 1, "bigCarSpeed": .3}))
        engine.test_clock[0] += .4
        engine.tick(.4)
    assert engine.controller.positions[0] == pytest.approx(100.6)
    assert engine.snapshot()["content"]["big_car_vel"] == pytest.approx(.3)
    assert engine.snapshot()["digital_twin"]["integration_dt_s"] == .4


def test_direction_limits_and_clamp_restrict_only_gantry(engine):
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("set_big_car_left", 1))
    engine.apply(message("set_ctrl_cmd", {"switch": 1, "bigCarSpeed": -1}))
    engine.tick(.1)
    assert engine.controller.positions[0] == 100
    engine.apply(message("set_big_car_left", 2))
    engine.tick(.1)
    assert engine.controller.positions[0] == pytest.approx(99.97)
    engine.apply(message("wheel_clamp", 1))
    engine.tick(.1)
    assert engine.controller.positions[0] == pytest.approx(99.97)


def test_control_conflicts_prevent_arming_and_revoke_live_control(engine):
    def busy(): raise RuntimeError("another driver owns the RTG")
    engine.guard = busy
    with pytest.raises(RuntimeError): engine.apply(message("ctr_sw_set", 1))
    assert not engine.armed
    engine.guard = lambda: None
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("vel", [1, 0]))
    engine.guard = busy
    with pytest.raises(RuntimeError): engine.tick(.1)
    assert not engine.armed
    assert engine.requested == [0, 0, 0]


def test_size_and_virtual_flags_are_reported_without_claiming_container_attachment(engine):
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("tool_size", 1))
    engine.apply(message("lock", 1))
    state = engine.snapshot()
    assert state["content"]["tool_20f"] == 1
    assert state["content"]["lock_state"] == 1
    assert "lock_state" in state["digital_twin"]["virtual_fields"]
    assert "weight" not in state["content"]


def test_readback_velocity_includes_another_controller_while_unarmed(engine):
    engine.controller.positions[0] += .2
    engine.tick(.1)
    assert engine.snapshot()["content"]["big_car_vel"] == pytest.approx(2)


def test_flat_visual_calibration_does_not_prevent_crossing_truck_stop(engine):
    class Plateau(Controller):
        def set_trolley_safe(self, value):
            self.positions[1] = .48 if value <= 1 else value
    engine.controller = Plateau()
    engine.controller.positions[1] = .48
    engine.apply(message("ctr_sw_set", 1))
    for _ in range(20):
        engine.apply(message("vel", [.5, 0]))
        engine.tick(.1)
    assert engine.controller.positions[1] == pytest.approx(1.48)


@pytest.mark.parametrize("rope_mode", ["rope_length", "height"])
def test_rcms_readback_uses_height_and_actual_velocity_independent_of_legacy_rope_mode(engine, rope_mode):
    engine.config = PlcConfig(rope_pos_mode=rope_mode)
    # A passive encoder update proves these are readback values, not commands.
    engine.controller.positions = [123.25, 6.5, 11.5]
    engine.tick(.5)
    packet = engine.snapshot()
    state = packet["content"]
    assert state["bigcar_elec_distance"] == state["bigcar_engine_distance"] == 123.25
    assert state["smallcar_pos"] == 6.5
    assert state["lifting_pos"] == 11.5
    assert state["smallcar_vel"] == pytest.approx(3)
    assert state["lifting_vel"] == pytest.approx(3)
    assert state["bigcar1_vel_fb"] == state["bigcar2_vel_fb"] == pytest.approx(-46.5)
    assert state["lifting_up_state"] == state["smallcar_front_state"] == state["bigcar_right_state"] == 1
    if rope_mode == "rope_length":
        assert state["rope_pos"] == 6.5
        assert state["rope_vel"] == -state["lifting_vel"]
    assert state["ctr_off_state"] == 1


def test_rcms_virtual_pump_and_stop_limits_follow_controller_state(engine):
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("tool_sw_set", 1))
    engine.apply(message("wheel_clamp", 1))
    engine.controller.positions = [0, 18.06, .5]
    state = engine.snapshot()["content"]
    assert state["sprdp_on_state"] == 1 and state["sprdp_off_state"] == 0
    assert state["limit_smallcar_front"] == 1 and state["limit_smallcar_back"] == 0
    assert state["limit_lifting_down"] == 1 and state["limit_lifting_up"] == 0
    assert state["not_allow_bigcar_left"] == state["not_allow_bigcar_right"] == 1
    assert state["ctr_off_state"] == 0


def test_rcms_bigcarvel_requires_control_and_separates_handle_from_actual_drive_speed(engine):
    with pytest.raises(ValueError, match="control is off"):
        engine.apply(message("bigcarvel", [2, 2, 2]))
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("bigcarvel", [2, .5, .5]))
    engine.tick(.1)
    state = engine.snapshot()["content"]
    assert state["bigcar_elec_distance"] == pytest.approx(99.95)
    assert state["bigcar_handle_vel"] == 2
    assert state["bigcar1_vel_fb"] == state["bigcar2_vel_fb"] == pytest.approx(.5)
    assert state["bigcar_left_state"] == 1 and state["bigcar_right_state"] == 0
    engine.apply(message("bigcarvel", [-2, -.5, -.5]))
    engine.tick(.1)
    assert engine.snapshot()["content"]["bigcar_right_state"] == 1
    assert engine.controller.positions[0] == pytest.approx(100)


@pytest.mark.parametrize("speeds", [[2, 2], [2, 2, 1], [0, 4, 4], [True, 1, 1], [0, float('nan'), 0]])
def test_rcms_bad_or_unequal_gantry_speeds_do_not_mutate_motion(engine, speeds):
    engine.apply(message("ctr_sw_set", 1))
    with pytest.raises(ValueError):
        engine.apply(message("bigcarvel", speeds))
    assert engine.requested == [0, 0, 0]


def test_rcms_axes_are_independent_and_trolley_heartbeat_cannot_keep_gantry_moving(engine):
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("bigcarvel", [.5, .5, .5]))
    engine.apply(message("vel", [.25, .1]))
    engine.tick(.1)
    assert engine.controller.positions == pytest.approx([99.95, 5.025, 10.01])
    engine.test_clock[0] = .4
    engine.apply(message("vel", [.25, .1]))
    engine.test_clock[0] = .7
    engine.apply(message("heartbeat", 1))
    engine.tick(.1)
    assert engine.requested == [0, .25, .1]
    assert engine.snapshot()["content"]["bigcar_handle_vel"] == 0
    assert engine.snapshot()["content"]["bigcar1_vel_fb"] == 0
    engine.apply(message("bigcarvel", [.5, .5, .5]))
    engine.apply(message("bigcarstop", None))
    assert engine.requested == [0, .25, .1]
    engine.apply(message("ctr_sw_set", 0))
    assert engine.requested == [0, 0, 0]


def test_latest_combined_move_applies_three_axes_with_integral_float_stop_codes(engine):
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("move", [.5, .25, 10.0, 20.0, .4, .4]))
    engine.tick(.1)
    assert engine.controller.positions == pytest.approx([99.96, 5.05, 10.025])
    engine.apply(message("move", [0, 0, 10, 10, 0, 0]))
    assert engine.requested == [0, 0, 0]


@pytest.mark.parametrize("value", [[1, 0, 12, 10, 1, 1], [1, 0, 10, 22, 1, 1],
                                  [1, 2, 10, 10, 1, 1], [1, 0, 10, 10, 1, -1], [1, 0]])
def test_latest_combined_move_rejects_entire_command_before_any_axis_changes(engine, value):
    engine.apply(message("ctr_sw_set", 1))
    with pytest.raises(ValueError):
        engine.apply(message("move", value))
    assert engine.requested == [0, 0, 0]
    assert engine.controller.positions == [100, 5, 10]


def test_digital_estop_latches_and_requires_reset_then_explicit_control_enable(engine):
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("vel", [1, 0]))
    engine.apply(message("estop", None))
    assert not engine.armed and engine.snapshot()["content"]["estop_state"] == 0
    engine.apply(message("heartbeat", 1))
    with pytest.raises(ValueError, match="emergency stop"):
        engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("reset_one_key", None))
    assert not engine.armed and engine.snapshot()["content"]["estop_state"] == 1
    with pytest.raises(ValueError, match="control is off"):
        engine.apply(message("vel", [1, 0]))
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("vel", [1, 0]))
    engine.tick(.1)
    assert engine.controller.positions[1] == pytest.approx(5.1)


@pytest.mark.parametrize("cmd,value", [("tool_action", 11), ("tool_move", 21), ("tool_reset", 11)])
def test_spreader_trim_requires_control_and_never_auto_arms(engine, cmd, value):
    with pytest.raises(ValueError, match="control is off"):
        engine.apply(message(cmd, value))
    assert engine.controller.trim == [0, 0] and not engine.armed


def test_spreader_jog_moves_independently_and_each_stop_only_stops_its_axis(engine):
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("tool_action", 11))
    engine.apply(message("tool_move", 21))
    engine.apply(message("vel", [.5, .2]))
    engine.tick(.2)
    assert engine.controller.trim == pytest.approx([.2, -.01])
    assert engine.controller.positions == pytest.approx([100, 5.1, 10.04])
    engine.apply(message("tool_action", 10))
    engine.tick(.2)
    assert engine.controller.trim == pytest.approx([.2, -.02])
    engine.apply(message("tool_move", 20))
    engine.tick(.2)
    assert engine.controller.trim == pytest.approx([.2, -.02])
    assert engine.requested == [0, .5, .2]


def test_spreader_limits_stop_jogs_and_return_zero_is_smooth_and_axis_specific(engine):
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("tool_action", 11))
    engine.apply(message("tool_move", 11))
    for _ in range(70):
        engine.tick(.1)
    assert engine.controller.trim == pytest.approx([5, .3])
    assert engine.trim_requested == [0, 0]
    engine.apply(message("tool_reset", 11))
    engine.tick(.1)
    assert engine.controller.trim == pytest.approx([4.9, .3])
    engine.apply(message("tool_reset", 21))
    for _ in range(65):
        engine.tick(.1)
    assert engine.controller.trim == pytest.approx([0, 0])
    assert engine.trim_target == [None, None]
    state = engine.snapshot()
    assert state["content"]["to_mid_state"] == 1
    assert state["digital_twin"]["spreader_trim"]["shift_zero"]


def test_spreader_return_can_be_interrupted_without_snapping_or_changing_other_axis(engine):
    engine.controller.trim = [-1., -.1]
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("tool_reset", 11))
    engine.tick(.1)
    assert engine.controller.trim == pytest.approx([-.9, -.1])
    engine.apply(message("tool_action", 20))
    engine.tick(.1)
    assert engine.controller.trim == pytest.approx([-.9, -.1])
    assert engine.trim_target[0] is None


def test_discrete_spreader_jog_lease_is_independent_of_linear_axis_watchdog(engine):
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("tool_move", 11))
    engine.apply(message("vel", [1, 0]))
    engine.test_clock[0] = .7
    engine.tick(.1)
    assert engine.requested == [0, 0, 0]
    assert engine.trim_requested[1] > 0
    engine.test_clock[0] = 10.1
    engine.tick(.1)
    assert engine.trim_requested == [0, 0]


def test_combined_move_supports_all_five_axes_and_uses_stream_watchdog(engine):
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("move", [.5, .2, 11.0, 21.0, .4, .4]))
    engine.tick(.1)
    assert engine.controller.trim == pytest.approx([.1, -.005])
    assert engine.controller.positions == pytest.approx([99.96, 5.05, 10.02])
    engine.test_clock[0] = .7
    engine.tick(.1)
    assert engine.trim_requested == [0, 0] and engine.requested == [0, 0, 0]


@pytest.mark.parametrize("stop_cmd,value", [("ctr_sw_set", 0), ("estop", None), ("reset_one_key", None)])
def test_control_off_estop_and_reset_cancel_trim_without_zeroing_pose(engine, stop_cmd, value):
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("tool_action", 11))
    engine.tick(.1)
    before = tuple(engine.controller.trim)
    engine.apply(message(stop_cmd, value))
    engine.tick(.1)
    assert tuple(engine.controller.trim) == before
    assert not engine.armed and engine.trim_requested == [0, 0]


def test_disconnect_and_frame_stall_stop_trim_and_never_resume(engine):
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("tool_move", 11))
    engine.tick(1)
    assert engine.controller.trim == [0, 0] and engine.trim_requested == [0, 0]
    engine.apply(message("tool_move", 11))
    engine.tick(.1)
    before = tuple(engine.controller.trim)
    engine.tick(.1, connected=False)
    engine.tick(.1)
    assert tuple(engine.controller.trim) == before and not engine.armed


def test_invalid_combined_move_preserves_already_running_trim_and_linear_axes(engine):
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("tool_action", 11))
    engine.apply(message("vel", [.2, .1]))
    with pytest.raises(ValueError):
        engine.apply(message("move", [1, .2, 21, 11, 1, -1]))
    assert engine.trim_requested == [1, 0] and engine.requested == [0, .2, .1]


def test_unavailable_trim_rejects_before_moving_other_axes(engine):
    engine.controller.set_spreader_trim = None
    engine.apply(message("ctr_sw_set", 1))
    with pytest.raises(ValueError, match="geometry is unavailable"):
        engine.apply(message("move", [1, .2, 11, 11, 1, 1]))
    assert engine.requested == [0, 0, 0]


def test_virtual_bypass_does_not_disable_axis_limits_and_correction_does_not_move_geometry(engine):
    reply = engine.apply(message("autocorrection ", 1))
    assert reply["cmd"] == "autocorrection " and reply["effect"] == "virtual_state"
    engine.apply(message("bigcar_correction", 1))
    for code in (11, 21, 31):
        engine.apply(message("set_bypass", code))
    state = engine.snapshot()["content"]
    assert state["bypass_fb"] == state["land_bypass_fb"] == state["spreader_bypass_fb"] == 1
    engine.apply(message("set_bypass", 11))
    assert engine.snapshot()["content"]["bypass_fb"] == 0
    engine.apply(message("ctr_sw_set", 1))
    engine.controller.positions[1] = 18.05
    engine.apply(message("vel", [1, 0]))
    engine.tick(.1)
    assert engine.controller.positions[1] == 18.06
    assert engine.controller.positions[0] == 100


def test_comm_test_echo_and_manual_selection_do_not_arm_or_keep_old_velocities(engine):
    engine.apply(message("comm_test", 123))
    assert engine.snapshot()["content"]["comm_test"] == 123
    engine.apply(message("manual_mc", 1))
    assert not engine.armed
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("vel", [.5, 0]))
    engine.apply(message("manual_mc", 1))
    assert engine.armed and engine.requested == [0, 0, 0]


def test_physics_requires_updated_adapter_and_explicit_control_enable(engine):
    with pytest.raises(ValueError, match="control is off"):
        engine.apply(message("sim_physics", 1))
    engine.apply(message("ctr_sw_set", 1))
    with pytest.raises(ValueError, match="updated Kit"):
        engine.apply(message("sim_physics", 1))
    for value in (True, 2, None):
        with pytest.raises(ValueError):
            engine.apply(message("sim_physics", value))


def test_physics_mode_change_requires_stopped_axes_and_preserves_owner_on_control_off(engine):
    from types import SimpleNamespace
    interaction = SimpleNamespace(enabled=False, carried=None)
    interaction.set_enabled = lambda enabled: setattr(interaction, "enabled", enabled)
    interaction.backend = SimpleNamespace(check=lambda: None)
    engine.interaction = interaction
    ownership = []
    engine.on_arm = lambda armed: ownership.append(bool(armed or interaction.enabled))
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("vel", [.5, 0]))
    with pytest.raises(ValueError, match="stop all axes"):
        engine.apply(message("sim_physics", 1))
    assert not interaction.enabled
    engine.apply(message("vel", [0, 0]))
    engine.apply(message("sim_physics", 1))
    engine.apply(message("ctr_sw_set", 0))
    assert not engine.armed and interaction.enabled and ownership[-1]
    engine.apply(message("ctr_sw_set", 1))
    engine.apply(message("sim_physics", 0))
    engine.apply(message("ctr_sw_set", 0))
    assert not ownership[-1]
