"""Gamepad-operated remote manual mode for the primary RTG.

Run this script inside the Omniverse Kit Script Editor after opening
``scenes/smart_port.usda``. It first uses Kit's own gamepad input (the same
device source used by the viewport), then falls back to Windows XInput. This
needs no third-party Python dependency.

The mode has one deliberate safety boundary: while active, incoming PLC frames
are still received but cannot overwrite manual motion.  Exiting the mode gives
the PLC receiver authority again.
"""

from __future__ import annotations

import builtins
import ctypes
from ctypes import wintypes
import importlib
import math
from pathlib import Path
import sys
import time
from dataclasses import dataclass
from typing import Any, Optional


def _resolve_script_dir() -> Path:
    """Find sibling helpers when Kit evaluates this source from a temp buffer."""

    candidates = (
        Path(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts"),
        Path.cwd() / "omniverse" / "scripts",
        Path(__file__).resolve().parent,
    )
    for candidate in candidates:
        if (candidate / "rtg_live_control.py").is_file():
            return candidate
    return Path(__file__).resolve().parent


SCRIPT_DIR = _resolve_script_dir()
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


def _load_rtg_live_control(force_reload: bool = False) -> Any:
    """Return the live-control module, refreshing it across Script Editor runs."""

    import rtg_live_control  # type: ignore

    if force_reload:
        try:
            return importlib.reload(rtg_live_control)
        except Exception:
            return rtg_live_control
    return rtg_live_control


REMOTE_MANUAL_MODE_FLAG = "_SMART_PORT_RTG_REMOTE_MANUAL_ACTIVE"
REMOTE_MANUAL_CONTROLLER_KEY = "_SMART_PORT_RTG_GAMEPAD_MANUAL_CONTROLLER"
REMOTE_MANUAL_STATUS_KEY = "_SMART_PORT_RTG_REMOTE_MANUAL_STATUS"
STATUS_PRIM_PATH = "/World/LiveTwinState"
DEMO_SEQUENCE_ARTIFACT_ROOTS = (
    "/World/PortAndRTG/LIVE_internal_yard_truck_unified_40ft",
    "/World/LiveContainers/SequenceTruckLane",
)
DEMO_SEQUENCE_ARTIFACT_NAME_PREFIXES = (
    "LIVE_internal_lane_truck",
    "Task01_TruckLoadedBox",
    "Task02_LoadedTruckBox",
)
SPREADER_GEOMETRY_NAME_PREFIXES = (
    "RTG_SPREAD_REDRAW",
)
TROLLEY_PATH = (
    "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL/"
    "ANIM_CTRL_RTG_TROLLEY_TRAVEL"
)
HOIST_PATH = (
    f"{TROLLEY_PATH}/ANIM_CTRL_RTG_HOIST_VERTICAL"
)
GANTRY_PATH = "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL"
GLOBAL_CAMERA_PATH = f"{GANTRY_PATH}/REMOTE_MANUAL_GLOBAL_CAMERA"
SPREADER_UNDERSIDE_CAMERA_PATH = f"{TROLLEY_PATH}/REMOTE_MANUAL_TROLLEY_CAMERA"

# XInput button masks.  XInput is available on supported Windows gamepads
# without installing pygame or opening a second application window.
XINPUT_GAMEPAD_A = 0x1000
XINPUT_GAMEPAD_B = 0x2000
XINPUT_GAMEPAD_X = 0x4000
XINPUT_GAMEPAD_BACK = 0x0020
XINPUT_GAMEPAD_START = 0x0010
XINPUT_GAMEPAD_DPAD_UP = 0x0001
XINPUT_GAMEPAD_DPAD_DOWN = 0x0002
XINPUT_GAMEPAD_RIGHT_SHOULDER = 0x0200

GANTRY_SPEED_M_S = 1.50
TROLLEY_SPEED_M_S = 1.00
HOIST_SPEED_M_S = 0.90
AXIS_DEADZONE = 0.16
# A box is picked when at least two visible red lock feet are seated on the
# constrained bay/row top plane.  The authored red-foot mesh has a lateral
# visual offset, so bay and row alignment come from the gantry and trolley
# centres; the red feet are used for vertical seating.
PICK_LOCK_FOOT_EDGE_TOLERANCE_M = 0.20
PICK_MIN_CONTACT_FEET = 2
PICK_HORIZONTAL_TOLERANCE_M = 0.70
PICK_VERTICAL_TOLERANCE_M = 0.20
RELEASE_GROUND_SUPPORT_Z_M = 0.12
RELEASE_SUPPORT_MIN_OVERLAP_RATIO = 0.20
RELEASE_SUPPORT_Z_TOLERANCE_M = 0.05
RELEASE_CENTER_STABILITY_MARGIN_M = 0.02
RELEASE_TOPPLE_ANGLE_DEG = 90.0
RELEASE_SETTLE_ANIMATION_SECONDS = 0.35
RELEASE_TOPPLE_ANIMATION_SECONDS = 0.85
RELEASE_TOPPLE_DROP_PHASE_RATIO = 0.42
RELEASE_CONTAINER_MASS_KG = 2400.0
RELEASE_INITIAL_DOWNWARD_SPEED_M_S = 0.05
RELEASE_TOPPLE_ANGULAR_SPEED_DEG_S = 18.0
RELEASE_TOPPLE_PERTURBATION_DEG_S = 1.5
MANUAL_PHYSICS_TIMESTEP_SECONDS = 1.0 / 60.0
MANUAL_PHYSICS_MAX_SUBSTEPS = 3
MANUAL_PHYSICS_SETTLE_SECONDS = 0.90
MANUAL_PHYSICS_SETTLE_TRANSLATION_M = 0.001
MANUAL_PHYSICS_GROUND_THICKNESS_M = 0.20
MANUAL_PHYSICS_GROUND_HALF_EXTENT_M = 2500.0
MANUAL_PHYSICS_ROOT_PATH = "/World/ManualReleasePhysics"
MANUAL_PHYSICS_GROUND_PATH = f"{MANUAL_PHYSICS_ROOT_PATH}/Ground"
MANUAL_PHYSICS_MATERIAL_PATH = f"{MANUAL_PHYSICS_ROOT_PATH}/ContainerMaterial"
MANUAL_PHYSICS_SPREADER_PROXY_PATH = (
    f"{MANUAL_PHYSICS_ROOT_PATH}/SpreaderCollisionProxy"
)
MANUAL_COLLISION_LINEAR_SPEED_M_S = 0.45
MANUAL_COLLISION_ANGULAR_SPEED_DEG_S = 14.0
# Keep this synchronized with rtg_live_control.HOIST_USD_PER_ENGINEERING_M.
# Hoist commands are physical/engineering metres while the rendered live yard
# compresses one 2.60 m tier into 0.939353 world metres.
HOIST_WORLD_Z_PER_ENGINEERING_M = (0.904353 + 0.035) / 2.60
CARRIED_CONTAINER_CONTACT_CLEARANCE_M = 0.005
CARRIED_CONTAINER_CONTACT_LOG_INTERVAL_SECONDS = 0.75
CARRIED_CONTAINER_COLLISION_CLEARANCE_M = 0.02
CARRIED_CONTAINER_COLLISION_LOG_INTERVAL_SECONDS = 0.75
CARRIED_CONTAINER_COLLISION_NUDGE_MIN_M = 0.002
CARRIED_CONTAINER_COLLISION_NUDGE_MAX_M = 0.012
CARRIED_CONTAINER_COLLISION_NUDGE_RESPONSE_RATIO = 0.06
SPREADER_VERTICAL_CONTACT_CLEARANCE_M = 0.005
SPREADER_VERTICAL_CONTACT_EDGE_TOLERANCE_M = 0.08
SPREADER_VERTICAL_CONTACT_LOG_INTERVAL_SECONDS = 0.75
SPREADER_COLLISION_HORIZONTAL_MARGIN_M = 0.08
SPREADER_COLLISION_VERTICAL_MARGIN_M = 0.08
SPREADER_COLLISION_TOOL_HEIGHT_M = 0.85
SPREADER_COLLISION_MIN_HORIZONTAL_MOTION_M = 0.006
SPREADER_COLLISION_SCAN_INTERVAL_SECONDS = 0.08
SPREADER_COLLISION_ROW_WINDOW = 1
SPREADER_COLLISION_BAY_WINDOW = 2
SPREADER_COLLISION_MIN_NUDGE_M = 0.015
SPREADER_COLLISION_MAX_NUDGE_M = 0.08
SPREADER_COLLISION_RESPONSE_RATIO = 0.35
TROLLEY_ROW_CENTERS_M = {
    1: 18.06,
    2: 15.19,
    3: 12.35,
    4: 9.50,
    5: 6.66,
    6: 3.71,
}
TROLLEY_ROW_CAPTURE_TOLERANCE_M = 1.45
TROLLEY_CARRIED_BOX_X_CALIBRATION = (
    (0.48, -3.75),
    (1.00, -3.75),
    (3.71, -1.0981016666666639),
    (6.66, 0.4081853333333361),
    (9.50, 1.9144723333333360),
    (12.35, 3.4207593333333360),
    (15.19, 4.9270463333333360),
    (18.06, 6.4333333333333360),
)
CONTAINER_NOMINAL_HEIGHT_M = 2.60

# Measured from the composed RTG scene and shared with the validated
# pick/place demo.  These convert a live container's world centre directly to
# the three authored controller values; unlike same-frame probing, they do not
# depend on Kit's deferred transform evaluation.
GANTRY_HOME_USD_Y = 23.90
SPREADER_WORLD_X_AT_TROLLEY_ZERO = -3.7426
SPREADER_WORLD_Y_AT_GANTRY_HOME = 29.46
TROLLEY_USD_TO_WORLD_X = -1.4741
REFERENCE_PICK_CONTAINER_CENTER_Z = 3.3902355
REFERENCE_PICK_HOIST_Z = 0.90
# Keep the bay-centre reverse calibration local so manual takeover still
# resolves the displayed bay correctly if Kit has a stale rtg_live_control
# module cached from an earlier Script Editor run.
GANTRY_BAY_CENTERLINE_CORRECTION_Y_M = -1.3375
GANTRY_40FT_BAY_CENTERLINE_FINE_OFFSET_Y_M = -0.2625
GANTRY_20FT_SPREADER_CENTER_OFFSET_Y_M = -0.30


class _XInputGamepad(ctypes.Structure):
    _fields_ = [
        ("wButtons", wintypes.WORD),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class _XInputState(ctypes.Structure):
    _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", _XInputGamepad)]


@dataclass(frozen=True)
class GamepadState:
    buttons: int
    left_x: float
    left_y: float
    right_y: float

    def pressed(self, button: int) -> bool:
        return bool(self.buttons & button)


@dataclass(frozen=True)
class KeyboardState:
    """Keyboard motion state sampled once per Kit update."""

    gantry: float
    trolley: float
    hoist: float
    deadman: bool
    just_pressed: frozenset[str]


def normalized_axis(value: int, deadzone: float = AXIS_DEADZONE) -> float:
    """Normalize an XInput thumb axis and remove its centre deadzone."""

    raw = max(-1.0, min(1.0, float(value) / 32767.0))
    magnitude = abs(raw)
    if magnitude <= deadzone:
        return 0.0
    return math.copysign((magnitude - deadzone) / (1.0 - deadzone), raw)


def normalized_directional_axis(
    positive: float, negative: float, deadzone: float = AXIS_DEADZONE
) -> float:
    """Apply the same centred-stick deadzone to Kit directional inputs."""

    raw = max(-1.0, min(1.0, float(positive) - float(negative)))
    magnitude = abs(raw)
    if magnitude <= deadzone:
        return 0.0
    return math.copysign((magnitude - deadzone) / (1.0 - deadzone), raw)


class XInputGamepad:
    """Small dependency-free XInput reader for controller index 0..3."""

    def __init__(self, controller_index: int = 0) -> None:
        if sys.platform != "win32":
            raise RuntimeError("gamepad manual mode currently requires Windows XInput")
        if not 0 <= int(controller_index) <= 3:
            raise ValueError("controller_index must be between 0 and 3")
        self._controller_index = int(controller_index)
        self._get_state = None
        for library_name in ("xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll"):
            try:
                library = ctypes.WinDLL(library_name)
                get_state = library.XInputGetState
                get_state.argtypes = (wintypes.DWORD, ctypes.POINTER(_XInputState))
                get_state.restype = wintypes.DWORD
                self._get_state = get_state
                break
            except OSError:
                continue
        if self._get_state is None:
            raise RuntimeError("Windows XInput is unavailable")

    def poll(self) -> Optional[GamepadState]:
        state = _XInputState()
        result = self._get_state(self._controller_index, ctypes.byref(state))
        if result != 0:  # ERROR_DEVICE_NOT_CONNECTED
            return None
        pad = state.Gamepad
        return GamepadState(
            buttons=int(pad.wButtons),
            left_x=normalized_axis(pad.sThumbLX),
            left_y=normalized_axis(pad.sThumbLY),
            right_y=normalized_axis(pad.sThumbRY),
        )

    def stop(self) -> None:
        """Keep the reader interface compatible with KitGamepad."""


class KitGamepad:
    """Read and consume the gamepad exposed by the running Kit application.

    Unlike the XInput fallback, this supports a controller that Kit already
    recognizes (including DirectInput devices).  Returning ``True`` from the
    event callback consumes the event so it cannot be used by another Kit
    action mapping while manual mode is active.
    """

    def __init__(self, controller_index: int = 0) -> None:
        import carb.input  # type: ignore
        import omni.appwindow  # type: ignore

        self._controller_index = int(controller_index)
        self._carb_input = carb.input
        self._app_window = omni.appwindow.get_default_app_window()
        self._gamepad = self._app_window.get_gamepad(self._controller_index)
        if self._gamepad is None:
            raise RuntimeError(f"Kit has no gamepad at index {controller_index}")
        self._input = carb.input.acquire_input_interface()
        self._subscription = self._input.subscribe_to_gamepad_events(
            self._gamepad, self._consume_event
        )

    def _consume_event(self, _event: Any) -> bool:
        # ``True`` marks this event handled for the input dispatcher.  Motion
        # is sampled from the authoritative current gamepad values in poll().
        return True

    def poll(self) -> Optional[GamepadState]:
        gamepad_input = self._carb_input.GamepadInput

        def value(input_name: str) -> float:
            return max(
                0.0,
                float(
                    self._input.get_gamepad_value(
                        self._gamepad, getattr(gamepad_input, input_name)
                    )
                ),
            )

        def button(input_name: str) -> bool:
            return value(input_name) > 0.5

        buttons = 0
        for input_name, button_mask in (
            ("A", XINPUT_GAMEPAD_A),
            ("B", XINPUT_GAMEPAD_B),
            ("X", XINPUT_GAMEPAD_X),
            ("MENU1", XINPUT_GAMEPAD_BACK),
            ("MENU2", XINPUT_GAMEPAD_START),
            ("DPAD_UP", XINPUT_GAMEPAD_DPAD_UP),
            ("DPAD_DOWN", XINPUT_GAMEPAD_DPAD_DOWN),
            ("RIGHT_SHOULDER", XINPUT_GAMEPAD_RIGHT_SHOULDER),
        ):
            if button(input_name):
                buttons |= button_mask
        return GamepadState(
            buttons=buttons,
            left_x=normalized_directional_axis(
                value("LEFT_STICK_RIGHT"), value("LEFT_STICK_LEFT")
            ),
            left_y=normalized_directional_axis(
                value("LEFT_STICK_UP"), value("LEFT_STICK_DOWN")
            ),
            right_y=normalized_directional_axis(
                value("RIGHT_STICK_UP"), value("RIGHT_STICK_DOWN")
            ),
        )

    def stop(self) -> None:
        if self._subscription is not None:
            self._input.unsubscribe_to_gamepad_events(
                self._gamepad, self._subscription
            )
            self._subscription = None


class KitKeyboard:
    """Consume a compact, RTG-specific keyboard map while manual mode is on."""

    _INPUT_NAMES = {
        "W": ("W",), "S": ("S",), "A": ("A",), "D": ("D",),
        "HOIST_UP": ("UP",), "HOIST_DOWN": ("DOWN",), "SPACE": ("SPACE",),
        "G": ("G",), "F": ("F",), "ONE": ("KEY_1", "NUM_1"), "TWO": ("KEY_2", "NUM_2"),
        "HOME": ("HOME",), "END": ("END",), "ESCAPE": ("ESCAPE",),
        "ENTER": ("ENTER",),
    }

    def __init__(self) -> None:
        import carb.input  # type: ignore
        import omni.appwindow  # type: ignore

        self._carb_input = carb.input
        self._input = carb.input.acquire_input_interface()
        self._keyboard = omni.appwindow.get_default_app_window().get_keyboard()
        if self._keyboard is None:
            raise RuntimeError("Kit has no keyboard device")
        keyboard_input = carb.input.KeyboardInput
        self._input_to_action = {}
        for action, names in self._INPUT_NAMES.items():
            for name in names:
                input_value = getattr(keyboard_input, name, None)
                if input_value is not None:
                    self._input_to_action[str(input_value)] = action
        self._pressed: set[str] = set()
        self._just_pressed: set[str] = set()
        self._subscription = self._input.subscribe_to_keyboard_events(
            self._keyboard, self._on_event
        )

    def _on_event(self, event: Any) -> bool:
        action = self._input_to_action.get(str(event.input))
        if action is None:
            return False
        event_type = str(event.type)
        if "KEY_PRESS" in event_type:
            if action not in self._pressed:
                self._just_pressed.add(action)
            self._pressed.add(action)
        elif "KEY_RELEASE" in event_type:
            self._pressed.discard(action)
        return True

    def poll(self) -> KeyboardState:
        just_pressed = frozenset(self._just_pressed)
        self._just_pressed.clear()
        return KeyboardState(
            gantry=float(("W" in self._pressed) - ("S" in self._pressed)),
            trolley=float(("D" in self._pressed) - ("A" in self._pressed)),
            hoist=float(("HOIST_UP" in self._pressed) - ("HOIST_DOWN" in self._pressed)),
            deadman="SPACE" in self._pressed,
            just_pressed=just_pressed,
        )

    def stop(self) -> None:
        if self._subscription is not None:
            self._input.unsubscribe_to_keyboard_events(self._keyboard, self._subscription)
            self._subscription = None


class RtgGamepadManualController:
    """Apply dead-man gamepad motion and pick/release commands on Kit's UI thread."""

    def __init__(self, controller_index: int = 0) -> None:
        self._gamepad = None
        self._gamepad_start_error: Optional[str] = None
        try:
            self._gamepad = self._create_gamepad_reader(controller_index)
        except Exception as exc:  # noqa: BLE001 - keyboard-only mode is intentional
            self._gamepad_start_error = str(exc)
        self._keyboard: Optional[KitKeyboard] = None
        self._subscription = None
        self._rtg_controller = None
        self._stage_id: Optional[int] = None
        self._gantry_m = 0.0
        self._trolley_m = 0.0
        self._hoist_m = 17.7
        self._last_update_at = time.monotonic()
        self._previous_buttons = 0
        self._gamepad_was_connected = False
        self._deadman_active = False
        self._emergency_stopped = False
        self._carried_source_path: Optional[str] = None
        self._carried_component_paths: list[str] = []
        self._carried_lock_offset_world: Any = None
        self._carried_component_origins_world: dict[str, Any] = {}
        self._carried_component_offsets_x: dict[str, float] = {}
        self._carried_hoist_origin_world: Any = None
        self._last_carried_collision_center_world: Any = None
        self._last_carried_collision_notice_at = 0.0
        self._last_disconnect_notice_at = 0.0
        self._camera_control_previous: Any = None
        self._active_camera_mode = "GLOBAL"
        self._bay_target: Optional[tuple[str, int, int]] = None
        self._display_bay_target: Optional[tuple[str, int]] = None
        # Keep the business bay reported immediately before manual takeover as
        # the origin for subsequent continuous gantry travel.  Bay-addressed
        # stops contain visual centreline corrections, so converting their raw
        # controller value as an ordinary metre command can shift the reported
        # bay even though the RTG has not moved.
        self._gantry_bay_reference: Optional[tuple[str, int, float]] = None
        self._lock_foot_paths: Optional[tuple[str, ...]] = None
        self._spreader_geometry_paths: Optional[tuple[str, ...]] = None
        self._last_collision_probe_center_world: Any = None
        self._last_collision_notice_at = 0.0
        self._last_collision_scan_at = 0.0
        self._last_vertical_contact_notice_at = 0.0
        self._last_spreader_contact_notice_at = 0.0
        self._spreader_vertical_contact_active = False
        self._live_container_root_paths: Optional[tuple[str, ...]] = None
        self._container_contact_root_paths: Optional[tuple[str, ...]] = None
        self._pending_release_animations: list[dict[str, Any]] = []
        self._dynamic_releases: list[dict[str, Any]] = []
        self._manual_physics_accumulator = 0.0
        self._manual_physics_time = 0.0
        self._manual_physics_started = False
        self._manual_physics_error_reported = False
        self._manual_physics_start_reported = False
        self._manual_physics_stage_id: Optional[int] = None
        # Alignment needs Kit to evaluate the newly-authored transforms before
        # each measurement.  Keep this tiny state machine on update frames
        # instead of estimating the red twist-lock offset from a different RTG
        # asset/export.
        self._pending_pick_alignment: Optional[dict[str, Any]] = None

    def start(self) -> None:
        stage = self._current_stage()
        if stage is None:
            raise RuntimeError("No USD stage is open. Open smart_port.usda first.")
        self._disable_kit_gamepad_camera_control()
        self._bind_stage(stage)
        try:
            self._keyboard = KitKeyboard()
        except Exception as exc:  # noqa: BLE001 - keep gamepad mode available
            self._log(f"Keyboard input unavailable: {exc}")
        initial_gamepad_state = (
            self._gamepad.poll() if self._gamepad is not None else None
        )
        gamepad_connected = initial_gamepad_state is not None
        if initial_gamepad_state is not None:
            # A button held during takeover is not a fresh action edge.
            self._previous_buttons = initial_gamepad_state.buttons
            self._gamepad_was_connected = True
        if not gamepad_connected and self._keyboard is None:
            raise RuntimeError(
                "No compatible gamepad or keyboard input is available. "
                "Check the controller connection and Kit input devices."
            )
        if not gamepad_connected:
            cause = self._gamepad_start_error or "controller is disconnected"
            self._log(f"Gamepad unavailable; keyboard-only manual mode enabled: {cause}")
        self._activate_camera(stage, "GLOBAL")
        self._pause_timeline_for_manual_mode()
        self._disable_demo_sequence_artifacts(stage)
        self._ensure_manual_collision_api(stage)
        import omni.kit.app  # type: ignore

        stream = omni.kit.app.get_app().get_update_event_stream()
        self._subscription = stream.create_subscription_to_pop(
            self._on_update, name="SmartPortRtgGamepadManual"
        )
        self._log(
            "Remote manual enabled. Hold RB or Space as the motion deadman switch."
        )

    def stop(self) -> None:
        self._subscription = None
        if self._gamepad is not None:
            self._gamepad.stop()
        if self._keyboard is not None:
            self._keyboard.stop()
            self._keyboard = None
        stage = self._current_stage()
        if stage is not None:
            self._freeze_dynamic_bodies(stage)
        self._restore_kit_gamepad_camera_control()
        self._previous_buttons = 0
        self._gamepad_was_connected = False

    def _on_update(self, _event: Any) -> None:
        stage = self._current_stage()
        if stage is None:
            return
        self._bind_stage(stage)
        now = time.monotonic()
        elapsed = min(max(now - self._last_update_at, 0.0), 0.15)
        self._last_update_at = now
        state = self._gamepad.poll() if self._gamepad is not None else None
        keyboard_state = self._keyboard.poll() if self._keyboard is not None else KeyboardState(
            gantry=0.0, trolley=0.0, hoist=0.0, deadman=False, just_pressed=frozenset()
        )
        gamepad_connected = state is not None
        if state is None and self._keyboard is None:
            self._deadman_active = False
            if now - self._last_disconnect_notice_at > 2.0:
                self._last_disconnect_notice_at = now
                self._log("Gamepad disconnected; motion stopped and manual authority retained.")
            self._write_status(stage, connected=False)
            return
        if state is None:
            self._gamepad_was_connected = False
            # A keyboard operator retains safe local authority while a gamepad
            # is being reconnected.  The zero state ensures no stale stick
            # sample can become motion during that interval.
            state = GamepadState(buttons=0, left_x=0.0, left_y=0.0, right_y=0.0)
        elif not self._gamepad_was_connected:
            # Reconnection follows the same edge rule as initial takeover.
            self._previous_buttons = state.buttons
            self._gamepad_was_connected = True

        discrete_command = self._handle_buttons(stage, state)
        # Latch the edge before later USD/PhysX work can fail. A held action
        # button must never repeat because a post-command frame raised.
        self._previous_buttons = state.buttons
        discrete_command = self._handle_keyboard_buttons(stage, keyboard_state) or discrete_command
        (
            deadman,
            gantry_input,
            trolley_input,
            hoist_input,
        ) = self._authorized_motion_inputs(state, keyboard_state)
        self._deadman_active = deadman
        # The locking/key action frame must never also become a motion frame.
        # This prevents a held shoulder+A press from applying a stale analogue
        # sample and making the spreader appear to jump sideways.
        horizontal_motion_requested = False
        lowering_requested = False
        if deadman and not self._emergency_stopped and not discrete_command:
            horizontal_motion_requested = (
                abs(gantry_input) > 0.001 or abs(trolley_input) > 0.001
            )
            lowering_requested = hoist_input < -0.001
            if abs(gantry_input) > 0.001:
                self._release_bay_target_for_continuous_travel()
            if abs(gantry_input) > 0.001 or abs(trolley_input) > 0.001 or abs(hoist_input) > 0.001:
                self._pending_pick_alignment = None
            self._gantry_m += gantry_input * GANTRY_SPEED_M_S * elapsed
            self._trolley_m += trolley_input * TROLLEY_SPEED_M_S * elapsed
            # Pushing the right stick up raises the spreader (larger engineering
            # hoist value), matching the panel's usual operator convention.
            self._hoist_m += hoist_input * HOIST_SPEED_M_S * elapsed
        # Manual mode owns the session layer continuously.  Reapply the last
        # commanded position even when RB/the sticks are released; otherwise
        # authored timeline samples or a stale controller target can make the
        # RTG visibly snap back to its previous pose.
        self._apply_held_pose()
        aligning_pick = self._pending_pick_alignment is not None
        self._advance_pick_alignment(stage)
        self._limit_spreader_vertical_contact(
            stage,
            lowering_requested=lowering_requested or aligning_pick,
        )
        # Keep a locked box synchronized on every Kit frame.  This covers the
        # lock frame itself and any pose update that happens between gamepad
        # events, rather than making payload motion depend on another stick
        # event being emitted.
        self._move_carried_container(stage)
        self._advance_release_animations(stage)
        self._update_spreader_collision_proxy(stage)
        self._advance_dynamic_releases(stage, elapsed)
        self._nudge_containers_touched_by_spreader(
            stage,
            elapsed,
            horizontal_motion_requested=horizontal_motion_requested,
        )
        self._write_status(stage, connected=gamepad_connected)

    @staticmethod
    def _authorized_motion_inputs(
        state: GamepadState,
        keyboard_state: KeyboardState,
    ) -> tuple[bool, float, float, float]:
        """Apply each device's deadman before combining its motion axes."""

        gamepad_deadman = state.pressed(XINPUT_GAMEPAD_RIGHT_SHOULDER)
        keyboard_deadman = keyboard_state.deadman
        gantry = (
            (state.left_y if gamepad_deadman else 0.0)
            + (keyboard_state.gantry if keyboard_deadman else 0.0)
        )
        trolley = (
            (state.left_x if gamepad_deadman else 0.0)
            + (keyboard_state.trolley if keyboard_deadman else 0.0)
        )
        hoist = (
            (state.right_y if gamepad_deadman else 0.0)
            + (keyboard_state.hoist if keyboard_deadman else 0.0)
        )
        return (
            gamepad_deadman or keyboard_deadman,
            max(-1.0, min(1.0, gantry)),
            max(-1.0, min(1.0, trolley)),
            max(-1.0, min(1.0, hoist)),
        )

    def _handle_buttons(self, stage: Any, state: GamepadState) -> bool:
        """Handle one-shot buttons and report whether one consumed this frame."""

        command_triggered = False

        def just_pressed(button: int) -> bool:
            return state.pressed(button) and not bool(self._previous_buttons & button)

        if just_pressed(XINPUT_GAMEPAD_BACK):
            self._emergency_stopped = True
            self._pending_pick_alignment = None
            self._log("Emergency stop active. Press Start to resume manual mode.")
            # Emergency stop wins over Start and every other command sampled in
            # the same frame.
            return True
        if just_pressed(XINPUT_GAMEPAD_START):
            command_triggered = True
            self._emergency_stopped = False
            self._log("Emergency stop cleared. Hold RB before moving.")
        if just_pressed(XINPUT_GAMEPAD_DPAD_UP):
            command_triggered = True
            self._activate_camera(stage, "GLOBAL")
        if just_pressed(XINPUT_GAMEPAD_DPAD_DOWN):
            command_triggered = True
            self._activate_camera(stage, "SPREADER_UNDERSIDE")
        if self._emergency_stopped:
            return command_triggered
        # Gripping and spreader resizing also require the dead-man switch.
        if not state.pressed(XINPUT_GAMEPAD_RIGHT_SHOULDER):
            return command_triggered
        if just_pressed(XINPUT_GAMEPAD_A):
            command_triggered = True
            if self._carried_source_path:
                self._release_container(stage)
            else:
                self._pick_container(stage)
        if just_pressed(XINPUT_GAMEPAD_X):
            command_triggered = True
            self._bay_target = None
            self._display_bay_target = None
            self._rtg_controller.set_spreader_size(20, smooth=False)
        if just_pressed(XINPUT_GAMEPAD_B):
            command_triggered = True
            self._bay_target = None
            self._display_bay_target = None
            self._rtg_controller.set_spreader_size(40, smooth=False)
        return command_triggered

    def _handle_keyboard_buttons(self, stage: Any, state: KeyboardState) -> bool:
        """Map keyboard one-shots to the same safe operations as the gamepad."""

        actions = state.just_pressed
        if not actions:
            return False
        command_triggered = False
        if "ESCAPE" in actions:
            self._emergency_stopped = True
            self._pending_pick_alignment = None
            self._log("Emergency stop active. Press Enter to resume manual mode.")
            # Escape wins over Enter/G/size switching in the same input batch.
            return True
        if "ENTER" in actions:
            self._emergency_stopped = False
            self._log("Emergency stop cleared. Hold Space before moving.")
            command_triggered = True
        if "HOME" in actions:
            self._activate_camera(stage, "GLOBAL")
            command_triggered = True
        if "END" in actions:
            self._activate_camera(stage, "SPREADER_UNDERSIDE")
            command_triggered = True
        if "F" in actions:
            next_mode = (
                "SPREADER_UNDERSIDE"
                if self._active_camera_mode == "GLOBAL"
                else "GLOBAL"
            )
            self._activate_camera(stage, next_mode)
            command_triggered = True
        if self._emergency_stopped:
            return command_triggered
        if "G" in actions:
            if self._carried_source_path:
                self._release_container(stage)
            else:
                self._pick_container(stage)
            command_triggered = True
        if "ONE" in actions:
            self._bay_target = None
            self._display_bay_target = None
            self._rtg_controller.set_spreader_size(20, smooth=False)
            command_triggered = True
        if "TWO" in actions:
            self._bay_target = None
            self._display_bay_target = None
            self._rtg_controller.set_spreader_size(40, smooth=False)
            command_triggered = True
        return command_triggered

    def _bind_stage(self, stage: Any) -> None:
        if self._stage_id == id(stage) and self._rtg_controller is not None:
            return
        rtg_live_control = _load_rtg_live_control(force_reload=True)

        self._rtg_controller = rtg_live_control.RTGController(stage)
        self._stage_id = id(stage)
        self._lock_foot_paths = None
        self._spreader_geometry_paths = None
        self._live_container_root_paths = None
        self._container_contact_root_paths = None
        self._last_collision_probe_center_world = None
        self._last_collision_scan_at = 0.0
        self._last_spreader_contact_notice_at = 0.0
        self._spreader_vertical_contact_active = False
        self._last_carried_collision_center_world = None
        self._dynamic_releases = []
        self._manual_physics_accumulator = 0.0
        self._manual_physics_time = 0.0
        self._manual_physics_started = False
        self._manual_physics_error_reported = False
        self._manual_physics_start_reported = False
        self._manual_physics_stage_id = None
        self._gantry_m, self._trolley_m, self._hoist_m = self._rtg_controller.get_positions()
        # Resolve the takeover bay from the physical gantry centre.  The
        # status prim can still contain the preceding simulator bay and must
        # not become the manual big-car reference.
        self._gantry_bay_reference = None
        takeover_spreader_ft = self._current_spreader_size_ft()
        takeover_center_m = self._gantry_bay_center_m(takeover_spreader_ft)
        takeover_bay = self._nearest_bay()
        self._gantry_bay_reference = (
            (takeover_bay[0], takeover_bay[1], self._gantry_m)
            if takeover_bay[0] is not None and takeover_bay[1] is not None
            else None
        )
        self._last_update_at = time.monotonic()
        self._log(
            "Took over current pose "
            f"gantry={self._gantry_m:.2f} trolley={self._trolley_m:.2f} hoist={self._hoist_m:.2f}"
            f" gantryCenter={takeover_center_m:.2f}"
            + (
                f" spreader={takeover_spreader_ft}ft"
                if takeover_spreader_ft is not None
                else ""
            )
            + (
                f" bay={takeover_bay[0]}/{takeover_bay[1]:03d}"
                if takeover_bay[0] is not None and takeover_bay[1] is not None
                else ""
            )
        )

    @staticmethod
    def _stage_reported_bay(stage: Any) -> Optional[tuple[str, int]]:
        """Read the last PLC/simulation business bay before manual takeover."""

        prim = stage.GetPrimAtPath(STATUS_PRIM_PATH)
        if not prim:
            return None
        field_attr = prim.GetAttribute("rtg:currentField")
        bay_attr = prim.GetAttribute("rtg:currentBay")
        if not field_attr or not bay_attr:
            return None
        field = str(field_attr.Get() or "").strip().upper()
        try:
            bay = int(bay_attr.Get() or 0)
        except (TypeError, ValueError):
            return None
        if not field or bay <= 0:
            return None
        try:
            from yard_coordinate_mapping import validate_bay

            validate_bay(field, bay)
        except (ImportError, ValueError):
            return None
        return field, bay

    @staticmethod
    def _create_gamepad_reader(controller_index: int) -> Any:
        """Prefer Kit's own input device; retain XInput for standalone Kit builds."""

        try:
            return KitGamepad(controller_index)
        except Exception as kit_error:  # noqa: BLE001 - XInput is a deliberate fallback
            try:
                reader = XInputGamepad(controller_index)
            except Exception as xinput_error:  # noqa: BLE001 - give both useful causes
                raise RuntimeError(
                    f"Kit gamepad unavailable ({kit_error}); XInput fallback also failed ({xinput_error})"
                ) from xinput_error
            print(f"[RTG Gamepad Manual] Kit gamepad unavailable; using XInput fallback: {kit_error}")
            return reader

    def _disable_kit_gamepad_camera_control(self) -> None:
        """Stop the viewport's default gamepad navigation during RTG takeover."""

        try:
            import carb  # type: ignore

            settings = carb.settings.get_settings()
            key = "persistent/app/omniverse/gamepadCameraControl"
            self._camera_control_previous = settings.get(key)
            settings.set(key, False)
            self._log("Disabled Kit gamepad camera navigation.")
        except Exception as exc:  # noqa: BLE001 - not all standalone Kit apps expose settings
            self._log(f"Could not disable Kit gamepad camera navigation: {exc}")

    def _restore_kit_gamepad_camera_control(self) -> None:
        if self._camera_control_previous is None:
            return
        try:
            import carb  # type: ignore

            carb.settings.get_settings().set(
                "persistent/app/omniverse/gamepadCameraControl",
                self._camera_control_previous,
            )
        except Exception:
            pass
        self._camera_control_previous = None

    def _pause_timeline_for_manual_mode(self) -> None:
        """Stop authored demo/task animation from competing with manual control."""

        try:
            import omni.timeline  # type: ignore

            timeline = omni.timeline.get_timeline_interface()
            is_playing = getattr(timeline, "is_playing", None)
            if callable(is_playing) and not is_playing():
                return
            pause = getattr(timeline, "pause", None)
            if callable(pause):
                pause()
                self._log("Paused Kit timeline playback for remote manual mode.")
                return
            stop = getattr(timeline, "stop", None)
            if callable(stop):
                stop()
                self._log("Stopped Kit timeline playback for remote manual mode.")
        except Exception as exc:  # noqa: BLE001 - manual motion must still start
            self._log(f"Could not pause Kit timeline playback: {exc}")

    def _disable_demo_sequence_artifacts(self, stage: Any) -> int:
        """Deactivate sequence-demo truck/lane artifacts during manual control."""

        from pxr import Usd, UsdGeom

        paths: set[str] = set()
        for path in DEMO_SEQUENCE_ARTIFACT_ROOTS:
            prim = stage.GetPrimAtPath(path)
            if prim:
                paths.add(path)
        for prim in stage.Traverse():
            try:
                name = str(prim.GetName())
                path = str(prim.GetPath())
            except RuntimeError:
                continue
            if any(
                name.startswith(prefix)
                for prefix in DEMO_SEQUENCE_ARTIFACT_NAME_PREFIXES
            ):
                paths.add(path)

        disabled = 0
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            for path in self._top_level_paths(paths):
                prim = stage.GetPrimAtPath(path)
                if not prim:
                    continue
                try:
                    prim.SetActive(False)
                except Exception:
                    UsdGeom.Imageable(prim).MakeInvisible()
                disabled += 1
        if disabled:
            self._log(f"Disabled {disabled} sequence-demo artifact(s) for manual mode.")
        return disabled

    @staticmethod
    def _top_level_paths(paths: set[str]) -> list[str]:
        selected: list[str] = []
        for path in sorted(paths, key=lambda item: (item.count("/"), item)):
            if any(path.startswith(f"{parent}/") for parent in selected):
                continue
            selected.append(path)
        return selected

    def _activate_camera(self, stage: Any, mode: str) -> None:
        """Select an RTG-attached operational view in the active viewport."""

        camera_path = self._ensure_manual_camera(stage, mode)
        try:
            from omni.kit.viewport.utility import get_active_viewport  # type: ignore

            viewport = get_active_viewport()
            if viewport is None:
                raise RuntimeError("No active viewport")
            viewport.camera_path = camera_path
        except Exception as exc:  # noqa: BLE001 - preserve manual motion if UI changes
            self._log(f"Could not switch to {mode} view: {exc}")
            return
        self._active_camera_mode = mode
        label = "RTG global" if mode == "GLOBAL" else "trolley overhead"
        self._log(f"Switched to {label} view.")

    @staticmethod
    def _camera_matrix(eye: Any, target: Any, up: Any) -> Any:
        from pxr import Gf

        matrix = Gf.Matrix4d(1.0)
        try:
            matrix.SetLookAt(eye, target, up)
            return matrix.GetInverse()
        except Exception:
            # The two installed views have fixed non-collinear up vectors, so
            # this branch is primarily a compatibility fallback for old Kit.
            matrix = Gf.Matrix4d(1.0)
            matrix.SetTranslate(eye)
            return matrix

    def _ensure_manual_camera(self, stage: Any, mode: str) -> str:
        """Author the two camera mounts in the session layer on first use."""

        from pxr import Gf, Usd, UsdGeom, UsdPhysics

        if mode == "GLOBAL":
            path = GLOBAL_CAMERA_PATH
            # Parent to gantry: this operational overview travels with the RTG
            # while still keeping the complete machine and work zone in frame.
            eye = Gf.Vec3d(16.0, -26.0, 17.0)
            target = Gf.Vec3d(0.0, 0.0, 3.0)
            up = Gf.Vec3d(0.0, 0.0, 1.0)
        elif mode == "SPREADER_UNDERSIDE":
            path = SPREADER_UNDERSIDE_CAMERA_PATH
            # Parent to trolley: camera looks straight down from the trolley
            # frame so the operator sees the spreader, lock feet, and
            # surrounding container tops from above.  The camera stays on the
            # trolley regardless of hoist height, giving a stable aerial view.
            eye = Gf.Vec3d(2.55, -5.0, 8.0)
            target = Gf.Vec3d(2.55, -5.0, 0.0)
            up = Gf.Vec3d(0.0, 1.0, 0.0)
        else:
            raise ValueError(f"Unknown manual camera mode: {mode}")

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            camera = UsdGeom.Camera.Define(stage, path)
            camera.CreateProjectionAttr(UsdGeom.Tokens.perspective)
            camera.CreateFocalLengthAttr(18.0 if mode == "GLOBAL" else 14.0)
            camera.CreateClippingRangeAttr(Gf.Vec2f(0.03, 2500.0))
            prim = camera.GetPrim()
            xform = UsdGeom.Xformable(prim)
            transform_attr = prim.GetAttribute("xformOp:transform")
            if not transform_attr:
                transform_attr = xform.AddTransformOp().GetAttr()
            transform_attr.Set(self._camera_matrix(eye, target, up))
            UsdGeom.Imageable(prim).MakeInvisible()
        return path

    def _pick_container(self, stage: Any) -> None:
        from pxr import Gf, Usd, UsdGeom

        if self._pending_pick_alignment is not None:
            self._log("Automatic lock-foot alignment is still running; wait for the aligned message.")
            return
        # Use the real red lock feet.  The hoist origin is offset from the
        # yellow spreader and can otherwise select a box behind the spreader.
        lock_foot_points = self._spreader_lock_foot_points(stage)
        if not lock_foot_points:
            self._log("Could not resolve the spreader lock-foot position.")
            return
        lock_contact = self._lock_foot_center(lock_foot_points)
        current_field, current_bay = self._nearest_bay(stage)
        current_bay_id = (
            f"{current_field}/{current_bay:03d}"
            if current_field is not None and current_bay is not None
            else None
        )
        current_row = self._nearest_row_for_trolley_m(self._trolley_m)
        current_row_id = f"R{current_row:02d}" if current_row is not None else "--"
        if current_row is None:
            self._log(
                "No stack row is under the trolley centre; "
                f"trolley={self._trolley_m:.2f} m."
            )
            return
        trolley_delta = self._trolley_row_delta_m(current_row)
        trolley_horizontal = abs(trolley_delta)
        # ``xformOp:scale`` is local to an inventory prim.  The live yard can
        # additionally be placed through parent/reference transforms, so it
        # must not be used as a world-space box dimension for a lock decision.
        # The rendered world bounds are the same geometry the operator sees.
        bbox_cache = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            useExtentsHint=False,
        )
        candidate = None
        candidate_bounds = None
        candidate_distance = float("inf")
        candidate_foot_count = 0
        candidate_horizontal = 0.0
        candidate_vertical = 0.0
        nearest_prim = None
        nearest_horizontal = 0.0
        nearest_vertical = 0.0
        nearest_dx = 0.0
        nearest_dy = 0.0
        nearest_dz = 0.0
        nearest_foot_count = 0
        nearest_gap = float("inf")
        nearest_distance = float("inf")
        for prim in stage.Traverse():
            bay_attr = prim.GetAttribute("live:bayId")
            if not bay_attr or "Container" not in prim.GetName():
                continue
            # Longitudinal contact is represented by the current business bay.
            # This prevents ignoring Y from ever locking a geometrically
            # similar box in another bay, while avoiding false failures caused
            # by the RTG's bay-stop visual centreline corrections.
            if current_bay_id is not None and str(bay_attr.Get()) != current_bay_id:
                continue
            row = self._live_container_row(prim)
            if current_row is not None and row is not None and row != current_row:
                continue
            bounds = self._world_container_bounds(bbox_cache, prim)
            if bounds is None:
                continue
            minimum, maximum = bounds
            center = (minimum + maximum) * 0.5
            dx = trolley_delta
            dy = float(center[1] - lock_contact[1])
            dz = float(maximum[2] - lock_contact[2])
            horizontal = trolley_horizontal
            vertical = abs(dz)
            foot_count, footprint_error, footprint_gap = self._container_lock_foot_contact(
                lock_foot_points, minimum, maximum, ignore_x=True, ignore_y=True
            )
            if horizontal > PICK_HORIZONTAL_TOLERANCE_M:
                foot_count = 0
                footprint_error += horizontal
                footprint_gap += horizontal
            # Diagnostics use the same trolley-centre row alignment that gates
            # locking, while vertical seating still comes from the red feet.
            distance = footprint_gap
            if distance < nearest_distance:
                nearest_prim = prim
                nearest_horizontal = horizontal
                nearest_vertical = vertical
                nearest_dx = dx
                nearest_dy = dy
                nearest_dz = dz
                nearest_foot_count = foot_count
                nearest_gap = footprint_gap
                nearest_distance = distance
            if foot_count >= PICK_MIN_CONTACT_FEET:
                if (
                    foot_count > candidate_foot_count
                    or (
                        foot_count == candidate_foot_count
                        and footprint_error < candidate_distance
                    )
                ):
                    candidate = prim
                    candidate_bounds = bounds
                    candidate_distance = footprint_error
                    candidate_foot_count = foot_count
                    candidate_horizontal = horizontal
                    candidate_vertical = vertical
        if not candidate:
            if nearest_prim is not None:
                self._log(
                    "No container is aligned below the spreader; "
                    f"currentBay={current_bay_id or '--'} "
                    f"currentRow={current_row_id} "
                    f"nearest={nearest_prim.GetPath()} "
                    f"trolleyX={nearest_horizontal:.02f} m "
                    f"vertical={nearest_vertical:.02f} m "
                    f"deltaX={nearest_dx:+.02f} m (D+/A-) "
                    f"deltaY={nearest_dy:+.02f} m (gantry ignored for lock) "
                    f"deltaZ={nearest_dz:+.02f} m (Up+/Down-) "
                    f"lockFeet={nearest_foot_count}/{len(lock_foot_points)} "
                    f"gap={nearest_gap:.02f} m "
                    f"(lock-foot contact requires >= {PICK_MIN_CONTACT_FEET} feet)."
                )
            else:
                self._log(
                    "No live container was found in the current bay "
                    f"{current_bay_id or '--'} row {current_row_id}."
                )
            return
        self._deactivate_dynamic_release_for_pick(stage, str(candidate.GetPath()))
        self._carried_source_path = str(candidate.GetPath())
        self._carried_component_paths = [
            str(component.GetPath()) for component in self._container_components(candidate)
        ]
        assert candidate_bounds is not None
        candidate_minimum, candidate_maximum = candidate_bounds
        candidate_center = (candidate_minimum + candidate_maximum) * 0.5
        lock_center = lock_contact - Gf.Vec3d(
            0.0, 0.0, float(candidate_maximum[2] - candidate_minimum[2]) * 0.5
        )
        # Do not snap a box when it is locked.  Preserve its observed contact
        # offset and apply only subsequent RTG motion; this avoids an apparent
        # spreader jump and makes an imperfect manual alignment visibly fail.
        self._carried_lock_offset_world = candidate_center - lock_center
        self._capture_carried_attachment(stage)
        self._last_collision_probe_center_world = None
        self._log(
            f"Container locked: {self._carried_source_path} "
            f"(row={current_row_id}, lockFeet={candidate_foot_count}, "
            f"horizontal={candidate_horizontal:.02f} m, vertical={candidate_vertical:.02f} m)"
        )

    def _apply_held_pose(self) -> None:
        """Write the held pose without losing a bay-addressed alignment target."""

        if self._bay_target is not None:
            block, bay, size_ft = self._bay_target
            from rtg_live_control import SPREADER_20FT_CENTER_OFFSET_Y_M

            self._gantry_m = self._rtg_controller.set_gantry_bay(
                block,
                bay,
                center_offset_y=(
                    SPREADER_20FT_CENTER_OFFSET_Y_M if size_ft == 20 else 0.0
                ),
                container_size_ft=size_ft,
            )
            self._trolley_m = self._rtg_controller.set_trolley_safe(self._trolley_m)
            self._hoist_m = self._rtg_controller.set_hoist_safe(self._hoist_m)
            return
        self._gantry_m, self._trolley_m, self._hoist_m = (
            self._rtg_controller.set_positions_safe(
                gantry_m=self._gantry_m,
                trolley_m=self._trolley_m,
                hoist_m=self._hoist_m,
            )
        )

    def _release_bay_target_for_continuous_travel(self) -> None:
        """Continue from a calibrated bay stop without an initial visual jump."""

        if self._bay_target is None:
            return
        block, bay, size_ft = self._bay_target
        from rtg_live_control import (
            RTG_BAY_CENTERLINE_CORRECTION_Y_M,
            SPREADER_20FT_CENTER_OFFSET_Y_M,
        )
        from yard_coordinate_mapping import bay_center_m

        self._gantry_m = (
            bay_center_m(block, bay)
            + RTG_BAY_CENTERLINE_CORRECTION_Y_M
            + (SPREADER_20FT_CENTER_OFFSET_Y_M if size_ft == 20 else 0.0)
        )
        self._gantry_bay_reference = (block, bay, self._gantry_m)
        self._bay_target = None
        self._display_bay_target = None

    def position_for_pick(
        self,
        *,
        block: str,
        bay: int,
        row: int,
        tier: int,
        container_size_ft: int = 20,
    ) -> dict[str, Any]:
        """Move directly to a nominal stack-top contact point for grip testing."""

        if row not in TROLLEY_ROW_CENTERS_M:
            raise ValueError("row must be between 1 and 6")
        if not 1 <= int(tier) <= 6:
            raise ValueError("tier must be between 1 and 6")
        if container_size_ft not in (20, 40):
            raise ValueError("container_size_ft must be 20 or 40")

        from yard_coordinate_mapping import bay_center_m, validate_bay

        normalized_block = str(block).strip().upper()
        validate_bay(normalized_block, int(bay))
        stage = self._current_stage()
        if stage is None:
            raise RuntimeError("No USD stage is open.")
        self._bind_stage(stage)
        self._gantry_m = bay_center_m(normalized_block, int(bay))
        self._trolley_m = TROLLEY_ROW_CENTERS_M[int(row)]
        # A live stack can contain 2.59 m or 2.90 m boxes.  The nominal 2.60 m
        # tier height remains inside the manual grip tolerance and is a safe,
        # deterministic starting point for this test command.
        self._hoist_m = CONTAINER_NOMINAL_HEIGHT_M * int(tier)
        self._rtg_controller.set_spreader_size(container_size_ft, smooth=False)
        # First enter the requested *business* bay through the same calibrated
        # mapping used by normal RTG commands.  A live inventory prim can have
        # a local/reference transform that is not a gantry travel coordinate;
        # using that raw transform here was what incorrectly parked 3C/055 at
        # the visible 3C/062 area.  The following update-frame solver uses the
        # visible red lock feet only for small final alignment.
        self._bay_target = (normalized_block, int(bay), container_size_ft)
        self._apply_held_pose()
        target_container = self._find_live_container(
            stage, normalized_block, int(bay), int(row), int(tier)
        )
        # Do not retain the bay target: final lock-foot alignment needs to own
        # the continuous gantry coordinate after this correctly-calibrated stop.
        self._release_bay_target_for_continuous_travel()
        self._apply_held_pose()
        self._pending_pick_alignment = {
            "targetPath": str(target_container.GetPath()),
            "phase": "settle",
            "axisIndex": 0,
        }
        # Do not mask the actual gantry position with the requested bay in the
        # HUD.  Operators must see the real bay after any manual travel.
        self._display_bay_target = None
        self._write_status(stage, connected=True)
        target = f"{normalized_block}/{int(bay):03d} row={row} tier={tier}"
        self._log(
            f"Positioned for grip test: {target} ({container_size_ft} ft); "
            "measuring red lock-foot alignment."
        )
        return {
            "target": target,
            "gantryMeter": self._gantry_m,
            "trolleyMeter": self._trolley_m,
            "hoistMeter": self._hoist_m,
            "spreaderSizeFt": container_size_ft,
        }

    def _position_axes_for_live_container(self, container: Any) -> None:
        """Set engineering axes from the model's verified world calibration."""

        from pxr import Gf, UsdGeom
        from rtg_live_control import (
            gantry_usd_to_actual,
            hoist_usd_to_actual,
            trolley_usd_to_actual,
        )

        center = UsdGeom.XformCache().GetLocalToWorldTransform(container).Transform(
            Gf.Vec3d()
        )
        gantry_usd = GANTRY_HOME_USD_Y + (
            float(center[1]) - SPREADER_WORLD_Y_AT_GANTRY_HOME
        )
        trolley_usd = (
            float(center[0]) - SPREADER_WORLD_X_AT_TROLLEY_ZERO
        ) / TROLLEY_USD_TO_WORLD_X
        hoist_usd = REFERENCE_PICK_HOIST_Z + (
            float(center[2]) - REFERENCE_PICK_CONTAINER_CENTER_Z
        )
        self._gantry_m = gantry_usd_to_actual(gantry_usd)
        self._trolley_m = trolley_usd_to_actual(trolley_usd)
        self._hoist_m = hoist_usd_to_actual(hoist_usd)
        self._log(
            "Applied live-container axis target "
            f"gantry={self._gantry_m:.2f} trolley={self._trolley_m:.2f} "
            f"hoist={self._hoist_m:.2f}"
        )

    def _advance_pick_alignment(self, stage: Any) -> None:
        """Calibrate the three live axes against the visible red lock feet.

        USD session transforms are evaluated on Kit update frames.  Measuring a
        probe immediately after writing it reads the preceding transform and
        was the source of the 10.79 m false miss.  This state machine writes one
        small probe, waits a frame, measures its real world gain, then corrects
        that one axis before proceeding to the next.
        """

        pending = self._pending_pick_alignment
        if pending is None:
            return
        from pxr import Gf, UsdGeom

        target = stage.GetPrimAtPath(pending["targetPath"])
        scale = self._get_scale(target)
        lock_contact = self._spreader_container_center(stage)
        if not target or scale is None or lock_contact is None:
            self._pending_pick_alignment = None
            self._log("Automatic lock-foot alignment failed: target or lock-foot geometry is unavailable.")
            return
        center = UsdGeom.XformCache().GetLocalToWorldTransform(target).Transform(Gf.Vec3d())
        desired = center + Gf.Vec3d(0.0, 0.0, float(scale[2]) * 0.5)
        # Gantry is positioned solely by ``set_gantry_bay`` above.  Inventory
        # references may carry a local longitudinal offset, and using their
        # raw transform to auto-correct the gantry was able to move a requested
        # 3C/055 stop into the visible 3C/062 area.  The final contact solver
        # therefore corrects only trolley (X) and hoist (Z); the operator keeps
        # explicit authority for any longitudinal change.
        axes = (
            ("_trolley_m", 0, "trolley"),
            ("_hoist_m", 2, "hoist"),
        )
        phase = pending["phase"]
        if phase == "settle":
            axis_name, component, label = axes[pending["axisIndex"]]
            probe = 0.25
            pending["baseline"] = lock_contact
            pending["probe"] = probe
            pending["axisName"] = axis_name
            pending["component"] = component
            pending["label"] = label
            setattr(self, axis_name, float(getattr(self, axis_name)) + probe)
            pending["phase"] = "measure_probe"
            self._apply_held_pose()
            return
        if phase == "measure_probe":
            component = int(pending["component"])
            baseline = pending["baseline"]
            gain = float(lock_contact[component] - baseline[component]) / float(pending["probe"])
            if abs(gain) < 0.01:
                self._pending_pick_alignment = None
                self._log(
                    f"Automatic lock-foot alignment failed: {pending['label']} axis did not move the lock feet."
                )
                return
            error = float(desired[component] - lock_contact[component])
            axis_name = pending["axisName"]
            setattr(self, axis_name, float(getattr(self, axis_name)) + error / gain)
            pending["axisIndex"] += 1
            pending["phase"] = "verify" if pending["axisIndex"] >= len(axes) else "settle"
            self._apply_held_pose()
            return
        residual = lock_contact - desired
        maximum_error = max(abs(float(residual[index])) for index in range(3))
        self._pending_pick_alignment = None
        if maximum_error <= 0.20:
            self._log(
                "Red lock-foot alignment complete "
                f"(dx={float(residual[0]):.02f}, dy={float(residual[1]):.02f}, "
                f"dz={float(residual[2]):.02f} m). Press G to lock."
            )
            return
        self._log(
            "Red lock-foot alignment retained an offset "
            f"(dx={float(residual[0]):.02f}, dy={float(residual[1]):.02f}, "
            f"dz={float(residual[2]):.02f} m); use the meters for final manual adjustment."
        )

    @staticmethod
    def _find_live_container(
        stage: Any, block: str, bay: int, row: int, tier: int
    ) -> Any:
        """Return the exact live inventory box requested for a grip test."""

        expected_bay_id = f"{block}/{bay:03d}"
        for prim in stage.Traverse():
            if "Container" not in prim.GetName():
                continue
            bay_attr = prim.GetAttribute("live:bayId")
            row_attr = prim.GetAttribute("live:row")
            tier_attr = prim.GetAttribute("live:tier")
            if not bay_attr or not row_attr or not tier_attr:
                continue
            if (
                str(bay_attr.Get()) == expected_bay_id
                and int(row_attr.Get()) == row
                and int(tier_attr.Get()) == tier
            ):
                return prim
        raise RuntimeError(
            f"No live container exists at {expected_bay_id}, row {row}, tier {tier}."
        )

    def _align_lock_feet_to_container(self, stage: Any, container: Any) -> None:
        """Numerically align the three RTG axes to a live box's lock plane."""

        from pxr import Gf, UsdGeom

        scale = self._get_scale(container)
        if scale is None:
            raise RuntimeError(f"Container has no scale: {container.GetPath()}")
        center = UsdGeom.XformCache().GetLocalToWorldTransform(container).Transform(
            Gf.Vec3d()
        )
        desired_contact = center + Gf.Vec3d(0.0, 0.0, float(scale[2]) * 0.5)

        # The controller hierarchy is effectively orthogonal: gantry changes
        # world Y, trolley changes X, and hoist changes Z.  Measure those gains
        # in the composed stage instead of relying on fragile authored offsets.
        for _ in range(2):
            lock_contact = self._spreader_container_center(stage)
            if lock_contact is None:
                raise RuntimeError("Could not resolve the spreader lock-foot position.")
            errors = (
                float(desired_contact[1] - lock_contact[1]),
                float(desired_contact[0] - lock_contact[0]),
                float(desired_contact[2] - lock_contact[2]),
            )
            axis_names = ("_gantry_m", "_trolley_m", "_hoist_m")
            world_components = (1, 0, 2)
            for axis_name, world_component, error in zip(
                axis_names, world_components, errors
            ):
                if abs(error) < 0.01:
                    continue
                original = float(getattr(self, axis_name))
                probe = 0.25
                setattr(self, axis_name, original + probe)
                self._apply_held_pose()
                probed_contact = self._spreader_container_center(stage)
                setattr(self, axis_name, original)
                if probed_contact is None:
                    continue
                gain = float(probed_contact[world_component] - lock_contact[world_component]) / probe
                if abs(gain) < 0.0001:
                    continue
                setattr(self, axis_name, original + error / gain)
                self._apply_held_pose()
        self._apply_held_pose()
        final_contact = self._spreader_container_center(stage)
        if final_contact is None:
            raise RuntimeError("Could not resolve final spreader lock-foot position.")
        residual = final_contact - desired_contact
        maximum_error = max(abs(float(residual[index])) for index in range(3))
        self._log(
            "Target alignment residual "
            f"dx={float(residual[0]):.03f} dy={float(residual[1]):.03f} "
            f"dz={float(residual[2]):.03f} m"
        )
        if maximum_error > 0.08:
            raise RuntimeError(
                "Target alignment did not converge; manual position was retained "
                f"but residual is {maximum_error:.03f} m."
            )

    def _release_container(self, stage: Any = None) -> None:
        support_note = ""
        if stage is not None and self._carried_source_path:
            support_note = self._settle_released_container(stage)
        self._log(f"Container released: {self._carried_source_path}{support_note}")
        self._carried_source_path = None
        self._carried_component_paths = []
        self._carried_lock_offset_world = None
        self._carried_component_origins_world = {}
        self._carried_component_offsets_x = {}
        self._carried_hoist_origin_world = None
        self._last_collision_probe_center_world = None
        self._last_carried_collision_center_world = None

    def _settle_released_container(self, stage: Any) -> str:
        """Drop the released payload vertically and topple it if its centre is unsupported."""

        if not self._carried_source_path:
            return ""
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

        source = stage.GetPrimAtPath(self._carried_source_path)
        if not source:
            return ""
        bbox_cache = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            useExtentsHint=False,
        )
        source_bounds = self._world_container_bounds(bbox_cache, source)
        if source_bounds is None:
            return ""
        source_minimum, source_maximum = source_bounds
        component_paths = set(self._carried_component_paths or [self._carried_source_path])
        supports = []
        support_roots_seen = set()
        for prim in stage.Traverse():
            if "Container" not in prim.GetName():
                continue
            root = self._live_container_root(prim)
            if not self._is_container_contact_root_name(
                str(root.GetName()),
                has_live_bay=bool(root.GetAttribute("live:bayId")),
            ):
                continue
            path = str(root.GetPath())
            if path in component_paths or path in support_roots_seen:
                continue
            support_roots_seen.add(path)
            bounds = self._world_container_bounds(bbox_cache, root)
            if bounds is None:
                continue
            supports.append((path, bounds[0], bounds[1]))

        support_z, support_path = self._release_support_from_bounds(
            source_minimum,
            source_maximum,
            supports,
        )
        support_bounds_by_path = {
            path: (support_minimum, support_maximum)
            for path, support_minimum, support_maximum in supports
        }
        selected_support_bounds = support_bounds_by_path.get(support_path)
        z_delta = support_z - float(source_minimum[2])
        current_field, current_bay = self._nearest_bay(stage)
        current_bay_id = (
            f"{current_field}/{current_bay:03d}"
            if current_field is not None and current_bay is not None
            else None
        )
        current_row = self._nearest_row_for_trolley_m(self._trolley_m)
        support_location = None
        if support_path != "ground":
            support_location = self._live_container_location(
                stage.GetPrimAtPath(support_path)
            )
        topple_plan = None
        if selected_support_bounds is not None:
            topple_plan = self._release_topple_plan(
                source_minimum,
                source_maximum,
                selected_support_bounds[0],
                selected_support_bounds[1],
            )
        released_location = self._released_location_from_support(
            current_bay_id=current_bay_id,
            current_row=current_row,
            support_location=support_location,
        )
        # Do not teleport the box onto the computed support and do not rotate
        # it to a scripted 90-degree pose.  The support lookup remains useful
        # for inventory metadata and for choosing a tiny initial tipping
        # direction, but gravity, collision and the centre of mass now decide
        # the actual trajectory from the operator's exact release position.
        release_mode = "dynamic_rigid"
        topple_note = ""
        if topple_plan is not None:
            axis, fall_sign, _pivot_edge = topple_plan
            topple_note = f" initialToppleAxis={axis} sign={fall_sign:+d}"
        self._activate_dynamic_release(
            stage,
            source=source,
            component_paths=component_paths,
            support_paths=[path for path, _minimum, _maximum in supports],
            topple_plan=topple_plan,
            expected_free_drop_m=max(0.0, -z_delta),
        )
        for path in component_paths:
            prim = stage.GetPrimAtPath(path)
            if not prim:
                continue
            release_mode_attr = prim.GetAttribute(
                "live:manualReleaseMode"
            ) or prim.CreateAttribute(
                "live:manualReleaseMode",
                Sdf.ValueTypeNames.Token,
            )
            release_mode_attr.Set(release_mode)
            support_attr = prim.GetAttribute(
                "live:manualSupportPath"
            ) or prim.CreateAttribute(
                "live:manualSupportPath",
                Sdf.ValueTypeNames.String,
            )
            support_attr.Set(support_path)
            toppled_attr = prim.GetAttribute(
                "live:manualToppled"
            ) or prim.CreateAttribute(
                "live:manualToppled",
                Sdf.ValueTypeNames.Bool,
            )
            toppled_attr.Set(topple_plan is not None)
            if path == self._carried_source_path and released_location is not None:
                bay_id, row, tier = released_location
                bay_attr = prim.GetAttribute("live:bayId") or prim.CreateAttribute(
                    "live:bayId",
                    Sdf.ValueTypeNames.String,
                )
                bay_attr.Set(bay_id)
                row_attr = prim.GetAttribute("live:row") or prim.CreateAttribute(
                    "live:row",
                    Sdf.ValueTypeNames.Int,
                )
                row_attr.Set(row)
                tier_attr = prim.GetAttribute("live:tier") or prim.CreateAttribute(
                    "live:tier",
                    Sdf.ValueTypeNames.Int,
                )
                tier_attr.Set(tier)
                tiers_attr = prim.GetAttribute("live:tiers") or prim.CreateAttribute(
                    "live:tiers",
                    Sdf.ValueTypeNames.Int,
                )
                tiers_attr.Set(tier)
        return (
            f" ({release_mode} freeDrop={max(0.0, -z_delta):.02f} m "
            f"support={support_path}{topple_note})"
        )

    def _activate_dynamic_release(
        self,
        stage: Any,
        *,
        source: Any,
        component_paths: set[str] | list[str] | tuple[str, ...],
        support_paths: list[str],
        topple_plan: Optional[tuple[str, int, float]],
        expected_free_drop_m: float,
    ) -> None:
        """Turn a released container into a PhysX body at its current pose."""

        from pxr import Gf, Usd, UsdGeom, UsdPhysics

        source_path = str(source.GetPath())
        self._deactivate_dynamic_release_for_pick(stage, source_path)
        cache = UsdGeom.XformCache()
        initial_source_world = Gf.Matrix4d(
            cache.GetLocalToWorldTransform(source)
        )
        followers: dict[str, Any] = {}
        for path in component_paths:
            if path == source_path:
                continue
            component = stage.GetPrimAtPath(path)
            if component:
                followers[path] = Gf.Matrix4d(
                    cache.GetLocalToWorldTransform(component)
                )
        active_dynamic_paths = {
            str(release.get("sourcePath", ""))
            for release in self._dynamic_releases
        }

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            self._ensure_manual_physics_ground(stage)
            for support_path in support_paths:
                support = stage.GetPrimAtPath(support_path)
                if not support or support_path == source_path:
                    continue
                collision = UsdPhysics.CollisionAPI.Apply(support)
                collision.CreateCollisionEnabledAttr(True)
                if support_path not in active_dynamic_paths:
                    support_body = UsdPhysics.RigidBodyAPI.Get(
                        stage, support.GetPath()
                    )
                    if support_body:
                        support_body.CreateRigidBodyEnabledAttr(False)
                self._bind_manual_physics_material(stage, support)

            # The main container cube is the single dynamic collision body.
            # Decorative ribs/doors remain visual followers; treating every
            # sibling mesh as a rigid body would make the container explode
            # into separate parts as soon as gravity starts.
            collision = UsdPhysics.CollisionAPI.Apply(source)
            collision.CreateCollisionEnabledAttr(True)
            rigid_body = UsdPhysics.RigidBodyAPI.Apply(source)
            rigid_body.CreateRigidBodyEnabledAttr(True)
            rigid_body.CreateKinematicEnabledAttr(False)
            rigid_body.CreateStartsAsleepAttr(False)
            rigid_body.CreateVelocityAttr(
                Gf.Vec3f(0.0, 0.0, -RELEASE_INITIAL_DOWNWARD_SPEED_M_S)
            )
            angular_velocity = Gf.Vec3f(
                *self._release_angular_velocity_components(topple_plan)
            )
            rigid_body.CreateAngularVelocityAttr(angular_velocity)
            UsdPhysics.MassAPI.Apply(source).CreateMassAttr(
                RELEASE_CONTAINER_MASS_KG
            )
            try:
                from pxr import PhysxSchema

                physx_body = PhysxSchema.PhysxRigidBodyAPI.Apply(source)
                physx_body.CreateDisableGravityAttr(False)
                physx_body.CreateSleepThresholdAttr(0.0)
            except Exception:
                pass
            self._bind_manual_physics_material(stage, source)

            for path in followers:
                component = stage.GetPrimAtPath(path)
                if not component:
                    continue
                component_collision = UsdPhysics.CollisionAPI.Get(
                    stage, component.GetPath()
                )
                if component_collision:
                    component_collision.CreateCollisionEnabledAttr(False)
                component_body = UsdPhysics.RigidBodyAPI.Get(
                    stage, component.GetPath()
                )
                if component_body:
                    component_body.CreateRigidBodyEnabledAttr(False)

        self._dynamic_releases.append(
            {
                "sourcePath": source_path,
                "initialSourceWorld": initial_source_world,
                "initialPhysicsPose": self._rigid_pose_matrix(
                    initial_source_world
                ),
                "followers": followers,
                "needsWake": True,
                "releasedAt": time.monotonic(),
                "expectedFreeDropM": float(expected_free_drop_m),
                "initialOriginZ": float(
                    initial_source_world.ExtractTranslation()[2]
                ),
                "motionReported": False,
                "stuckReported": False,
            }
        )
        self._manual_physics_accumulator = 0.0
        self._manual_physics_error_reported = False

    def _ensure_manual_physics_ground(self, stage: Any) -> None:
        """Create an invisible static yard floor for released rigid bodies."""

        from pxr import Gf, UsdGeom, UsdPhysics

        ground = UsdGeom.Cube.Get(stage, MANUAL_PHYSICS_GROUND_PATH)
        if not ground:
            ground = UsdGeom.Cube.Define(stage, MANUAL_PHYSICS_GROUND_PATH)
            ground.CreateSizeAttr(2.0)
            ground.CreateVisibilityAttr(UsdGeom.Tokens.invisible)
            xform = UsdGeom.Xformable(ground)
            xform.AddTranslateOp().Set(
                Gf.Vec3d(
                    0.0,
                    0.0,
                    RELEASE_GROUND_SUPPORT_Z_M
                    - MANUAL_PHYSICS_GROUND_THICKNESS_M * 0.5,
                )
            )
            xform.AddScaleOp().Set(
                Gf.Vec3f(
                    MANUAL_PHYSICS_GROUND_HALF_EXTENT_M,
                    MANUAL_PHYSICS_GROUND_HALF_EXTENT_M,
                    MANUAL_PHYSICS_GROUND_THICKNESS_M * 0.5,
                )
            )
        collision = UsdPhysics.CollisionAPI.Apply(ground.GetPrim())
        collision.CreateCollisionEnabledAttr(True)
        self._bind_manual_physics_material(stage, ground.GetPrim())

    @staticmethod
    def _bind_manual_physics_material(stage: Any, prim: Any) -> None:
        """Bind a moderately high-friction material without requiring PhysxSchema."""

        from pxr import Sdf, UsdPhysics, UsdShade

        material = UsdShade.Material.Get(stage, MANUAL_PHYSICS_MATERIAL_PATH)
        if not material:
            material = UsdShade.Material.Define(
                stage, MANUAL_PHYSICS_MATERIAL_PATH
            )
            material_api = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
            material_api.CreateStaticFrictionAttr(0.82)
            material_api.CreateDynamicFrictionAttr(0.65)
            material_api.CreateRestitutionAttr(0.03)
        relationship = prim.GetRelationship("material:binding:physics")
        if not relationship:
            relationship = prim.CreateRelationship(
                "material:binding:physics", custom=False
            )
        relationship.SetTargets([Sdf.Path(MANUAL_PHYSICS_MATERIAL_PATH)])

    def _advance_dynamic_releases(self, stage: Any, elapsed: float) -> None:
        """Step paused-timeline PhysX and keep container detail meshes together."""

        if not self._dynamic_releases:
            return
        self._manual_physics_accumulator += min(max(float(elapsed), 0.0), 0.1)
        substeps = 0
        while (
            self._manual_physics_accumulator >= MANUAL_PHYSICS_TIMESTEP_SECONDS
            and substeps < MANUAL_PHYSICS_MAX_SUBSTEPS
        ):
            if not self._simulate_manual_physics_step(
                stage,
                MANUAL_PHYSICS_TIMESTEP_SECONDS
            ):
                break
            self._manual_physics_accumulator -= MANUAL_PHYSICS_TIMESTEP_SECONDS
            substeps += 1
        if substeps == MANUAL_PHYSICS_MAX_SUBSTEPS:
            self._manual_physics_accumulator = min(
                self._manual_physics_accumulator,
                MANUAL_PHYSICS_TIMESTEP_SECONDS,
            )
        self._sync_dynamic_release_followers(stage)

    def _simulate_manual_physics_step(
        self, stage: Any, step_seconds: float
    ) -> bool:
        """Advance PhysX while the authored Kit animation timeline stays paused."""

        try:
            import omni.physx  # type: ignore

            physics_interface = omni.physx.get_physx_interface()
            if not self._manual_physics_started:
                start = getattr(physics_interface, "start_simulation", None)
                if callable(start):
                    start()
                self._manual_physics_started = True
            self._manual_physics_time += float(step_seconds)
            get_simulation_interface = getattr(
                omni.physx, "get_physx_simulation_interface", None
            )
            simulation_interface = (
                get_simulation_interface()
                if callable(get_simulation_interface)
                else physics_interface
            )
            self._flush_and_wake_dynamic_releases(
                stage,
                physics_interface,
                simulation_interface,
            )

            # Kit 105-107 exposes a synchronous current-stage path on the main
            # PhysX interface.  Prefer it for an already-open Composer stage;
            # calling IPhysxSimulation.simulate() without explicitly attaching
            # that stage can succeed silently while advancing no bodies.
            update_simulation = getattr(
                physics_interface, "update_simulation", None
            )
            if callable(update_simulation):
                update_simulation(
                    float(step_seconds),
                    self._manual_physics_time,
                )
                update_transformations = getattr(
                    physics_interface, "update_transformations", None
                )
                if callable(update_transformations):
                    update_transformations(True, True, True, False)
                self._capture_physx_dynamic_transforms(physics_interface)
                if not self._manual_physics_start_reported:
                    self._manual_physics_start_reported = True
                    self._log(
                        "Manual PhysX stepping active "
                        f"(backend=update_simulation, dt={step_seconds:.04f}s)."
                    )
                return True

            attach_stage = getattr(simulation_interface, "attach_stage", None)
            if callable(attach_stage) and self._manual_physics_stage_id is None:
                self._manual_physics_stage_id = self._stage_cache_id(stage)
                try:
                    attach_stage(self._manual_physics_stage_id)
                except Exception:
                    # An open Kit stage is commonly already attached.
                    pass
            simulate = getattr(simulation_interface, "simulate", None)
            fetch_results = getattr(simulation_interface, "fetch_results", None)
            if not callable(simulate) or not callable(fetch_results):
                raise RuntimeError(
                    "the Kit PhysX simulation interface has no simulate/fetch_results methods"
                )
            try:
                simulate(float(step_seconds), self._manual_physics_time)
            except TypeError:
                simulate(float(step_seconds))
            fetch_results()
            update_transformations = getattr(
                physics_interface, "update_transformations", None
            )
            if callable(update_transformations):
                try:
                    update_transformations(True, True, True, False)
                except TypeError:
                    pass
            self._capture_physx_dynamic_transforms(physics_interface)
            if not self._manual_physics_start_reported:
                self._manual_physics_start_reported = True
                self._log(
                    "Manual PhysX stepping active "
                    f"(backend=simulate/fetch_results, dt={step_seconds:.04f}s)."
                )
            return True
        except Exception as exc:  # noqa: BLE001 - Kit builds expose different PhysX APIs
            if not self._manual_physics_error_reported:
                self._manual_physics_error_reported = True
                self._log(
                    "Released rigid-body physics could not be advanced while the "
                    f"timeline is paused: {exc}"
                )
            return False

    def _capture_physx_dynamic_transforms(
        self, physics_interface: Any
    ) -> None:
        """Read PhysX poses directly when Fabric output is not written to USD."""

        get_transform = getattr(
            physics_interface, "get_rigidbody_transformation", None
        )
        if not callable(get_transform):
            return
        from pxr import Gf

        for release in self._dynamic_releases:
            source_path = str(release["sourcePath"])
            try:
                result = get_transform(source_path)
            except Exception:
                result = None
            if not result or not bool(result.get("ret_val")):
                misses = int(release.get("physxPoseMisses", 0)) + 1
                release["physxPoseMisses"] = misses
                if misses == 3:
                    force_load = getattr(
                        physics_interface,
                        "force_load_physics_from_usd",
                        None,
                    )
                    if callable(force_load):
                        try:
                            force_load()
                        except Exception:
                            pass
                    release["needsWake"] = True
                    release["wakeAttempts"] = 0
                if misses == 12 and not release.get("registrationFailureReported"):
                    release["registrationFailureReported"] = True
                    self._log(
                        "PhysX did not register the released rigid body "
                        f"(path={source_path})."
                    )
                continue

            position = result.get("position")
            rotation = result.get("rotation")
            if position is None or rotation is None:
                continue
            px, py, pz = self._vector_components(position, 3)
            qx, qy, qz, qw = self._quaternion_xyzw(rotation)
            pose = Gf.Matrix4d(1.0)
            pose.SetRotate(
                Gf.Quatd(
                    qw,
                    Gf.Vec3d(qx, qy, qz),
                )
            )
            pose.SetTranslateOnly(Gf.Vec3d(px, py, pz))
            release["physxPoseWorld"] = pose
            if not release.get("physxRegistrationReported"):
                release["physxRegistrationReported"] = True
                self._log(
                    f"PhysX rigid body registered (path={source_path})."
                )

    @staticmethod
    def _stage_cache_id(stage: Any) -> int:
        from pxr import UsdUtils

        stage_cache = UsdUtils.StageCache.Get()
        stage_id = stage_cache.GetId(stage)
        if not stage_id.IsValid():
            stage_id = stage_cache.Insert(stage)
        return int(stage_id.ToLongInt())

    def _flush_and_wake_dynamic_releases(
        self,
        stage: Any,
        physics_interface: Any,
        simulation_interface: Any,
    ) -> None:
        """Register just-authored bodies and wake re-enabled rigid bodies."""

        pending = [
            release
            for release in self._dynamic_releases
            if release.get("needsWake")
        ]
        if not pending:
            return

        flush_changes = getattr(simulation_interface, "flush_changes", None)
        if callable(flush_changes):
            try:
                flush_changes()
            except Exception:
                pass
        else:
            force_load = getattr(
                physics_interface, "force_load_physics_from_usd", None
            )
            if callable(force_load):
                try:
                    force_load()
                except Exception:
                    pass

        wake_by_path = getattr(physics_interface, "wake_up", None)
        wake_by_id = getattr(simulation_interface, "wake_up", None)
        awakened = 0
        for release in pending:
            source_path = str(release["sourcePath"])
            release["wakeAttempts"] = int(release.get("wakeAttempts", 0)) + 1
            try:
                if callable(wake_by_path):
                    wake_by_path(source_path)
                elif callable(wake_by_id):
                    from pxr import PhysicsSchemaTools, Sdf

                    if self._manual_physics_stage_id is None:
                        self._manual_physics_stage_id = self._stage_cache_id(stage)
                    wake_by_id(
                        self._manual_physics_stage_id,
                        PhysicsSchemaTools.sdfPathToInt(Sdf.Path(source_path)),
                    )
                else:
                    # The authored downward velocity will wake older backends.
                    release["needsWake"] = False
                    continue
                release["needsWake"] = False
                awakened += 1
            except Exception:
                if release["wakeAttempts"] >= 6:
                    release["needsWake"] = False
        if awakened:
            self._log(
                f"Registered and woke {awakened} released rigid body/bodies."
            )

    def _sync_dynamic_release_followers(self, stage: Any) -> None:
        """Apply the dynamic body's world delta to its decorative sibling meshes."""

        from pxr import Gf, Usd, UsdGeom, UsdPhysics

        cache = UsdGeom.XformCache()
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            for release in list(self._dynamic_releases):
                source = stage.GetPrimAtPath(release["sourcePath"])
                if not source:
                    self._dynamic_releases.remove(release)
                    continue
                initial_source_world = release["initialSourceWorld"]
                current_source_world = Gf.Matrix4d(
                    cache.GetLocalToWorldTransform(source)
                )
                current_physics_pose = release.get("physxPoseWorld")
                current_motion_pose = (
                    Gf.Matrix4d(current_physics_pose)
                    if current_physics_pose is not None
                    else self._rigid_pose_matrix(current_source_world)
                )
                current_origin_z = float(
                    current_motion_pose.ExtractTranslation()[2]
                )
                origin_delta_z = (
                    current_origin_z - float(release["initialOriginZ"])
                )
                release_age = time.monotonic() - float(release["releasedAt"])
                if (
                    abs(origin_delta_z) >= 0.01
                    and not release.get("motionReported")
                ):
                    release["motionReported"] = True
                    self._log(
                        "Released rigid body is moving "
                        f"(path={release['sourcePath']}, dz={origin_delta_z:+.03f} m)."
                    )
                elif (
                    float(release.get("expectedFreeDropM", 0.0)) > 0.05
                    and release_age >= 0.40
                    and abs(origin_delta_z) < 0.005
                    and not release.get("secondWakeApplied")
                ):
                    body = UsdPhysics.RigidBodyAPI.Get(stage, source.GetPath())
                    if body:
                        body.CreateVelocityAttr(
                            Gf.Vec3f(
                                0.0,
                                0.0,
                                -max(
                                    RELEASE_INITIAL_DOWNWARD_SPEED_M_S * 4.0,
                                    0.20,
                                ),
                            )
                        )
                    release["needsWake"] = True
                    release["wakeAttempts"] = 0
                    release["secondWakeApplied"] = True
                elif (
                    float(release.get("expectedFreeDropM", 0.0)) > 0.05
                    and release_age >= 1.0
                    and abs(origin_delta_z) < 0.005
                    and not release.get("stuckReported")
                ):
                    release["stuckReported"] = True
                    self._log(
                        "Released rigid body remained stationary after wake-up "
                        f"(path={release['sourcePath']}, "
                        f"expectedFreeDrop={release['expectedFreeDropM']:.02f} m)."
                    )
                world_delta = (
                    release["initialPhysicsPose"].GetInverse()
                    * current_motion_pose
                )
                # PhysX may publish the dynamic pose only to Fabric/fast-cache
                # in some Kit configurations.  The decorative siblings below
                # already follow the queried PhysX pose, so also author that
                # exact pose back onto the visible main cube.  Otherwise the
                # ribs/top panel appear to fall while the container body stays
                # frozen at its pre-release USD transform.
                source_parent = source.GetParent()
                if source_parent:
                    target_source_world = (
                        Gf.Matrix4d(initial_source_world) * world_delta
                    )
                    source_parent_world = Gf.Matrix4d(
                        cache.GetLocalToWorldTransform(source_parent)
                    )
                    target_source_local = (
                        target_source_world * source_parent_world.GetInverse()
                    )
                    self._set_local_transform_matrix(
                        source,
                        target_source_local,
                    )
                for path, initial_world in release["followers"].items():
                    component = stage.GetPrimAtPath(path)
                    if not component or not component.GetParent():
                        continue
                    target_world = Gf.Matrix4d(initial_world) * world_delta
                    parent_world = Gf.Matrix4d(
                        cache.GetLocalToWorldTransform(component.GetParent())
                    )
                    target_local = target_world * parent_world.GetInverse()
                    self._set_local_transform_matrix(component, target_local)

    def _deactivate_dynamic_release_for_pick(
        self, stage: Any, source_path: str
    ) -> None:
        """Stop a previously released body before attaching it to the spreader."""

        matching = [
            release
            for release in getattr(self, "_dynamic_releases", [])
            if release.get("sourcePath") == source_path
        ]
        if not matching:
            return
        try:
            from pxr import Gf, Usd, UsdPhysics

            with Usd.EditContext(stage, stage.GetSessionLayer()):
                source = stage.GetPrimAtPath(source_path)
                if source:
                    body = UsdPhysics.RigidBodyAPI.Get(stage, source.GetPath())
                    if body:
                        body.CreateVelocityAttr(Gf.Vec3f(0.0, 0.0, 0.0))
                        body.CreateAngularVelocityAttr(Gf.Vec3f(0.0, 0.0, 0.0))
                        body.CreateRigidBodyEnabledAttr(False)
                        # Return kinematic control to USD.  After dynamic release
                        # PhysX owns the composed world transform; disabling the
                        # body without making it kinematic leaves the last
                        # simulated position in a PhysX-authored override that
                        # _move_carried_container cannot displace.
                        body.CreateKinematicEnabledAttr(True)
        except Exception:
            pass
        self._dynamic_releases = [
            release
            for release in self._dynamic_releases
            if release.get("sourcePath") != source_path
        ]

    def _capture_carried_attachment(self, stage: Any) -> None:
        """Capture the payload and primary-hoist world origins at lock time."""

        from pxr import Gf, UsdGeom

        cache = UsdGeom.XformCache()
        hoist = stage.GetPrimAtPath(HOIST_PATH)
        if not hoist:
            self._log("Container lock retained, but hoist transform is unavailable for payload carry.")
            return
        self._carried_hoist_origin_world = cache.GetLocalToWorldTransform(hoist).Transform(
            Gf.Vec3d()
        )
        self._last_carried_collision_center_world = None
        self._last_carried_collision_notice_at = 0.0
        self._carried_component_origins_world = {}
        self._carried_component_offsets_x = {}
        calibrated_x = self._carried_box_world_x(self._trolley_m)
        for path in self._carried_component_paths:
            component = stage.GetPrimAtPath(path)
            if component:
                origin_world = cache.GetLocalToWorldTransform(component).Transform(
                    Gf.Vec3d()
                )
                self._carried_component_origins_world[path] = origin_world
                if calibrated_x is not None:
                    self._carried_component_offsets_x[path] = (
                        float(origin_world[0]) - calibrated_x
                    )

    def _move_carried_container(self, stage: Any) -> None:
        if not self._carried_source_path:
            return
        from pxr import Gf, Usd, UsdGeom

        source = stage.GetPrimAtPath(self._carried_source_path)
        if not source:
            self._release_container()
            return
        if self._carried_hoist_origin_world is None or not self._carried_component_origins_world:
            return
        hoist = stage.GetPrimAtPath(HOIST_PATH)
        if not hoist:
            return
        cache = UsdGeom.XformCache()
        hoist_world = cache.GetLocalToWorldTransform(hoist).Transform(Gf.Vec3d())
        world_delta = hoist_world - self._carried_hoist_origin_world
        bbox_cache = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            useExtentsHint=False,
        )
        source_bounds = self._world_container_bounds(bbox_cache, source)
        horizontal_contact = None
        if source_bounds is not None:
            horizontal_contact = self._carried_horizontal_collision_adjustment(
                stage,
                bbox_cache,
                cache,
                source,
                source_bounds,
                world_delta,
            )
        if horizontal_contact is not None:
            correction_x, correction_y, support_path = horizontal_contact
            if self._bay_target is not None:
                self._release_bay_target_for_continuous_travel()
            if abs(correction_x) > 0.0:
                calibrated_x = self._carried_box_world_x(self._trolley_m)
                if calibrated_x is not None:
                    self._trolley_m = self._trolley_m_for_carried_box_world_x(
                        calibrated_x + correction_x
                    )
                else:
                    self._trolley_m += correction_x
            if abs(correction_y) > 0.0:
                self._gantry_m += correction_y
            self._apply_held_pose()
            cache = UsdGeom.XformCache()
            hoist_world = cache.GetLocalToWorldTransform(hoist).Transform(Gf.Vec3d())
            world_delta = hoist_world - self._carried_hoist_origin_world
            now = time.monotonic()
            if (
                now - self._last_carried_collision_notice_at
                > CARRIED_CONTAINER_COLLISION_LOG_INTERVAL_SECONDS
            ):
                self._last_carried_collision_notice_at = now
                self._log(
                    "Carried container collision blocked motion "
                    f"(dx={correction_x:+.03f} m, dy={correction_y:+.03f} m, "
                    f"support={support_path})."
                )
        contact = self._carried_vertical_contact_adjustment(
            stage,
            cache,
            source,
            world_delta,
        )
        if contact is not None:
            penetration, support_path = contact
            if penetration > 0.0:
                self._hoist_m += penetration
                self._apply_held_pose()
                cache = UsdGeom.XformCache()
                hoist_world = cache.GetLocalToWorldTransform(hoist).Transform(Gf.Vec3d())
                world_delta = hoist_world - self._carried_hoist_origin_world
                now = time.monotonic()
                if (
                    now - self._last_vertical_contact_notice_at
                    > CARRIED_CONTAINER_CONTACT_LOG_INTERVAL_SECONDS
                ):
                    self._last_vertical_contact_notice_at = now
                    self._log(
                        "Carried container vertical contact limited hoist "
                        f"(lifted {penetration:.02f} m, support={support_path})."
                    )
        if source_bounds is not None:
            final_bounds = self._carried_proposed_bounds(
                cache,
                source,
                world_delta,
                source_bounds,
            )
            if final_bounds is not None:
                self._last_carried_collision_center_world = self._bounds_center(
                    final_bounds[0],
                    final_bounds[1],
                )
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            for path in self._carried_component_paths:
                component = stage.GetPrimAtPath(path)
                origin_world = self._carried_component_origins_world.get(path)
                if not component or origin_world is None:
                    continue
                parent = component.GetParent()
                if not parent:
                    continue
                parent_world = cache.GetLocalToWorldTransform(parent)
                target_world = origin_world + world_delta
                calibrated_x = self._carried_box_world_x(self._trolley_m)
                offset_x = self._carried_component_offsets_x.get(path)
                if calibrated_x is not None and offset_x is not None:
                    target_world = Gf.Vec3d(
                        calibrated_x + offset_x,
                        float(target_world[1]),
                        float(target_world[2]),
                    )
                target_local = parent_world.GetInverse().Transform(target_world)
                self._set_translate(component, target_local)

    def _carried_proposed_bounds(
        self,
        cache: Any,
        source: Any,
        world_delta: Any,
        source_bounds: tuple[Any, Any],
    ) -> Optional[tuple[tuple[float, float, float], tuple[float, float, float]]]:
        """Return the source container bounds after applying the carried target delta."""

        from pxr import Gf

        source_minimum, source_maximum = source_bounds
        source_origin = cache.GetLocalToWorldTransform(source).Transform(Gf.Vec3d())
        target_origin = self._carried_component_origins_world.get(self._carried_source_path or "")
        if target_origin is None:
            return None
        target_world = target_origin + world_delta
        calibrated_x = self._carried_box_world_x(self._trolley_m)
        offset_x = self._carried_component_offsets_x.get(self._carried_source_path or "")
        if calibrated_x is not None and offset_x is not None:
            target_world = Gf.Vec3d(
                calibrated_x + offset_x,
                float(target_world[1]),
                float(target_world[2]),
            )
        proposed_delta = target_world - source_origin
        return (
            (
                float(source_minimum[0]) + float(proposed_delta[0]),
                float(source_minimum[1]) + float(proposed_delta[1]),
                float(source_minimum[2]) + float(proposed_delta[2]),
            ),
            (
                float(source_maximum[0]) + float(proposed_delta[0]),
                float(source_maximum[1]) + float(proposed_delta[1]),
                float(source_maximum[2]) + float(proposed_delta[2]),
            ),
        )

    def _carried_horizontal_collision_adjustment(
        self,
        stage: Any,
        bbox_cache: Any,
        cache: Any,
        source: Any,
        source_bounds: tuple[Any, Any],
        world_delta: Any,
    ) -> Optional[tuple[float, float, str]]:
        """Return a horizontal correction that keeps a carried box out of other boxes."""

        proposed_bounds = self._carried_proposed_bounds(
            cache,
            source,
            world_delta,
            source_bounds,
        )
        if proposed_bounds is None:
            self._last_carried_collision_center_world = None
            return None
        proposed_minimum, proposed_maximum = proposed_bounds
        proposed_center = self._bounds_center(proposed_minimum, proposed_maximum)
        previous_center = self._last_carried_collision_center_world
        if previous_center is None:
            return None
        motion_xy = (
            float(proposed_center[0]) - float(previous_center[0]),
            float(proposed_center[1]) - float(previous_center[1]),
        )
        if math.hypot(*motion_xy) < SPREADER_COLLISION_MIN_HORIZONTAL_MOTION_M:
            return None

        carried_paths = set(self._carried_component_paths or [])
        carried_root = self._carried_source_path or ""
        best: Optional[tuple[float, float, str]] = None
        best_distance = 0.0
        for root_path in self._container_contact_root_path_cache(stage):
            if root_path == carried_root or root_path in carried_paths:
                continue
            root = stage.GetPrimAtPath(root_path)
            if not root:
                continue
            bounds = self._world_container_bounds(bbox_cache, root)
            if bounds is None:
                continue
            correction_x, correction_y = self._carried_collision_separation_delta(
                proposed_minimum,
                proposed_maximum,
                bounds[0],
                bounds[1],
                motion_xy,
            )
            distance = math.hypot(correction_x, correction_y)
            if distance > best_distance:
                best = (correction_x, correction_y, root_path)
                best_distance = distance
        return best

    def _carried_vertical_contact_adjustment(
        self,
        stage: Any,
        cache: Any,
        source: Any,
        world_delta: Any,
    ) -> Optional[tuple[float, str]]:
        """Return the upward correction needed to keep a carried box on top of supports."""

        from pxr import Gf, Usd, UsdGeom

        bbox_cache = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            useExtentsHint=False,
        )
        source_bounds = self._world_container_bounds(bbox_cache, source)
        if source_bounds is None:
            return None
        proposed_bounds = self._carried_proposed_bounds(
            cache,
            source,
            world_delta,
            source_bounds,
        )
        if proposed_bounds is None:
            return None
        proposed_minimum, proposed_maximum = proposed_bounds
        current_field, current_bay = self._nearest_bay(stage)
        current_bay_id = (
            f"{current_field}/{current_bay:03d}"
            if current_field is not None and current_bay is not None
            else None
        )
        current_row = self._nearest_row_for_trolley_m(self._trolley_m)
        support_bounds = self._support_bounds_for_carried_contact(
            stage,
            bbox_cache,
            current_bay_id=current_bay_id,
            current_row=current_row,
        )
        return self._vertical_contact_penetration(
            proposed_minimum,
            proposed_maximum,
            support_bounds,
        )

    def _support_bounds_for_carried_contact(
        self,
        stage: Any,
        bbox_cache: Any,
        *,
        current_bay_id: Optional[str],
        current_row: Optional[int],
    ) -> list[tuple[str, Any, Any]]:
        carried_paths = set(self._carried_component_paths or [])
        carried_root = self._carried_source_path or ""
        support_bounds = []
        for root_path in self._container_contact_root_path_cache(stage):
            if root_path == carried_root or root_path in carried_paths:
                continue
            root = stage.GetPrimAtPath(root_path)
            if not root:
                continue
            bounds = self._world_container_bounds(bbox_cache, root)
            if bounds is None:
                continue
            support_bounds.append((root_path, bounds[0], bounds[1]))
        return support_bounds

    @staticmethod
    def _container_components(source: Any) -> list[Any]:
        parent = source.GetParent()
        if not parent:
            return [source]
        prefix = f"{source.GetName()}_"
        return [source] + [
            prim for prim in parent.GetChildren() if prim.GetName().startswith(prefix)
        ]

    @staticmethod
    def _get_translate(prim: Any) -> Any:
        attr = prim.GetAttribute("xformOp:translate") if prim else None
        if attr:
            return attr.Get()
        matrix_attr = prim.GetAttribute("xformOp:transform") if prim else None
        if matrix_attr and matrix_attr.Get() is not None:
            return matrix_attr.Get().ExtractTranslation()
        return None

    @staticmethod
    def _set_translate(prim: Any, value: Any) -> None:
        attr = prim.GetAttribute("xformOp:translate")
        if attr:
            attr.Set(value)
            return
        matrix_attr = prim.GetAttribute("xformOp:transform")
        matrix = matrix_attr.Get() if matrix_attr else None
        if matrix_attr and matrix is not None:
            matrix.SetTranslateOnly(value)
            matrix_attr.Set(matrix)

    @staticmethod
    def _set_local_transform_matrix(prim: Any, matrix: Any) -> None:
        """Replace local xform ops with one matrix for rigid-body visual followers."""

        from pxr import Gf, UsdGeom

        xform = UsdGeom.Xformable(prim)
        matrix_attr = prim.GetAttribute("xformOp:transform")
        if matrix_attr:
            op = UsdGeom.XformOp(matrix_attr)
            xform.SetXformOpOrder([op])
        else:
            xform.ClearXformOpOrder()
            op = xform.AddTransformOp(UsdGeom.XformOp.PrecisionDouble)
        op.Set(Gf.Matrix4d(matrix))

    @staticmethod
    def _rigid_pose_matrix(world_matrix: Any) -> Any:
        """Return translation/rotation without the visual cube's non-uniform scale."""

        from pxr import Gf

        transform = Gf.Transform(Gf.Matrix4d(world_matrix))
        pose = Gf.Matrix4d(1.0)
        pose.SetRotate(transform.GetRotation().GetQuat())
        pose.SetTranslateOnly(transform.GetTranslation())
        return pose

    @staticmethod
    def _vector_components(value: Any, count: int) -> tuple[float, ...]:
        names = ("x", "y", "z", "w")
        components = []
        for index in range(count):
            try:
                component = value[index]
            except (TypeError, IndexError, KeyError):
                component = getattr(value, names[index])
            components.append(float(component))
        return tuple(components)

    @staticmethod
    def _quaternion_xyzw(value: Any) -> tuple[float, float, float, float]:
        components = RtgGamepadManualController._vector_components(value, 4)
        return (
            float(components[0]),
            float(components[1]),
            float(components[2]),
            float(components[3]),
        )

    @staticmethod
    def _set_rotate_xyz_degrees(prim: Any, degrees: tuple[float, float, float]) -> None:
        from pxr import Gf, UsdGeom

        xform = UsdGeom.Xformable(prim)
        ordered_ops = list(xform.GetOrderedXformOps())
        ops_by_name = {op.GetOpName(): op for op in ordered_ops}
        translate_op = ops_by_name.get("xformOp:translate")
        scale_op = ops_by_name.get("xformOp:scale")
        rotate_op = ops_by_name.get("xformOp:rotateXYZ")
        if rotate_op is None:
            rotate_op = xform.AddRotateXYZOp()
        xform_order = []
        if translate_op is not None:
            xform_order.append(translate_op)
        xform_order.append(rotate_op)
        if scale_op is not None:
            xform_order.append(scale_op)
        preserved_names = {op.GetOpName() for op in xform_order}
        xform_order.extend(
            op for op in ordered_ops if op.GetOpName() not in preserved_names
        )
        try:
            xform.SetXformOpOrder(xform_order)
        except Exception:
            pass
        rotate_op.GetAttr().Set(Gf.Vec3f(*degrees))

    @staticmethod
    def _get_rotate_xyz_degrees(prim: Any) -> tuple[float, float, float]:
        attr = prim.GetAttribute("xformOp:rotateXYZ") if prim else None
        value = attr.Get() if attr else None
        if value is None:
            return 0.0, 0.0, 0.0
        try:
            return float(value[0]), float(value[1]), float(value[2])
        except (TypeError, ValueError, IndexError):
            return 0.0, 0.0, 0.0

    @staticmethod
    def _get_scale(prim: Any) -> Any:
        attr = prim.GetAttribute("xformOp:scale") if prim else None
        return attr.Get() if attr else None

    @staticmethod
    def _bounds_center(minimum: Any, maximum: Any) -> tuple[float, float, float]:
        return (
            (float(minimum[0]) + float(maximum[0])) * 0.5,
            (float(minimum[1]) + float(maximum[1])) * 0.5,
            (float(minimum[2]) + float(maximum[2])) * 0.5,
        )

    @staticmethod
    def _point_tuple(point: Any) -> tuple[float, float, float]:
        return (float(point[0]), float(point[1]), float(point[2]))

    @staticmethod
    def _lerp_tuple(
        start: Any,
        end: Any,
        ratio: float,
    ) -> tuple[float, float, float]:
        clamped = max(0.0, min(1.0, float(ratio)))
        return (
            float(start[0]) + (float(end[0]) - float(start[0])) * clamped,
            float(start[1]) + (float(end[1]) - float(start[1])) * clamped,
            float(start[2]) + (float(end[2]) - float(start[2])) * clamped,
        )

    @staticmethod
    def _ease_out_cubic(ratio: float) -> float:
        clamped = max(0.0, min(1.0, float(ratio)))
        return 1.0 - (1.0 - clamped) ** 3

    @staticmethod
    def _bounds_corners(minimum: Any, maximum: Any) -> list[tuple[float, float, float]]:
        return [
            (float(x), float(y), float(z))
            for x in (minimum[0], maximum[0])
            for y in (minimum[1], maximum[1])
            for z in (minimum[2], maximum[2])
        ]

    @staticmethod
    def _bounds_from_points(
        points: list[tuple[float, float, float]],
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        return (
            (
                min(point[0] for point in points),
                min(point[1] for point in points),
                min(point[2] for point in points),
            ),
            (
                max(point[0] for point in points),
                max(point[1] for point in points),
                max(point[2] for point in points),
            ),
        )

    @staticmethod
    def _rotate_point_around_axis(
        point: Any,
        pivot: Any,
        axis: str,
        angle_radians: float,
    ) -> tuple[float, float, float]:
        px = float(point[0]) - float(pivot[0])
        py = float(point[1]) - float(pivot[1])
        pz = float(point[2]) - float(pivot[2])
        cosine = math.cos(angle_radians)
        sine = math.sin(angle_radians)
        if axis == "X":
            rotated = (
                px,
                py * cosine - pz * sine,
                py * sine + pz * cosine,
            )
        elif axis == "Y":
            rotated = (
                px * cosine + pz * sine,
                py,
                -px * sine + pz * cosine,
            )
        else:
            raise ValueError(f"Unsupported topple axis: {axis}")
        return (
            rotated[0] + float(pivot[0]),
            rotated[1] + float(pivot[1]),
            rotated[2] + float(pivot[2]),
        )

    @staticmethod
    def _release_topple_plan(
        released_minimum: Any,
        released_maximum: Any,
        support_minimum: Any,
        support_maximum: Any,
    ) -> Optional[tuple[str, int, float]]:
        """Return the support-edge axis to topple over when the centre is unsupported."""

        center = RtgGamepadManualController._bounds_center(
            released_minimum, released_maximum
        )
        margin = RELEASE_CENTER_STABILITY_MARGIN_M
        x_under = float(support_minimum[0]) + margin - center[0]
        x_over = center[0] - (float(support_maximum[0]) - margin)
        y_under = float(support_minimum[1]) + margin - center[1]
        y_over = center[1] - (float(support_maximum[1]) - margin)
        x_excess = x_under if x_under > 0.0 else x_over if x_over > 0.0 else 0.0
        y_excess = y_under if y_under > 0.0 else y_over if y_over > 0.0 else 0.0
        if x_excess <= 0.0 and y_excess <= 0.0:
            return None
        if x_excess >= y_excess:
            fall_sign = -1 if x_under > 0.0 else 1
            pivot_edge = (
                float(support_minimum[0])
                if fall_sign < 0
                else float(support_maximum[0])
            )
            return "Y", fall_sign, pivot_edge
        fall_sign = -1 if y_under > 0.0 else 1
        pivot_edge = (
            float(support_minimum[1])
            if fall_sign < 0
            else float(support_maximum[1])
        )
        return "X", fall_sign, pivot_edge

    @staticmethod
    def _topple_angle_degrees(axis: str, fall_sign: int) -> float:
        sign = 1.0 if int(fall_sign) >= 0 else -1.0
        if axis == "Y":
            return RELEASE_TOPPLE_ANGLE_DEG * sign
        if axis == "X":
            return -RELEASE_TOPPLE_ANGLE_DEG * sign
        raise ValueError(f"Unsupported topple axis: {axis}")

    @staticmethod
    def _release_angular_velocity_components(
        topple_plan: Optional[tuple[str, int, float]],
    ) -> tuple[float, float, float]:
        """Return a small tip impulse; never author a final 90-degree pose."""

        if topple_plan is None:
            return 0.0, 0.0, 0.0
        axis, fall_sign, _pivot_edge = topple_plan
        signed_speed = float(fall_sign) * RELEASE_TOPPLE_ANGULAR_SPEED_DEG_S
        if axis == "Y":
            return (
                RELEASE_TOPPLE_PERTURBATION_DEG_S,
                signed_speed,
                0.0,
            )
        if axis == "X":
            return (
                signed_speed,
                RELEASE_TOPPLE_PERTURBATION_DEG_S,
                0.0,
            )
        raise ValueError(f"Unsupported topple axis: {axis}")

    def _translate_components_by_world_delta(
        self,
        stage: Any,
        component_paths: set[str] | list[str] | tuple[str, ...],
        world_delta: Any,
    ) -> None:
        from pxr import Gf, Usd, UsdGeom

        cache = UsdGeom.XformCache()
        delta = Gf.Vec3d(
            float(world_delta[0]),
            float(world_delta[1]),
            float(world_delta[2]),
        )
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            for path in component_paths:
                component = stage.GetPrimAtPath(path)
                if not component:
                    continue
                parent = component.GetParent()
                if not parent:
                    continue
                origin_world = cache.GetLocalToWorldTransform(component).Transform(
                    Gf.Vec3d()
                )
                target_world = origin_world + delta
                parent_world = cache.GetLocalToWorldTransform(parent)
                self._set_translate(
                    component,
                    parent_world.GetInverse().Transform(target_world),
                )

    def _queue_release_animation(
        self,
        components: list[dict[str, Any]],
        *,
        toppled: bool,
    ) -> None:
        if not hasattr(self, "_pending_release_animations"):
            self._pending_release_animations = []
        self._pending_release_animations.append(
            {
                "startedAt": time.monotonic(),
                "duration": (
                    RELEASE_TOPPLE_ANIMATION_SECONDS
                    if toppled
                    else RELEASE_SETTLE_ANIMATION_SECONDS
                ),
                "toppled": toppled,
                "components": components,
            }
        )

    def _advance_release_animations(self, stage: Any) -> None:
        animations = getattr(self, "_pending_release_animations", [])
        if not animations:
            return
        from pxr import Gf, Usd, UsdGeom

        now = time.monotonic()
        remaining = []
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            for animation in animations:
                duration = max(float(animation.get("duration", 0.001)), 0.001)
                progress = (now - float(animation.get("startedAt", now))) / duration
                complete = progress >= 1.0
                clamped = 1.0 if complete else max(0.0, min(1.0, progress))
                toppled = bool(animation.get("toppled"))
                cache = UsdGeom.XformCache()
                for component_state in animation.get("components", []):
                    component = stage.GetPrimAtPath(component_state["path"])
                    if not component:
                        continue
                    parent = component.GetParent()
                    if not parent:
                        continue
                    if toppled:
                        drop_phase = RELEASE_TOPPLE_DROP_PHASE_RATIO
                        if clamped <= drop_phase:
                            drop_ratio = self._ease_out_cubic(clamped / drop_phase)
                            target_world = self._lerp_tuple(
                                component_state["start"],
                                component_state["drop"],
                                drop_ratio,
                            )
                            rotate = component_state["startRotate"]
                        else:
                            topple_ratio = self._ease_out_cubic(
                                (clamped - drop_phase) / (1.0 - drop_phase)
                            )
                            target_world = self._lerp_tuple(
                                component_state["drop"],
                                component_state["final"],
                                topple_ratio,
                            )
                            rotate = self._lerp_tuple(
                                component_state["startRotate"],
                                component_state["finalRotate"],
                                topple_ratio,
                            )
                    else:
                        settle_ratio = self._ease_out_cubic(clamped)
                        target_world = self._lerp_tuple(
                            component_state["start"],
                            component_state["final"],
                            settle_ratio,
                        )
                        rotate = component_state["finalRotate"]
                    parent_world = cache.GetLocalToWorldTransform(parent)
                    self._set_translate(
                        component,
                        parent_world.GetInverse().Transform(Gf.Vec3d(*target_world)),
                    )
                    self._set_rotate_xyz_degrees(component, rotate)
                if not complete:
                    remaining.append(animation)
        self._pending_release_animations = remaining

    def _spreader_lock_foot_points(self, stage: Any) -> list[Any]:
        """Return the primary RTG's visible lock-foot contact points in world space."""

        from pxr import Gf, Usd, UsdGeom

        lock_foot_token = "RTG_SPREAD_REDRAW_red_corner_lock_foot"
        bbox_cache = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            useExtentsHint=False,
        )
        points = []
        if self._lock_foot_paths is None:
            self._lock_foot_paths = tuple(
                str(prim.GetPath())
                for prim in stage.Traverse()
                # The yard also contains static left/right RTGs with identical
                # Blender mesh names.  Only the lock feet below the primary
                # dynamic RTG hoist belong to this manual controller; averaging
                # all three machines displaced the calculated contact point by
                # several bays even when the central spreader visibly touched a
                # box.
                if (
                    lock_foot_token in prim.GetName()
                    and str(prim.GetPath()).startswith(f"{HOIST_PATH}/")
                )
            )
        for path in self._lock_foot_paths:
            prim = stage.GetPrimAtPath(path)
            if not prim:
                continue
            bounds = bbox_cache.ComputeWorldBound(prim).ComputeAlignedBox()
            minimum = bounds.GetMin()
            maximum = bounds.GetMax()
            if minimum[0] > maximum[0] or minimum[1] > maximum[1]:
                continue
            center = (minimum + maximum) * 0.5
            points.append(Gf.Vec3d(float(center[0]), float(center[1]), float(minimum[2])))
        return points

    def _limit_spreader_vertical_contact(
        self,
        stage: Any,
        *,
        lowering_requested: bool,
    ) -> None:
        """Keep visible lock feet from sinking below nearby live container tops."""

        if not lowering_requested and not self._spreader_vertical_contact_active:
            return
        lock_foot_points = self._spreader_lock_foot_points(stage)
        if not lock_foot_points:
            self._spreader_vertical_contact_active = False
            return
        from pxr import Usd, UsdGeom

        bbox_cache = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            useExtentsHint=False,
        )
        tool_bounds_list = self._spreader_actual_vertical_contact_bound_list(
            stage,
            bbox_cache,
            lock_foot_points,
        )
        tool_bounds = self._union_bounds(tool_bounds_list)
        support_bounds = self._support_bounds_for_spreader_vertical_contact(
            stage,
            bbox_cache,
            tool_bounds=tool_bounds,
        )
        penetration, support_path = self._spreader_vertical_contact_penetration(
            lock_foot_points,
            support_bounds,
        )
        tool_penetration, tool_support_path = (
            self._spreader_tool_bounds_vertical_contact_penetration(
                tool_bounds_list,
                support_bounds,
            )
        )
        if tool_penetration > penetration:
            penetration = tool_penetration
            support_path = tool_support_path
        if penetration <= 0.0:
            self._spreader_vertical_contact_active = False
            return
        self._spreader_vertical_contact_active = True
        self._hoist_m += penetration
        self._apply_held_pose()
        now = time.monotonic()
        if (
            penetration >= 0.01
            and
            now - self._last_spreader_contact_notice_at
            > SPREADER_VERTICAL_CONTACT_LOG_INTERVAL_SECONDS
        ):
            self._last_spreader_contact_notice_at = now
            self._log(
                "Spreader vertical contact limited hoist "
                f"(lifted {penetration:.03f} m, support={support_path})."
            )

    def _support_bounds_for_spreader_vertical_contact(
        self,
        stage: Any,
        bbox_cache: Any,
        *,
        tool_bounds: tuple[Any, Any],
    ) -> list[tuple[str, Any, Any]]:
        carried_paths = set(self._carried_component_paths or [])
        carried_root = self._carried_source_path or ""
        support_bounds = []
        tool_minimum, tool_maximum = tool_bounds
        for root_path in self._container_contact_root_path_cache(stage):
            if root_path == carried_root or root_path in carried_paths:
                continue
            root = stage.GetPrimAtPath(root_path)
            if not root:
                continue
            bounds = self._world_container_bounds(bbox_cache, root)
            if bounds is None:
                continue
            if not self._bounds_overlap_xy(
                tool_minimum,
                tool_maximum,
                bounds[0],
                bounds[1],
            ):
                continue
            support_bounds.append((root_path, bounds[0], bounds[1]))
        return support_bounds

    def _spreader_actual_vertical_contact_bound_list(
        self,
        stage: Any,
        bbox_cache: Any,
        lock_foot_points: list[Any],
    ) -> list[tuple[Any, Any]]:
        """Return real visible spreader mesh AABBs, falling back to lock feet."""

        bounds: list[tuple[Any, Any]] = []
        if self._spreader_geometry_paths is None:
            paths: list[str] = []
            for prim in stage.Traverse():
                try:
                    path = str(prim.GetPath())
                    name = str(prim.GetName())
                except RuntimeError:
                    continue
                if not path.startswith(f"{HOIST_PATH}/"):
                    continue
                if not any(name.startswith(prefix) for prefix in SPREADER_GEOMETRY_NAME_PREFIXES):
                    continue
                paths.append(path)
            self._spreader_geometry_paths = tuple(paths)
        for path in self._spreader_geometry_paths:
            prim = stage.GetPrimAtPath(path)
            if not prim:
                continue
            prim_bounds = bbox_cache.ComputeWorldBound(prim).ComputeAlignedBox()
            minimum = prim_bounds.GetMin()
            maximum = prim_bounds.GetMax()
            if any(float(minimum[index]) > float(maximum[index]) for index in range(3)):
                continue
            bounds.append((minimum, maximum))
        if bounds:
            return bounds
        return [self._spreader_tool_vertical_contact_bounds(lock_foot_points)]

    @staticmethod
    def _spreader_tool_vertical_contact_bounds(
        lock_foot_points: list[Any],
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """Approximate the underside footprint of the spreader body."""

        return (
            (
                min(float(point[0]) for point in lock_foot_points)
                - SPREADER_VERTICAL_CONTACT_EDGE_TOLERANCE_M,
                min(float(point[1]) for point in lock_foot_points)
                - SPREADER_VERTICAL_CONTACT_EDGE_TOLERANCE_M,
                min(float(point[2]) for point in lock_foot_points),
            ),
            (
                max(float(point[0]) for point in lock_foot_points)
                + SPREADER_VERTICAL_CONTACT_EDGE_TOLERANCE_M,
                max(float(point[1]) for point in lock_foot_points)
                + SPREADER_VERTICAL_CONTACT_EDGE_TOLERANCE_M,
                max(float(point[2]) for point in lock_foot_points)
                + SPREADER_COLLISION_TOOL_HEIGHT_M,
            ),
        )

    @staticmethod
    def _bounds_overlap_xy(
        minimum_a: Any,
        maximum_a: Any,
        minimum_b: Any,
        maximum_b: Any,
    ) -> bool:
        return (
            float(minimum_a[0]) <= float(maximum_b[0])
            and float(maximum_a[0]) >= float(minimum_b[0])
            and float(minimum_a[1]) <= float(maximum_b[1])
            and float(maximum_a[1]) >= float(minimum_b[1])
        )

    @staticmethod
    def _spreader_vertical_contact_penetration(
        lock_foot_points: list[Any],
        support_bounds: list[tuple[str, Any, Any]],
    ) -> tuple[float, str]:
        """Return the upward correction needed to keep lock feet above box tops."""

        best_penetration = 0.0
        best_path = "ground"
        for point in lock_foot_points:
            x = float(point[0])
            y = float(point[1])
            z = float(point[2])
            for path, support_minimum, support_maximum in support_bounds:
                min_x = float(support_minimum[0]) - SPREADER_VERTICAL_CONTACT_EDGE_TOLERANCE_M
                max_x = float(support_maximum[0]) + SPREADER_VERTICAL_CONTACT_EDGE_TOLERANCE_M
                min_y = float(support_minimum[1]) - SPREADER_VERTICAL_CONTACT_EDGE_TOLERANCE_M
                max_y = float(support_maximum[1]) + SPREADER_VERTICAL_CONTACT_EDGE_TOLERANCE_M
                if x < min_x or x > max_x or y < min_y or y > max_y:
                    continue
                penetration = (
                    float(support_maximum[2])
                    + SPREADER_VERTICAL_CONTACT_CLEARANCE_M
                    - z
                )
                if penetration > best_penetration:
                    best_penetration = penetration
                    best_path = path
        return max(0.0, best_penetration), best_path

    @staticmethod
    def _spreader_tool_vertical_contact_penetration(
        tool_minimum: Any,
        tool_maximum: Any,
        support_bounds: list[tuple[str, Any, Any]],
    ) -> tuple[float, str]:
        """Return an upward correction for one spreader underside AABB."""

        return RtgGamepadManualController._spreader_tool_bounds_vertical_contact_penetration(
            [(tool_minimum, tool_maximum)],
            support_bounds,
        )

    @staticmethod
    def _spreader_tool_bounds_vertical_contact_penetration(
        tool_bounds: list[tuple[Any, Any]],
        support_bounds: list[tuple[str, Any, Any]],
    ) -> tuple[float, str]:
        """Return an upward correction for independently-tested spreader AABBs."""

        best_penetration = 0.0
        best_path = "ground"
        for tool_minimum, tool_maximum in tool_bounds:
            for path, support_minimum, support_maximum in support_bounds:
                if not RtgGamepadManualController._bounds_overlap_xy(
                    tool_minimum,
                    tool_maximum,
                    support_minimum,
                    support_maximum,
                ):
                    continue
                penetration = (
                    float(support_maximum[2])
                    + SPREADER_VERTICAL_CONTACT_CLEARANCE_M
                    - float(tool_minimum[2])
                )
                if penetration > best_penetration:
                    best_penetration = penetration
                    best_path = path
        return max(0.0, best_penetration), best_path

    @staticmethod
    def _lock_foot_center(lock_foot_points: list[Any]) -> Any:
        """Return a diagnostic centre for a set of visible lock-foot points."""

        from pxr import Gf

        count = float(len(lock_foot_points))
        return Gf.Vec3d(
            sum(float(point[0]) for point in lock_foot_points) / count,
            sum(float(point[1]) for point in lock_foot_points) / count,
            sum(float(point[2]) for point in lock_foot_points) / count,
        )

    @staticmethod
    def _nearest_row_for_trolley_m(trolley_m: float) -> Optional[int]:
        """Resolve the stack row under the physical trolley centre."""

        if not TROLLEY_ROW_CENTERS_M:
            return None
        row = min(
            TROLLEY_ROW_CENTERS_M,
            key=lambda candidate: abs(
                float(trolley_m) - TROLLEY_ROW_CENTERS_M[candidate]
            ),
        )
        distance = abs(float(trolley_m) - TROLLEY_ROW_CENTERS_M[row])
        return row if distance <= TROLLEY_ROW_CAPTURE_TOLERANCE_M else None

    def _trolley_row_delta_m(self, row: int) -> float:
        """Return the signed trolley correction to the centre of ``row``."""

        return TROLLEY_ROW_CENTERS_M[int(row)] - float(self._trolley_m)

    @staticmethod
    def _xy_overlap_area(
        minimum_a: Any,
        maximum_a: Any,
        minimum_b: Any,
        maximum_b: Any,
    ) -> float:
        overlap_x = max(
            0.0,
            min(float(maximum_a[0]), float(maximum_b[0]))
            - max(float(minimum_a[0]), float(minimum_b[0])),
        )
        overlap_y = max(
            0.0,
            min(float(maximum_a[1]), float(maximum_b[1]))
            - max(float(minimum_a[1]), float(minimum_b[1])),
        )
        return overlap_x * overlap_y

    @staticmethod
    def _release_support_from_bounds(
        released_minimum: Any,
        released_maximum: Any,
        support_bounds: list[tuple[str, Any, Any]],
    ) -> tuple[float, str]:
        footprint_area = max(
            (
                float(released_maximum[0]) - float(released_minimum[0])
            )
            * (
                float(released_maximum[1]) - float(released_minimum[1])
            ),
            0.001,
        )
        best_z = RELEASE_GROUND_SUPPORT_Z_M
        best_path = "ground"
        released_top_z = float(released_maximum[2])
        for path, support_minimum, support_maximum in support_bounds:
            support_top_z = float(support_maximum[2])
            if support_top_z > released_top_z + RELEASE_SUPPORT_Z_TOLERANCE_M:
                continue
            overlap_ratio = (
                RtgGamepadManualController._xy_overlap_area(
                    released_minimum,
                    released_maximum,
                    support_minimum,
                    support_maximum,
                )
                / footprint_area
            )
            if overlap_ratio < RELEASE_SUPPORT_MIN_OVERLAP_RATIO:
                continue
            if support_top_z > best_z:
                best_z = support_top_z
                best_path = path
        return best_z, best_path

    @staticmethod
    def _vertical_contact_penetration(
        released_minimum: Any,
        released_maximum: Any,
        support_bounds: list[tuple[str, Any, Any]],
    ) -> tuple[float, str]:
        support_z, support_path = (
            RtgGamepadManualController._release_support_from_bounds(
                released_minimum,
                released_maximum,
                support_bounds,
            )
        )
        penetration = (
            float(support_z)
            + CARRIED_CONTAINER_CONTACT_CLEARANCE_M
            - float(released_minimum[2])
        )
        return max(0.0, penetration), support_path

    @staticmethod
    def _carried_box_world_x(trolley_m: Optional[float]) -> Optional[float]:
        """Map trolley metres to the rendered carried-box world X centre."""

        if trolley_m is None:
            return None
        value = float(trolley_m)
        points = TROLLEY_CARRIED_BOX_X_CALIBRATION
        if value <= points[0][0]:
            return points[0][1]
        for (source_a, target_a), (source_b, target_b) in zip(points, points[1:]):
            if value <= source_b:
                if math.isclose(source_a, source_b):
                    return target_b
                ratio = (value - source_a) / (source_b - source_a)
                return target_a + (target_b - target_a) * ratio
        return points[-1][1]

    @staticmethod
    def _trolley_m_for_carried_box_world_x(world_x: float) -> float:
        """Inverse of ``_carried_box_world_x`` for collision blocking."""

        value = float(world_x)
        points = TROLLEY_CARRIED_BOX_X_CALIBRATION
        for (source_a, target_a), (source_b, target_b) in zip(points, points[1:]):
            if math.isclose(target_a, target_b):
                if math.isclose(value, target_a):
                    return source_b
                continue
            if min(target_a, target_b) <= value <= max(target_a, target_b):
                ratio = (value - target_a) / (target_b - target_a)
                return source_a + (source_b - source_a) * ratio
        if value <= min(point[1] for point in points):
            return points[0][0]
        return points[-1][0]

    @staticmethod
    def _live_container_row(prim: Any) -> Optional[int]:
        row_attr = prim.GetAttribute("live:row")
        if not row_attr:
            return None
        try:
            row = int(row_attr.Get())
        except (TypeError, ValueError):
            return None
        return row if row in TROLLEY_ROW_CENTERS_M else None

    @staticmethod
    def _live_container_root(prim: Any) -> Any:
        """Return the main container prim for a detail prim when possible."""

        if not prim:
            return prim
        bay_attr = prim.GetAttribute("live:bayId")
        if bay_attr:
            return prim
        name = str(prim.GetName())
        marker = "_Container"
        marker_index = name.find(marker)
        if marker_index < 0:
            return prim
        root_name = name[: marker_index + len(marker)]
        if root_name == name:
            return prim
        parent = prim.GetParent()
        if not parent:
            return prim
        try:
            root = parent.GetChild(root_name)
        except Exception:
            return prim
        return root if root else prim

    @staticmethod
    def _is_container_contact_root_name(name: str, *, has_live_bay: bool) -> bool:
        """Return whether a prim name denotes one physical container root."""

        if has_live_bay:
            return True
        return "_Container" in str(name)

    @staticmethod
    def _live_container_location(prim: Any) -> Optional[tuple[str, int, int]]:
        if not prim:
            return None
        root = RtgGamepadManualController._live_container_root(prim)
        bay_attr = root.GetAttribute("live:bayId")
        row_attr = root.GetAttribute("live:row")
        tier_attr = root.GetAttribute("live:tier")
        if not bay_attr or not row_attr or not tier_attr:
            return None
        bay_id = str(bay_attr.Get() or "").strip()
        try:
            row = int(row_attr.Get())
            tier = int(tier_attr.Get())
        except (TypeError, ValueError):
            return None
        if not bay_id or row not in TROLLEY_ROW_CENTERS_M or tier < 1:
            return None
        return bay_id, row, tier

    def _live_container_root_path_cache(self, stage: Any) -> tuple[str, ...]:
        cached = getattr(self, "_live_container_root_paths", None)
        if cached is not None:
            return cached
        seen: set[str] = set()
        paths: list[str] = []
        for prim in stage.Traverse():
            if "Container" not in prim.GetName():
                continue
            root = self._live_container_root(prim)
            if not root or not root.GetAttribute("live:bayId"):
                continue
            path = str(root.GetPath())
            if path in seen:
                continue
            seen.add(path)
            paths.append(path)
        self._live_container_root_paths = tuple(paths)
        return self._live_container_root_paths

    def _container_contact_root_path_cache(self, stage: Any) -> tuple[str, ...]:
        cached = getattr(self, "_container_contact_root_paths", None)
        if cached is not None:
            return cached
        seen: set[str] = set()
        paths: list[str] = []
        for root_path in self._live_container_root_path_cache(stage):
            seen.add(root_path)
            paths.append(root_path)
        for prim in stage.Traverse():
            try:
                name = str(prim.GetName())
                path = str(prim.GetPath())
            except RuntimeError:
                continue
            if "container" not in name.lower():
                continue
            if path.startswith(f"{HOIST_PATH}/"):
                continue
            root = self._live_container_root(prim)
            if not root:
                continue
            root_path = str(root.GetPath())
            if root_path.startswith(f"{HOIST_PATH}/") or root_path in seen:
                continue
            if not self._is_container_contact_root_name(
                str(root.GetName()),
                has_live_bay=bool(root.GetAttribute("live:bayId")),
            ):
                continue
            seen.add(root_path)
            paths.append(root_path)
        self._container_contact_root_paths = tuple(paths)
        return self._container_contact_root_paths

    @staticmethod
    def _parse_bay_id(value: Optional[str]) -> Optional[tuple[str, int]]:
        if not value:
            return None
        text = str(value).strip().upper()
        if "/" not in text:
            return None
        field, bay_text = text.split("/", 1)
        try:
            bay = int(bay_text)
        except ValueError:
            return None
        return field, bay

    @staticmethod
    def _is_nearby_collision_candidate(
        location: Optional[tuple[str, int, int]],
        *,
        current_bay_id: Optional[str],
        current_row: Optional[int],
    ) -> bool:
        if location is None:
            return True
        bay_id, row, _tier = location
        if current_row is not None and abs(int(row) - int(current_row)) > SPREADER_COLLISION_ROW_WINDOW:
            return False
        current_bay = RtgGamepadManualController._parse_bay_id(current_bay_id)
        candidate_bay = RtgGamepadManualController._parse_bay_id(bay_id)
        if current_bay is None or candidate_bay is None:
            return True
        if candidate_bay[0] != current_bay[0]:
            return False
        bay_distance = abs(candidate_bay[1] - current_bay[1])
        return bay_distance <= SPREADER_COLLISION_BAY_WINDOW * 2

    @staticmethod
    def _released_location_from_support(
        *,
        current_bay_id: Optional[str],
        current_row: Optional[int],
        support_location: Optional[tuple[str, int, int]],
    ) -> Optional[tuple[str, int, int]]:
        if support_location is not None:
            bay_id, row, tier = support_location
            return bay_id, row, tier + 1
        if current_bay_id is not None and current_row is not None:
            return current_bay_id, current_row, 1
        return None

    @staticmethod
    def _world_container_bounds(bbox_cache: Any, prim: Any) -> Optional[tuple[Any, Any]]:
        """Return the visible container body's world-space min/max bounds."""

        bounds = bbox_cache.ComputeWorldBound(prim).ComputeAlignedBox()
        minimum = bounds.GetMin()
        maximum = bounds.GetMax()
        if any(float(minimum[index]) > float(maximum[index]) for index in range(3)):
            return None
        return minimum, maximum

    @staticmethod
    def _container_lock_foot_contact(
        lock_foot_points: list[Any],
        minimum: Any,
        maximum: Any,
        *,
        ignore_x: bool = False,
        ignore_y: bool = False,
    ) -> tuple[int, float, float]:
        """Count lock feet seated on a container's visible world-space top face.

        Returns ``(contacted_feet, contact_error, total_gap)``.  When an axis
        is ignored, the caller must already have constrained it through a more
        stable controller centre.  ``total_gap`` remains meaningful even when
        no foot has contact, so failure messages identify the nearest box.
        """

        min_x = float(minimum[0]) - PICK_LOCK_FOOT_EDGE_TOLERANCE_M
        max_x = float(maximum[0]) + PICK_LOCK_FOOT_EDGE_TOLERANCE_M
        min_y = float(minimum[1]) - PICK_LOCK_FOOT_EDGE_TOLERANCE_M
        max_y = float(maximum[1]) + PICK_LOCK_FOOT_EDGE_TOLERANCE_M
        top_z = float(maximum[2])
        contacted: list[float] = []
        total_gap = 0.0
        for point in lock_foot_points:
            x = float(point[0])
            y = float(point[1])
            dx_outside = (
                0.0
                if ignore_x
                else max(min_x - x, 0.0, x - max_x)
            )
            dy_outside = (
                0.0
                if ignore_y
                else max(min_y - y, 0.0, y - max_y)
            )
            dz = abs(float(point[2]) - top_z)
            total_gap += dx_outside + dy_outside + dz
            if (
                dx_outside == 0.0
                and dy_outside == 0.0
                and dz <= PICK_VERTICAL_TOLERANCE_M
            ):
                # Prefer the box whose contacted feet are deepest within its
                # footprint if two box bounds overlap at an edge.
                if ignore_x and ignore_y:
                    contacted.append(dz)
                else:
                    edge_x = 1.0 if ignore_x else min(x - min_x, max_x - x)
                    edge_y = 1.0 if ignore_y else min(y - min_y, max_y - y)
                    contacted.append((1.0 / max(edge_x + edge_y, 0.001)) + dz)
        return len(contacted), sum(contacted), total_gap

    def _spreader_container_center(self, stage: Any) -> Any:
        """Return the average visible lock-foot point for carry diagnostics."""

        points = self._spreader_lock_foot_points(stage)
        return self._lock_foot_center(points) if points else None

    def _ensure_manual_collision_api(self, stage: Any) -> None:
        """Mark the visible lock feet as collision participants for manual mode."""

        try:
            from pxr import Usd, UsdPhysics

            self._spreader_lock_foot_points(stage)
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                for path in self._lock_foot_paths or ():
                    prim = stage.GetPrimAtPath(path)
                    if prim:
                        UsdPhysics.CollisionAPI.Apply(prim)
        except Exception:
            # The deterministic contact response below still works when a Kit
            # build lacks authoring support for the physics schema.
            return

    def _update_spreader_collision_proxy(self, stage: Any) -> None:
        """Apply kinematic rigid bodies to the spreader lock feet for PhysX collision.

        The lock feet already receive CollisionAPI in _ensure_manual_collision_api
        when manual mode first binds the stage.  This method adds RigidBodyAPI
        (kinematic) so that dynamic released containers can detect and respond
        to spreader contact during _advance_dynamic_releases.

        The method is a no-op when no dynamic releases are active or when the
        USD Physics schema is unavailable in the current Kit build.
        """

        if not self._dynamic_releases:
            return
        try:
            from pxr import Usd, UsdPhysics

            with Usd.EditContext(stage, stage.GetSessionLayer()):
                for path in (self._lock_foot_paths or ()):
                    prim = stage.GetPrimAtPath(path)
                    if not prim:
                        continue
                    UsdPhysics.CollisionAPI.Apply(prim)
                    rigid_api = UsdPhysics.RigidBodyAPI.Apply(prim)
                    rigid_api.CreateRigidBodyEnabledAttr(True)
                    rigid_api.CreateKinematicEnabledAttr(True)
        except Exception:
            return

    @staticmethod
    def _expanded_bounds(
        minimum: Any,
        maximum: Any,
        *,
        margin_xy: float,
        margin_z: float,
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        return (
            (
                float(minimum[0]) - margin_xy,
                float(minimum[1]) - margin_xy,
                float(minimum[2]) - margin_z,
            ),
            (
                float(maximum[0]) + margin_xy,
                float(maximum[1]) + margin_xy,
                float(maximum[2]) + margin_z,
            ),
        )

    @staticmethod
    def _union_bounds(
        bounds: list[tuple[Any, Any]],
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        minimum = (
            min(float(bound[0][0]) for bound in bounds),
            min(float(bound[0][1]) for bound in bounds),
            min(float(bound[0][2]) for bound in bounds),
        )
        maximum = (
            max(float(bound[1][0]) for bound in bounds),
            max(float(bound[1][1]) for bound in bounds),
            max(float(bound[1][2]) for bound in bounds),
        )
        return minimum, maximum

    @staticmethod
    def _axis_overlap(
        minimum_a: Any,
        maximum_a: Any,
        minimum_b: Any,
        maximum_b: Any,
        axis_index: int,
    ) -> float:
        return max(
            0.0,
            min(float(maximum_a[axis_index]), float(maximum_b[axis_index]))
            - max(float(minimum_a[axis_index]), float(minimum_b[axis_index])),
        )

    @staticmethod
    def _collision_nudge_delta(
        tool_minimum: Any,
        tool_maximum: Any,
        target_minimum: Any,
        target_maximum: Any,
        motion_xy: tuple[float, float] = (0.0, 0.0),
        *,
        min_nudge_m: float = SPREADER_COLLISION_MIN_NUDGE_M,
        max_nudge_m: float = SPREADER_COLLISION_MAX_NUDGE_M,
        response_ratio: float = SPREADER_COLLISION_RESPONSE_RATIO,
    ) -> tuple[float, float]:
        """Return a small horizontal displacement caused by tool/container contact."""

        overlap_x = RtgGamepadManualController._axis_overlap(
            tool_minimum, tool_maximum, target_minimum, target_maximum, 0
        )
        overlap_y = RtgGamepadManualController._axis_overlap(
            tool_minimum, tool_maximum, target_minimum, target_maximum, 1
        )
        overlap_z = RtgGamepadManualController._axis_overlap(
            tool_minimum, tool_maximum, target_minimum, target_maximum, 2
        )
        if overlap_x <= 0.0 or overlap_y <= 0.0 or overlap_z <= 0.0:
            return 0.0, 0.0

        motion_x, motion_y = motion_xy
        if math.hypot(motion_x, motion_y) < SPREADER_COLLISION_MIN_HORIZONTAL_MOTION_M:
            return 0.0, 0.0
        if abs(motion_x) >= abs(motion_y):
            axis_index = 0
            direction = 1.0 if motion_x >= 0.0 else -1.0
            penetration = overlap_x
        else:
            axis_index = 1
            direction = 1.0 if motion_y >= 0.0 else -1.0
            penetration = overlap_y

        amount = max(
            float(min_nudge_m),
            min(
                float(max_nudge_m),
                penetration * float(response_ratio),
            ),
        )
        if axis_index == 0:
            return direction * amount, 0.0
        return 0.0, direction * amount

    @staticmethod
    def _carried_collision_separation_delta(
        carried_minimum: Any,
        carried_maximum: Any,
        target_minimum: Any,
        target_maximum: Any,
        motion_xy: tuple[float, float],
    ) -> tuple[float, float]:
        """Return a correction for the carried box opposite its horizontal motion."""

        overlap_x = RtgGamepadManualController._axis_overlap(
            carried_minimum, carried_maximum, target_minimum, target_maximum, 0
        )
        overlap_y = RtgGamepadManualController._axis_overlap(
            carried_minimum, carried_maximum, target_minimum, target_maximum, 1
        )
        overlap_z = RtgGamepadManualController._axis_overlap(
            carried_minimum, carried_maximum, target_minimum, target_maximum, 2
        )
        if overlap_x <= 0.0 or overlap_y <= 0.0 or overlap_z <= 0.0:
            return 0.0, 0.0
        motion_x, motion_y = motion_xy
        if math.hypot(motion_x, motion_y) < SPREADER_COLLISION_MIN_HORIZONTAL_MOTION_M:
            return 0.0, 0.0
        if abs(motion_x) >= abs(motion_y):
            return (
                -math.copysign(overlap_x + CARRIED_CONTAINER_COLLISION_CLEARANCE_M, motion_x),
                0.0,
            )
        return (
            0.0,
            -math.copysign(overlap_y + CARRIED_CONTAINER_COLLISION_CLEARANCE_M, motion_y),
        )

    def _manual_tool_collision_bounds(self, stage: Any, bbox_cache: Any) -> Optional[tuple[Any, Any]]:
        """Return an approximate AABB for the spreader plus any carried payload."""

        bounds: list[tuple[Any, Any]] = []
        if self._carried_source_path:
            source = stage.GetPrimAtPath(self._carried_source_path)
            if source:
                source_bounds = self._world_container_bounds(bbox_cache, source)
                if source_bounds is not None:
                    bounds.append(
                        self._expanded_bounds(
                            source_bounds[0],
                            source_bounds[1],
                            margin_xy=SPREADER_COLLISION_HORIZONTAL_MARGIN_M,
                            margin_z=SPREADER_COLLISION_VERTICAL_MARGIN_M,
                        )
                    )
        lock_foot_points = self._spreader_lock_foot_points(stage)
        if lock_foot_points:
            minimum = (
                min(float(point[0]) for point in lock_foot_points),
                min(float(point[1]) for point in lock_foot_points),
                min(float(point[2]) for point in lock_foot_points)
                - SPREADER_COLLISION_VERTICAL_MARGIN_M,
            )
            maximum = (
                max(float(point[0]) for point in lock_foot_points),
                max(float(point[1]) for point in lock_foot_points),
                min(float(point[2]) for point in lock_foot_points)
                + SPREADER_COLLISION_TOOL_HEIGHT_M,
            )
            bounds.append(
                self._expanded_bounds(
                    minimum,
                    maximum,
                    margin_xy=SPREADER_COLLISION_HORIZONTAL_MARGIN_M,
                    margin_z=0.0,
                )
            )
        if not bounds:
            return None
        return self._union_bounds(bounds)

    def _nudge_containers_touched_by_spreader(
        self,
        stage: Any,
        elapsed: float,
        *,
        horizontal_motion_requested: bool,
    ) -> None:
        """Apply a tiny deterministic side push when the spreader/payload overlaps boxes."""

        if not horizontal_motion_requested:
            self._last_collision_probe_center_world = None
            return
        now = time.monotonic()
        if now - self._last_collision_scan_at < SPREADER_COLLISION_SCAN_INTERVAL_SECONDS:
            return
        self._last_collision_scan_at = now
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

        bbox_cache = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            useExtentsHint=False,
        )
        tool_bounds = self._manual_tool_collision_bounds(stage, bbox_cache)
        if tool_bounds is None:
            self._last_collision_probe_center_world = None
            return
        tool_minimum, tool_maximum = tool_bounds
        probe_center = self._bounds_center(tool_minimum, tool_maximum)
        previous_center = self._last_collision_probe_center_world
        self._last_collision_probe_center_world = probe_center
        if previous_center is None:
            return
        motion_xy = (
            float(probe_center[0]) - float(previous_center[0]),
            float(probe_center[1]) - float(previous_center[1]),
        )
        if math.hypot(*motion_xy) < SPREADER_COLLISION_MIN_HORIZONTAL_MOTION_M:
            return

        carried_paths = set(self._carried_component_paths or [])
        carried_root = self._carried_source_path or ""
        carrying_payload = bool(carried_root)
        nudge_min_m = (
            CARRIED_CONTAINER_COLLISION_NUDGE_MIN_M
            if carrying_payload
            else SPREADER_COLLISION_MIN_NUDGE_M
        )
        nudge_max_m = (
            CARRIED_CONTAINER_COLLISION_NUDGE_MAX_M
            if carrying_payload
            else SPREADER_COLLISION_MAX_NUDGE_M
        )
        nudge_response_ratio = (
            CARRIED_CONTAINER_COLLISION_NUDGE_RESPONSE_RATIO
            if carrying_payload
            else SPREADER_COLLISION_RESPONSE_RATIO
        )
        animating_paths = {
            str(component_state.get("path", ""))
            for animation in getattr(self, "_pending_release_animations", [])
            for component_state in animation.get("components", [])
        }
        nudges: list[tuple[str, tuple[float, float]]] = []
        for root_path in self._container_contact_root_path_cache(stage):
            if (
                root_path == carried_root
                or root_path in carried_paths
                or root_path in animating_paths
            ):
                continue
            root = stage.GetPrimAtPath(root_path)
            if not root:
                continue
            bounds = self._world_container_bounds(bbox_cache, root)
            if bounds is None:
                continue
            delta_xy = self._collision_nudge_delta(
                tool_minimum,
                tool_maximum,
                bounds[0],
                bounds[1],
                motion_xy,
                min_nudge_m=nudge_min_m,
                max_nudge_m=nudge_max_m,
                response_ratio=nudge_response_ratio,
            )
            if delta_xy != (0.0, 0.0):
                nudges.append((root_path, delta_xy))

        if not nudges:
            return
        max_shift = 0.0
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            for root_path, delta_xy in nudges:
                root = stage.GetPrimAtPath(root_path)
                if not root:
                    continue
                component_paths = [
                    str(component.GetPath())
                    for component in self._container_components(root)
                ]
                for component_path in component_paths:
                    component = stage.GetPrimAtPath(component_path)
                    if not component:
                        continue
                    parent = component.GetParent()
                    if not parent:
                        continue
                    origin_world = UsdGeom.XformCache().GetLocalToWorldTransform(
                        component
                    ).Transform(Gf.Vec3d())
                    target_world = origin_world + Gf.Vec3d(
                        delta_xy[0], delta_xy[1], 0.0
                    )
                    parent_world = UsdGeom.XformCache().GetLocalToWorldTransform(parent)
                    self._set_translate(
                        component,
                        parent_world.GetInverse().Transform(target_world),
                    )
                    try:
                        UsdPhysics.CollisionAPI.Apply(component)
                    except Exception:
                        pass
                max_shift = max(max_shift, math.hypot(*delta_xy))
                shift_attr = root.GetAttribute(
                    "live:manualCollisionShiftMeters"
                ) or root.CreateAttribute(
                    "live:manualCollisionShiftMeters",
                    Sdf.ValueTypeNames.Double,
                )
                prior_shift = shift_attr.Get() if shift_attr else 0.0
                try:
                    prior_shift = float(prior_shift or 0.0)
                except (TypeError, ValueError):
                    prior_shift = 0.0
                shift_attr.Set(prior_shift + math.hypot(*delta_xy))
        now = time.monotonic()
        if now - self._last_collision_notice_at > 0.75:
            self._last_collision_notice_at = now
            self._log(
                "Manual collision response nudged "
                f"{len(nudges)} container(s), maxShift={max_shift:.02f} m."
            )

    def _write_status(self, stage: Any, *, connected: bool) -> None:
        from pxr import Sdf, Usd, UsdGeom

        field, bay = self._nearest_bay(stage)
        bay_id = f"{field}/{bay:03d}" if field and bay else "--"
        manual_state = {
            "rtgName": "RTG_220",
            # The HUD uses this rather than a task/inventory field.  It is the
            # exact prim resolved from the lock-foot contact on this manual
            # lift, and becomes empty immediately on release.
            "manualPayload": self._carried_source_path or "",
            "pose": {
                "currentField": field,
                "currentBay": bay,
                "bigcarElecDistance": self._gantry_m,
                "trolleyMeter": self._trolley_m,
                "hoistMeter": self._hoist_m,
            },
            "task": {},
        }
        manual_applied = {
            "gantryMeter": self._gantry_m,
            "trolleyMeter": self._trolley_m,
            "hoistMeter": self._hoist_m,
            "spreaderSizeFt": self._rtg_controller.current_spreader_size_ft(),
            "controlMode": "REMOTE_MANUAL",
        }
        builtins.__dict__[REMOTE_MANUAL_STATUS_KEY] = {
            "state": manual_state,
            "applied": manual_applied,
        }
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            prim = UsdGeom.Xform.Define(stage, STATUS_PRIM_PATH).GetPrim()
            values = (
                ("rtg:controlMode", Sdf.ValueTypeNames.String, "REMOTE_MANUAL"),
                ("rtg:manualGamepadConnected", Sdf.ValueTypeNames.Bool, connected),
                ("rtg:manualDeadmanActive", Sdf.ValueTypeNames.Bool, self._deadman_active),
                ("rtg:manualEmergencyStop", Sdf.ValueTypeNames.Bool, self._emergency_stopped),
                ("rtg:manualCameraMode", Sdf.ValueTypeNames.String, self._active_camera_mode),
                ("rtg:spreaderLocked", Sdf.ValueTypeNames.Bool, bool(self._carried_source_path)),
                ("rtg:carriedContainer", Sdf.ValueTypeNames.String, self._carried_source_path or ""),
                ("rtg:currentField", Sdf.ValueTypeNames.String, field or ""),
                ("rtg:currentBay", Sdf.ValueTypeNames.Int, bay or 0),
                ("rtg:currentBayId", Sdf.ValueTypeNames.String, bay_id),
                ("rtg:gantryMeter", Sdf.ValueTypeNames.Double, self._gantry_m),
                ("rtg:trolleyMeter", Sdf.ValueTypeNames.Double, self._trolley_m),
                ("rtg:hoistMeter", Sdf.ValueTypeNames.Double, self._hoist_m),
            )
            for name, type_name, value in values:
                attr = prim.GetAttribute(name) or prim.CreateAttribute(name, type_name)
                attr.Set(value)

    def _nearest_bay(self, stage: Any = None) -> tuple[Optional[str], Optional[int]]:
        """Return the current bay calculated from the physical gantry centre.

        Twenty-foot boxes occupy odd bays while forty-foot boxes occupy even
        bays.  The even number between two short-box bays is the logical
        forty-foot position, so an unrestricted nearest-centre lookup can
        incorrectly report 1C/018 for a 20-foot big-car stop at 1C/019.
        """

        bay_target = getattr(self, "_bay_target", None)
        if bay_target is not None:
            return bay_target[0], bay_target[1]
        display_bay_target = getattr(self, "_display_bay_target", None)
        if display_bay_target is not None:
            return display_bay_target

        from yard_coordinate_mapping import YARD_BLOCKS, bay_center_m

        spreader_size_ft = self._current_spreader_size_ft()
        gantry_m = self._gantry_m
        bay_reference = getattr(self, "_gantry_bay_reference", None)
        if bay_reference is not None:
            reference_block, reference_bay, reference_gantry_m = bay_reference
            gantry_m = (
                bay_center_m(reference_block, reference_bay)
                + self._gantry_m
                - reference_gantry_m
            )
        else:
            gantry_m = self._gantry_bay_center_m(spreader_size_ft)

        nearest: tuple[float, Optional[str], Optional[int]] = (float("inf"), None, None)
        for block in YARD_BLOCKS:
            for bay in range(1, block.bay_count + 1):
                if spreader_size_ft == 20 and bay % 2 == 0:
                    continue
                if spreader_size_ft == 40 and bay % 2 != 0:
                    continue
                distance = abs(gantry_m - bay_center_m(block.name, bay))
                if distance < nearest[0]:
                    nearest = (distance, block.name, bay)
        return nearest[1], nearest[2]

    def _gantry_bay_center_m(self, spreader_size_ft: Optional[int]) -> float:
        """Map the current engineering gantry metre back to a yard bay centre."""

        controller = getattr(self, "_rtg_controller", None)
        if controller is None:
            return float(self._gantry_m)

        center_reader = getattr(
            controller,
            "get_gantry_bay_center_m",
            None,
        )
        if callable(center_reader):
            try:
                return float(center_reader())
            except (RuntimeError, TypeError, ValueError):
                pass

        correction = GANTRY_BAY_CENTERLINE_CORRECTION_Y_M
        fine_40ft_correction = GANTRY_40FT_BAY_CENTERLINE_FINE_OFFSET_Y_M
        offset_20ft_correction = GANTRY_20FT_SPREADER_CENTER_OFFSET_Y_M
        try:
            rtg_live_control = _load_rtg_live_control()
            correction = float(
                getattr(
                    rtg_live_control,
                    "RTG_BAY_CENTERLINE_CORRECTION_Y_M",
                    correction,
                )
            )
            fine_40ft_correction = float(
                getattr(
                    rtg_live_control,
                    "RTG_40FT_BAY_CENTERLINE_FINE_OFFSET_Y_M",
                    fine_40ft_correction,
                )
            )
            offset_20ft_correction = float(
                getattr(
                    rtg_live_control,
                    "SPREADER_20FT_CENTER_OFFSET_Y_M",
                    offset_20ft_correction,
                )
            )
        except Exception:
            pass
        if spreader_size_ft == 40:
            correction += fine_40ft_correction
        elif spreader_size_ft == 20:
            correction += offset_20ft_correction
        return float(self._gantry_m) - correction

    def _current_spreader_size_ft(self) -> Optional[int]:
        """Return a stable 20/40-foot mode for bay-parity selection."""

        controller = getattr(self, "_rtg_controller", None)
        if controller is None:
            return None
        try:
            size = controller.current_spreader_size_ft()
        except Exception:
            return None
        if size is None:
            return None
        return 20 if float(size) < 30.0 else 40

    @staticmethod
    def _current_stage() -> Any:
        try:
            import omni.usd  # type: ignore

            return omni.usd.get_context().get_stage()
        except Exception:
            return None

    @staticmethod
    def _log(message: str) -> None:
        print(f"[RTG Gamepad Manual] {message}")


# Script Editor reloads execute this file again in the same Kit process.  Keep
# hold of the previous controller long enough for start_gamepad_manual_mode()
# to stop its update subscription before replacing it with the new code.
_manual_controller: Optional[RtgGamepadManualController] = getattr(
    builtins, REMOTE_MANUAL_CONTROLLER_KEY, None
)


def start_gamepad_manual_mode(controller_index: int = 0) -> RtgGamepadManualController:
    """Enable remote manual control and make the local gamepad pose-authoritative."""

    global _manual_controller
    stop_gamepad_manual_mode()
    controller = RtgGamepadManualController(controller_index)
    # Do not freeze live PLC control when no compatible gamepad is present.
    try:
        controller.start()
    except Exception:
        builtins.__dict__[REMOTE_MANUAL_MODE_FLAG] = False
        raise
    builtins.__dict__[REMOTE_MANUAL_MODE_FLAG] = True
    builtins.__dict__[REMOTE_MANUAL_CONTROLLER_KEY] = controller
    _manual_controller = controller
    return controller


def stop_gamepad_manual_mode() -> None:
    """Disable remote manual mode and return position authority to live input."""

    global _manual_controller
    controller = _manual_controller or getattr(
        builtins, REMOTE_MANUAL_CONTROLLER_KEY, None
    )
    if controller is not None:
        controller.stop()
        _manual_controller = None
    builtins.__dict__[REMOTE_MANUAL_MODE_FLAG] = False
    builtins.__dict__.pop(REMOTE_MANUAL_CONTROLLER_KEY, None)
    builtins.__dict__.pop(REMOTE_MANUAL_STATUS_KEY, None)


def get_gamepad_manual_status() -> dict[str, Any]:
    """Return a small Script Editor-friendly status snapshot."""

    controller = _manual_controller
    return {
        "active": bool(getattr(builtins, REMOTE_MANUAL_MODE_FLAG, False)),
        "controllerIndex": (
            getattr(controller._gamepad, "_controller_index", None) if controller else None
        ),
        "emergencyStopped": controller._emergency_stopped if controller else None,
        "carriedContainer": controller._carried_source_path if controller else None,
    }


def position_gamepad_manual_for_pick(
    block: str = "3C",
    bay: int = 55,
    row: int = 6,
    tier: int = 2,
) -> dict[str, Any]:
    """Position the active manual controller for a stack grip test.

    With the default simulation inventory, the defaults target the occupied
    20-foot box at 3C/055, row 6, tier 2.
    """

    if _manual_controller is None:
        raise RuntimeError("Start gamepad manual mode before positioning for a grip test.")
    return _manual_controller.position_for_pick(
        block=block,
        bay=bay,
        row=row,
        tier=tier,
        container_size_ft=20,
    )
