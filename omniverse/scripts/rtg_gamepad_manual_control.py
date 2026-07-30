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
HOIST_PATH = (
    "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL/"
    "ANIM_CTRL_RTG_TROLLEY_TRAVEL/ANIM_CTRL_RTG_HOIST_VERTICAL"
)
GANTRY_PATH = "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL"
GLOBAL_CAMERA_PATH = f"{GANTRY_PATH}/REMOTE_MANUAL_GLOBAL_CAMERA"
SPREADER_UNDERSIDE_CAMERA_PATH = f"{HOIST_PATH}/REMOTE_MANUAL_SPREADER_UNDERSIDE_CAMERA"

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
        self._deadman_active = False
        self._emergency_stopped = False
        self._carried_source_path: Optional[str] = None
        self._carried_component_paths: list[str] = []
        self._carried_lock_offset_world: Any = None
        self._carried_component_origins_world: dict[str, Any] = {}
        self._carried_component_offsets_x: dict[str, float] = {}
        self._carried_hoist_origin_world: Any = None
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
        gamepad_connected = self._gamepad is not None and self._gamepad.poll() is not None
        if not gamepad_connected and self._keyboard is None:
            raise RuntimeError(
                "No compatible gamepad or keyboard input is available. "
                "Check the controller connection and Kit input devices."
            )
        if not gamepad_connected:
            cause = self._gamepad_start_error or "controller is disconnected"
            self._log(f"Gamepad unavailable; keyboard-only manual mode enabled: {cause}")
        self._activate_camera(stage, "GLOBAL")
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
        self._restore_kit_gamepad_camera_control()
        self._previous_buttons = 0

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
            # A keyboard operator retains safe local authority while a gamepad
            # is being reconnected.  The zero state ensures no stale stick
            # sample can become motion during that interval.
            state = GamepadState(buttons=0, left_x=0.0, left_y=0.0, right_y=0.0)

        discrete_command = self._handle_buttons(stage, state)
        discrete_command = self._handle_keyboard_buttons(stage, keyboard_state) or discrete_command
        deadman = state.pressed(XINPUT_GAMEPAD_RIGHT_SHOULDER) or keyboard_state.deadman
        self._deadman_active = deadman
        # The locking/key action frame must never also become a motion frame.
        # This prevents a held shoulder+A press from applying a stale analogue
        # sample and making the spreader appear to jump sideways.
        if deadman and not self._emergency_stopped and not discrete_command:
            gantry_input = max(-1.0, min(1.0, state.left_y + keyboard_state.gantry))
            trolley_input = max(-1.0, min(1.0, state.left_x + keyboard_state.trolley))
            hoist_input = max(-1.0, min(1.0, state.right_y + keyboard_state.hoist))
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
        self._advance_pick_alignment(stage)
        # Keep a locked box synchronized on every Kit frame.  This covers the
        # lock frame itself and any pose update that happens between gamepad
        # events, rather than making payload motion depend on another stick
        # event being emitted.
        self._move_carried_container(stage)
        self._write_status(stage, connected=gamepad_connected)
        self._previous_buttons = state.buttons

    def _handle_buttons(self, stage: Any, state: GamepadState) -> bool:
        """Handle one-shot buttons and report whether one consumed this frame."""

        command_triggered = False

        def just_pressed(button: int) -> bool:
            return state.pressed(button) and not bool(self._previous_buttons & button)

        if just_pressed(XINPUT_GAMEPAD_BACK):
            command_triggered = True
            self._emergency_stopped = True
            self._log("Emergency stop active. Press Start to resume manual mode.")
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
                self._release_container()
            else:
                self._pick_container(stage)
        if just_pressed(XINPUT_GAMEPAD_X):
            command_triggered = True
            self._bay_target = None
            self._display_bay_target = None
            self._rtg_controller.set_spreader_size(20)
        if just_pressed(XINPUT_GAMEPAD_B):
            command_triggered = True
            self._bay_target = None
            self._display_bay_target = None
            self._rtg_controller.set_spreader_size(40)
        return command_triggered

    def _handle_keyboard_buttons(self, stage: Any, state: KeyboardState) -> bool:
        """Map keyboard one-shots to the same safe operations as the gamepad."""

        actions = state.just_pressed
        if not actions:
            return False
        command_triggered = False
        if "ESCAPE" in actions:
            self._emergency_stopped = True
            self._log("Emergency stop active. Press Enter to resume manual mode.")
            command_triggered = True
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
                self._release_container()
            else:
                self._pick_container(stage)
            command_triggered = True
        if "ONE" in actions:
            self._bay_target = None
            self._display_bay_target = None
            self._rtg_controller.set_spreader_size(20)
            command_triggered = True
        if "TWO" in actions:
            self._bay_target = None
            self._display_bay_target = None
            self._rtg_controller.set_spreader_size(40)
            command_triggered = True
        return command_triggered

    def _bind_stage(self, stage: Any) -> None:
        if self._stage_id == id(stage) and self._rtg_controller is not None:
            return
        rtg_live_control = _load_rtg_live_control(force_reload=True)

        self._rtg_controller = rtg_live_control.RTGController(stage)
        self._stage_id = id(stage)
        self._lock_foot_paths = None
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
        label = "RTG global" if mode == "GLOBAL" else "spreader underside"
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

        from pxr import Gf, Usd, UsdGeom

        if mode == "GLOBAL":
            path = GLOBAL_CAMERA_PATH
            # Parent to gantry: this operational overview travels with the RTG
            # while still keeping the complete machine and work zone in frame.
            eye = Gf.Vec3d(16.0, -26.0, 17.0)
            target = Gf.Vec3d(0.0, 0.0, 3.0)
            up = Gf.Vec3d(0.0, 0.0, 1.0)
        elif mode == "SPREADER_UNDERSIDE":
            path = SPREADER_UNDERSIDE_CAMERA_PATH
            # Parent to hoist: camera points vertically through the twistlocks
            # to support final box alignment and lock/unlock confirmation.
            eye = Gf.Vec3d(2.55, -5.0, 2.48)
            target = Gf.Vec3d(2.55, -5.0, -4.0)
            up = Gf.Vec3d(0.0, 1.0, 0.0)
        else:
            raise ValueError(f"Unknown manual camera mode: {mode}")

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            camera = UsdGeom.Camera.Define(stage, path)
            camera.CreateProjectionAttr(UsdGeom.Tokens.perspective)
            camera.CreateFocalLengthAttr(18.0 if mode == "GLOBAL" else 12.0)
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

    def _release_container(self) -> None:
        self._log(f"Container released: {self._carried_source_path}")
        self._carried_source_path = None
        self._carried_component_paths = []
        self._carried_lock_offset_world = None
        self._carried_component_origins_world = {}
        self._carried_component_offsets_x = {}
        self._carried_hoist_origin_world = None

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
        return attr.Get() if attr else None

    @staticmethod
    def _set_translate(prim: Any, value: Any) -> None:
        attr = prim.GetAttribute("xformOp:translate")
        if attr:
            attr.Set(value)

    @staticmethod
    def _get_scale(prim: Any) -> Any:
        attr = prim.GetAttribute("xformOp:scale") if prim else None
        return attr.Get() if attr else None

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
