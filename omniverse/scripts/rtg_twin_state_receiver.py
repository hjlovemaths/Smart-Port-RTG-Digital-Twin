"""
Receive RCMS/WPF RTG twin-state snapshots and drive the primary Omniverse RTG.

WPF side:
  - Listens on 127.0.0.1:48700
  - Sends one newline-delimited JSON RtgTwinState per frame

Omniverse side:
  - This script connects as a TCP client.
  - The networking thread only parses JSON and stores the latest state.
  - The Omniverse update callback applies the latest state on the Kit main
    thread by reusing ``rtg_live_control.RTGController``.

Typical use in Omniverse Script Editor:

    exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_twin_state_receiver.py", encoding="utf-8").read())

Stop later with:

    stop_live_driver()
"""

from __future__ import annotations

import json
import math
import re
import socket
import sys
import threading
import time
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional


_previous_stop_live_driver = globals().get("stop_live_driver")
if callable(_previous_stop_live_driver):
    try:
        _previous_stop_live_driver()
    except Exception as exc:
        print(f"[RTG Twin Driver] previous driver stop failed: {exc}")


def _resolve_script_dir() -> Path:
    """Find project scripts when Kit evaluates this file from a temp buffer."""

    candidates = (
        Path(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts"),
        Path.cwd() / "omniverse" / "scripts",
        Path(__file__).resolve().parent,
    )
    for candidate in candidates:
        if (candidate / "rtg_task_state_machine.py").is_file():
            return candidate
    return Path(__file__).resolve().parent


SCRIPT_DIR = _resolve_script_dir()
if str(SCRIPT_DIR) in sys.path:
    sys.path.remove(str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR))

# Kit's Script Editor executes this receiver from a generated temp file.  Load
# the sibling state-machine source explicitly instead of relying on its module
# search path, which can omit the project directory in embedded Python.
_state_machine_module = types.ModuleType("rtg_task_state_machine")
_state_machine_module.__file__ = str(SCRIPT_DIR / "rtg_task_state_machine.py")
sys.modules["rtg_task_state_machine"] = _state_machine_module
exec(
    compile(
        (SCRIPT_DIR / "rtg_task_state_machine.py").read_text(encoding="utf-8"),
        _state_machine_module.__file__,
        "exec",
    ),
    _state_machine_module.__dict__,
)
OperationState = _state_machine_module.OperationState
OperationTask = _state_machine_module.OperationTask
PlcOperationSnapshot = _state_machine_module.PlcOperationSnapshot
RtgOperationStateMachine = _state_machine_module.RtgOperationStateMachine
TaskSlot = _state_machine_module.TaskSlot

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 48700
STATUS_PRIM_PATH = "/World/LiveTwinState"
LIVE_TASK_STACK_PATH = "/World/LiveContainers/LiveTaskStack"
LIVE_TASK_TRUCK_PATH = "/World/LiveContainers/LiveTaskTruck"
LEGACY_LIVE_HOISTED_LOAD_PATH = (
    "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL/"
    "ANIM_CTRL_RTG_TROLLEY_TRAVEL/ANIM_CTRL_RTG_HOIST_VERTICAL/"
    "LIVE_TwinHoistedContainer"
)
LIVE_HOISTED_LOAD_PATH = "/World/LiveContainers/LiveTwinHoistedContainer"

TASK_STATE_IDLE = "IDLE"
TASK_STATE_GANTRY_TO_SOURCE = "GANTRY_TO_SOURCE"
TASK_STATE_TRUCK_APPROACH = "TRUCK_APPROACH"
TASK_STATE_TRUCK_WAITING = "TRUCK_WAITING"
TASK_STATE_READY_TO_PICK = "READY_TO_PICK"
TASK_STATE_CARRYING = "CARRYING"
TASK_STATE_PLACED = "PLACED"
TASK_STATE_TRUCK_EXITING = "TRUCK_EXITING"

WORK_TYPE_LOAD = "LOAD"
WORK_TYPE_UNLOAD = "UNLOAD"
WORK_TYPE_SWITCH = "SWITCH"

TRUCK_APPROACH_SECONDS = 5.0
TRUCK_SETTLE_SECONDS = 1.5
TRUCK_EXIT_SECONDS = 4.0
# Match the visually tuned truck lane used by apply_task_sequence_demo.py.  The
# geometric lane centre is too far outside the RTG leg from the normal operator
# camera; -4.65 m keeps the truck inside the lane but closer to the stack.
TRUCK_OPERATING_LANE_CENTER_X_M = -4.65
# At the truck trolley stop, the spreader world centre is -4.65 m. Keep the
# trailer root on that same centreline instead of adding a camera-view offset.
TASK_TRUCK_VISUAL_X_OFFSET_M = 0.0
# The truck root is the trailer/container centre, while the cab protrudes in
# the travel direction.  Stopping exactly on the bay centre makes the truck look
# like it has driven a little past the work position, so hold it back slightly.
TASK_TRUCK_STOP_Y_OFFSET_M = 2.0
# Local attachment point on ANIM_CTRL_RTG_HOIST_VERTICAL. The carried box is
# authored at world scope so it keeps the source box dimensions instead of
# inheriting RTG_PRIMARY_DYNAMIC's non-uniform X scale.
HOISTED_LOAD_LOCAL_CENTER_X = 2.55
HOISTED_LOAD_LOCAL_CENTER_Y = -5.00
SPREADER_CONTAINER_CONTACT_LOCAL_Z = 2.55
STATUS_OVERLAY_UPDATE_SECONDS = 0.2


def _hoisted_container_local_center_z(container_height: float) -> float:
    return SPREADER_CONTAINER_CONTACT_LOCAL_Z - float(container_height) * 0.5


@dataclass
class RtgTwinReceiverConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    reconnect_seconds: float = 2.0
    log_every_seconds: float = 1.0


def _as_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _field_name(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


def _fmt_number(value: Any, digits: int = 2, suffix: str = "") -> str:
    number = _as_float(value)
    if number is None:
        return "--"
    return f"{number:.{digits}f}{suffix}"


def _fmt_text(value: Any, default: str = "--") -> str:
    text = None if value is None else str(value).strip()
    return text if text else default


def _fmt_int(value: Any, default: str = "--") -> str:
    if value is None:
        return default
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return default


def _work_type_text(value: Any) -> str:
    text = _fmt_text(value, "").upper()
    if text == WORK_TYPE_LOAD:
        return WORK_TYPE_LOAD
    if text == WORK_TYPE_UNLOAD:
        return WORK_TYPE_UNLOAD
    if text in (WORK_TYPE_SWITCH, "REHANDLE"):
        return WORK_TYPE_SWITCH
    return text or "--"


def _task_work_type(task: Dict[str, Any]) -> str:
    """Return the normalized RCMS work type used by the live task flow."""

    value = str(task.get("workType") or task.get("taskType") or "").strip().upper()
    if value == "REHANDLE":
        return WORK_TYPE_SWITCH
    return value


def _truck_box_visible(work_type: str, flow_state: str) -> bool:
    """Return whether the task truck should currently carry a visible box."""

    if work_type == WORK_TYPE_LOAD:
        # Inbound/LOAD arrives with a box and leaves empty.
        return flow_state in (
            TASK_STATE_TRUCK_APPROACH,
            TASK_STATE_TRUCK_WAITING,
            TASK_STATE_READY_TO_PICK,
        )
    if work_type == WORK_TYPE_UNLOAD:
        # Outbound/UNLOAD arrives empty and leaves after the box is released.
        return flow_state in (TASK_STATE_PLACED, TASK_STATE_TRUCK_EXITING)
    return False


def _outbound_source_visible(work_type: str, flow_state: str) -> bool:
    """Return whether the selected yard source box remains in its stack."""

    if work_type not in (WORK_TYPE_UNLOAD, WORK_TYPE_SWITCH):
        return True
    return flow_state in (
        TASK_STATE_TRUCK_APPROACH,
        TASK_STATE_TRUCK_WAITING,
        TASK_STATE_READY_TO_PICK,
    )


def _task_state_text(task: Dict[str, Any], has_task: bool) -> str:
    """Return an ASCII-only task state for the Omniverse HUD.

    Kit's default UI font often cannot render Chinese in this app build, so the
    overlay deliberately uses English display text while preserving the original
    task payload unchanged.
    """

    if not has_task:
        return "IDLE"

    raw = (
        task.get("state")
        or task.get("taskState")
        or task.get("status")
        or task.get("taskStatus")
        or task.get("workState")
    )
    text = _fmt_text(raw, "").strip()
    normalized = text.upper()
    if normalized in {
        "ACTIVE",
        "RUNNING",
        "WORKING",
        "IN_PROGRESS",
        "TASKING",
        "LOAD",
        "UNLOAD",
        "SWITCH",
        "REHANDLE",
    }:
        return "ACTIVE"
    if normalized in {"IDLE", "NONE", "NO_TASK", "EMPTY", "WAITING"}:
        return "IDLE"
    if normalized in {"DONE", "FINISHED", "COMPLETE", "COMPLETED"}:
        return "DONE"
    if normalized in {"ERROR", "FAULT", "FAILED"}:
        return "ERROR"

    # Common Chinese state words from RCMS/task messages.  Keep the HUD ASCII.
    if any(word in text for word in ("任务中", "作业中", "执行中", "进行中")):
        return "ACTIVE"
    if any(word in text for word in ("空闲", "无任务", "待命", "等待")):
        return "IDLE"
    if any(word in text for word in ("完成", "结束")):
        return "DONE"
    if any(word in text for word in ("失败", "错误", "故障", "异常")):
        return "ERROR"
    return "ACTIVE"


class RtgTwinStatusOverlay:
    """Small omni.ui HUD for current PLC pose and task information."""

    def __init__(self) -> None:
        self._window = None
        self._labels: Dict[str, Any] = {}
        self._last_values: Dict[str, str] = {}
        self._build()

    def update(self, state: Dict[str, Any], applied: Dict[str, Any]) -> None:
        pose = state.get("pose") or {}
        task = state.get("task") or {}
        has_task = bool(task)

        gantry_m = pose.get("bigcarElecDistance")
        if gantry_m is None:
            gantry_m = applied.get("gantryMeter")

        values = {
            "device_id": _fmt_text(state.get("rtgName")),
            "field": _fmt_text(pose.get("currentField")),
            "bay": _fmt_int(pose.get("currentBay")),
            "gantry": _fmt_number(gantry_m, 2, " m"),
            "trolley": _fmt_number(applied.get("trolleyMeter") or pose.get("trolleyMeter"), 2, " m"),
            "hoist": _fmt_number(applied.get("hoistMeter") or pose.get("hoistMeter"), 2, " m"),
            "task_state": _task_state_text(task, has_task),
            "task_type": _fmt_text(task.get("taskType") if has_task else None),
            "task_id": _fmt_text(task.get("taskId") if has_task else None),
            "start_pos": _fmt_text(task.get("startPos") if has_task else None),
            "end_pos": _fmt_text(task.get("endPos") if has_task else None),
            "work_type": _work_type_text(task.get("workType") if has_task else None),
            "container_no": _fmt_text(task.get("containerNo") if has_task else None),
            "container_type": _fmt_text(task.get("containerType") if has_task else None),
            "container_height": _fmt_text(task.get("containerHeight") if has_task else None),
            "spreader_pos": _fmt_text(task.get("spreaderPos") if has_task else None),
            "car_type": _fmt_text(task.get("carType") if has_task else None),
            "car_no": _fmt_text(task.get("carNo") if has_task else None),
            "container_size": _fmt_text(task.get("containerSize") if has_task else None),
            "bay_map": _fmt_text(task.get("bayMap") if has_task else None),
        }
        for key, value in values.items():
            if self._last_values.get(key) == value:
                continue
            self._last_values[key] = value
            self._set_label_text(key, value)

    def destroy(self) -> None:
        if self._window is not None:
            try:
                self._window.visible = False
            except Exception:
                pass
            self._window = None
        self._labels.clear()

    def _build(self) -> None:
        import omni.ui as ui  # type: ignore

        flags = 0
        for name in (
            "WINDOW_FLAGS_NO_TITLE_BAR",
            "WINDOW_FLAGS_NO_RESIZE",
            "WINDOW_FLAGS_NO_SCROLLBAR",
            "WINDOW_FLAGS_NO_COLLAPSE",
        ):
            flags |= getattr(ui, name, 0)

        self._window = ui.Window(
            "RTG Twin HUD",
            width=270,
            height=540,
            visible=True,
            flags=flags,
        )
        for attr, value in (("position_x", 12), ("position_y", 82)):
            try:
                setattr(self._window, attr, value)
            except Exception:
                pass

        panel_style = {
            "background_color": 0xE6061827,
            "border_color": 0xFF195D83,
            "border_width": 1,
            "border_radius": 4,
        }
        title_style = {"color": 0xFFFFFFFF, "font_size": 14}
        name_style = {"color": 0xFF9AAABA, "font_size": 12}
        value_style = {"color": 0xFFFFFFFF, "font_size": 13}
        accent_style = {"color": 0xFF22DFFF, "font_size": 18}

        with self._window.frame:
            with ui.VStack(spacing=6, style={"background_color": 0xAA02111F}):
                with ui.Frame(style=panel_style, height=205):
                    with ui.VStack(spacing=5):
                        ui.Spacer(height=6)
                        ui.Label("DEVICE", style=title_style, height=20)
                        self._row(ui, "Device", "device_id", name_style, accent_style, height=28)
                        self._row(ui, "Field", "field", name_style, value_style)
                        self._row(ui, "Bay", "bay", name_style, value_style)
                        self._row(ui, "Gantry", "gantry", name_style, value_style)
                        self._row(ui, "Trolley", "trolley", name_style, value_style)
                        self._row(ui, "Hoist", "hoist", name_style, value_style)
                        ui.Spacer(height=2)

                with ui.Frame(style=panel_style, height=320):
                    with ui.VStack(spacing=5):
                        ui.Spacer(height=6)
                        ui.Label("TASK", style=title_style, height=20)
                        for label, key in (
                            ("State", "task_state"),
                            ("Task ID", "task_id"),
                            ("Start Pos", "start_pos"),
                            ("End Pos", "end_pos"),
                            ("Work Type", "work_type"),
                            ("Cntr No", "container_no"),
                            ("Cntr Type", "container_type"),
                            ("Cntr Height", "container_height"),
                            ("Spreader", "spreader_pos"),
                            ("Car Type", "car_type"),
                            ("Car No", "car_no"),
                            ("Cntr Size", "container_size"),
                            ("Bay Map", "bay_map"),
                        ):
                            self._row(ui, label, key, name_style, value_style)
                        ui.Spacer(height=2)

    def _row(self, ui: Any, name: str, key: str, name_style: Dict[str, Any], value_style: Dict[str, Any], height: int = 20) -> None:
        with ui.HStack(height=height):
            ui.Label(name, width=88, style=name_style)
            label = ui.Label("--", alignment=ui.Alignment.RIGHT, style=value_style)
            self._labels[key] = label

    def _set_label_text(self, key: str, value: str) -> None:
        label = self._labels.get(key)
        if label is None:
            return
        try:
            label.text = value
        except Exception:
            try:
                label.model.set_value(value)
            except Exception:
                pass


class RtgTwinStateReceiver:
    """Background TCP client for newline-delimited JSON twin-state frames."""

    def __init__(
        self,
        config: Optional[RtgTwinReceiverConfig] = None,
        on_state: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.config = config or RtgTwinReceiverConfig()
        self.on_state = on_state
        self.latest_state: Optional[Dict[str, Any]] = None
        self.latest_state_at: float = 0.0
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_log_at: float = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="RtgTwinStateReceiver", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._connect_and_read()
            except Exception as exc:  # noqa: BLE001 - useful in Kit console
                self._log(f"connection error: {exc}")
            time.sleep(self.config.reconnect_seconds)

    def _connect_and_read(self) -> None:
        self._log(f"connecting to {self.config.host}:{self.config.port}")
        with socket.create_connection(
            (self.config.host, self.config.port), timeout=5.0
        ) as sock:
            self._log("connected")
            sock.settimeout(1.0)
            buffer = b""
            while not self._stop_event.is_set():
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    self._log("server closed connection")
                    return
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    self._handle_line(line)

    def _handle_line(self, line: bytes) -> None:
        if not line.strip():
            return
        state = json.loads(line.decode("utf-8"))
        self.latest_state = state
        self.latest_state_at = time.time()
        if self.on_state:
            self.on_state(state)
        self._log_state_summary(state)

    def _log_state_summary(self, state: Dict[str, Any]) -> None:
        now = time.time()
        if now - self._last_log_at < self.config.log_every_seconds:
            return
        self._last_log_at = now
        pose = state.get("pose") or {}
        task = state.get("task") or {}
        self._log(
            "state "
            f"rtg={state.get('rtgName')} "
            f"field={pose.get('currentField')} bay={pose.get('currentBay')} "
            f"trolley={pose.get('trolleyMeter')} hoist={pose.get('hoistMeter')} "
            f"lock={pose.get('lockState')} "
            f"task={task.get('taskId')} {task.get('workType')} bayMap={task.get('bayMap')}"
        )

    @staticmethod
    def _log(message: str) -> None:
        print(f"[RTG Twin Receiver] {message}")


class OmniverseRtgTwinDriver:
    """Apply RtgTwinState snapshots to the current Omniverse USD stage."""

    def __init__(self) -> None:
        self._latest_state: Optional[Dict[str, Any]] = None
        self._latest_lock = threading.Lock()
        self._subscription = None
        self._controller = None
        self._controller_stage_id: Optional[int] = None
        self._last_applied_key: Optional[tuple[Any, ...]] = None
        self._last_task_stack_key: Optional[tuple[Any, ...]] = None
        self._last_task_vehicle_key: Optional[tuple[Any, ...]] = None
        self._last_task_source_path: Optional[str] = None
        self._task_source_has_moved = False
        self._task_source_original_translates: Dict[str, Any] = {}
        self._task_vehicle_started_at: float = 0.0
        self._task_flow_state: str = TASK_STATE_IDLE
        self._task_flow_state_started_at: float = 0.0
        self._task_flow_saw_locked: bool = False
        self._operation_state_machine: Optional[RtgOperationStateMachine] = None
        self._operation_task_key: Optional[tuple[Any, ...]] = None
        self._status_overlay: Optional[RtgTwinStatusOverlay] = None
        self._last_status_overlay_at: float = 0.0
        self._last_error_at = 0.0

    def start(self) -> None:
        if self._subscription is not None:
            return
        try:
            import omni.kit.app  # type: ignore

            stream = omni.kit.app.get_app().get_update_event_stream()
            self._subscription = stream.create_subscription_to_pop(
                self._on_update, name="SmartPortRtgTwinDriver"
            )
            self._log("Omniverse main-thread driver started")
        except Exception as exc:  # noqa: BLE001 - Kit may not be loaded in tests
            self._log(f"unable to start Omniverse update driver: {exc}")

    def stop(self) -> None:
        self._subscription = None
        self._controller = None
        self._controller_stage_id = None
        self._last_applied_key = None
        if self._status_overlay is not None:
            self._status_overlay.destroy()
            self._status_overlay = None

    def enqueue(self, state: Dict[str, Any]) -> None:
        with self._latest_lock:
            self._latest_state = state

    def _on_update(self, _event: Any) -> None:
        with self._latest_lock:
            state = self._latest_state
            self._latest_state = None
        if state is None:
            return
        try:
            self.apply_state(state)
        except Exception as exc:  # noqa: BLE001 - keep Kit running
            now = time.time()
            if now - self._last_error_at > 1.0:
                self._last_error_at = now
                self._log(f"apply failed: {exc}")

    def apply_state(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        stage = self._current_stage()
        if stage is None:
            return None

        controller = self._controller_for_stage(stage)
        pose = state.get("pose") or {}
        task = state.get("task") or {}

        field = _field_name(pose.get("currentField"))
        bay = _as_int(pose.get("currentBay"), 0)
        trolley_m = _as_float(pose.get("trolleyMeter"))
        hoist_m = _as_float(pose.get("hoistMeter"))
        gantry_m = _as_float(pose.get("bigcarElecDistance"))

        # Prefer PLC field/bay because it matches yard occupation.  If PLC has
        # not published a bay yet, fall back to continuous gantry metres.
        if field and bay > 0:
            applied_gantry_m = controller.set_gantry_bay(field, bay)
            gantry_source = f"{field}/{bay:03d}"
        elif gantry_m is not None:
            applied_gantry_m = controller.set_gantry_safe(gantry_m)
            gantry_source = "bigcarElecDistance"
        else:
            applied_gantry_m = None
            gantry_source = "none"

        applied_trolley_m = (
            controller.set_trolley_safe(trolley_m) if trolley_m is not None else None
        )
        applied_hoist_m = (
            controller.set_hoist_safe(hoist_m) if hoist_m is not None else None
        )

        applied = {
            "gantryMeter": applied_gantry_m,
            "gantrySource": gantry_source,
            "trolleyMeter": applied_trolley_m,
            "hoistMeter": applied_hoist_m,
        }
        self._sync_task_stack(stage, task)
        if _task_work_type(task) == WORK_TYPE_SWITCH:
            self._sync_task_switch(stage, task, pose)
        else:
            self._sync_task_vehicle(stage, task, pose)
        self._write_status(stage, state, applied)
        self._sync_status_overlay(state, applied)

        key = (
            field,
            bay,
            round(applied_gantry_m, 3) if applied_gantry_m is not None else None,
            round(applied_trolley_m, 3) if applied_trolley_m is not None else None,
            round(applied_hoist_m, 3) if applied_hoist_m is not None else None,
            _as_int(pose.get("lockState"), 0),
            task.get("taskId"),
        )
        if key != self._last_applied_key:
            self._last_applied_key = key
            self._log(
                "applied "
                f"gantry={applied_gantry_m}({gantry_source}) "
                f"trolley={applied_trolley_m} hoist={applied_hoist_m} "
                f"lock={pose.get('lockState')}"
            )
        return applied

    def _sync_status_overlay(
        self, state: Dict[str, Any], applied: Dict[str, Any]
    ) -> None:
        now = time.time()
        if now - self._last_status_overlay_at < STATUS_OVERLAY_UPDATE_SECONDS:
            return
        self._last_status_overlay_at = now
        try:
            if self._status_overlay is None:
                self._status_overlay = RtgTwinStatusOverlay()
            self._status_overlay.update(state, applied)
        except Exception as exc:  # noqa: BLE001 - UI should not stop RTG driving
            if now - self._last_error_at > 1.0:
                self._last_error_at = now
                self._log(f"status overlay failed: {exc}")

    def _sync_task_stack(self, stage: Any, task: Dict[str, Any]) -> None:
        key = self._task_stack_key(task)
        if key == self._last_task_stack_key:
            return
        self._last_task_stack_key = key

        from pxr import Sdf, Usd, UsdGeom

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            old = stage.GetPrimAtPath(LIVE_TASK_STACK_PATH)
            if old:
                stage.RemovePrim(old.GetPath())

            if key is None:
                return

            block, bay_number, pattern, container_size_ft = key
            root = UsdGeom.Xform.Define(stage, LIVE_TASK_STACK_PATH).GetPrim()
            root.CreateAttribute("live:source", Sdf.ValueTypeNames.Token).Set(
                "rtg_twin_state"
            )
            root.CreateAttribute("live:block", Sdf.ValueTypeNames.String).Set(block)
            root.CreateAttribute("live:bayNumber", Sdf.ValueTypeNames.Int).Set(
                bay_number
            )
            root.CreateAttribute("live:bayMapPattern", Sdf.ValueTypeNames.String).Set(
                pattern
            )
            root.CreateAttribute("live:containerSizeFt", Sdf.ValueTypeNames.Int).Set(
                container_size_ft
            )

            self._create_live_stack_geometry(
                stage,
                LIVE_TASK_STACK_PATH,
                block,
                bay_number,
                pattern,
                container_size_ft,
            )
            self._log(
                f"rendered live task stack {block}/{bay_number:03d} "
                f"{container_size_ft}FT bayMap={pattern}"
            )

    def _sync_task_vehicle(
        self, stage: Any, task: Dict[str, Any], pose: Dict[str, Any]
    ) -> None:
        key = self._task_vehicle_key(task)
        if key is None:
            self._remove_task_vehicle(stage)
            return

        work_type, block, bay_number, _pattern, container_size_ft, task_id = key
        if work_type not in (WORK_TYPE_LOAD, WORK_TYPE_UNLOAD):
            self._remove_task_vehicle(stage)
            return

        if key != self._last_task_vehicle_key:
            self._restore_task_source(stage)
            self._last_task_vehicle_key = key
            self._last_task_source_path = (
                self._task_source_container_path(task)
                if work_type == WORK_TYPE_UNLOAD
                else None
            )
            self._task_vehicle_started_at = time.time()
            self._task_flow_state = TASK_STATE_TRUCK_APPROACH
            self._task_flow_state_started_at = self._task_vehicle_started_at
            self._task_flow_saw_locked = False
            self._create_task_vehicle(
                stage,
                block,
                bay_number,
                container_size_ft,
                task_id,
                work_type,
            )
            self._log(
                f"rendered {'inbound loaded' if work_type == WORK_TYPE_LOAD else 'outbound empty'} "
                f"truck for {block}/{bay_number:03d} {container_size_ft}FT task={task_id} "
                f"source={self._last_task_source_path or '--'}"
            )

        flow_state = self._advance_operation_flow(stage, key, task, pose)
        truck_progress = self._update_task_vehicle_pose(
            stage, block, bay_number, container_size_ft, flow_state
        )
        self._sync_task_source_visual(stage, work_type, flow_state)
        self._sync_task_visuals(
            stage,
            container_size_ft,
            work_type,
            flow_state,
            truck_progress,
            pose,
        )

    def _task_vehicle_key(
        self, task: Dict[str, Any]
    ) -> Optional[tuple[str, str, int, str, int, str]]:
        stack_key = self._task_stack_key(task)
        if stack_key is None:
            return None
        block, bay_number, pattern, container_size_ft = stack_key
        work_type = _task_work_type(task)
        task_id = str(task.get("taskId") or "")
        return work_type, block, bay_number, pattern, container_size_ft, task_id

    def _sync_task_switch(
        self, stage: Any, task: Dict[str, Any], pose: Dict[str, Any]
    ) -> None:
        """Show a rehandled source box on the spreader while PLC is locked."""

        key = self._task_vehicle_key(task)
        if key is None or key[0] != WORK_TYPE_SWITCH:
            self._remove_task_vehicle(stage)
            return

        _work_type, block, bay_number, _pattern, container_size_ft, task_id = key
        if key != self._last_task_vehicle_key:
            self._remove_task_vehicle(stage)
            self._last_task_vehicle_key = key
            self._last_task_source_path = self._task_source_container_path(task)
            self._task_flow_state = TASK_STATE_READY_TO_PICK
            self._task_flow_state_started_at = time.time()
            self._task_flow_saw_locked = False
            self._set_task_flow_attr(stage, self._task_flow_state)
            self._log(
                f"rendered rehandle source={self._last_task_source_path or '--'} "
                f"for {block}/{bay_number:03d} {container_size_ft}FT task={task_id}"
            )

        flow_state = self._advance_operation_flow(stage, key, task, pose)
        self._sync_task_source_visual(stage, WORK_TYPE_SWITCH, flow_state)
        self._sync_task_visuals(
            stage,
            container_size_ft,
            WORK_TYPE_SWITCH,
            flow_state,
            1.0,
            pose,
        )

    def _remove_task_vehicle(self, stage: Any) -> None:
        from pxr import Usd

        if self._last_task_vehicle_key is None and self._last_task_source_path is None:
            return
        self._restore_task_source(stage)
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            for path in (
                LIVE_TASK_TRUCK_PATH,
                LIVE_HOISTED_LOAD_PATH,
                LEGACY_LIVE_HOISTED_LOAD_PATH,
            ):
                prim = stage.GetPrimAtPath(path)
                if prim:
                    stage.RemovePrim(prim.GetPath())
        self._last_task_vehicle_key = None
        self._task_flow_state = TASK_STATE_IDLE
        self._task_flow_state_started_at = 0.0
        self._task_flow_saw_locked = False
        self._operation_state_machine = None
        self._operation_task_key = None
        self._task_source_has_moved = False
        self._task_source_original_translates.clear()

    def _advance_operation_flow(
        self,
        stage: Any,
        key: tuple[str, str, int, str, int, str],
        task: Dict[str, Any],
        pose: Dict[str, Any],
    ) -> str:
        """Advance the formal PLC-driven operation state machine.

        The return value remains the compact visual phase used by the existing
        truck/container renderer.  The detailed state is also authored on the
        live prim so the UI and diagnostics can show the true task phase.
        """

        if self._operation_task_key != key:
            self._operation_task_key = key
            self._operation_state_machine = self._build_operation_state_machine(
                task, key
            )
            # Reloading the Script Editor while a PLC task is already locked
            # must resume the visible carried source instead of replaying every
            # pre-pick state from the beginning.
            if (
                self._operation_state_machine is not None
                and _as_int(pose.get("lockState"), 0) == 1
            ):
                self._operation_state_machine.state = OperationState.TROLLEY_TO_TARGET
                self._operation_state_machine.entered_at = time.time()
                self._log(f"resumed locked operation task={key[-1]}")
            if self._operation_state_machine is None:
                self._log(f"state machine unavailable for task={key[-1]}")
                return self._task_flow_state

        machine = self._operation_state_machine
        if machine is None:
            return self._task_flow_state

        plc = PlcOperationSnapshot(
            field=_field_name(pose.get("currentField")),
            bay=_as_int(pose.get("currentBay"), 0) or None,
            trolley_m=_as_float(pose.get("trolleyMeter")),
            hoist_m=_as_float(pose.get("hoistMeter")),
            locked=_as_int(pose.get("lockState"), 0) == 1,
            alarmed=_as_int(pose.get("alarmStatus"), 0) != 0,
            vision_confirmed=bool(task.get("visionConfirmed", True)),
        )
        transition = machine.update(plc, time.time())
        self._set_operation_state_attr(stage, machine.state.value)
        if transition is not None:
            self._log(
                "operation state "
                f"{transition.previous.value} -> {transition.current.value}: "
                f"{transition.reason}; task={key[-1]}"
            )

        visual_state = machine.visual_phase()
        if visual_state != self._task_flow_state:
            now = time.time()
            self._task_flow_state = visual_state
            self._task_flow_state_started_at = now
            if visual_state == TASK_STATE_TRUCK_APPROACH:
                self._task_vehicle_started_at = now
            self._set_task_flow_attr(stage, visual_state)
        return visual_state

    def _build_operation_state_machine(
        self,
        task: Dict[str, Any],
        key: tuple[str, str, int, str, int, str],
    ) -> Optional[RtgOperationStateMachine]:
        work_type, block, bay_number, _pattern, _size, task_id = key
        source_details = self._parse_task_position_details(
            task.get("startPos") or task.get("sourcePos")
        )
        target_details = self._parse_task_position_details(
            task.get("endPos") or task.get("targetPos")
        )

        def slot(
            details: Optional[tuple[str, int, Optional[int], Optional[int]]],
            *,
            fallback_row: Optional[int] = None,
        ) -> Optional[TaskSlot]:
            if details is None:
                return None
            field, bay, row, tier = details
            return TaskSlot(field, bay, row if row is not None else fallback_row, tier)

        if work_type == WORK_TYPE_LOAD:
            # The truck and the destination stack occupy the same task bay.
            target = slot(target_details)
            if target is None or target.row is None:
                return None
            source = TaskSlot(target.field, target.bay)
        elif work_type == WORK_TYPE_UNLOAD:
            source = slot(source_details)
            if source is None or source.row is None:
                return None
            target = TaskSlot(source.field, source.bay)
        elif work_type == WORK_TYPE_SWITCH:
            source = slot(source_details)
            target = slot(target_details)
            if source is None or target is None or source.row is None or target.row is None:
                return None
        else:
            return None

        try:
            return RtgOperationStateMachine(
                OperationTask(
                    task_id=task_id,
                    work_type=work_type,
                    source=source,
                    target=target,
                    vision_required=bool(task.get("visionRequired", False)),
                ),
                truck_approach_seconds=TRUCK_APPROACH_SECONDS,
                truck_settle_seconds=TRUCK_SETTLE_SECONDS,
                truck_exit_seconds=TRUCK_EXIT_SECONDS,
            )
        except ValueError as exc:
            self._log(f"invalid operation task: {exc}")
            return None

    def _advance_task_flow(
        self,
        stage: Any,
        key: tuple[str, str, int, str, int, str],
        pose: Dict[str, Any],
    ) -> str:
        now = time.time()
        locked = _as_int(pose.get("lockState"), 0) == 1
        if locked:
            self._task_flow_saw_locked = True

        elapsed = now - self._task_flow_state_started_at
        next_state = self._task_flow_state

        if self._task_flow_state == TASK_STATE_TRUCK_APPROACH:
            if elapsed >= TRUCK_APPROACH_SECONDS:
                next_state = TASK_STATE_TRUCK_WAITING
        elif self._task_flow_state == TASK_STATE_TRUCK_WAITING:
            if elapsed >= TRUCK_SETTLE_SECONDS:
                next_state = TASK_STATE_READY_TO_PICK
        elif self._task_flow_state == TASK_STATE_READY_TO_PICK:
            if locked:
                next_state = TASK_STATE_CARRYING
        elif self._task_flow_state == TASK_STATE_CARRYING:
            # In the real PLC flow, lock releasing after it has been seen locked
            # is the most reliable sign that the box has been placed.
            if self._task_flow_saw_locked and not locked and elapsed >= 0.5:
                next_state = TASK_STATE_PLACED
        elif self._task_flow_state == TASK_STATE_PLACED:
            if elapsed >= 1.0:
                next_state = TASK_STATE_TRUCK_EXITING

        if next_state != self._task_flow_state:
            self._task_flow_state = next_state
            self._task_flow_state_started_at = now
            self._set_task_flow_attr(stage, next_state)
            self._log(f"task flow -> {next_state} key={key}")

        return self._task_flow_state

    def _advance_switch_flow(
        self,
        stage: Any,
        key: tuple[str, str, int, str, int, str],
        pose: Dict[str, Any],
    ) -> str:
        now = time.time()
        locked = _as_int(pose.get("lockState"), 0) == 1
        if locked:
            self._task_flow_saw_locked = True

        elapsed = now - self._task_flow_state_started_at
        next_state = self._task_flow_state
        if self._task_flow_state == TASK_STATE_READY_TO_PICK and locked:
            next_state = TASK_STATE_CARRYING
        elif (
            self._task_flow_state == TASK_STATE_CARRYING
            and self._task_flow_saw_locked
            and not locked
            and elapsed >= 0.5
        ):
            next_state = TASK_STATE_PLACED

        if next_state != self._task_flow_state:
            self._task_flow_state = next_state
            self._task_flow_state_started_at = now
            self._set_task_flow_attr(stage, next_state)
            self._log(f"rehandle flow -> {next_state} key={key}")
        return self._task_flow_state

    def _set_task_flow_attr(self, stage: Any, flow_state: str) -> None:
        from pxr import Sdf, Usd

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            prim = stage.GetPrimAtPath(LIVE_TASK_TRUCK_PATH)
            if not prim:
                prim = stage.GetPrimAtPath(LIVE_TASK_STACK_PATH)
            if prim:
                self._set_attr(
                    prim, "live:taskFlowState", Sdf.ValueTypeNames.String, flow_state
                )

    def _set_operation_state_attr(self, stage: Any, operation_state: str) -> None:
        """Expose the detailed shared state-machine phase on the live USD prim."""

        from pxr import Sdf, Usd

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            prim = stage.GetPrimAtPath(LIVE_TASK_TRUCK_PATH)
            if not prim:
                prim = stage.GetPrimAtPath(LIVE_TASK_STACK_PATH)
            if prim:
                self._set_attr(
                    prim,
                    "live:operationState",
                    Sdf.ValueTypeNames.String,
                    operation_state,
                )

    def _create_task_vehicle(
        self,
        stage: Any,
        block: str,
        bay_number: int,
        container_size_ft: int,
        task_id: str,
        work_type: str,
    ) -> None:
        from pxr import Gf, Sdf, Usd, UsdGeom
        from build_live_containers import (
            CONTAINER_VISUAL_HEIGHT_Z_M,
            CONTAINER_WIDTH_X_M,
            PAD_TOP_Z_M,
            container_length_for_bay,
            create_container_box,
            create_container_details,
            material,
        )

        container_length_y = container_length_for_bay(
            block, bay_number, container_size_ft
        )
        trailer_length_y = max(container_length_y + 0.55, 5.8)

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            old = stage.GetPrimAtPath(LIVE_TASK_TRUCK_PATH)
            if old:
                stage.RemovePrim(old.GetPath())

            root = UsdGeom.Xform.Define(stage, LIVE_TASK_TRUCK_PATH).GetPrim()
            root.CreateAttribute("live:source", Sdf.ValueTypeNames.Token).Set(
                "rtg_twin_task_vehicle"
            )
            root.CreateAttribute("live:taskId", Sdf.ValueTypeNames.String).Set(task_id)
            root.CreateAttribute("live:workType", Sdf.ValueTypeNames.String).Set(
                work_type
            )
            root.CreateAttribute("live:block", Sdf.ValueTypeNames.String).Set(block)
            root.CreateAttribute("live:bayNumber", Sdf.ValueTypeNames.Int).Set(
                bay_number
            )
            root.CreateAttribute("live:containerSizeFt", Sdf.ValueTypeNames.Int).Set(
                container_size_ft
            )
            self._set_xform_translate(root, Gf.Vec3d(0.0, 0.0, PAD_TOP_Z_M))

            self._create_task_cube(
                stage,
                f"{LIVE_TASK_TRUCK_PATH}/Trailer",
                Gf.Vec3d(0.0, 0.0, 0.36),
                Gf.Vec3f(2.25, trailer_length_y, 0.18),
                Gf.Vec3f(0.28, 0.24, 0.18),
            )
            self._create_task_cube(
                stage,
                f"{LIVE_TASK_TRUCK_PATH}/Cab",
                Gf.Vec3d(0.0, -trailer_length_y * 0.5 - 0.95, 0.66),
                Gf.Vec3f(1.65, 1.35, 0.78),
                Gf.Vec3f(0.92, 0.90, 0.84),
            )
            self._create_task_cube(
                stage,
                f"{LIVE_TASK_TRUCK_PATH}/CabWindshield",
                Gf.Vec3d(0.0, -trailer_length_y * 0.5 - 1.64, 0.83),
                Gf.Vec3f(1.15, 0.05, 0.30),
                Gf.Vec3f(0.02, 0.04, 0.055),
            )
            for index, (x, y) in enumerate(
                (
                    (-1.00, -trailer_length_y * 0.36),
                    (1.00, -trailer_length_y * 0.36),
                    (-1.00, trailer_length_y * 0.36),
                    (1.00, trailer_length_y * 0.36),
                    (-0.84, -trailer_length_y * 0.5 - 1.02),
                    (0.84, -trailer_length_y * 0.5 - 1.02),
                ),
                start=1,
            ):
                self._create_task_cube(
                    stage,
                    f"{LIVE_TASK_TRUCK_PATH}/Wheel_{index:02d}",
                    Gf.Vec3d(x, y, 0.22),
                    Gf.Vec3f(0.30, 0.18, 0.34),
                    Gf.Vec3f(0.015, 0.015, 0.013),
                )

            box_center_z = 0.36 + 0.09 + CONTAINER_VISUAL_HEIGHT_Z_M * 0.5
            truck_box_path = f"{LIVE_TASK_TRUCK_PATH}/LoadedContainer"
            truck_box_scale = Gf.Vec3f(
                CONTAINER_WIDTH_X_M,
                container_length_y,
                CONTAINER_VISUAL_HEIGHT_Z_M,
            )
            truck_box_material = material(
                stage,
                "TaskTruckContainerBlue",
                Gf.Vec3f(0.04, 0.20, 0.48),
            )
            create_container_box(
                stage,
                truck_box_path,
                Gf.Vec3d(0.0, 0.0, box_center_z),
                truck_box_scale,
                truck_box_material,
                bay_id=f"{block}/{bay_number:03d}",
                row=0,
                tier=0,
                tiers=1,
            )
            create_container_details(
                stage,
                truck_box_path,
                Gf.Vec3d(0.0, 0.0, box_center_z),
                truck_box_scale,
            )

    def _update_task_vehicle_pose(
        self,
        stage: Any,
        block: str,
        bay_number: int,
        container_size_ft: int,
        flow_state: str,
    ) -> float:
        from pxr import Gf, Usd
        from build_live_containers import (
            container_center_y_for_bay,
        )

        lane_center_x = self._task_truck_lane_center_x()
        target_y = (
            container_center_y_for_bay(block, bay_number, container_size_ft)
            + TASK_TRUCK_STOP_Y_OFFSET_M
        )
        start_y = target_y + 16.0
        exit_y = target_y - 18.0
        if flow_state == TASK_STATE_GANTRY_TO_SOURCE:
            progress = 0.0
            current_y = start_y
        elif flow_state == TASK_STATE_TRUCK_APPROACH:
            progress = min(
                1.0,
                max(
                    0.0,
                    (time.time() - self._task_vehicle_started_at)
                    / TRUCK_APPROACH_SECONDS,
                ),
            )
            eased = progress * progress * (3.0 - 2.0 * progress)
            current_y = start_y + (target_y - start_y) * eased
        elif flow_state == TASK_STATE_TRUCK_EXITING:
            progress = min(
                1.0,
                max(
                    0.0,
                    (time.time() - self._task_flow_state_started_at)
                    / TRUCK_EXIT_SECONDS,
                ),
            )
            eased = progress * progress * (3.0 - 2.0 * progress)
            current_y = target_y + (exit_y - target_y) * eased
        else:
            progress = 1.0
            current_y = target_y

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            root = stage.GetPrimAtPath(LIVE_TASK_TRUCK_PATH)
            if root:
                self._set_xform_translate(root, Gf.Vec3d(lane_center_x, current_y, 0.0))
                self._set_visibility(
                    root,
                    not (
                        flow_state in ("COMPLETE", "FAULT")
                        or flow_state == TASK_STATE_GANTRY_TO_SOURCE
                        or flow_state == TASK_STATE_TRUCK_EXITING
                        and progress >= 1.0
                    ),
                )
        return progress

    @staticmethod
    def _task_truck_lane_center_x() -> float:
        from build_live_containers import (
            BAY_WIDTH_M,
            LEFT_INNER_SAFETY_X_M,
            TRUCK_LANE_WIDTH_X_M,
        )

        lane_left_x = -BAY_WIDTH_M * 0.5 + LEFT_INNER_SAFETY_X_M
        lane_right_x = lane_left_x + TRUCK_LANE_WIDTH_X_M
        visual_center_x = (
            TRUCK_OPERATING_LANE_CENTER_X_M + TASK_TRUCK_VISUAL_X_OFFSET_M
        )
        return min(max(visual_center_x, lane_left_x), lane_right_x)

    def _sync_task_visuals(
        self,
        stage: Any,
        container_size_ft: int,
        work_type: str,
        flow_state: str,
        truck_progress: float,
        pose: Optional[Dict[str, Any]] = None,
    ) -> None:
        from pxr import Gf, Usd
        from build_live_containers import (
            CONTAINER_VISUAL_HEIGHT_Z_M,
            CONTAINER_WIDTH_X_M,
            CONTAINER_LENGTH_20FT_Y_M,
            CONTAINER_LENGTH_40FT_Y_M,
        )

        # PLC lock is authoritative.  Do not make the visual wait for the
        # state machine's next update frame after a successful grab.
        carrying = flow_state == TASK_STATE_CARRYING or (
            work_type in (WORK_TYPE_UNLOAD, WORK_TYPE_SWITCH)
            and _as_int((pose or {}).get("lockState"), 0) == 1
        )
        truck_box_visible = _truck_box_visible(work_type, flow_state)
        truck_has_exited = (
            flow_state == TASK_STATE_TRUCK_EXITING and truck_progress >= 1.0
        )
        if truck_has_exited:
            truck_box_visible = False

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            legacy_load = stage.GetPrimAtPath(LEGACY_LIVE_HOISTED_LOAD_PATH)
            if legacy_load:
                stage.RemovePrim(legacy_load.GetPath())

            self._set_container_visual_visibility(
                stage,
                f"{LIVE_TASK_TRUCK_PATH}/LoadedContainer",
                truck_box_visible,
            )

            # For UNLOAD/SWITCH, the box under the spreader is the exact source
            # box created by the bay-map renderer.  Move it in place instead of
            # creating a second LiveTwinHoistedContainer copy.
            if work_type in (WORK_TYPE_UNLOAD, WORK_TYPE_SWITCH):
                stale_copy = stage.GetPrimAtPath(LIVE_HOISTED_LOAD_PATH)
                if stale_copy:
                    stage.RemovePrim(stale_copy.GetPath())
                if carrying:
                    self._move_task_source_with_spreader(stage)
                return

            if not carrying:
                old = stage.GetPrimAtPath(LIVE_HOISTED_LOAD_PATH)
                if old:
                    stage.RemovePrim(old.GetPath())
                return

            container_length_y = (
                CONTAINER_LENGTH_40FT_Y_M
                if container_size_ft == 40
                else CONTAINER_LENGTH_20FT_Y_M
            )
            load = stage.GetPrimAtPath(LIVE_HOISTED_LOAD_PATH)
            load_body = stage.GetPrimAtPath(f"{LIVE_HOISTED_LOAD_PATH}/Body")
            if load and not load_body:
                # Older receiver builds created a fixed blue Cube directly at
                # LIVE_HOISTED_LOAD_PATH. Never reuse that stale placeholder.
                stage.RemovePrim(load.GetPath())
                load = None
                self._log("removed legacy fixed-color hoisted container")
            scale = Gf.Vec3f(
                CONTAINER_WIDTH_X_M,
                container_length_y,
                CONTAINER_VISUAL_HEIGHT_Z_M,
            )
            source_path = self._hoisted_container_source_path(work_type)
            source = stage.GetPrimAtPath(source_path) if source_path else None
            source_scale = self._get_xform_scale(source)
            attachment_height = float(
                source_scale[2] if source_scale is not None else scale[2]
            )
            world_center = self._hoisted_container_world_center(
                stage, attachment_height
            )
            if world_center is None:
                return
            center = self._live_container_local_point(stage, world_center)
            if not load:
                self._create_hoisted_container_visual(
                    stage,
                    center,
                    scale,
                    work_type,
                )
            else:
                self._set_xform_translate(load, center)
                self._set_visibility(load, True)

    def _create_hoisted_container_visual(
        self,
        stage: Any,
        center: Any,
        fallback_scale: Any,
        work_type: str,
    ) -> bool:
        """Create a carried copy with the source box dimensions and material."""

        from pxr import Gf, UsdGeom, UsdShade
        from build_live_containers import (
            create_container_box,
            create_container_details,
            material,
        )

        source_path = self._hoisted_container_source_path(work_type)
        source = stage.GetPrimAtPath(source_path) if source_path else None
        if not source:
            self._log(
                "cannot create hoisted container: "
                f"missing source for workType={work_type} path={source_path or '--'}"
            )
            return False

        scale = self._get_xform_scale(source)
        if scale is None:
            scale = fallback_scale

        computed_binding = UsdShade.MaterialBindingAPI(
            source
        ).ComputeBoundMaterial()
        bound_material = computed_binding[0] if computed_binding else None
        if not bound_material:
            display_color = Gf.Vec3f(0.47, 0.50, 0.46)
            source_gprim = UsdGeom.Gprim(source)
            colors = source_gprim.GetDisplayColorAttr().Get() if source_gprim else None
            if colors:
                display_color = colors[0]
            bound_material = material(
                stage,
                f"TaskHoisted_{source.GetName()}",
                display_color,
            )

        def source_attr(name: str, default: Any) -> Any:
            attr = source.GetAttribute(name)
            value = attr.Get() if attr else None
            return default if value is None else value

        root = UsdGeom.Xform.Define(stage, LIVE_HOISTED_LOAD_PATH).GetPrim()
        self._set_xform_translate(root, center)
        body_path = f"{LIVE_HOISTED_LOAD_PATH}/Body"
        local_center = Gf.Vec3d(0.0, 0.0, 0.0)
        create_container_box(
            stage,
            body_path,
            local_center,
            scale,
            bound_material,
            bay_id=str(source_attr("live:bayId", "")),
            row=int(source_attr("live:row", 0)),
            tier=int(source_attr("live:tier", 0)),
            tiers=int(source_attr("live:tiers", 1)),
        )
        create_container_details(
            stage,
            body_path,
            local_center,
            scale,
        )
        self._log(
            f"created hoisted copy from {source_path} "
            f"scale=({float(scale[0]):.3f}, {float(scale[1]):.3f}, {float(scale[2]):.3f})"
        )
        return True

    def _hoisted_container_source_path(self, work_type: str) -> Optional[str]:
        if work_type in (WORK_TYPE_UNLOAD, WORK_TYPE_SWITCH):
            return self._last_task_source_path
        return f"{LIVE_TASK_TRUCK_PATH}/LoadedContainer"

    @staticmethod
    def _hoisted_container_world_center(
        stage: Any, container_height: float
    ) -> Any:
        """Return the world-space box centre directly below the spreader."""

        from pxr import Gf, UsdGeom
        from rtg_live_control import HOIST_PATH

        hoist = stage.GetPrimAtPath(HOIST_PATH)
        if not hoist:
            return None
        local_center = Gf.Vec3d(
            HOISTED_LOAD_LOCAL_CENTER_X,
            HOISTED_LOAD_LOCAL_CENTER_Y,
            _hoisted_container_local_center_z(container_height),
        )
        return UsdGeom.XformCache().GetLocalToWorldTransform(hoist).Transform(
            local_center
        )

    @staticmethod
    def _live_container_local_point(stage: Any, world_point: Any) -> Any:
        """Convert a world point to the local coordinates of LiveContainers.

        The carried visual lives below ``/World/LiveContainers``.  Writing a
        world-space point straight to that child used to apply the parent
        transform a second time, so generated boxes drifted away from the
        spreader as the RTG moved.
        """

        from pxr import UsdGeom

        parent = stage.GetPrimAtPath("/World/LiveContainers")
        if not parent:
            return world_point
        parent_world = UsdGeom.XformCache().GetLocalToWorldTransform(parent)
        return parent_world.GetInverse().Transform(world_point)

    def _sync_task_source_visual(
        self, stage: Any, work_type: str, flow_state: str
    ) -> None:
        if not self._last_task_source_path:
            return
        from pxr import Usd

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            # Keep the source box visible until a successful attachment has
            # physically moved it to the spreader.  Previously the state
            # machine could enter CARRYING first, hide (for example) the
            # R03_T01 box from bay-map 441000, and then fail to attach it in
            # the same frame.  That made the third-row box disappear from
            # both the stack and the spreader.
            self._set_container_visual_visibility(
                stage,
                self._last_task_source_path,
                True,
            )

    def _restore_task_source(self, stage: Any) -> None:
        if not self._last_task_source_path:
            return
        from pxr import Usd

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            for path, translate in self._task_source_original_translates.items():
                prim = stage.GetPrimAtPath(path)
                if prim:
                    self._set_xform_translate(prim, translate)
            self._set_container_visual_visibility(
                stage, self._last_task_source_path, True
            )
        self._last_task_source_path = None
        self._task_source_has_moved = False
        self._task_source_original_translates.clear()

    def _move_task_source_with_spreader(self, stage: Any) -> None:
        """Attach the original task-stack source container to the spreader."""

        if not self._last_task_source_path:
            return
        from pxr import Gf, UsdGeom

        source = stage.GetPrimAtPath(self._last_task_source_path)
        if not source:
            return
        source_scale = self._get_xform_scale(source)
        source_translate = self._get_xform_translate(source)
        parent = source.GetParent()
        if source_scale is None or source_translate is None or not parent:
            return

        spreader_world = self._hoisted_container_world_center(
            stage, float(source_scale[2])
        )
        if spreader_world is None:
            return
        parent_world = UsdGeom.XformCache().GetLocalToWorldTransform(parent)
        target_local = parent_world.GetInverse().Transform(spreader_world)
        delta = Gf.Vec3d(
            float(target_local[0]) - float(source_translate[0]),
            float(target_local[1]) - float(source_translate[1]),
            float(target_local[2]) - float(source_translate[2]),
        )
        for component in self._task_source_components(source):
            current = self._get_xform_translate(component)
            if current is None:
                continue
            path = str(component.GetPath())
            if path not in self._task_source_original_translates:
                self._task_source_original_translates[path] = current
            self._set_xform_translate(
                component,
                Gf.Vec3d(
                    float(current[0]) + float(delta[0]),
                    float(current[1]) + float(delta[1]),
                    float(current[2]) + float(delta[2]),
                ),
            )
            self._set_visibility(component, True)
        self._task_source_has_moved = True

    @staticmethod
    def _task_source_components(source: Any) -> list[Any]:
        """Return the source body and its sibling ribs/top-detail prims."""

        components = [source]
        parent = source.GetParent()
        if not parent:
            return components
        prefix = f"{source.GetName()}_"
        components.extend(
            sibling
            for sibling in parent.GetChildren()
            if sibling.GetName().startswith(prefix)
        )
        return components

    def _set_container_visual_visibility(
        self, stage: Any, source_path: str, visible: bool
    ) -> None:
        """Show or hide a container body together with its sibling details."""

        source = stage.GetPrimAtPath(source_path)
        if not source:
            return
        self._set_visibility(source, visible)

        detail_prefix = f"{source.GetName()}_"
        parent = source.GetParent()
        if not parent:
            return
        for sibling in parent.GetChildren():
            if sibling.GetName().startswith(detail_prefix):
                self._set_visibility(sibling, visible)

    @staticmethod
    def _create_task_cube(
        stage: Any,
        path: str,
        center: Any,
        scale: Any,
        color: Any,
    ) -> None:
        from pxr import UsdGeom

        cube = UsdGeom.Cube.Define(stage, path)
        cube.CreateSizeAttr(1.0)
        xform = UsdGeom.Xformable(cube.GetPrim())
        xform.AddTranslateOp().Set(center)
        xform.AddScaleOp().Set(scale)
        cube.CreateDisplayColorAttr([color])

    @staticmethod
    def _set_xform_translate(prim: Any, value: Any) -> None:
        from pxr import UsdGeom

        xform = UsdGeom.Xformable(prim)
        for op in xform.GetOrderedXformOps():
            if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                op.Set(value)
                return
        xform.AddTranslateOp().Set(value)

    @staticmethod
    def _get_xform_scale(prim: Any) -> Any:
        if not prim:
            return None
        from pxr import UsdGeom

        xform = UsdGeom.Xformable(prim)
        for op in xform.GetOrderedXformOps():
            if op.GetOpType() == UsdGeom.XformOp.TypeScale:
                return op.Get()
        return None

    @staticmethod
    def _set_xform_scale(prim: Any, value: Any) -> None:
        from pxr import UsdGeom

        xform = UsdGeom.Xformable(prim)
        for op in xform.GetOrderedXformOps():
            if op.GetOpType() == UsdGeom.XformOp.TypeScale:
                op.Set(value)
                return
        xform.AddScaleOp().Set(value)

    @staticmethod
    def _set_visibility(prim: Any, visible: bool) -> None:
        if not prim:
            return
        from pxr import UsdGeom

        imageable = UsdGeom.Imageable(prim)
        if visible:
            imageable.MakeVisible()
        else:
            imageable.MakeInvisible()

    def _task_stack_key(self, task: Dict[str, Any]) -> Optional[tuple[str, int, str, int]]:
        pattern = str(task.get("bayMap") or "").strip()
        if not pattern:
            return None
        if not re.fullmatch(r"\d{6}", pattern):
            return None

        work_type = _task_work_type(task)
        if work_type in (WORK_TYPE_UNLOAD, WORK_TYPE_SWITCH):
            pos = (
                task.get("startPos")
                or task.get("sourcePos")
                or task.get("endPos")
                or task.get("targetPos")
            )
        else:
            pos = (
                task.get("endPos")
                or task.get("targetPos")
                or task.get("startPos")
                or task.get("sourcePos")
            )
        parsed = self._parse_task_position(pos)
        if parsed is None:
            return None
        block, bay_number = parsed

        container_size_ft = self._container_size_ft(task.get("containerSize"), bay_number)
        # If cntr_size is missing/unknown, prefer the bay parity rule.
        if container_size_ft == 40 and bay_number % 2 == 1:
            bay_number += 1
        elif container_size_ft == 20 and bay_number % 2 == 0:
            bay_number = max(1, bay_number - 1)
        return block, bay_number, pattern, container_size_ft

    @staticmethod
    def _parse_task_position(value: Any) -> Optional[tuple[str, int]]:
        parsed = OmniverseRtgTwinDriver._parse_task_position_details(value)
        if parsed is None:
            return None
        block, bay_number, _row, _tier = parsed
        return block, bay_number

    @staticmethod
    def _parse_task_position_details(
        value: Any,
    ) -> Optional[tuple[str, int, Optional[int], Optional[int]]]:
        if value is None:
            return None
        text = str(value).strip().upper().replace("-", "").replace("/", "")
        match = re.match(r"^([1-4]C)(\d+)", text)
        if not match:
            return None
        block = match.group(1)
        digits = match.group(2)
        if len(digits) >= 4:
            bay_text = digits[:-2]
            row = int(digits[-2])
            tier = int(digits[-1])
        else:
            bay_text = digits
            row = None
            tier = None
        bay_number = int(bay_text)
        return block, bay_number, row, tier

    def _task_source_container_path(self, task: Dict[str, Any]) -> Optional[str]:
        parsed = self._parse_task_position_details(
            task.get("startPos") or task.get("sourcePos")
        )
        if parsed is None:
            self._log("outbound task has no parseable source position")
            return None

        _block, _bay_number, row, tier = parsed
        pattern = str(task.get("bayMap") or "").strip()
        if (
            row is None
            or tier is None
            or not re.fullmatch(r"\d{6}", pattern)
            or row < 1
            or row > len(pattern)
            or tier < 1
            or tier > int(pattern[row - 1])
        ):
            self._log(
                f"outbound source is outside bay map: "
                f"pos={task.get('startPos')} bayMap={pattern}"
            )
            return None
        return f"{LIVE_TASK_STACK_PATH}/R{row:02d}_T{tier:02d}_Container"

    @staticmethod
    def _container_size_ft(value: Any, bay_number: int) -> int:
        size = _as_int(value, 0)
        if size in (20, 1):
            return 20
        if size in (40, 2, 45, 3):
            return 40
        return 40 if bay_number % 2 == 0 else 20

    def _create_live_stack_geometry(
        self,
        stage: Any,
        root_path: str,
        block: str,
        bay_number: int,
        pattern: str,
        container_size_ft: int,
    ) -> None:
        from pxr import Gf, Sdf, UsdShade
        from build_live_containers import (
            CONTAINER_COLORS,
            CONTAINER_VISUAL_HEIGHT_Z_M,
            CONTAINER_WIDTH_X_M,
            PAD_TOP_Z_M,
            ROW_GAP_X_M,
            STACK_FLOOR_CLEARANCE_M,
            TIER_GAP_Z_M,
            container_center_y_for_bay,
            container_length_for_bay,
            create_container_box,
            create_container_details,
            create_truck_lane_markings,
            material,
            parse_pattern,
            reference_first_row_center_x_m,
            validate_container_bay_number,
        )

        validate_container_bay_number(block, bay_number, container_size_ft)
        heights = parse_pattern(pattern)
        materials = [
            material(stage, f"LiveTwin_Container_{name}", color)
            for name, color in CONTAINER_COLORS
        ]
        bay_center_y = container_center_y_for_bay(block, bay_number, container_size_ft)
        container_length_y = container_length_for_bay(
            block, bay_number, container_size_ft
        )
        row_width = CONTAINER_WIDTH_X_M
        row_pitch = row_width + ROW_GAP_X_M
        first_row_center_x = reference_first_row_center_x_m(len(heights))
        leftmost_stack_x = first_row_center_x - (len(heights) - 1) * row_pitch
        column_centers = [
            leftmost_stack_x + (len(heights) - row_index) * row_pitch
            for row_index in range(1, len(heights) + 1)
        ]

        for row_index, height in enumerate(heights, start=1):
            x = column_centers[row_index - 1]
            for tier in range(1, height + 1):
                z = (
                    PAD_TOP_Z_M
                    + STACK_FLOOR_CLEARANCE_M
                    + (tier - 0.5) * CONTAINER_VISUAL_HEIGHT_Z_M
                    + (tier - 1) * TIER_GAP_Z_M
                )
                container_path = f"{root_path}/R{row_index:02d}_T{tier:02d}_Container"
                mat = materials[(row_index * 3 + tier * 5) % len(materials)]
                create_container_box(
                    stage,
                    container_path,
                    Gf.Vec3d(x, bay_center_y, z),
                    Gf.Vec3f(row_width, container_length_y, CONTAINER_VISUAL_HEIGHT_Z_M),
                    mat,
                    bay_id=f"{block}/{bay_number:03d}",
                    row=row_index,
                    tier=tier,
                    tiers=height,
                )
                create_container_details(
                    stage,
                    container_path,
                    Gf.Vec3d(x, bay_center_y, z),
                    Gf.Vec3f(row_width, container_length_y, CONTAINER_VISUAL_HEIGHT_Z_M),
                )

        create_truck_lane_markings(stage, f"{root_path}/TruckLane")

    def _current_stage(self):
        try:
            import omni.usd  # type: ignore

            return omni.usd.get_context().get_stage()
        except Exception:
            return None

    def _controller_for_stage(self, stage: Any):
        stage_id = id(stage)
        if self._controller is not None and self._controller_stage_id == stage_id:
            return self._controller

        # Script Editor reruns keep Python modules cached. Reload both the box
        # geometry constants and controller calibration without restarting Kit.
        loaded_modules = {}
        for module_name in ("build_live_containers", "rtg_live_control"):
            module = types.ModuleType(module_name)
            module.__file__ = str(SCRIPT_DIR / f"{module_name}.py")
            sys.modules[module_name] = module
            exec(
                compile(
                    Path(module.__file__).read_text(encoding="utf-8"),
                    module.__file__,
                    "exec",
                ),
                module.__dict__,
            )
            loaded_modules[module_name] = module

        self._controller = loaded_modules["rtg_live_control"].RTGController(stage)
        self._controller_stage_id = stage_id
        self._log("RTGController reloaded and bound to current stage")
        return self._controller

    def _write_status(
        self, stage: Any, state: Dict[str, Any], applied: Dict[str, Any]
    ) -> None:
        from pxr import Sdf, Usd, UsdGeom

        pose = state.get("pose") or {}
        task = state.get("task") or {}
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            prim = UsdGeom.Xform.Define(stage, STATUS_PRIM_PATH).GetPrim()
            self._set_attr(prim, "rtg:rtgName", Sdf.ValueTypeNames.String, state.get("rtgName"))
            self._set_attr(
                prim,
                "rtg:lastTwinTimestampUtc",
                Sdf.ValueTypeNames.String,
                state.get("timestampUtc"),
            )
            self._set_attr(
                prim, "rtg:currentField", Sdf.ValueTypeNames.String, pose.get("currentField")
            )
            self._set_attr(
                prim, "rtg:currentBay", Sdf.ValueTypeNames.Int, _as_int(pose.get("currentBay"), 0)
            )
            self._set_attr(
                prim, "rtg:trolleyMeter", Sdf.ValueTypeNames.Double, applied.get("trolleyMeter")
            )
            self._set_attr(
                prim, "rtg:hoistMeter", Sdf.ValueTypeNames.Double, applied.get("hoistMeter")
            )
            self._set_attr(
                prim, "rtg:gantryMeter", Sdf.ValueTypeNames.Double, applied.get("gantryMeter")
            )
            self._set_attr(
                prim, "rtg:gantrySource", Sdf.ValueTypeNames.String, applied.get("gantrySource")
            )
            self._set_attr(
                prim, "rtg:lockState", Sdf.ValueTypeNames.Int, _as_int(pose.get("lockState"), 0)
            )
            self._set_attr(
                prim, "rtg:taskId", Sdf.ValueTypeNames.String, task.get("taskId")
            )
            self._set_attr(
                prim, "rtg:workType", Sdf.ValueTypeNames.String, task.get("workType")
            )
            self._set_attr(
                prim, "rtg:bayMap", Sdf.ValueTypeNames.String, task.get("bayMap")
            )
            self._set_attr(
                prim, "rtg:lastAppliedUnix", Sdf.ValueTypeNames.Double, time.time()
            )

    @staticmethod
    def _set_attr(prim: Any, name: str, type_name: Any, value: Any) -> None:
        attr = prim.GetAttribute(name)
        if not attr:
            attr = prim.CreateAttribute(name, type_name)
        if value is not None:
            attr.Set(value)

    @staticmethod
    def _log(message: str) -> None:
        print(f"[RTG Twin Driver] {message}")


_receiver: Optional[RtgTwinStateReceiver] = None
_driver: Optional[OmniverseRtgTwinDriver] = None


def start_receiver(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    on_state: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> RtgTwinStateReceiver:
    """Start or restart the raw TCP receiver."""
    global _receiver
    if _receiver is not None:
        _receiver.stop()
    _receiver = RtgTwinStateReceiver(
        RtgTwinReceiverConfig(host=host, port=port), on_state=on_state
    )
    _receiver.start()
    return _receiver


def start_live_driver(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> RtgTwinStateReceiver:
    """Start receiver and apply incoming states to the primary RTG."""
    global _driver
    if _driver is not None:
        _driver.stop()
    _driver = OmniverseRtgTwinDriver()
    _driver.start()
    return start_receiver(host, port, on_state=_driver.enqueue)


def stop_receiver() -> None:
    global _receiver
    if _receiver is not None:
        _receiver.stop()
        _receiver = None


def stop_live_driver() -> None:
    global _driver
    stop_receiver()
    if _driver is not None:
        _driver.stop()
        _driver = None


def get_latest_state() -> Optional[Dict[str, Any]]:
    return _receiver.latest_state if _receiver else None


def apply_latest_state_once() -> Optional[Dict[str, Any]]:
    """Manual helper for debugging in Script Editor."""
    if _receiver is None or _receiver.latest_state is None:
        return None
    driver = _driver or OmniverseRtgTwinDriver()
    return driver.apply_state(_receiver.latest_state)


if __name__ == "__main__":
    start_live_driver()
