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

import builtins
import copy
import importlib
import json
import math
import os
import re
import socket
import sys
import tempfile
import threading
import time
import types
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional


_previous_stop_live_driver = globals().get("stop_live_driver")
_previous_builtin_stop_live_driver = getattr(
    builtins,
    "_smart_port_rtg_twin_stop_live_driver",
    None,
)


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

if callable(_previous_stop_live_driver):
    try:
        _previous_stop_live_driver()
    except Exception as exc:
        print(f"[RTG Twin Driver] previous driver stop failed: {exc}")
if callable(_previous_builtin_stop_live_driver) and _previous_builtin_stop_live_driver is not _previous_stop_live_driver:
    try:
        _previous_builtin_stop_live_driver()
    except Exception as exc:
        print(f"[RTG Twin Driver] previous builtin driver stop failed: {exc}")

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
bay_heights_from_pattern = _state_machine_module.bay_heights_from_pattern
resolve_container_height_m = _state_machine_module.resolve_container_height_m

from yard_inventory import (  # noqa: E402
    DEFAULT_LIVE_INVENTORY_PATH,
    DEFAULT_SIM_INVENTORY_PATH,
    YardInventory,
    bay_id as inventory_bay_id,
    normalize_bay_number as normalize_inventory_bay_number,
    parse_task_position as parse_inventory_task_position,
)
from rtg_billboard_utils import (  # noqa: E402
    active_viewport_camera_position,
    camera_facing_rotate_xyz,
    camera_facing_rotate_xyz_from_camera,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 48700
RECEIVER_STATE_LOG_ENABLED = False
STATUS_PRIM_PATH = "/World/LiveTwinState"
RUNINFO_FLOATING_ATTACH_PATH = (
    "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL/"
)
LEGACY_RUNINFO_FLOATING_ROOT_PATH = (
    f"{RUNINFO_FLOATING_ATTACH_PATH}"
    "RTG_RUNINFO_FLOATING_BILLBOARD"
)
RUNINFO_FLOATING_ROOT_PATH = "/World/LiveHUD/RTG_RUNINFO_FLOATING_BILLBOARD"
RUNINFO_FLOATING_PANEL_PATH = f"{RUNINFO_FLOATING_ROOT_PATH}/Panel"
RUNINFO_FLOATING_MATERIAL_PATH = "/World/Looks/RTG_RunInfo_Floating_Billboard"
RUNINFO_FLOATING_ABOVE_RTG_PATH = "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC"
RUNINFO_FLOATING_ABOVE_GAP_M = 1.2
RUNINFO_FLOATING_RIGHT_X_OFFSET_M = 1.2
RUNINFO_FLOATING_BACK_Y_OFFSET_M = 6.0
RUNINFO_FLOATING_UP_Z_OFFSET_M = 1.0
RUNINFO_FLOATING_ENABLED = True
RUNINFO_LEVEL_PROMPT = "PROMPT"
RUNINFO_LEVEL_ALERT = "ALERT"
RUNINFO_LEVEL_EXCEPTION = "EXCEPTION"
RUNINFO_LEVEL_STYLES = {
    RUNINFO_LEVEL_PROMPT: {
        "title": "提示信息",
        "badge": "提示",
        "fill": (7, 18, 33, 250),
        "glow": (0, 190, 255, 86),
        "outline": (65, 205, 255, 238),
        "inner": (75, 190, 255, 130),
        "accent": (54, 216, 255, 255),
        "badge_text": (5, 22, 35, 255),
        "body": (224, 248, 255, 255),
    },
    RUNINFO_LEVEL_ALERT: {
        "title": "告警信息",
        "badge": "告警",
        "fill": (32, 24, 9, 250),
        "glow": (255, 171, 36, 110),
        "outline": (255, 190, 55, 248),
        "inner": (255, 198, 62, 145),
        "accent": (255, 194, 55, 255),
        "badge_text": (45, 27, 4, 255),
        "body": (255, 238, 189, 255),
    },
    RUNINFO_LEVEL_EXCEPTION: {
        "title": "异常信息",
        "badge": "异常",
        "fill": (39, 11, 19, 250),
        "glow": (255, 65, 83, 125),
        "outline": (255, 83, 98, 252),
        "inner": (255, 87, 105, 150),
        "accent": (255, 83, 98, 255),
        "badge_text": (52, 7, 15, 255),
        "body": (255, 220, 224, 255),
    },
}
RUNINFO_FLOATING_IMAGE_WIDTH = 1536
RUNINFO_FLOATING_IMAGE_HEIGHT = 448
RUNINFO_FLOATING_PANEL_WIDTH_M = 12.0
RUNINFO_FLOATING_PANEL_HEIGHT_M = 3.5
RUNINFO_FLOATING_LOCAL_TRANSLATE = (-0.35, -5.05, 13.4)
RUNINFO_FLOATING_FACE_VERTEX_INDICES = (0, 1, 2, 3)
RUNINFO_FLOATING_UV_COORDS = (
    (0.0, 0.0),
    (1.0, 0.0),
    (1.0, 1.0),
    (0.0, 1.0),
)
RUNINFO_FLOATING_TEXT_FONT_SIZE = 60
RUNINFO_FLOATING_TITLE_FONT_SIZE = 44
RUNINFO_FLOATING_CLOCK_FONT_SIZE = 44
RUNINFO_FLOATING_TEXT_MAX_LINES = 3
RUNINFO_FLOATING_TEXT_LINE_GAP = 8
RUNINFO_FLOATING_CLOCK_FORMAT = "%Y-%m-%d %H:%M:%S"
RUNINFO_FLOATING_TIME_OFFSET_HOURS = 8
RUNINFO_FLOATING_DEFAULT_CLOCK_TEXT = "--"
RUNINFO_FLOATING_DEFAULT_TEXT = "待执行"
RUNINFO_FLOATING_ROTATE_XYZ = (0.0, 0.0, 180.0)
RUNINFO_CLEANUP_PATH_TOKENS = ("runinfo", "run_info")
RUNINFO_EXPLICIT_STALE_ROOT_PATHS = (
    LEGACY_RUNINFO_FLOATING_ROOT_PATH,
    "/World/PortAndRTG/RTG_RUNINFO_FLOATING_BILLBOARD",
)
TASK_PANEL_ROOT_PATH = "/World/LiveHUD/RTG_TASK_INFO_PANEL"
TASK_PANEL_MESH_PATH = f"{TASK_PANEL_ROOT_PATH}/Panel"
TASK_PANEL_MATERIAL_PATH = "/World/Looks/RTG_Task_Info_Panel"
TASK_PANEL_IMAGE_WIDTH = 1024
TASK_PANEL_IMAGE_HEIGHT = 768
TASK_PANEL_WIDTH_M = 7.6
TASK_PANEL_HEIGHT_M = 5.7
TASK_PANEL_CONTAINER_WIDTH_X_M = 1.306287
TASK_PANEL_ONE_TIER_VISUAL_HEIGHT_M = 0.904353
TASK_PANEL_WORLD_X = 12.0 + TASK_PANEL_WIDTH_M * 0.25 + TASK_PANEL_CONTAINER_WIDTH_X_M
TASK_PANEL_WORLD_Z = TASK_PANEL_HEIGHT_M * 0.5 + TASK_PANEL_ONE_TIER_VISUAL_HEIGHT_M
TASK_PANEL_Y_OFFSET_M = -2.2
TASK_PANEL_ROTATE_XYZ = (0.0, 0.0, 180.0)
TASK_PANEL_UV_COORDS = (
    (0.0, 0.0),
    (1.0, 0.0),
    (1.0, 1.0),
    (0.0, 1.0),
)
TASK_PANEL_DIAGNOSTIC_LOG_ENABLED = True
LIVE_INFO_PANEL_ROTATE_XYZ = (0.0, 0.0, 180.0)
LIVE_INFO_CAMERA_FACING_PANEL_ROOT_PATHS = (
    "/World/LiveHUD/RTG_DEVICE_LOCATION_PANEL",
    "/World/LiveHUD/RTG_CONTAINER_INFO_PANEL",
    RUNINFO_FLOATING_ROOT_PATH,
)
LIVE_INFO_CAMERA_FACING_PANEL_ROTATE_XYZ = {
    "/World/LiveHUD/RTG_DEVICE_LOCATION_PANEL": LIVE_INFO_PANEL_ROTATE_XYZ,
    "/World/LiveHUD/RTG_CONTAINER_INFO_PANEL": LIVE_INFO_PANEL_ROTATE_XYZ,
    RUNINFO_FLOATING_ROOT_PATH: RUNINFO_FLOATING_ROTATE_XYZ,
}
LIVE_INFO_CAMERA_FACING_UPDATE_INTERVAL_SECONDS = 1.0 / 30.0
TASK_ROUTE_INDICATOR_ROOT_PATH = "/World/LiveHUD/RTG_TASK_ROUTE_INDICATOR"
TASK_ROUTE_INDICATOR_LINE_PATH = f"{TASK_ROUTE_INDICATOR_ROOT_PATH}/Line"
TASK_ROUTE_INDICATOR_HEAD_PATH = f"{TASK_ROUTE_INDICATOR_ROOT_PATH}/Head"
TASK_ROUTE_INDICATOR_PULSE_PATH = f"{TASK_ROUTE_INDICATOR_ROOT_PATH}/Pulse"
TASK_ROUTE_INDICATOR_START_PATH = f"{TASK_ROUTE_INDICATOR_ROOT_PATH}/Start"
TASK_ROUTE_INDICATOR_END_PATH = f"{TASK_ROUTE_INDICATOR_ROOT_PATH}/End"
TASK_ROUTE_INDICATOR_TRUCK_PATH = f"{TASK_ROUTE_INDICATOR_ROOT_PATH}/Truck"
TASK_ROUTE_INDICATOR_THICKNESS_M = 0.08
TASK_ROUTE_INDICATOR_HEAD_LENGTH_M = 0.52
TASK_ROUTE_INDICATOR_HEAD_WIDTH_M = 0.34
TASK_ROUTE_INDICATOR_Z_LIFT_M = 0.45
TASK_ROUTE_INDICATOR_PULSE_SECONDS = 1.3
TASK_ROUTE_TRUCK_MARKER_Z_M = 1.0
TASK_PANEL_BAY_MAP_ROWS_LEFT_TO_RIGHT = (6, 5, 4, 3, 2, 1)
USER_SIDE_VIEW_CAMERA_PATH = (
    "/World/PortAndRTG/Omniverse_User_View/Omniverse_User_View_data"
)
USER_SIDE_VIEW_CAMERA_TRANSLATE = (
    89.73994204564764,
    58.58709433744814,
    -257.5675701451497,
)
USER_SIDE_VIEW_CAMERA_ROTATE_YXZ = (-181.93245, 520.10876, -543.2226)
USER_SIDE_VIEW_CAMERA_CENTER_OF_INTEREST = (0.0, 0.0, -87.21255104343727)
DEMO_SEQUENCE_ARTIFACT_ROOTS = (
    "/World/PortAndRTG/LIVE_internal_yard_truck_unified_40ft",
    "/World/LiveContainers/SequenceTruckLane",
)
DEMO_SEQUENCE_ARTIFACT_NAME_PREFIXES = (
    "LIVE_internal_lane_truck",
    "Task01_TruckLoadedBox",
    "Task02_LoadedTruckBox",
)
LIVE_TASK_STACK_PATH = "/World/LiveContainers/LiveTaskStack"
YARD_INVENTORY_ROOT_PATH = "/World/LiveContainers/YardInventory"
INVENTORY_MODE_LIVE = "live"
INVENTORY_MODE_SIMULATION = "simulation"
INVENTORY_SYNC_READY = "READY"
INVENTORY_SYNC_PENDING = "PENDING"
INVENTORY_WARNING_INTERVAL_SECONDS = 5.0
LIVE_TASK_TRUCK_PATH = "/World/LiveContainers/LiveTaskTruck"
# The live stack uses this box as the physical bay-centre reference whenever
# the pattern contains it.  Every box in a stack shares the same bay Y centre,
# so it represents the rendered 40 ft work line without relying on ground text.
RTG_CROSSBEAM_ALIGNMENT_CONTAINER_PATH = (
    f"{LIVE_TASK_STACK_PATH}/R02_T02_Container"
)
RTG_LOWER_PORTAL_CROSSBEAM_NAME = "lower_portal_crossbeam_x_5_8"
RTG_HOIST_PATH = (
    "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL/"
    "ANIM_CTRL_RTG_TROLLEY_TRAVEL/ANIM_CTRL_RTG_HOIST_VERTICAL"
)
RTG_ROPE_SYSTEM_PATH = (
    "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL/"
    "ANIM_CTRL_RTG_TROLLEY_TRAVEL/RTG_DYNAMIC_HOIST_ROPES"
)
LEGACY_LIVE_HOISTED_LOAD_PATH = f"{RTG_HOIST_PATH}/LIVE_TwinHoistedContainer"
LIVE_HOISTED_LOAD_PATH = "/World/LiveContainers/LiveTwinHoistedContainer"

TASK_STATE_IDLE = "IDLE"
TASK_STATE_GANTRY_TO_SOURCE = "GANTRY_TO_SOURCE"
TASK_STATE_TRUCK_APPROACH = "TRUCK_APPROACH"
TASK_STATE_TRUCK_WAITING = "TRUCK_WAITING"
TASK_STATE_READY_TO_PICK = "READY_TO_PICK"
TASK_STATE_CARRYING = "CARRYING"
TASK_STATE_PLACED = "PLACED"
TASK_STATE_TRUCK_EXITING = "TRUCK_EXITING"
TASK_VISUAL_STATES = frozenset(
    (
        TASK_STATE_IDLE,
        TASK_STATE_GANTRY_TO_SOURCE,
        TASK_STATE_TRUCK_APPROACH,
        TASK_STATE_TRUCK_WAITING,
        TASK_STATE_READY_TO_PICK,
        TASK_STATE_CARRYING,
        TASK_STATE_PLACED,
        TASK_STATE_TRUCK_EXITING,
        "COMPLETE",
        "FAULT",
    )
)

WORK_TYPE_LOAD = "LOAD"
WORK_TYPE_UNLOAD = "UNLOAD"
WORK_TYPE_SWITCH = "SWITCH"

TRUCK_APPROACH_SECONDS = 5.0
TRUCK_SETTLE_SECONDS = 1.5
TRUCK_EXIT_SECONDS = 4.0
# Move the task truck and its painted lane 0.90 m toward the container side.
# The companion trolley calibration puts the visible spreader at -3.75 m too.
TRUCK_OPERATING_LANE_CENTER_X_M = -3.75
# At the truck trolley stop, the spreader world centre is -3.75 m. Keep the
# trailer root on that same centreline instead of adding a camera-view offset.
TASK_TRUCK_VISUAL_X_OFFSET_M = 0.0
# 40 ft truck stops use the same physical box-centre mark for LOAD and UNLOAD.
# This keeps the inbound box and the outbound placed box on the identical bay
# line, independent of the hook model's local visual offset.
TASK_TRUCK_LONG_BOX_HOOK_OFFSET_Y_M = 0.45
TASK_TRUCK_FRONT_CONTAINER_CLEARANCE_Y_M = 0.25
TASK_TRUCK_LOADED_BOX_LOCAL_Y_M = 0.0
# Keep the blue task-trailer deck visibly narrower without reducing the box
# support area below the rendered 1.306 m container width.
TASK_TRUCK_TRAILER_WIDTH_X_M = 2.00
TASK_TRUCK_TRAILER_WHEEL_X_M = 0.88
PLC_CONTAINER_TIER_HEIGHT_M = 2.60
PLC_TRUCK_CONTACT_HOIST_M = 4.32
REFERENCE_CONTAINER_VISUAL_HEIGHT_Z_M = 0.904353
REFERENCE_TIER_GAP_Z_M = 0.035
SPREADER_20FT_CENTER_OFFSET_Y_M = -0.30
# Calibrated world-space container centres for the PLC trolley stops.  The RTG
# asset has an inverted/non-uniform X hierarchy, so transforming the rope
# points through that hierarchy and then writing the result under
# /World/LiveContainers can make an attached box travel in the opposite
# direction from the visible trolley.  These stops are the same row centres
# used by the bay-map renderer and keep the carried box on the spreader from
# the truck lane through rows 6..1.
TROLLEY_CARRIED_BOX_X_CALIBRATION = (
    (0.48, TRUCK_OPERATING_LANE_CENTER_X_M),
    (1.00, TRUCK_OPERATING_LANE_CENTER_X_M),
    (3.71, -1.0981016666666639),  # row 6
    (6.66, 0.4081853333333361),   # row 5
    (9.50, 1.9144723333333360),   # row 4
    (12.35, 3.4207593333333360),  # row 3
    (15.19, 4.9270463333333360),  # row 2
    (18.06, 6.4333333333333360),  # row 1
)
# Local attachment point on ANIM_CTRL_RTG_HOIST_VERTICAL. The carried box is
# authored at world scope so it keeps the source box dimensions instead of
# inheriting RTG_PRIMARY_DYNAMIC's non-uniform X scale.
HOISTED_LOAD_LOCAL_CENTER_X = 2.55
HOISTED_LOAD_LOCAL_CENTER_Y = -5.00
SPREADER_CONTAINER_CONTACT_LOCAL_Z = 2.55
STATUS_OVERLAY_UPDATE_SECONDS = 0.2
RUNINFO_OVERLAY_ENABLED = False
LEGACY_RUNINFO_WINDOW_TITLE = "RTG RunInfo HUD"
RUNINFO_OVERLAY_WIDTH = 320
RUNINFO_OVERLAY_HEIGHT = 58
RUNINFO_OVERLAY_MARGIN_RIGHT = 12
RUNINFO_OVERLAY_MARGIN_BOTTOM = 10
RUNINFO_FALLBACK_VIEWPORT_SIZE = (1809, 753)
RUNINFO_TEXT_IMAGE_WIDTH = RUNINFO_OVERLAY_WIDTH - 18
RUNINFO_TEXT_IMAGE_HEIGHT = 31
RUNINFO_TEXT_FONT_SIZE = 13
CJK_FONT_PATHS = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
)
RESOLVED_LOCKED_KEY = "_resolvedSpreaderLocked"
REMOTE_MANUAL_MODE_FLAG = "_SMART_PORT_RTG_REMOTE_MANUAL_ACTIVE"
REMOTE_MANUAL_STATUS_KEY = "_SMART_PORT_RTG_REMOTE_MANUAL_STATUS"


def remote_manual_mode_active() -> bool:
    """Return whether the local gamepad is the selected pose authority.

    The flag lives in ``builtins`` because Kit's Script Editor commonly runs
    project scripts from generated modules.  A process-global flag therefore
    works even when the receiver and the manual-control helper were executed
    from separate editor tabs.
    """

    return bool(getattr(builtins, REMOTE_MANUAL_MODE_FLAG, False))


def remote_manual_hud_snapshot() -> Optional[tuple[Dict[str, Any], Dict[str, Any]]]:
    """Return the current locally-authoritative pose for the Twin HUD."""

    snapshot = getattr(builtins, REMOTE_MANUAL_STATUS_KEY, None)
    if not isinstance(snapshot, dict):
        return None
    state = snapshot.get("state")
    applied = snapshot.get("applied")
    if not isinstance(state, dict) or not isinstance(applied, dict):
        return None
    return state, applied


def pause_timeline_for_live_control(log: Optional[Callable[[str], None]] = None) -> None:
    """Stop authored demo animation while live/manual control owns the scene."""

    try:
        import omni.timeline  # type: ignore

        timeline = omni.timeline.get_timeline_interface()
        is_playing = getattr(timeline, "is_playing", None)
        if callable(is_playing) and not is_playing():
            return
        pause = getattr(timeline, "pause", None)
        if callable(pause):
            pause()
            if log is not None:
                log("Paused Kit timeline playback for live RTG control")
            return
        stop = getattr(timeline, "stop", None)
        if callable(stop):
            stop()
            if log is not None:
                log("Stopped Kit timeline playback for live RTG control")
    except Exception as exc:  # noqa: BLE001 - live updates should still apply
        if log is not None:
            log(f"unable to pause Kit timeline playback: {exc}")


def disable_demo_sequence_artifacts(stage: Any) -> int:
    """Deactivate authored sequence-demo truck artifacts for live/manual modes."""

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
        for path in _top_level_paths(paths):
            prim = stage.GetPrimAtPath(path)
            if not prim:
                continue
            try:
                prim.SetActive(False)
            except Exception:
                UsdGeom.Imageable(prim).MakeInvisible()
            disabled += 1
    return disabled


def _top_level_paths(paths: set[str]) -> list[str]:
    selected: list[str] = []
    for path in sorted(paths, key=lambda item: (item.count("/"), item)):
        if any(path.startswith(f"{parent}/") for parent in selected):
            continue
        selected.append(path)
    return selected


def _hoisted_container_local_center_z(container_height: float) -> float:
    return SPREADER_CONTAINER_CONTACT_LOCAL_Z - float(container_height) * 0.5


def _task_container_height_m(task: Dict[str, Any]) -> float:
    return resolve_container_height_m(
        task.get("containerHeight"),
        task.get("containerType"),
    )


def _container_visual_height_z(container_height_m: float) -> float:
    return (
        REFERENCE_CONTAINER_VISUAL_HEIGHT_Z_M
        * float(container_height_m)
        / PLC_CONTAINER_TIER_HEIGHT_M
    )


def _task_bay_slot_heights_m(
    task: Dict[str, Any],
    pattern: str,
    source_row: Optional[int] = None,
    source_tier: Optional[int] = None,
) -> tuple[tuple[float, ...], ...]:
    """Resolve per-slot heights, ready for future yard-inventory snapshots.

    ``baySlotHeights`` may be a six-row list or a mapping keyed by
    ``R01_T01``. Missing slots keep the current 2.6 m engineering default.
    """

    raw = task.get("baySlotHeights")
    current_height_m = _task_container_height_m(task)
    rows = []
    for row in range(1, 7):
        tier_count = int(pattern[row - 1]) if len(pattern) == 6 else 0
        row_values = []
        for tier in range(1, tier_count + 1):
            value = None
            if isinstance(raw, (list, tuple)) and len(raw) >= row:
                raw_row = raw[row - 1]
                if isinstance(raw_row, (list, tuple)) and len(raw_row) >= tier:
                    value = raw_row[tier - 1]
            elif isinstance(raw, dict):
                for key in (
                    f"R{row:02d}_T{tier:02d}",
                    f"R{row:02d}T{tier:02d}",
                    f"{row}-{tier}",
                ):
                    if key in raw:
                        value = raw[key]
                        break

            if isinstance(value, dict):
                height_m = resolve_container_height_m(
                    value.get("height")
                    or value.get("containerHeight")
                    or value.get("cntr_height"),
                    value.get("type")
                    or value.get("containerType")
                    or value.get("cntr_type"),
                )
            else:
                height_m = _state_machine_module.parse_container_height_m(value)

            if height_m is None:
                if row == source_row and tier == source_tier:
                    height_m = current_height_m
                else:
                    height_m = PLC_CONTAINER_TIER_HEIGHT_M
            row_values.append(float(height_m))
        rows.append(tuple(row_values))
    return tuple(rows)


def _gantry_crossbeam_alignment_delta_y(
    container_center_y: float, crossbeam_center_y: float
) -> float:
    """Return the gantry Y correction that puts the beam on a box centreline."""

    return float(container_center_y) - float(crossbeam_center_y)


def _carried_box_world_x(trolley_m: Optional[float]) -> Optional[float]:
    """Map a PLC trolley reading to the matching rendered lane/row centre."""

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


def _spreader_center_offset_y(size_ft: Optional[int]) -> float:
    """Return the short-box-only bay-travel correction for the visible spreader."""

    return SPREADER_20FT_CENTER_OFFSET_Y_M if size_ft == 20 else 0.0


def _simulated_visual_phase(task: Dict[str, Any]) -> Optional[str]:
    """Return the simulator's authoritative visual phase when explicitly marked.

    The PLC-free simulator already executes the same formal operation state
    machine.  Running a second, independently timed machine in the scene
    receiver can lag behind the short simulator animation and leave the source
    box in ``TROLLEY_TO_SOURCE`` after the simulator has already unlocked it.
    Real PLC tasks do not carry ``stateSource=SIMULATION`` and continue to use
    the receiver's PLC-driven state machine.
    """

    if str(task.get("stateSource") or "").strip().upper() != "SIMULATION":
        return None
    phase = str(task.get("visualPhase") or "").strip().upper()
    return phase if phase in TASK_VISUAL_STATES else None


def _hoist_uses_simulated_load_bottom(task: Dict[str, Any]) -> bool:
    """Return whether a locked hoist value needs one-tier visual compensation."""

    return str(task.get("stateSource") or "").strip().upper() == "SIMULATION"


def _task_is_complete(task: Dict[str, Any]) -> bool:
    """Return whether a task has reached its terminal successful state."""

    if not task:
        return False
    if _simulated_visual_phase(task) == "COMPLETE":
        return True
    # Service marks a task COMPLETED when the runinfo log contains the
    # "本关任务完成" command matching the current active task ID.
    twin_state = str(task.get("_twin_operation_state") or "").strip().upper()
    if twin_state == "COMPLETED":
        return True
    operation_state = str(task.get("operationState") or "").strip().upper()
    if operation_state in {"COMPLETE", "COMPLETED", "DONE", "FINISHED"}:
        return True
    return _task_state_text(task, True) == "DONE"


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


def _pose_spreader_locked(pose: Dict[str, Any]) -> bool:
    """Return the resolved twist-lock state, with legacy lock-only fallback."""

    if RESOLVED_LOCKED_KEY in pose:
        return bool(pose[RESOLVED_LOCKED_KEY])
    return _as_int(pose.get("lockState"), 0) != 0


def _requested_spreader_size_ft(
    pose: Dict[str, Any],
    task: Dict[str, Any],
    inferred_task_size_ft: Optional[int] = None,
) -> Optional[int]:
    """Resolve 20/40 ft demand from PLC limits, then from task box length."""

    tool_20 = _as_int(pose.get("tool20f"), 0) != 0
    tool_40 = _as_int(pose.get("tool40f"), 0) != 0
    if tool_20 != tool_40:
        return 20 if tool_20 else 40
    if tool_20 and tool_40:
        return None

    size = _as_int(task.get("containerSize"), 0)
    if size in (20, 1):
        return 20
    if size in (40, 2, 45, 3):
        return 40
    return inferred_task_size_ft if inferred_task_size_ft in (20, 40) else None


def _spreader_size_limit_confirmed(
    pose: Dict[str, Any],
    requested_size_ft: Optional[int],
) -> bool:
    """Return whether PLC has confirmed the requested telescope endpoint."""

    tool_20 = _as_int(pose.get("tool20f"), 0) != 0
    tool_40 = _as_int(pose.get("tool40f"), 0) != 0
    if tool_20 == tool_40:
        return False
    return (requested_size_ft == 20 and tool_20) or (
        requested_size_ft == 40 and tool_40
    )


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


def _find_cjk_font_path() -> Optional[Path]:
    for path in CJK_FONT_PATHS:
        candidate = Path(path)
        try:
            if candidate.is_file():
                return candidate
        except Exception:
            continue
    return None


def _wrap_bitmap_text(draw: Any, text: str, font: Any, max_width: int, max_lines: int) -> list[str]:
    lines: list[str] = []
    for paragraph in str(text or "--").splitlines() or ["--"]:
        current = ""
        for char in paragraph:
            candidate = current + char
            try:
                width = draw.textbbox((0, 0), candidate, font=font)[2]
            except Exception:
                width = len(candidate) * RUNINFO_TEXT_FONT_SIZE
            if current and width > max_width:
                lines.append(current)
                current = char
                if len(lines) >= max_lines:
                    break
            else:
                current = candidate
        if len(lines) >= max_lines:
            break
        if current:
            lines.append(current)
        if len(lines) >= max_lines:
            break
    if not lines:
        lines = ["--"]
    if len(lines) == max_lines:
        last = lines[-1]
        while last:
            candidate = last + "..."
            try:
                width = draw.textbbox((0, 0), candidate, font=font)[2]
            except Exception:
                width = len(candidate) * RUNINFO_TEXT_FONT_SIZE
            if width <= max_width:
                lines[-1] = candidate
                break
            last = last[:-1]
    return lines[:max_lines]


def _render_runinfo_bitmap(text: str, width: int, height: int, output_path: Path) -> bool:
    """Render RUNINFO text to PNG so CJK does not depend on omni.ui fonts."""

    font_path = _find_cjk_font_path()
    if font_path is None:
        return False
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore

        image = Image.new("RGBA", (int(width), int(height)), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(font_path), RUNINFO_TEXT_FONT_SIZE)
        line_height = RUNINFO_TEXT_FONT_SIZE + 2
        lines = _wrap_bitmap_text(
            draw,
            text,
            font,
            max(1, int(width) - 4),
            max(1, int(height) // line_height),
        )
        y = 0
        for line in lines:
            draw.text((2, y), line, fill=(238, 221, 170, 255), font=font)
            y += line_height
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path)
        return True
    except Exception:
        return False


def _format_runinfo_time_text(value: Any) -> str:
    text = _fmt_text(value, "").strip()
    if not text:
        return RUNINFO_FLOATING_DEFAULT_CLOCK_TEXT
    numeric_value = _as_float(text, None)
    if numeric_value is not None:
        timestamp = (
            numeric_value / 1000.0
            if numeric_value > 10_000_000_000
            else numeric_value
        )
        try:
            return (
                datetime.fromtimestamp(timestamp)
                + timedelta(hours=RUNINFO_FLOATING_TIME_OFFSET_HOURS)
            ).strftime(
                RUNINFO_FLOATING_CLOCK_FORMAT
            )
        except (OSError, OverflowError, ValueError):
            return text
    normalized = text.replace("T", " ").replace("/", "-")
    if normalized.endswith("Z"):
        normalized = normalized[:-1]
    if "." in normalized:
        normalized = normalized.split(".", 1)[0]
    try:
        return (
            datetime.fromisoformat(normalized)
            + timedelta(hours=RUNINFO_FLOATING_TIME_OFFSET_HOURS)
        ).strftime(
            RUNINFO_FLOATING_CLOCK_FORMAT
        )
    except ValueError:
        return text


def _runinfo_time_text(runinfo: Dict[str, Any]) -> str:
    for key in ("timespan", "timeSpan", "msg_timespan", "msgTimeSpan"):
        value = runinfo.get(key)
        if value is not None:
            return _format_runinfo_time_text(value)
    return _format_runinfo_time_text(runinfo.get("receivedAtUtc"))


def _dismiss_legacy_runinfo_window() -> None:
    """Hide the pre-3D RunInfo window left behind by an older script run."""

    try:
        import omni.ui as ui  # type: ignore

        workspace = getattr(ui, "Workspace", None)
        get_window = getattr(workspace, "get_window", None)
        if not callable(get_window):
            return
        window = get_window(LEGACY_RUNINFO_WINDOW_TITLE)
        if window is not None:
            try:
                window.visible = False
            except Exception:
                pass
    except Exception:
        return


def _is_descendant_path(path: str, root_path: str) -> bool:
    return path == root_path or path.startswith(f"{root_path}/")


def _is_runinfo_cleanup_path(path: str, name: str) -> bool:
    lowered = f"{path}/{name}".lower()
    return any(token in lowered for token in RUNINFO_CLEANUP_PATH_TOKENS)


def _remove_explicit_stale_runinfo_billboards(stage: Any) -> int:
    stale: list[Any] = []
    for path in RUNINFO_EXPLICIT_STALE_ROOT_PATHS:
        try:
            prim = stage.GetPrimAtPath(path)
            if prim:
                stale.append(prim.GetPath())
        except Exception:
            continue
    if not stale:
        return 0

    try:
        from pxr import Usd
    except Exception:
        return 0

    removed = 0
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for path_obj in stale:
            try:
                stage.RemovePrim(path_obj)
                removed += 1
            except Exception:
                continue
    return removed


def _remove_runinfo_billboards(stage: Any, *, keep_current: bool) -> int:
    """Delete 3D RunInfo billboards from the stage."""

    try:
        from pxr import Usd
    except Exception:
        return 0

    keep_roots = (
        RUNINFO_FLOATING_ROOT_PATH,
        RUNINFO_FLOATING_MATERIAL_PATH,
    )
    stale: list[tuple[str, Any]] = []

    def add_prim(prim: Any) -> None:
        if not prim:
            return
        try:
            path_obj = prim.GetPath()
            path = str(path_obj)
            name = str(prim.GetName())
        except Exception:
            return
        if keep_current and any(_is_descendant_path(path, root) for root in keep_roots):
            return
        if _is_runinfo_cleanup_path(path, name):
            stale.append((path, path_obj))

    try:
        traverse = getattr(stage, "TraverseAll", None)
        if not callable(traverse):
            traverse = getattr(stage, "Traverse", None)
        if callable(traverse):
            for prim in list(traverse()):
                add_prim(prim)
    except Exception:
        pass

    explicit_paths = (
        *RUNINFO_EXPLICIT_STALE_ROOT_PATHS,
        RUNINFO_FLOATING_ROOT_PATH,
        RUNINFO_FLOATING_MATERIAL_PATH,
    )
    for explicit_path in explicit_paths:
        try:
            add_prim(stage.GetPrimAtPath(explicit_path))
        except Exception:
            pass

    unique: dict[str, Any] = {}
    for path, path_obj in stale:
        unique.setdefault(path, path_obj)

    root_paths: list[tuple[str, Any]] = []
    for path, path_obj in sorted(unique.items(), key=lambda item: item[0].count("/")):
        if any(_is_descendant_path(path, existing) for existing, _ in root_paths):
            continue
        root_paths.append((path, path_obj))

    removed = 0
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for _path, path_obj in root_paths:
            try:
                stage.RemovePrim(path_obj)
                removed += 1
            except Exception:
                continue
    return removed


def _remove_stale_runinfo_billboards(stage: Any) -> int:
    return _remove_runinfo_billboards(stage, keep_current=True)


def _current_omniverse_stage() -> Any:
    try:
        import omni.usd  # type: ignore

        return omni.usd.get_context().get_stage()
    except Exception:
        return None


def cleanup_runinfo_billboards() -> int:
    """Immediately delete all 3D RunInfo billboards in the current stage."""

    stage = _current_omniverse_stage()
    if stage is None:
        return 0
    return _remove_runinfo_billboards(stage, keep_current=False)


def _runinfo_level_key(runinfo: Optional[Dict[str, Any]]) -> str:
    """Map the upstream runinfo msg_type to one of the three visual panels."""

    if not isinstance(runinfo, dict):
        return RUNINFO_LEVEL_PROMPT
    raw_type = runinfo.get("msgType")
    if raw_type is None:
        raw_type = runinfo.get("msg_type")
    try:
        msg_type = int(raw_type)
    except (TypeError, ValueError):
        msg_type = 1
    return {
        1: RUNINFO_LEVEL_PROMPT,
        2: RUNINFO_LEVEL_ALERT,
        3: RUNINFO_LEVEL_EXCEPTION,
    }.get(msg_type, RUNINFO_LEVEL_PROMPT)


def _render_floating_runinfo_bitmap(
    text: str,
    output_path: Path,
    clock_text: Optional[str] = None,
    level_key: str = RUNINFO_LEVEL_PROMPT,
) -> bool:
    """Render one of the three transparent 3D RUNINFO panel variants."""

    font_path = _find_cjk_font_path()
    if font_path is None:
        return False
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont  # type: ignore

        width = RUNINFO_FLOATING_IMAGE_WIDTH
        height = RUNINFO_FLOATING_IMAGE_HEIGHT
        style = RUNINFO_LEVEL_STYLES.get(
            level_key,
            RUNINFO_LEVEL_STYLES[RUNINFO_LEVEL_PROMPT],
        )
        scale = max(1.0, width / 768.0)
        margin = int(22 * scale)
        inner_margin = int(34 * scale)
        corner_radius = int(30 * scale)
        inner_radius = int(24 * scale)
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        panel_box = (margin, margin, width - margin, height - margin)
        glow_draw.rounded_rectangle(
            panel_box,
            radius=corner_radius,
            fill=style["glow"],
            outline=style["outline"],
            width=int(5 * scale),
        )
        image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(int(8 * scale))))

        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            panel_box,
            radius=corner_radius,
            fill=style["fill"],
            outline=style["outline"],
            width=int(3 * scale),
        )
        draw.rounded_rectangle(
            (inner_margin, inner_margin, width - inner_margin, height - inner_margin),
            radius=inner_radius,
            outline=style["inner"],
            width=int(2 * scale),
        )
        title_font = ImageFont.truetype(str(font_path), RUNINFO_FLOATING_TITLE_FONT_SIZE)
        clock_font = ImageFont.truetype(str(font_path), RUNINFO_FLOATING_CLOCK_FONT_SIZE)
        body_font = ImageFont.truetype(str(font_path), RUNINFO_FLOATING_TEXT_FONT_SIZE)
        draw.text(
            (int(54 * scale), 42),
            style["title"],
            fill=style["accent"],
            font=title_font,
        )
        title_box = draw.textbbox(
            (int(54 * scale), 42),
            style["title"],
            font=title_font,
        )
        draw.ellipse(
            (
                title_box[2] + int(24 * scale),
                50,
                title_box[2] + int(40 * scale),
                66,
            ),
            fill=style["accent"],
        )
        badge_font = ImageFont.truetype(str(font_path), 32)
        badge_text = style["badge"]
        badge_box = draw.textbbox((0, 0), badge_text, font=badge_font)
        badge_width = badge_box[2] - badge_box[0] + int(42 * scale)
        badge_x2 = width - int(54 * scale)
        badge_x1 = badge_x2 - badge_width
        draw.rounded_rectangle(
            (badge_x1, 31, badge_x2, 78),
            radius=int(18 * scale),
            fill=(style["accent"][0], style["accent"][1], style["accent"][2], 150),
            outline=style["accent"],
            width=max(2, int(2 * scale)),
        )
        draw.text(
            (
                badge_x1 + int(21 * scale),
                37,
            ),
            badge_text,
            fill=style["badge_text"],
            font=badge_font,
        )
        clock_box = draw.textbbox((0, 0), clock_text or RUNINFO_FLOATING_DEFAULT_CLOCK_TEXT, font=clock_font)
        clock_width = clock_box[2] - clock_box[0]
        draw.text(
            (badge_x1 - clock_width - int(28 * scale), 39),
            clock_text or RUNINFO_FLOATING_DEFAULT_CLOCK_TEXT,
            fill=(245, 252, 255, 245),
            font=clock_font,
        )

        divider_y = 104
        draw.line(
            (int(54 * scale), divider_y, width - int(54 * scale), divider_y),
            fill=style["inner"],
            width=max(2, int(2 * scale)),
        )
        if level_key == RUNINFO_LEVEL_EXCEPTION:
            draw.rounded_rectangle(
                (
                    int(54 * scale),
                    divider_y + 18,
                    int(68 * scale),
                    height - 22,
                ),
                radius=int(6 * scale),
                fill=style["accent"],
            )
        elif level_key == RUNINFO_LEVEL_ALERT:
            draw.ellipse(
                (
                    int(54 * scale),
                    divider_y + 26,
                    int(72 * scale),
                    divider_y + 44,
                ),
                fill=style["accent"],
            )

        body_x = int(88 * scale) if level_key != RUNINFO_LEVEL_PROMPT else int(54 * scale)
        lines = _wrap_bitmap_text(
            draw,
            text.strip() or "--",
            body_font,
            width - body_x - int(54 * scale),
            RUNINFO_FLOATING_TEXT_MAX_LINES,
        )
        line_gap = int(RUNINFO_FLOATING_TEXT_LINE_GAP * scale)
        text_area_top = divider_y + 22
        text_area_bottom = height - int(38 * scale)
        line_heights = [
            draw.textbbox((0, 0), line or " ", font=body_font)[3]
            - draw.textbbox((0, 0), line or " ", font=body_font)[1]
            for line in lines
        ]
        line_height = max(line_heights or [RUNINFO_FLOATING_TEXT_FONT_SIZE])
        total_text_height = (
            len(lines) * line_height
            + max(0, len(lines) - 1) * line_gap
        )
        y = max(
            text_area_top,
            int((text_area_top + text_area_bottom - total_text_height) * 0.5),
        )
        for line in lines:
            draw.text(
                (body_x, y),
                line,
                fill=style["body"],
                font=body_font,
            )
            y += line_height + line_gap

        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path)
        return True
    except Exception:
        return False


def _task_panel_work_type_text(value: Any) -> str:
    text = _fmt_text(value, "").upper()
    return {
        WORK_TYPE_LOAD: "装箱",
        WORK_TYPE_UNLOAD: "卸箱",
        WORK_TYPE_SWITCH: "翻箱",
        "REHANDLE": "翻箱",
    }.get(text, text or "--")


def _task_panel_state_text(task: Dict[str, Any]) -> str:
    if not task:
        return "--"
    state = _task_state_text(task, True).upper()
    if state in {"IDLE", "--"}:
        return "--"
    if state in {"DONE", "COMPLETE", "COMPLETED", "FINISHED"}:
        return "已完成"
    if state in {"FAULT", "ERROR", "ALARM"}:
        return "异常"
    return "任务中"


def _task_panel_value_rows(task: Dict[str, Any]) -> list[tuple[str, str]]:
    container_type = _fmt_text(task.get("containerType"))
    container_height = _fmt_text(task.get("containerHeight"))
    type_height = " / ".join(
        part for part in (container_type, container_height) if part and part != "--"
    ) or "--"
    return [
        ("任务ID", _fmt_text(task.get("taskId"))),
        ("任务类型", _task_panel_work_type_text(task.get("workType") or task.get("taskType"))),
        ("箱号", _fmt_text(task.get("containerNo"))),
        ("箱型 / 箱高", type_height),
        ("吊具位置", _fmt_text(task.get("spreaderPos"))),
        ("车辆类型", _fmt_text(task.get("carType"))),
        ("车号", _fmt_text(task.get("carNo"))),
    ]


def _task_route_endpoints(
    task: Dict[str, Any],
) -> Optional[tuple[tuple[str, int, int], tuple[str, int, int]]]:
    work_type = _task_work_type(task)
    source = OmniverseRtgTwinDriver._parse_task_position_details(
        task.get("startPos") or task.get("sourcePos")
    )
    target = OmniverseRtgTwinDriver._parse_task_position_details(
        task.get("endPos") or task.get("targetPos")
    )
    source_slot = None
    target_slot = None
    if source is not None:
        _source_field, _source_bay, source_row, source_tier = source
        if source_row is not None and source_tier is not None and source_row > 0 and source_tier > 0:
            source_slot = ("slot", source_row, source_tier)
    if target is not None:
        _target_field, _target_bay, target_row, target_tier = target
        if target_row is not None and target_tier is not None and target_row > 0 and target_tier > 0:
            target_slot = ("slot", target_row, target_tier)

    if work_type == WORK_TYPE_LOAD:
        if target_slot is None:
            return None
        return ("truck", 0, 0), target_slot
    if work_type == WORK_TYPE_UNLOAD:
        if source_slot is None:
            return None
        return source_slot, ("truck", 0, 0)
    if work_type == WORK_TYPE_SWITCH:
        if source_slot is None or target_slot is None:
            return None
        return source_slot, target_slot

    if source_slot is None or target_slot is None:
        return None
    return source_slot, target_slot


def _draw_fitted_text(
    draw: Any,
    xy: tuple[int, int],
    text: str,
    font: Any,
    fill: tuple[int, int, int, int],
    *,
    max_width: int,
    anchor: Optional[str] = None,
) -> None:
    value = str(text or "--")
    while len(value) > 2 and draw.textbbox((0, 0), value, font=font)[2] > max_width:
        value = value[:-2] + "..."
    if anchor:
        draw.text(xy, value, fill=fill, font=font, anchor=anchor)
    else:
        draw.text(xy, value, fill=fill, font=font)


def _render_task_info_panel_bitmap(task: Dict[str, Any], output_path: Path) -> bool:
    font_path = _find_cjk_font_path()
    if font_path is None:
        return False
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont  # type: ignore

        width = TASK_PANEL_IMAGE_WIDTH
        height = TASK_PANEL_IMAGE_HEIGHT
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        panel_box = (4, 4, width - 5, height - 5)
        glow_draw.rounded_rectangle(
            panel_box,
            radius=16,
            fill=(82, 175, 216, 34),
            outline=(128, 210, 236, 154),
            width=4,
        )
        image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(10)))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            panel_box,
            radius=12,
            fill=(10, 38, 58, 248),
            outline=(130, 207, 232, 176),
            width=2,
        )
        draw.line((18, 18, width - 19, 18), fill=(255, 156, 22, 234), width=5)
        draw.rectangle(
            (14, 14, width - 15, height - 15),
            outline=(142, 219, 238, 48),
        )
        draw.polygon(
            [
                (width - 90, height - 4),
                (width - 4, height - 90),
                (width - 4, height - 4),
            ],
            fill=(153, 206, 218, 112),
        )

        title_font = ImageFont.truetype(str(font_path), 42)
        badge_font = ImageFont.truetype(str(font_path), 26)
        label_font = ImageFont.truetype(str(font_path), 28)
        value_font = ImageFont.truetype(str(font_path), 31)
        route_label_font = ImageFont.truetype(str(font_path), 24)
        route_value_font = ImageFont.truetype(str(font_path), 36)
        map_label_font = ImageFont.truetype(str(font_path), 24)

        draw.rectangle((0, 0, width, 104), fill=(22, 68, 91, 250))
        draw.text((40, 31), "作业信息", fill=(248, 252, 255, 255), font=title_font)
        status = _task_panel_state_text(task)
        status_bbox = draw.textbbox((0, 0), status, font=badge_font)
        badge_w = status_bbox[2] - status_bbox[0] + 44
        badge_box = (
            width - badge_w - 40,
            36,
            width - 40,
            76,
        )
        draw.rounded_rectangle(
            badge_box,
            radius=20,
            fill=(28, 94, 78, 228),
            outline=(91, 238, 178, 138),
        )
        draw.text((badge_box[0] + 22, 41), status, fill=(91, 238, 178, 255), font=badge_font)

        start_pos = _fmt_text(task.get("startPos") or task.get("sourcePos"))
        end_pos = _fmt_text(task.get("endPos") or task.get("targetPos"))
        route_y = 132
        box_w = 360
        box_h = 104
        route_left = 40
        route_gap = 54
        for x, label, value in (
            (route_left, "起始位置", start_pos),
            (route_left + box_w + route_gap + 54, "目标位置", end_pos),
        ):
            draw.rectangle(
                (x, route_y, x + box_w, route_y + box_h),
                fill=(29, 75, 96, 245),
                outline=(132, 214, 236, 120),
            )
            draw.text((x + 24, route_y + 16), label, fill=(198, 221, 232, 255), font=route_label_font)
            _draw_fitted_text(
                draw,
                (x + 24, route_y + 54),
                value,
                route_value_font,
                (196, 244, 255, 255),
                max_width=box_w - 48,
            )
        draw.text(
            (route_left + box_w + 24, route_y + 33),
            "→",
            fill=(255, 190, 78, 255),
            font=title_font,
        )

        y = 276
        for label, value in _task_panel_value_rows(task):
            draw.text((44, y), label, fill=(198, 221, 232, 255), font=label_font)
            color = (88, 222, 248, 255) if label == "箱号" else (246, 252, 255, 255)
            _draw_fitted_text(
                draw,
                (492, y - 2),
                value,
                value_font,
                color,
                max_width=300,
                anchor="ra",
            )
            y += 55

        pattern = str(task.get("bayMap") or "").strip()
        map_left = 568
        map_top = 276
        draw.text((map_left, map_top), "贝位图", fill=(198, 221, 232, 255), font=label_font)
        _draw_fitted_text(
            draw,
            (width - 44, map_top - 2),
            pattern or "--",
            value_font,
            (246, 252, 255, 255),
            max_width=220,
            anchor="ra",
        )
        if re.fullmatch(r"\d{6}", pattern):
            cell_w = 54
            cell_h = 24
            gap = 16
            lane_left = map_left + 6
            lane_w = 42
            left = lane_left + lane_w + gap
            bottom = height - 76
            draw.rectangle(
                (lane_left, bottom - 78, lane_left + lane_w, bottom),
                fill=(255, 190, 78, 132),
                outline=(255, 190, 78, 132),
            )
            draw.text((lane_left - 4, bottom + 12), "车道", fill=(255, 190, 78, 232), font=map_label_font)
            target_row = None
            parsed = OmniverseRtgTwinDriver._parse_task_position_details(
                task.get("endPos") or task.get("targetPos") or task.get("startPos") or task.get("sourcePos")
            )
            if parsed is not None:
                target_row = parsed[2]
            for column_index, row_index in enumerate(TASK_PANEL_BAY_MAP_ROWS_LEFT_TO_RIGHT):
                height_text = pattern[row_index - 1]
                x = left + column_index * (cell_w + gap)
                stack_height = max(0, int(height_text))
                draw.text((x + 16, bottom + 12), f"R{row_index}", fill=(198, 221, 232, 230), font=map_label_font)
                for tier in range(1, stack_height + 1):
                    y0 = bottom - tier * (cell_h + 9)
                    is_target = target_row == row_index and tier == stack_height
                    fill = (255, 190, 78, 210) if is_target else (63, 132, 154, 205)
                    outline = (255, 190, 78, 232) if is_target else (150, 224, 240, 118)
                    draw.rectangle((x, y0, x + cell_w, y0 + cell_h), fill=fill, outline=outline)
        else:
            draw.text((map_left + 6, map_top + 54), "--", fill=(246, 252, 255, 255), font=value_font)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path)
        return True
    except Exception:
        return False


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


def _task_trailer_layout(
    container_length_y: float,
    container_size_ft: int,
) -> tuple[float, float, float]:
    """Return trailer length, centre and cab centre for a task truck.

    A 20 ft box rides at the front of a 40 ft chassis, immediately behind the
    tractor. Its local centre remains at zero so the established RTG alignment
    remains valid; the spare deck extends rearward.
    """

    minimum_trailer_length = 5.8
    if container_size_ft == 20:
        minimum_trailer_length = container_length_y * 2.0 + 0.55
    trailer_length_y = max(container_length_y + 0.55, minimum_trailer_length)
    trailer_center_y = (
        (trailer_length_y - container_length_y) * 0.5
        - TASK_TRUCK_FRONT_CONTAINER_CLEARANCE_Y_M
    )
    cab_center_y = trailer_center_y - trailer_length_y * 0.5 - 0.95
    return trailer_length_y, trailer_center_y, cab_center_y


def _task_truck_vertical_layout(
    container_height_z: float,
    tier_gap_z: float,
) -> tuple[float, float]:
    """Return trailer and box local Z centres for the 4.32 m PLC contact point."""

    reference_visual_tier_pitch = (
        REFERENCE_CONTAINER_VISUAL_HEIGHT_Z_M + float(tier_gap_z)
    )
    box_bottom_z = (
        PLC_TRUCK_CONTACT_HOIST_M - PLC_CONTAINER_TIER_HEIGHT_M
    ) * reference_visual_tier_pitch / PLC_CONTAINER_TIER_HEIGHT_M
    trailer_height_z = 0.18
    return (
        box_bottom_z - trailer_height_z * 0.5,
        box_bottom_z + float(container_height_z) * 0.5,
    )


def _task_truck_stop_y_for_box_center(
    box_bay_center_y: float,
    container_size_ft: int,
    work_type: str,
) -> float:
    """Return the task truck root Y for its loaded-container centre.

    The loaded box is authored at local Y=0 under the truck root. For 40 ft
    LOAD and UNLOAD operations, parking the root directly at the bay centre
    makes the truck-box centreline and target-box centreline overlap exactly.
    """

    if container_size_ft == 40 and work_type in (WORK_TYPE_LOAD, WORK_TYPE_UNLOAD):
        return float(box_bay_center_y)

    return float(box_bay_center_y) + (
        TASK_TRUCK_LONG_BOX_HOOK_OFFSET_Y_M
        + _spreader_center_offset_y(container_size_ft)
    )


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
        _dismiss_legacy_runinfo_window()
        self._window = None
        self._runinfo_window = None
        self._labels: Dict[str, Any] = {}
        self._last_values: Dict[str, str] = {}
        self._runinfo_label: Any = None
        self._runinfo_image: Any = None
        self._runinfo_bitmap_seq = 0
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
            "payload": _fmt_text(state.get("manualPayload")),
            "field": _fmt_text(pose.get("currentField")),
            "bay": _fmt_int(pose.get("currentBay")),
            "bay_id": (
                f"{_fmt_text(pose.get('currentField'))}/{_fmt_int(pose.get('currentBay')).zfill(3)}"
                if pose.get("currentField") and _as_int(pose.get("currentBay"), 0) > 0
                else "--"
            ),
            "control_mode": _fmt_text(applied.get("controlMode") or "LIVE"),
            "gantry": _fmt_number(gantry_m, 2, " m"),
            "trolley": _fmt_number(applied.get("trolleyMeter") or pose.get("trolleyMeter"), 2, " m"),
            "hoist": _fmt_number(applied.get("hoistMeter") or pose.get("hoistMeter"), 2, " m"),
            "spreader_size": _fmt_number(applied.get("spreaderSizeFt"), 0, " ft"),
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
        # Runinfo messages update independently so completion/operation logs
        # remain visible even when the task payload is unchanged.
        runinfo_text = RUNINFO_FLOATING_DEFAULT_TEXT
        runinfo = state.get("runinfo")
        if isinstance(runinfo, dict):
            raw_text = str(runinfo.get("message") or "")
            runinfo_text = raw_text.strip() or RUNINFO_FLOATING_DEFAULT_TEXT
            if self._last_values.get("runinfo") != runinfo_text:
                self._last_values["runinfo"] = runinfo_text
                if RUNINFO_OVERLAY_ENABLED:
                    print(f"[RTG Twin Receiver] runinfo HUD update: {runinfo_text}")
                    self._set_runinfo_text(runinfo_text)
        else:
            # Log once per connection whether the service sends runinfo.
            if not getattr(self, "_runinfo_probe_logged", False):
                setattr(self, "_runinfo_probe_logged", True)
                print(
                    "[RTG Twin Receiver] runinfo field is "
                    f"{type(runinfo).__name__} — "
                    "service may need a restart to include runinfo data"
                )
                # Log once so the operator can confirm runinfo is flowing.
                if runinfo_text != RUNINFO_FLOATING_DEFAULT_TEXT:
                    print(f"[RTG Twin Receiver] runinfo received: {runinfo_text}")
        if RUNINFO_OVERLAY_ENABLED:
            self._position_runinfo_window()

    def destroy(self) -> None:
        if self._window is not None:
            try:
                self._window.visible = False
            except Exception:
                pass
            self._window = None
        if self._runinfo_window is not None:
            try:
                self._runinfo_window.visible = False
            except Exception:
                pass
            self._runinfo_window = None
        self._labels.clear()
        self._runinfo_label = None
        self._runinfo_image = None
        _dismiss_legacy_runinfo_window()

    def _build(self) -> None:
        import omni.ui as ui  # type: ignore

        # Kit's default UI font lacks CJK glyphs in some app builds.  Prefer
        # FontRegistry and fall back to a direct Windows font file path.
        _cjk_font = ""
        _cjk_paths = (
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
        )
        try:
            _font_registry = getattr(ui, "FontRegistry", None)
        except Exception:
            _font_registry = None
        if _font_registry is not None:
            for _path in _cjk_paths:
                try:
                    _family = _font_registry.add_font(_path)
                    if _family:
                        _cjk_font = str(_family)
                        break
                except Exception:
                    continue
        if not _cjk_font:
            for _path in _cjk_paths:
                try:
                    if Path(_path).is_file():
                        _cjk_font = _path
                        break
                except Exception:
                    continue

        def _mk_cjk_style(base: Dict[str, Any]) -> Dict[str, Any]:
            result = dict(base)
            if _cjk_font:
                result["font"] = _cjk_font
            return result

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
            height=630,
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
        title_style = _mk_cjk_style({"color": 0xFFFFFFFF, "font_size": 14})
        name_style = _mk_cjk_style({"color": 0xFF9AAABA, "font_size": 12})
        value_style = _mk_cjk_style({"color": 0xFFFFFFFF, "font_size": 13})
        accent_style = _mk_cjk_style({"color": 0xFF22DFFF, "font_size": 18})

        with self._window.frame:
            with ui.VStack(spacing=6, style={"background_color": 0xAA02111F}):
                with ui.Frame(style=panel_style, height=290):
                    with ui.VStack(spacing=5):
                        ui.Spacer(height=6)
                        ui.Label("DEVICE", style=title_style, height=20)
                        self._row(ui, "Device", "device_id", name_style, accent_style, height=28)
                        self._row(ui, "Payload", "payload", name_style, value_style)
                        self._row(ui, "Field", "field", name_style, value_style)
                        self._row(ui, "Bay", "bay", name_style, value_style)
                        self._row(ui, "Bay ID", "bay_id", name_style, accent_style)
                        self._row(ui, "Mode", "control_mode", name_style, value_style)
                        self._row(ui, "Gantry", "gantry", name_style, value_style)
                        self._row(ui, "Trolley", "trolley", name_style, value_style)
                        self._row(ui, "Hoist", "hoist", name_style, value_style)
                        self._row(ui, "Tool Size", "spreader_size", name_style, value_style)
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

        if RUNINFO_OVERLAY_ENABLED:
            self._runinfo_window = ui.Window(
                "RTG RunInfo HUD",
                width=RUNINFO_OVERLAY_WIDTH,
                height=RUNINFO_OVERLAY_HEIGHT,
                visible=True,
                flags=flags,
            )
            self._position_runinfo_window()
            runinfo_panel_style = {
                "background_color": 0xD6061827,
                "border_color": 0xFFFF4545,
                "border_width": 1,
                "border_radius": 3,
            }
            info_style = _mk_cjk_style({"color": 0xFFEEDDAA, "font_size": 12})
            with self._runinfo_window.frame:
                with ui.Frame(style=runinfo_panel_style):
                    with ui.VStack(spacing=2):
                        ui.Spacer(height=3)
                        ui.Label("RUNINFO", style=title_style, height=16)
                        self._runinfo_image = ui.Image(
                            "",
                            width=RUNINFO_TEXT_IMAGE_WIDTH,
                            height=RUNINFO_TEXT_IMAGE_HEIGHT,
                            fill_policy=ui.FillPolicy.STRETCH,
                        )
                        self._runinfo_label = ui.Label(
                            RUNINFO_FLOATING_DEFAULT_TEXT,
                            width=1,
                            height=1,
                            word_wrap=True,
                            alignment=ui.Alignment.LEFT,
                            style=info_style,
                        )
                        ui.Spacer(height=2)
            self._set_runinfo_text(RUNINFO_FLOATING_DEFAULT_TEXT)

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

    def _set_runinfo_text(self, text: str) -> None:
        if not RUNINFO_OVERLAY_ENABLED:
            return
        if self._runinfo_image is not None:
            self._runinfo_bitmap_seq += 1
            path = (
                Path(tempfile.gettempdir())
                / f"smart_port_rtg_runinfo_{os.getpid()}_{self._runinfo_bitmap_seq}.png"
            )
            if _render_runinfo_bitmap(
                text,
                RUNINFO_TEXT_IMAGE_WIDTH,
                RUNINFO_TEXT_IMAGE_HEIGHT,
                path,
            ):
                try:
                    self._runinfo_image.source_url = str(path)
                    return
                except Exception:
                    pass
        if self._runinfo_label is None:
            return
        try:
            self._runinfo_label.text = text
        except Exception:
            try:
                self._runinfo_label.model.set_value(text)
            except Exception:
                pass

    def _position_runinfo_window(self) -> None:
        if not RUNINFO_OVERLAY_ENABLED:
            return
        if self._runinfo_window is None:
            return
        width, height = self._active_viewport_size()
        position_x = max(0, width - RUNINFO_OVERLAY_WIDTH - RUNINFO_OVERLAY_MARGIN_RIGHT)
        position_y = max(0, height - RUNINFO_OVERLAY_HEIGHT - RUNINFO_OVERLAY_MARGIN_BOTTOM)
        for attr, value in (("position_x", position_x), ("position_y", position_y)):
            try:
                setattr(self._runinfo_window, attr, value)
            except Exception:
                pass

    def _active_viewport_size(self) -> tuple[int, int]:
        try:
            from omni.kit.viewport.utility import get_active_viewport  # type: ignore

            viewport = get_active_viewport()
            resolution = getattr(viewport, "resolution", None)
            parsed = self._parse_size(resolution)
            if parsed is not None:
                return parsed
        except Exception:
            pass
        return RUNINFO_FALLBACK_VIEWPORT_SIZE

    @staticmethod
    def _parse_size(value: Any) -> Optional[tuple[int, int]]:
        if value is None:
            return None
        try:
            if isinstance(value, (tuple, list)) and len(value) >= 2:
                width, height = int(value[0]), int(value[1])
            elif hasattr(value, "x") and hasattr(value, "y"):
                width, height = int(value.x), int(value.y)
            elif hasattr(value, "width") and hasattr(value, "height"):
                width, height = int(value.width), int(value.height)
            else:
                return None
        except Exception:
            return None
        if width <= 0 or height <= 0:
            return None
        return width, height

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
        if not RECEIVER_STATE_LOG_ENABLED:
            return
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

    def __init__(
        self,
        inventory_path: Optional[Path] = None,
        *,
        inventory_mode: str = INVENTORY_MODE_LIVE,
        inventory_autosave: bool = True,
    ) -> None:
        mode = str(inventory_mode or "").strip().lower()
        if mode not in {INVENTORY_MODE_LIVE, INVENTORY_MODE_SIMULATION}:
            raise ValueError("inventory_mode must be 'live' or 'simulation'")
        mode_environment_name = (
            "SMART_PORT_SIM_INVENTORY_PATH"
            if mode == INVENTORY_MODE_SIMULATION
            else "SMART_PORT_LIVE_INVENTORY_PATH"
        )
        configured_inventory_path = os.environ.get(mode_environment_name)
        default_inventory_path = (
            DEFAULT_SIM_INVENTORY_PATH
            if mode == INVENTORY_MODE_SIMULATION
            else DEFAULT_LIVE_INVENTORY_PATH
        )
        self._inventory_mode = mode
        self._inventory_path = Path(
            inventory_path
            or configured_inventory_path
            or default_inventory_path
        )
        self._inventory_load_error: Optional[str] = None
        try:
            self._inventory = YardInventory.load(self._inventory_path)
        except Exception as exc:  # noqa: BLE001 - a bad cache must not stop Kit
            self._inventory = YardInventory.empty()
            self._inventory_load_error = str(exc)
        self._inventory_autosave = bool(inventory_autosave)
        self._inventory_full_sync_ready = mode == INVENTORY_MODE_SIMULATION
        self._inventory_sync_reason = (
            None
            if self._inventory_full_sync_ready
            else self._inventory_load_error
            or "live inventory requires startup synchronization"
        )
        self._active_inventory_sync_state = (
            INVENTORY_SYNC_READY
            if self._inventory_full_sync_ready
            else INVENTORY_SYNC_PENDING
        )
        self._active_inventory_sync_reason: Optional[str] = self._inventory_sync_reason
        self._last_inventory_warning_at = 0.0
        self._inventory_stage_id: Optional[int] = None
        self._inventory_rendered_revisions: Dict[str, int] = {}
        self._hidden_inventory_paths: set[str] = set()
        self._active_inventory_task_key: Optional[tuple[Any, ...]] = None
        self._latest_state: Optional[Dict[str, Any]] = None
        self._latest_lock = threading.Lock()
        self._subscription = None
        self._controller = None
        self._controller_stage_id: Optional[int] = None
        self._demo_artifacts_stage_id: Optional[int] = None
        self._crossbeam_stage_id: Optional[int] = None
        self._lower_portal_crossbeam_path: Optional[str] = None
        self._lower_portal_crossbeam_checked: bool = False
        self._last_applied_key: Optional[tuple[Any, ...]] = None
        self._last_task_stack_key: Optional[tuple[Any, ...]] = None
        self._last_task_vehicle_key: Optional[tuple[Any, ...]] = None
        self._cleared_completed_task_key: Optional[tuple[str, str, str, str]] = None
        self._last_task_source_path: Optional[str] = None
        self._task_source_has_moved = False
        self._task_source_original_translates: Dict[str, Any] = {}
        self._task_source_truck_local_center: Any = None
        self._task_vehicle_started_at: float = 0.0
        self._task_flow_state: str = TASK_STATE_IDLE
        self._task_flow_state_started_at: float = 0.0
        self._task_flow_saw_locked: bool = False
        self._spreader_locked: bool = False
        self._spreader_lock_signal_seen: bool = False
        self._spreader_task_key: Optional[tuple[Any, ...]] = None
        self._spreader_task_target_size_ft: Optional[int] = None
        self._operation_state_machine: Optional[RtgOperationStateMachine] = None
        self._operation_task_key: Optional[tuple[Any, ...]] = None
        self._status_overlay: Optional[RtgTwinStatusOverlay] = None
        self._last_status_overlay_at: float = 0.0
        self._floating_runinfo_text: Optional[str] = None
        self._floating_runinfo_clock_text: Optional[str] = None
        self._floating_runinfo_level_key: Optional[str] = None
        self._floating_runinfo_bitmap_seq = 0
        self._floating_task_panel_key: Optional[tuple[Any, ...]] = None
        self._floating_task_panel_bitmap_seq = 0
        self._floating_task_panel_diag_key: Optional[tuple[Any, ...]] = None
        self._last_panel_facing_update_at = 0.0
        self._last_error_at = 0.0

    def start(self) -> None:
        if self._subscription is not None:
            return
        try:
            import omni.kit.app  # type: ignore

            _dismiss_legacy_runinfo_window()
            pause_timeline_for_live_control(self._log)
            stage = self._current_stage()
            if stage is not None:
                self._disable_demo_artifacts_for_stage(stage)
                self._sync_user_side_view_camera(stage)
                _remove_runinfo_billboards(stage, keep_current=False)
                self._sync_floating_runinfo_billboard(stage, {})
                self._sync_floating_task_panel(stage, {})
            if self._status_overlay is None:
                self._status_overlay = RtgTwinStatusOverlay()
            stream = omni.kit.app.get_app().get_update_event_stream()
            self._subscription = stream.create_subscription_to_pop(
                self._on_update, name="SmartPortRtgTwinDriver"
            )
            builtins._smart_port_rtg_panel_facing_driver = self
            self._log("Omniverse main-thread driver started")
        except Exception as exc:  # noqa: BLE001 - Kit may not be loaded in tests
            self._log(f"unable to start Omniverse update driver: {exc}")

    def stop(self) -> None:
        subscription = self._subscription
        if subscription is not None:
            try:
                subscription.unsubscribe()
            except Exception:
                pass
            self._subscription = None
        stage = self._current_stage()
        if stage is not None:
            try:
                _remove_runinfo_billboards(stage, keep_current=False)
            except Exception:
                pass
        if getattr(builtins, "_smart_port_rtg_panel_facing_driver", None) is self:
            builtins._smart_port_rtg_panel_facing_driver = None
        _dismiss_legacy_runinfo_window()
        self._controller = None
        self._controller_stage_id = None
        self._demo_artifacts_stage_id = None
        self._crossbeam_stage_id = None
        self._lower_portal_crossbeam_path = None
        self._lower_portal_crossbeam_checked = False
        self._last_applied_key = None
        self._last_task_stack_key = None
        self._last_task_vehicle_key = None
        self._cleared_completed_task_key = None
        self._inventory_stage_id = None
        self._inventory_rendered_revisions.clear()
        self._hidden_inventory_paths.clear()
        self._active_inventory_task_key = None
        self._spreader_task_key = None
        self._spreader_task_target_size_ft = None
        self._floating_runinfo_text = None
        self._floating_runinfo_clock_text = None
        self._floating_runinfo_level_key = None
        self._floating_runinfo_bitmap_seq = 0
        self._floating_task_panel_key = None
        self._floating_task_panel_bitmap_seq = 0
        self._floating_task_panel_diag_key = None
        if self._status_overlay is not None:
            self._status_overlay.destroy()
            self._status_overlay = None

    def enqueue(self, state: Dict[str, Any]) -> None:
        with self._latest_lock:
            self._latest_state = state

    def _on_update(self, _event: Any) -> None:
        self._sync_visible_panels_facing_camera_if_due()
        with self._latest_lock:
            state = self._latest_state
            self._latest_state = None
        if remote_manual_mode_active():
            # Keep accepting TCP snapshots, but never let a delayed PLC frame
            # overwrite an operator command while remote manual mode owns the
            # three motion axes.  The HUD still receives the local engineering
            # position and nearest bay calculated by the manual controller.
            manual_snapshot = remote_manual_hud_snapshot()
            if manual_snapshot is not None:
                self._sync_status_overlay(*manual_snapshot)
            # RunInfo is independent of motion authority.  Consume the latest
            # network state here so incoming messages still refresh the 3D
            # panel while manual control owns the RTG pose.
            if isinstance(state, dict):
                stage = self._current_stage()
                if stage is not None:
                    try:
                        self._sync_floating_runinfo_billboard(
                            stage,
                            state.get("runinfo") or {},
                        )
                    except Exception as exc:  # noqa: BLE001 - keep manual control alive
                        self._log(f"runinfo panel update failed: {exc}")
            return
        if state is None:
            return
        try:
            self.apply_state(state)
        except Exception as exc:  # noqa: BLE001 - keep Kit running
            now = time.time()
            if now - self._last_error_at > 1.0:
                self._last_error_at = now
                self._log(f"apply failed: {exc}")

    def _disable_demo_artifacts_for_stage(self, stage: Any) -> None:
        stage_id = id(stage)
        if self._demo_artifacts_stage_id == stage_id:
            return
        try:
            disabled = disable_demo_sequence_artifacts(stage)
            if disabled:
                self._log(
                    f"Disabled {disabled} sequence-demo artifact(s) for live PLC mode"
                )
        except Exception as exc:  # noqa: BLE001 - live PLC updates should continue
            self._log(f"unable to disable sequence-demo artifacts: {exc}")
        self._demo_artifacts_stage_id = stage_id

    def apply_state(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        stage = self._current_stage()
        if stage is None:
            return None

        self._disable_demo_artifacts_for_stage(stage)
        self._sync_inventory_scene(stage)
        controller = self._controller_for_stage(stage)
        pose = dict(state.get("pose") or {})
        task = dict(state.get("task") or {})
        if task:
            task = self._prepare_inventory_task(stage, task)
            state = dict(state)
            state["task"] = task
        pose[RESOLVED_LOCKED_KEY] = self._resolve_spreader_locked(pose)

        field = _field_name(pose.get("currentField"))
        bay = _as_int(pose.get("currentBay"), 0)
        trolley_m = _as_float(pose.get("trolleyMeter"))
        hoist_m = _as_float(pose.get("hoistMeter"))
        gantry_m = _as_float(pose.get("bigcarElecDistance"))
        task_is_gantry_traveling = str(
            task.get("operationState") or task.get("visualPhase") or ""
        ).upper() == "GANTRY_TO_SOURCE"
        (
            applied_spreader_size_ft,
            requested_spreader_size_ft,
        ) = self._sync_spreader_size_for_task(
            controller,
            task,
        )
        spreader_center_offset_y = _spreader_center_offset_y(
            requested_spreader_size_ft
        )

        # A completed task changes the durable yard inventory exactly once.
        # Only its truck, carried-box proxy, and disposable working stack are
        # removed; every persistent inventory bay remains in the world.
        task_complete = _task_is_complete(task)
        if task_complete:
            if self._settle_inventory_task(stage, task):
                self._clear_completed_task_visuals(stage, task)
        else:
            self._cleared_completed_task_key = None
            # Create the physical stack before positioning the RTG so the
            # rendered container centre is available as the final reference.
            if self._sync_task_stack(stage, task):
                self._hide_inventory_bay_for_task(stage, task)
            self._sync_task_route_indicator(stage, task)

        # Prefer PLC field/bay because it matches yard occupation.  If PLC has
        # not published a bay yet, fall back to continuous gantry metres.
        if field and bay > 0 and not task_is_gantry_traveling:
            applied_gantry_m = controller.set_gantry_bay(
                field,
                bay,
                center_offset_y=spreader_center_offset_y,
                container_size_ft=requested_spreader_size_ft,
            )
            # Keep gantry stopping on the stable fixed yard mapping. The
            # crossbeam measurement is exposed as a read-only diagnostic until
            # its parent hierarchy is verified in the active USD stage.
            crossbeam_alignment_y = None
            gantry_source = f"{field}/{bay:03d}"
        elif gantry_m is not None:
            applied_gantry_m = controller.set_gantry_safe(gantry_m)
            crossbeam_alignment_y = None
            gantry_source = "bigcarElecDistance"
        else:
            applied_gantry_m = None
            crossbeam_alignment_y = None
            gantry_source = "none"

        applied_trolley_m = (
            controller.set_trolley_safe(trolley_m) if trolley_m is not None else None
        )
        simulated_bottom_face_load = (
            bool(task)
            and _task_work_type(task)
            in (WORK_TYPE_LOAD, WORK_TYPE_UNLOAD, WORK_TYPE_SWITCH)
            and _pose_spreader_locked(pose)
            and _hoist_uses_simulated_load_bottom(task)
        )
        applied_hoist_m = (
            controller.set_hoist_safe(
                hoist_m,
                carried_load=simulated_bottom_face_load,
            )
            if hoist_m is not None
            else None
        )

        applied = {
            "gantryMeter": applied_gantry_m,
            "gantrySource": gantry_source,
            "trolleyMeter": applied_trolley_m,
            "hoistMeter": applied_hoist_m,
            "spreaderSizeFt": applied_spreader_size_ft,
            "spreaderTargetSizeFt": requested_spreader_size_ft,
            "spreaderCenterOffsetY": spreader_center_offset_y,
            "crossbeamAlignmentY": crossbeam_alignment_y,
        }
        if not task_complete:
            if _task_work_type(task) == WORK_TYPE_SWITCH:
                self._sync_task_switch(stage, task, pose)
            else:
                self._sync_task_vehicle(stage, task, pose)
            # The PLC-driven state machine may reach COMPLETE from physical
            # spreader / hoist signals without the task payload ever setting
            # ``operationState`` to a terminal value.  When the machine
            # declares the operation done, settle the durable inventory and
            # discard the disposable task visuals exactly once.
            if (
                task
                and self._operation_state_machine is not None
                and self._operation_state_machine.state is OperationState.COMPLETE
            ):
                self._log(
                    "state machine reached COMPLETE; settling inventory "
                    f"task={task.get('taskId') or '--'}"
                )
                if self._settle_inventory_task(stage, task):
                    self._clear_completed_task_visuals(stage, task)
        self._write_status(stage, state, applied)
        self._sync_status_overlay(state, applied)

        return applied

    def report_crossbeam_alignment(self) -> Optional[Dict[str, Any]]:
        """Report, without changing pose, the live box-to-crossbeam offset."""

        stage = self._current_stage()
        if stage is None:
            self._log("cannot report crossbeam alignment: no active USD stage")
            return None

        container = stage.GetPrimAtPath(RTG_CROSSBEAM_ALIGNMENT_CONTAINER_PATH)
        crossbeam = self._lower_portal_crossbeam_prim(stage)
        if not container or not crossbeam:
            self._log(
                "cannot report crossbeam alignment: reference box or "
                "lower portal crossbeam was not found"
            )
            return None

        container_center = self._world_bbox_center(container)
        crossbeam_center = self._world_bbox_center(crossbeam)
        if container_center is None or crossbeam_center is None:
            self._log("cannot report crossbeam alignment: empty USD bounds")
            return None

        result = {
            "containerPath": RTG_CROSSBEAM_ALIGNMENT_CONTAINER_PATH,
            "crossbeamPath": str(crossbeam.GetPath()),
            "containerCenter": tuple(float(value) for value in container_center),
            "crossbeamCenter": tuple(float(value) for value in crossbeam_center),
            "deltaY": _gantry_crossbeam_alignment_delta_y(
                container_center[1], crossbeam_center[1]
            ),
        }
        self._log(f"crossbeam alignment measurement: {result}")
        return result

    def restore_inventory_scene(
        self,
        *,
        force: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Restore the selected inventory immediately, before any task arrives.

        When *force* is True the stage-id guard is skipped so the complete
        yard is rebuilt even if another driver already synced this stage.
        """
        stage = self._current_stage()
        if stage is None:
            self._log("cannot restore yard inventory: no active USD stage")
            return None
        if force:
            self._inventory_stage_id = None
        self._sync_inventory_scene(stage)
        return {
            "mode": self._inventory_mode,
            "path": str(self._inventory_path),
            "stats": self._inventory.to_dict()["stats"],
        }

    def _lower_portal_crossbeam_prim(self, stage: Any):
        """Find and cache the authored lower portal crossbeam on this stage."""

        stage_id = id(stage)
        if self._crossbeam_stage_id != stage_id:
            self._crossbeam_stage_id = stage_id
            self._lower_portal_crossbeam_path = None
            self._lower_portal_crossbeam_checked = False

        if self._lower_portal_crossbeam_path:
            cached = stage.GetPrimAtPath(self._lower_portal_crossbeam_path)
            if cached:
                return cached
            self._lower_portal_crossbeam_path = None

        if self._lower_portal_crossbeam_checked:
            return None
        self._lower_portal_crossbeam_checked = True

        target_name = RTG_LOWER_PORTAL_CROSSBEAM_NAME.casefold()
        for prim in stage.Traverse():
            if prim.GetName().casefold() == target_name:
                self._lower_portal_crossbeam_path = str(prim.GetPath())
                self._log(
                    "using lower portal crossbeam for live stack alignment: "
                    f"{self._lower_portal_crossbeam_path}"
                )
                return prim
        return None

    @staticmethod
    def _world_bbox_center(prim: Any):
        """Return a prim's composed world-space bounds centre, or ``None``."""

        from pxr import Usd, UsdGeom

        cache = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            useExtentsHint=False,
        )
        bounds = cache.ComputeWorldBound(prim).ComputeAlignedBox()
        if bounds.IsEmpty():
            return None
        return (bounds.GetMin() + bounds.GetMax()) * 0.5

    def _resolve_spreader_locked(self, pose: Dict[str, Any]) -> bool:
        """Latch the two PLC end-position signals into one stable lock state."""

        lock_on = _as_int(pose.get("lockState"), 0) != 0
        if pose.get("unlockState") is None:
            self._spreader_locked = lock_on
            return self._spreader_locked

        unlock_on = _as_int(pose.get("unlockState"), 0) != 0
        previous = self._spreader_locked

        if lock_on and not unlock_on:
            self._spreader_locked = True
            self._spreader_lock_signal_seen = True
        elif unlock_on and not lock_on:
            self._spreader_locked = False
            self._spreader_lock_signal_seen = True
        elif not self._spreader_lock_signal_seen:
            # Older feeds publish only lockState. Preserve that behaviour until
            # a valid pair of open/closed end-position signals is observed.
            self._spreader_locked = lock_on

        if self._spreader_locked != previous:
            self._log(
                "spreader lock resolved "
                f"{previous} -> {self._spreader_locked} "
                f"(lock={pose.get('lockState')} unlock={pose.get('unlockState')})"
            )
        return self._spreader_locked

    @staticmethod
    def _inventory_task_key(task: Dict[str, Any]) -> tuple[Any, ...]:
        return (
            str(task.get("taskId") or ""),
            _task_work_type(task),
            str(task.get("startPos") or task.get("sourcePos") or ""),
            str(task.get("endPos") or task.get("targetPos") or ""),
            str(task.get("containerNo") or ""),
        )

    @staticmethod
    def _inventory_stack_path(stack_key: str) -> str:
        block, bay_text = stack_key.split("/", 1)
        return f"{YARD_INVENTORY_ROOT_PATH}/Bay_{block}_{int(bay_text):03d}"

    def _sync_inventory_scene(self, stage: Any) -> None:
        """Restore the complete persistent inventory when a stage is opened."""

        stage_id = id(stage)
        if self._inventory_stage_id == stage_id:
            return

        from pxr import Sdf, Usd, UsdGeom

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            old = stage.GetPrimAtPath(YARD_INVENTORY_ROOT_PATH)
            if old:
                stage.RemovePrim(old.GetPath())
            root = UsdGeom.Xform.Define(stage, YARD_INVENTORY_ROOT_PATH).GetPrim()
            root.CreateAttribute("inventory:schema", Sdf.ValueTypeNames.String).Set(
                "smart-port-yard-inventory/v1"
            )
            root.CreateAttribute("inventory:path", Sdf.ValueTypeNames.String).Set(
                str(self._inventory_path)
            )
            root.CreateAttribute("inventory:mode", Sdf.ValueTypeNames.Token).Set(
                self._inventory_mode
            )
            root.CreateAttribute(
                "inventory:startupSyncReady", Sdf.ValueTypeNames.Bool
            ).Set(bool(self._inventory_full_sync_ready))
            if self._inventory_sync_reason:
                root.CreateAttribute(
                    "inventory:syncReason", Sdf.ValueTypeNames.String
                ).Set(self._inventory_sync_reason)

        self._inventory_stage_id = stage_id
        self._inventory_rendered_revisions.clear()
        self._hidden_inventory_paths.clear()
        for stack in self._inventory.active_stacks():
            self._render_inventory_bay(stage, stack["bayId"])
        stats = self._inventory.to_dict()["stats"]
        self._log(
            "restored persistent yard inventory "
            f"mode={self._inventory_mode} "
            f"stacks={stats['occupiedStackCount']} "
            f"containers={stats['containerCount']} path={self._inventory_path}"
        )
        if self._inventory_load_error:
            self._warn_inventory(
                "inventory cache could not be loaded; using empty pending state: "
                f"{self._inventory_load_error}"
            )

    def _prepare_inventory_task(
        self, stage: Any, task: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Reconcile one new task snapshot and return its inventory-backed view."""

        key = self._inventory_task_key(task)
        if key != self._active_inventory_task_key:
            self._restore_hidden_inventory(stage)
            changed: set[str] = set()
            sync_state = INVENTORY_SYNC_READY
            sync_reason: Optional[str] = None
            inventory_before = copy.deepcopy(self._inventory)
            try:
                bay_map = str(task.get("bayMap") or "").strip()
                has_authoritative_bay_map = bool(re.fullmatch(r"\d{6}", bay_map))
                if (
                    self._inventory_mode == INVENTORY_MODE_LIVE
                    and not self._inventory_full_sync_ready
                    and not has_authoritative_bay_map
                ):
                    sync_state = INVENTORY_SYNC_PENDING
                    sync_reason = (
                        "live task has no authoritative six-digit bayMap"
                    )
                else:
                    changed.update(
                        self._inventory.reconcile_task_snapshot(task)
                    )

                if (
                    sync_state == INVENTORY_SYNC_READY
                    and self._inventory_mode == INVENTORY_MODE_LIVE
                    and _task_work_type(task) == WORK_TYPE_SWITCH
                ):
                    source_key = self._task_inventory_bay_key(
                        task,
                        task.get("startPos") or task.get("sourcePos"),
                    )
                    target_key = self._task_inventory_bay_key(
                        task,
                        task.get("endPos") or task.get("targetPos"),
                    )
                    if (
                        source_key
                        and target_key
                        and source_key != target_key
                        and not self._inventory_full_sync_ready
                    ):
                        target_bay_map = str(
                            task.get("targetBayMap") or ""
                        ).strip()
                        if not re.fullmatch(r"\d{6}", target_bay_map):
                            sync_state = INVENTORY_SYNC_PENDING
                            sync_reason = (
                                "cross-bay SWITCH requires targetBayMap or "
                                "a full authoritative inventory sync"
                            )
                        else:
                            target_task = dict(task)
                            target_task["workType"] = WORK_TYPE_LOAD
                            target_task["bayMap"] = target_bay_map
                            changed.update(
                                self._inventory.reconcile_task_snapshot(
                                    target_task
                                )
                            )

                if sync_state == INVENTORY_SYNC_READY and changed:
                    if self._inventory_autosave:
                        self._inventory.save(self._inventory_path)
                    # Reconcile may touch several bays (the task's own target,
                    # removed footprint conflicts, SWITCH cross-bay targets).
                    # Render every one except the active task bay — that bay is
                    # about to be hidden by _hide_inventory_bay_for_task and
                    # redrawn via LiveTaskStack anyway, so a persistent render
                    # here would be discarded immediately.
                    active_work = _task_work_type(task)
                    active_pos = (
                        task.get("startPos") or task.get("sourcePos")
                        if active_work in (WORK_TYPE_UNLOAD, WORK_TYPE_SWITCH)
                        else task.get("endPos") or task.get("targetPos")
                    )
                    active_key = self._task_inventory_bay_key(task, active_pos)
                    for stack_key in changed:
                        if stack_key != active_key:
                            self._render_inventory_bay(stage, stack_key)
                elif sync_state != INVENTORY_SYNC_READY:
                    self._inventory = inventory_before
            except Exception as exc:  # noqa: BLE001 - stale data is recoverable
                self._inventory = inventory_before
                sync_state = INVENTORY_SYNC_PENDING
                sync_reason = f"inventory reconciliation failed: {exc}"

            self._active_inventory_sync_state = sync_state
            self._active_inventory_sync_reason = sync_reason
            self._active_inventory_task_key = key
            if sync_state != INVENTORY_SYNC_READY:
                self._warn_inventory(
                    f"task={task.get('taskId') or '--'} pending synchronization: "
                    f"{sync_reason}"
                )

        result = (
            self._inventory.enrich_task(task)
            if self._active_inventory_sync_state == INVENTORY_SYNC_READY
            else dict(task)
        )
        result["inventoryMode"] = self._inventory_mode
        result["inventorySyncState"] = self._active_inventory_sync_state
        result["inventorySyncReason"] = self._active_inventory_sync_reason
        return result

    def _settle_inventory_task(
        self, stage: Any, task: Dict[str, Any]
    ) -> bool:
        """Commit a successful LOAD/UNLOAD/SWITCH transaction exactly once."""

        if (
            self._inventory_mode == INVENTORY_MODE_LIVE
            and task.get("inventorySyncState") != INVENTORY_SYNC_READY
        ):
            self._warn_inventory(
                "inventory commit blocked; keeping task visuals "
                f"task={task.get('taskId') or '--'} "
                f"reason={task.get('inventorySyncReason') or 'pending synchronization'}"
            )
            return False

        inventory_before = copy.deepcopy(self._inventory)
        try:
            transaction = self._inventory.settle_task(task)
            if transaction.get("applied") and self._inventory_autosave:
                self._inventory.save(self._inventory_path)
            if transaction.get("applied"):
                for stack_key in transaction.get("changedBays") or ():
                    self._render_inventory_bay(stage, str(stack_key))
                self._log(
                    "inventory committed "
                    f"task={transaction.get('taskId')} "
                    f"workType={transaction.get('workType')} "
                    f"bays={transaction.get('changedBays')}"
                )
            return True
        except Exception as exc:  # noqa: BLE001 - retain task visuals for retry
            self._inventory = inventory_before
            self._warn_inventory(
                "inventory commit failed; keeping task visuals "
                f"task={task.get('taskId') or '--'} error={exc}"
            )
            return False

    def _task_inventory_bay_key(
        self, task: Dict[str, Any], position: Any
    ) -> Optional[str]:
        details = parse_inventory_task_position(position)
        if details is None:
            return None
        block, bay_number, _row, _tier = details
        size_ft = self._container_size_ft(task.get("containerSize"), bay_number)
        try:
            normalized = normalize_inventory_bay_number(
                block, bay_number, size_ft
            )
        except ValueError:
            return None
        return inventory_bay_id(block, normalized)

    def _warn_inventory(self, message: str) -> None:
        now = time.time()
        if now - self._last_inventory_warning_at < INVENTORY_WARNING_INTERVAL_SECONDS:
            return
        self._last_inventory_warning_at = now
        self._log(message)

    def _render_inventory_bay(self, stage: Any, stack_key: str) -> None:
        """Rebuild one persistent bay from the inventory source of truth."""

        from pxr import Sdf, Usd, UsdGeom

        root_path = self._inventory_stack_path(stack_key)
        stack = self._inventory.stacks.get(stack_key)
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            old = stage.GetPrimAtPath(root_path)
            if old:
                stage.RemovePrim(old.GetPath())
            if stack is None or not any(stack["rows"]):
                self._inventory_rendered_revisions.pop(stack_key, None)
                self._hidden_inventory_paths.discard(root_path)
                return

            root = UsdGeom.Xform.Define(stage, root_path).GetPrim()
            pattern = self._inventory.stack_pattern(stack)
            root.CreateAttribute("inventory:bayId", Sdf.ValueTypeNames.String).Set(
                stack_key
            )
            root.CreateAttribute("inventory:block", Sdf.ValueTypeNames.String).Set(
                stack["block"]
            )
            root.CreateAttribute("inventory:bayNumber", Sdf.ValueTypeNames.Int).Set(
                int(stack["bayNumber"])
            )
            root.CreateAttribute(
                "inventory:containerSizeFt", Sdf.ValueTypeNames.Int
            ).Set(int(stack["containerSizeFt"]))
            root.CreateAttribute("inventory:bayMap", Sdf.ValueTypeNames.String).Set(
                pattern
            )
            root.CreateAttribute("inventory:revision", Sdf.ValueTypeNames.Int).Set(
                int(stack["revision"])
            )
            slot_heights = tuple(
                tuple(float(item.get("heightM") or PLC_CONTAINER_TIER_HEIGHT_M)
                      for item in row)
                for row in stack["rows"]
            )
            self._create_live_stack_geometry(
                stage,
                root_path,
                stack["block"],
                int(stack["bayNumber"]),
                pattern,
                int(stack["containerSizeFt"]),
                slot_heights,
                include_truck_lane=False,
            )
            self._inventory_rendered_revisions[stack_key] = int(stack["revision"])

    def _hide_inventory_bay_for_task(
        self, stage: Any, task: Dict[str, Any]
    ) -> None:
        stack_key = str(task.get("inventoryBayId") or "")
        if not stack_key:
            return
        path = self._inventory_stack_path(stack_key)
        prim = stage.GetPrimAtPath(path)
        if not prim:
            return
        self._set_visibility(prim, False)
        self._hidden_inventory_paths.add(path)

    def _restore_hidden_inventory(self, stage: Any) -> None:
        for path in tuple(self._hidden_inventory_paths):
            prim = stage.GetPrimAtPath(path)
            if prim:
                self._set_visibility(prim, True)
        self._hidden_inventory_paths.clear()

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
            elif (
                not RUNINFO_OVERLAY_ENABLED
                and getattr(self._status_overlay, "_runinfo_window", None) is not None
            ):
                # The cached overlay was built by an older script execution
                # that still included the runinfo panel. Replace it.
                try:
                    self._status_overlay.destroy()
                except Exception:
                    pass
                self._status_overlay = RtgTwinStatusOverlay()
            self._status_overlay.update(state, applied)
        except Exception as exc:  # noqa: BLE001 - UI should not stop RTG driving
            if now - self._last_error_at > 1.0:
                self._last_error_at = now
                self._log(f"status overlay failed: {exc}")

    def _sync_task_stack(self, stage: Any, task: Dict[str, Any]) -> bool:
        key = self._task_stack_key(task)
        render_key = self._task_stack_render_key(task)
        if render_key == self._last_task_stack_key:
            # Older Script Editor reloads may have left the optional task lane
            # under the same live stack.  Remove it even when the map key did
            # not change.
            self._remove_task_truck_lane(stage)
            return stage.GetPrimAtPath(LIVE_TASK_STACK_PATH) is not None

        # Destroy the prior task before creating another map.  In particular,
        # do not restore a previously carried source box after a new stack has
        # reused the same Rxx_Txx prim name.
        # This also clears stale task roots left in the session layer after a
        # Script Editor reload, when this fresh driver has no in-memory key.
        self._discard_task_visuals(stage, remove_stack=True)
        self._last_task_stack_key = render_key

        from pxr import Sdf, Usd, UsdGeom

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            old = stage.GetPrimAtPath(LIVE_TASK_STACK_PATH)
            if old:
                stage.RemovePrim(old.GetPath())

            if key is None:
                self._log(
                    "task bay-map visual skipped: missing or invalid task map "
                    f"task={task.get('taskId') or '--'} "
                    f"bayMap={task.get('bayMap') or '--'}"
                )
                return False

            block, bay_number, pattern, container_size_ft = key
            source_row = None
            source_tier = None
            if _task_work_type(task) in (WORK_TYPE_UNLOAD, WORK_TYPE_SWITCH):
                source_details = self._parse_task_position_details(
                    task.get("startPos") or task.get("sourcePos")
                )
                if source_details is not None:
                    _field, _bay, source_row, source_tier = source_details
            slot_heights_m = _task_bay_slot_heights_m(
                task,
                pattern,
                source_row,
                source_tier,
            )
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
            root.CreateAttribute(
                "live:taskContainerHeightMeters", Sdf.ValueTypeNames.Double
            ).Set(_task_container_height_m(task))

            self._create_live_stack_geometry(
                stage,
                LIVE_TASK_STACK_PATH,
                block,
                bay_number,
                pattern,
                container_size_ft,
                slot_heights_m,
                include_truck_lane=False,
            )
            root = stage.GetPrimAtPath(LIVE_TASK_STACK_PATH)
            if root:
                UsdGeom.Imageable(root).MakeVisible()
            self._log(
                f"rendered live task stack {block}/{bay_number:03d} "
                f"{container_size_ft}FT bayMap={pattern}"
            )
            return bool(root and any(int(height) > 0 for height in pattern))

    def _task_stack_render_key(self, task: Dict[str, Any]) -> Optional[tuple[Any, ...]]:
        """Return a full task identity for the disposable live bay-map visual."""

        stack_key = self._task_stack_key(task)
        if stack_key is None:
            return None
        return (
            *stack_key,
            _task_work_type(task),
            str(task.get("taskId") or ""),
            str(task.get("startPos") or task.get("sourcePos") or ""),
            str(task.get("endPos") or task.get("targetPos") or ""),
            round(_task_container_height_m(task), 3),
            repr(task.get("baySlotHeights")),
        )

    def _discard_task_visuals(self, stage: Any, *, remove_stack: bool) -> None:
        """Discard task-owned visuals without restoring a source box into a new map."""

        from pxr import Usd

        self._restore_hidden_inventory(stage)
        paths = [
            TASK_ROUTE_INDICATOR_ROOT_PATH,
            LIVE_TASK_TRUCK_PATH,
            LIVE_HOISTED_LOAD_PATH,
            LEGACY_LIVE_HOISTED_LOAD_PATH,
        ]
        if remove_stack:
            paths.insert(0, LIVE_TASK_STACK_PATH)
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            for path in paths:
                prim = stage.GetPrimAtPath(path)
                if prim:
                    stage.RemovePrim(prim.GetPath())

        self._last_task_stack_key = None
        self._last_task_vehicle_key = None
        self._last_task_source_path = None
        self._task_source_has_moved = False
        self._task_source_original_translates.clear()
        self._task_source_truck_local_center = None
        self._task_flow_state = TASK_STATE_IDLE
        self._task_flow_state_started_at = 0.0
        self._task_flow_saw_locked = False
        self._operation_state_machine = None
        self._operation_task_key = None

    @staticmethod
    def _remove_task_truck_lane(stage: Any) -> None:
        """Remove legacy lane markings owned by the disposable task stack."""

        from pxr import Usd

        lane_path = f"{LIVE_TASK_STACK_PATH}/TruckLane"
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            lane = stage.GetPrimAtPath(lane_path)
            if lane:
                stage.RemovePrim(lane.GetPath())

    def _sync_task_route_indicator(self, stage: Any, task: Dict[str, Any]) -> None:
        from pxr import Gf, Usd, UsdGeom

        stack_key = self._task_stack_key(task)
        endpoints = _task_route_endpoints(task)
        if stack_key is None or endpoints is None:
            self._remove_task_route_indicator(stage)
            return

        source_endpoint, target_endpoint = endpoints
        source = self._task_route_endpoint_world_point(task, stack_key, source_endpoint)
        target = self._task_route_endpoint_world_point(task, stack_key, target_endpoint)
        if source is None or target is None:
            self._remove_task_route_indicator(stage)
            return
        has_truck_endpoint = (
            source_endpoint[0] == "truck" or target_endpoint[0] == "truck"
        )

        direction = Gf.Vec3d(
            float(target[0]) - float(source[0]),
            0.0,
            float(target[2]) - float(source[2]),
        )
        length = math.sqrt(float(direction[0]) ** 2 + float(direction[2]) ** 2)
        if length < 0.05:
            direction = Gf.Vec3d(0.0, 0.0, 1.0)
            length = 1.0
        unit = Gf.Vec3d(float(direction[0]) / length, 0.0, float(direction[2]) / length)
        perp = Gf.Vec3d(
            -float(unit[2]) * TASK_ROUTE_INDICATOR_THICKNESS_M,
            0.0,
            float(unit[0]) * TASK_ROUTE_INDICATOR_THICKNESS_M,
        )
        head_back = Gf.Vec3d(
            float(target[0]) - float(unit[0]) * TASK_ROUTE_INDICATOR_HEAD_LENGTH_M,
            float(target[1]),
            float(target[2]) - float(unit[2]) * TASK_ROUTE_INDICATOR_HEAD_LENGTH_M,
        )
        head_perp = Gf.Vec3d(
            -float(unit[2]) * TASK_ROUTE_INDICATOR_HEAD_WIDTH_M,
            0.0,
            float(unit[0]) * TASK_ROUTE_INDICATOR_HEAD_WIDTH_M,
        )
        phase = (
            time.time() % TASK_ROUTE_INDICATOR_PULSE_SECONDS
        ) / TASK_ROUTE_INDICATOR_PULSE_SECONDS
        pulse = Gf.Vec3d(
            float(source[0]) + (float(target[0]) - float(source[0])) * phase,
            float(source[1]),
            float(source[2]) + (float(target[2]) - float(source[2])) * phase,
        )

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            old = stage.GetPrimAtPath(TASK_ROUTE_INDICATOR_ROOT_PATH)
            if old:
                stage.RemovePrim(old.GetPath())
            root = UsdGeom.Xform.Define(stage, TASK_ROUTE_INDICATOR_ROOT_PATH).GetPrim()
            UsdGeom.Imageable(root).MakeVisible()

            line = UsdGeom.Mesh.Define(stage, TASK_ROUTE_INDICATOR_LINE_PATH)
            line.CreatePointsAttr(
                [
                    Gf.Vec3f(source + perp),
                    Gf.Vec3f(source - perp),
                    Gf.Vec3f(target - perp),
                    Gf.Vec3f(target + perp),
                ]
            )
            line.CreateFaceVertexCountsAttr([4])
            line.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
            line.CreateDoubleSidedAttr(True)
            line.CreateDisplayColorAttr([Gf.Vec3f(0.12, 0.86, 1.0)])

            head = UsdGeom.Mesh.Define(stage, TASK_ROUTE_INDICATOR_HEAD_PATH)
            head.CreatePointsAttr(
                [
                    Gf.Vec3f(target),
                    Gf.Vec3f(head_back + head_perp),
                    Gf.Vec3f(head_back - head_perp),
                ]
            )
            head.CreateFaceVertexCountsAttr([3])
            head.CreateFaceVertexIndicesAttr([0, 1, 2])
            head.CreateDoubleSidedAttr(True)
            head.CreateDisplayColorAttr([Gf.Vec3f(1.0, 0.84, 0.18)])

            self._create_indicator_sphere(
                stage,
                TASK_ROUTE_INDICATOR_START_PATH,
                source,
                0.16,
                Gf.Vec3f(0.12, 0.86, 1.0),
            )
            self._create_indicator_sphere(
                stage,
                TASK_ROUTE_INDICATOR_END_PATH,
                target,
                0.18,
                Gf.Vec3f(1.0, 0.84, 0.18),
            )
            self._create_indicator_sphere(
                stage,
                TASK_ROUTE_INDICATOR_PULSE_PATH,
                pulse,
                0.22,
                Gf.Vec3f(1.0, 0.95, 0.34),
            )
            if has_truck_endpoint:
                truck_center = (
                    source if source_endpoint[0] == "truck" else target
                )
                self._create_route_truck_marker(stage, truck_center)

    def _remove_task_route_indicator(self, stage: Any) -> None:
        from pxr import Usd

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            root = stage.GetPrimAtPath(TASK_ROUTE_INDICATOR_ROOT_PATH)
            if root:
                stage.RemovePrim(root.GetPath())

    def _task_route_endpoint_world_point(
        self,
        task: Dict[str, Any],
        stack_key: tuple[str, int, str, int],
        endpoint: tuple[str, int, int],
    ) -> Any:
        if endpoint[0] == "truck":
            return self._task_route_truck_world_point(stack_key)
        if endpoint[0] != "slot":
            return None
        return self._task_route_slot_world_point(task, stack_key, (endpoint[1], endpoint[2]))

    def _task_route_slot_world_point(
        self,
        task: Dict[str, Any],
        stack_key: tuple[str, int, str, int],
        slot: tuple[int, int],
    ) -> Any:
        from pxr import Gf
        from build_live_containers import (
            CONTAINER_WIDTH_X_M,
            PAD_TOP_Z_M,
            ROW_GAP_X_M,
            STACK_FLOOR_CLEARANCE_M,
            TIER_GAP_Z_M,
            container_center_y_for_bay,
            reference_first_row_center_x_m,
        )

        block, bay_number, pattern, container_size_ft = stack_key
        row, tier = slot
        if row < 1 or row > 6 or tier < 1:
            return None

        row_pitch = CONTAINER_WIDTH_X_M + ROW_GAP_X_M
        first_row_center_x = reference_first_row_center_x_m(len(pattern))
        leftmost_stack_x = first_row_center_x - (len(pattern) - 1) * row_pitch
        x = leftmost_stack_x + (len(pattern) - row) * row_pitch
        y = container_center_y_for_bay(block, bay_number, container_size_ft)

        slot_heights_m = _task_bay_slot_heights_m(task, pattern)
        bottom_z = PAD_TOP_Z_M + STACK_FLOOR_CLEARANCE_M
        for current_tier in range(1, tier + 1):
            actual_height_m = (
                slot_heights_m[row - 1][current_tier - 1]
                if len(slot_heights_m) >= row
                and len(slot_heights_m[row - 1]) >= current_tier
                else PLC_CONTAINER_TIER_HEIGHT_M
            )
            visual_height_z = _container_visual_height_z(actual_height_m)
            center_z = bottom_z + visual_height_z * 0.5
            if current_tier == tier:
                return Gf.Vec3d(x, y, center_z + visual_height_z * 0.5 + TASK_ROUTE_INDICATOR_Z_LIFT_M)
            bottom_z += visual_height_z + TIER_GAP_Z_M
        return None

    def _task_route_truck_world_point(
        self,
        stack_key: tuple[str, int, str, int],
    ) -> Any:
        from pxr import Gf
        from build_live_containers import container_center_y_for_bay

        block, bay_number, _pattern, container_size_ft = stack_key
        return Gf.Vec3d(
            self._task_truck_lane_center_x(),
            container_center_y_for_bay(block, bay_number, container_size_ft),
            TASK_ROUTE_TRUCK_MARKER_Z_M + TASK_ROUTE_INDICATOR_Z_LIFT_M,
        )

    @staticmethod
    def _create_indicator_sphere(
        stage: Any,
        path: str,
        center: Any,
        radius: float,
        color: Any,
    ) -> None:
        from pxr import UsdGeom

        sphere = UsdGeom.Sphere.Define(stage, path)
        sphere.CreateRadiusAttr(float(radius))
        sphere.CreateDisplayColorAttr([color])
        xform = UsdGeom.Xformable(sphere.GetPrim())
        xform.AddTranslateOp().Set(center)

    def _create_route_truck_marker(self, stage: Any, center: Any) -> None:
        from pxr import Gf

        base = TASK_ROUTE_INDICATOR_TRUCK_PATH
        self._create_task_cube(
            stage,
            f"{base}/Chassis",
            Gf.Vec3d(float(center[0]), float(center[1]), TASK_ROUTE_TRUCK_MARKER_Z_M),
            Gf.Vec3f(0.72, 1.35, 0.18),
            Gf.Vec3f(0.08, 0.78, 1.0),
        )
        self._create_task_cube(
            stage,
            f"{base}/Cab",
            Gf.Vec3d(float(center[0]), float(center[1]) - 0.86, TASK_ROUTE_TRUCK_MARKER_Z_M + 0.2),
            Gf.Vec3f(0.62, 0.42, 0.36),
            Gf.Vec3f(1.0, 0.84, 0.18),
        )
        for index, (x_offset, y_offset) in enumerate(
            ((-0.42, -0.46), (0.42, -0.46), (-0.42, 0.46), (0.42, 0.46)),
            start=1,
        ):
            self._create_task_cube(
                stage,
                f"{base}/Wheel_{index:02d}",
                Gf.Vec3d(
                    float(center[0]) + x_offset,
                    float(center[1]) + y_offset,
                    TASK_ROUTE_TRUCK_MARKER_Z_M - 0.12,
                ),
                Gf.Vec3f(0.16, 0.22, 0.16),
                Gf.Vec3f(0.02, 0.03, 0.04),
            )

    def _clear_completed_task_visuals(self, stage: Any, task: Dict[str, Any]) -> None:
        """Remove all visuals owned by one successfully completed task."""

        key = (
            str(task.get("taskId") or ""),
            _task_work_type(task),
            str(task.get("startPos") or task.get("sourcePos") or ""),
            str(task.get("endPos") or task.get("targetPos") or ""),
        )
        if key == self._cleared_completed_task_key:
            return

        self._discard_task_visuals(stage, remove_stack=True)
        self._cleared_completed_task_key = key
        self._log(f"cleared completed task visuals task={key[0] or '--'}")

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
                _task_container_height_m(task),
            )
            self._log(
                f"rendered {'inbound loaded' if work_type == WORK_TYPE_LOAD else 'outbound empty'} "
                f"truck for {block}/{bay_number:03d} {container_size_ft}FT task={task_id} "
                f"source={self._last_task_source_path or '--'}"
            )

        flow_state = self._advance_operation_flow(stage, key, task, pose)
        truck_progress = self._update_task_vehicle_pose(
            stage, block, bay_number, container_size_ft, work_type, flow_state
        )
        self._sync_task_source_visual(stage, work_type, flow_state)
        self._sync_task_visuals(
            stage,
            container_size_ft,
            work_type,
            flow_state,
            truck_progress,
            pose,
            task,
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

    def _spreader_task_request(
        self, task: Dict[str, Any]
    ) -> Optional[tuple[tuple[Any, ...], int]]:
        """Return the identity and requested spreader size of an active task."""

        if not task:
            return None
        work_type = _task_work_type(task)
        if work_type not in (WORK_TYPE_LOAD, WORK_TYPE_UNLOAD, WORK_TYPE_SWITCH):
            return None
        start_pos = task.get("startPos") or task.get("sourcePos")
        end_pos = task.get("endPos") or task.get("targetPos")
        reference_pos = (
            start_pos
            if work_type in (WORK_TYPE_UNLOAD, WORK_TYPE_SWITCH)
            else end_pos
        ) or end_pos or start_pos
        parsed = self._parse_task_position(reference_pos)
        bay_number = parsed[1] if parsed is not None else 0
        container_size_ft = self._container_size_ft(
            task.get("containerSize"),
            bay_number,
        )
        if container_size_ft not in (20, 40):
            return None
        key = (
            str(task.get("taskId") or ""),
            work_type,
            str(start_pos or ""),
            str(end_pos or ""),
            container_size_ft,
        )
        return key, container_size_ft

    def _sync_spreader_size_for_task(
        self,
        controller: Any,
        task: Dict[str, Any],
    ) -> tuple[Optional[float], Optional[int]]:
        """Apply one size transform only when a new task needs a different tool."""

        request = self._spreader_task_request(task)
        current_size_ft = controller.current_spreader_size_ft()
        if request is None:
            # No task means no telescope command.  Leave the previous size in
            # place until a distinct task arrives and evaluates it again.
            self._spreader_task_key = None
            self._spreader_task_target_size_ft = None
            return current_size_ft, None

        task_key, target_size_ft = request
        needs_size_command = (
            task_key != self._spreader_task_key
            or current_size_ft is None
            or abs(current_size_ft - target_size_ft) > 0.01
        )
        if needs_size_command:
            is_new_task = task_key != self._spreader_task_key
            self._spreader_task_key = task_key
            self._spreader_task_target_size_ft = target_size_ft
            # A task boundary is the normal telescope command point.  Advance
            # toward the endpoint gradually while the gantry approaches; if
            # an authored USD sample later displaces it, keep progressing from
            # the composed live value without changing a matched tool.
            previous_size_ft = current_size_ft
            current_size_ft = controller.set_spreader_size(
                target_size_ft,
                smooth=True,
            )
            self._log(
                f"spreader {'task start' if is_new_task else 'endpoint repair'} "
                f"current={previous_size_ft} target={target_size_ft}ft "
                f"applied={current_size_ft}"
            )

        return current_size_ft, self._spreader_task_target_size_ft

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
            task,
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

        simulated_phase = _simulated_visual_phase(task)
        if simulated_phase is not None:
            operation_state = str(
                task.get("operationState") or simulated_phase
            ).strip().upper()
            self._set_operation_state_attr(stage, operation_state)
            if simulated_phase != self._task_flow_state:
                now = time.time()
                self._task_flow_state = simulated_phase
                self._task_flow_state_started_at = now
                if simulated_phase == TASK_STATE_TRUCK_APPROACH:
                    self._task_vehicle_started_at = now
                self._set_task_flow_attr(stage, simulated_phase)
                self._log(
                    "simulation flow -> "
                    f"{simulated_phase} operation={operation_state} task={key[-1]}"
                )
            return simulated_phase

        if self._operation_task_key != key:
            self._operation_task_key = key
            self._operation_state_machine = self._build_operation_state_machine(
                task, key
            )
            # Reloading the Script Editor while a PLC task is already locked
            # resumes at the lift check.  Do not skip directly to trolley
            # travel: the current bay map may require more clearance first.
            if (
                self._operation_state_machine is not None
                and _pose_spreader_locked(pose)
            ):
                self._operation_state_machine.state = OperationState.HOIST_UP
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
            locked=_pose_spreader_locked(pose),
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
        work_type, block, bay_number, pattern, _size, task_id = key
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
            container_height_m = _task_container_height_m(task)
            source_row = source.row if work_type in (WORK_TYPE_UNLOAD, WORK_TYPE_SWITCH) else None
            source_tier = source.tier if work_type in (WORK_TYPE_UNLOAD, WORK_TYPE_SWITCH) else None
            slot_heights_m = _task_bay_slot_heights_m(
                task,
                pattern,
                source_row,
                source_tier,
            )
            if work_type == WORK_TYPE_UNLOAD:
                placement_contact_m = (
                    PLC_TRUCK_CONTACT_HOIST_M
                    - PLC_CONTAINER_TIER_HEIGHT_M
                    + container_height_m
                )
            else:
                target_tier = max(1, target.tier or 1)
                target_row_heights = (
                    slot_heights_m[target.row - 1]
                    if target.row is not None and len(slot_heights_m) >= target.row
                    else ()
                )
                support_height_m = sum(target_row_heights[: target_tier - 1])
                missing_support_tiers = max(
                    0,
                    target_tier - 1 - len(target_row_heights),
                )
                placement_contact_m = (
                    support_height_m
                    + missing_support_tiers * PLC_CONTAINER_TIER_HEIGHT_M
                    + container_height_m
                )
            return RtgOperationStateMachine(
                OperationTask(
                    task_id=task_id,
                    work_type=work_type,
                    source=source,
                    target=target,
                    vision_required=bool(task.get("visionRequired", False)),
                    bay_heights=bay_heights_from_pattern(pattern),
                    container_height_m=container_height_m,
                    bay_slot_heights_m=slot_heights_m,
                ),
                truck_approach_seconds=TRUCK_APPROACH_SECONDS,
                truck_settle_seconds=TRUCK_SETTLE_SECONDS,
                truck_exit_seconds=TRUCK_EXIT_SECONDS,
                placement_load_bottom_m=placement_contact_m,
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
        locked = _pose_spreader_locked(pose)
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
        locked = _pose_spreader_locked(pose)
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
        container_height_m: float,
    ) -> None:
        from pxr import Gf, Sdf, Usd, UsdGeom
        from build_live_containers import (
            CONTAINER_VISUAL_HEIGHT_Z_M,
            CONTAINER_WIDTH_X_M,
            PAD_TOP_Z_M,
            TIER_GAP_Z_M,
            container_length_for_bay,
            create_container_box,
            create_container_details,
            material,
        )

        container_length_y = container_length_for_bay(
            block, bay_number, container_size_ft
        )
        container_visual_height_z = _container_visual_height_z(container_height_m)
        trailer_length_y, trailer_center_y, cab_center_y = _task_trailer_layout(
            container_length_y,
            container_size_ft,
        )
        trailer_center_z, box_center_z = _task_truck_vertical_layout(
            container_visual_height_z,
            TIER_GAP_Z_M,
        )

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
            root.CreateAttribute(
                "live:containerHeightMeters", Sdf.ValueTypeNames.Double
            ).Set(float(container_height_m))
            self._set_xform_translate(root, Gf.Vec3d(0.0, 0.0, PAD_TOP_Z_M))

            self._create_task_cube(
                stage,
                f"{LIVE_TASK_TRUCK_PATH}/Trailer",
                Gf.Vec3d(0.0, trailer_center_y, trailer_center_z),
                Gf.Vec3f(TASK_TRUCK_TRAILER_WIDTH_X_M, trailer_length_y, 0.18),
                Gf.Vec3f(0.28, 0.24, 0.18),
            )
            self._create_task_cube(
                stage,
                f"{LIVE_TASK_TRUCK_PATH}/Cab",
                Gf.Vec3d(0.0, cab_center_y, 0.66),
                Gf.Vec3f(1.65, 1.35, 0.78),
                Gf.Vec3f(0.92, 0.90, 0.84),
            )
            self._create_task_cube(
                stage,
                f"{LIVE_TASK_TRUCK_PATH}/CabWindshield",
                Gf.Vec3d(0.0, cab_center_y - 0.69, 0.83),
                Gf.Vec3f(1.15, 0.05, 0.30),
                Gf.Vec3f(0.02, 0.04, 0.055),
            )
            for index, (x, y) in enumerate(
                (
                    (
                        -TASK_TRUCK_TRAILER_WHEEL_X_M,
                        trailer_center_y - trailer_length_y * 0.36,
                    ),
                    (
                        TASK_TRUCK_TRAILER_WHEEL_X_M,
                        trailer_center_y - trailer_length_y * 0.36,
                    ),
                    (
                        -TASK_TRUCK_TRAILER_WHEEL_X_M,
                        trailer_center_y + trailer_length_y * 0.36,
                    ),
                    (
                        TASK_TRUCK_TRAILER_WHEEL_X_M,
                        trailer_center_y + trailer_length_y * 0.36,
                    ),
                    (-0.84, cab_center_y - 0.07),
                    (0.84, cab_center_y - 0.07),
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

            truck_box_path = f"{LIVE_TASK_TRUCK_PATH}/LoadedContainer"
            truck_box_scale = Gf.Vec3f(
                CONTAINER_WIDTH_X_M,
                container_length_y,
                container_visual_height_z,
            )
            truck_box_material = material(
                stage,
                "TaskTruckContainerBlue",
                Gf.Vec3f(0.04, 0.20, 0.48),
            )
            create_container_box(
                stage,
                truck_box_path,
                Gf.Vec3d(0.0, TASK_TRUCK_LOADED_BOX_LOCAL_Y_M, box_center_z),
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
                Gf.Vec3d(0.0, TASK_TRUCK_LOADED_BOX_LOCAL_Y_M, box_center_z),
                truck_box_scale,
            )

    def _update_task_vehicle_pose(
        self,
        stage: Any,
        block: str,
        bay_number: int,
        container_size_ft: int,
        work_type: str,
        flow_state: str,
    ) -> float:
        from pxr import Gf, Usd
        from build_live_containers import (
            container_center_y_for_bay,
        )

        lane_center_x = self._task_truck_lane_center_x()
        target_y = _task_truck_stop_y_for_box_center(
            container_center_y_for_bay(block, bay_number, container_size_ft),
            container_size_ft,
            work_type,
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
            truck_lane_bounds_x,
        )

        lane_left_x, lane_right_x = truck_lane_bounds_x()
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
        task: Optional[Dict[str, Any]] = None,
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
            and _pose_spreader_locked(pose or {})
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
                    # Lock is the attachment boundary.  From this frame on
                    # the exact source box must share the spreader's target
                    # transform with no extra interpolation; otherwise the
                    # trolley moves ahead while the box spends a second
                    # chasing it and the two visibly separate.
                    self._move_task_source_with_spreader(
                        stage,
                        trolley_m=_as_float((pose or {}).get("trolleyMeter")),
                    )
                elif work_type == WORK_TYPE_UNLOAD and flow_state in (
                    TASK_STATE_PLACED,
                    TASK_STATE_TRUCK_EXITING,
                ):
                    # Keep the exact bay-map source box (including its ribs
                    # and material) and move it onto the truck after unlock.
                    # The truck root is animated during TRUCK_EXITING, so this
                    # is intentionally repeated every frame to make the box
                    # visibly travel with the vehicle.
                    self._move_task_source_to_truck(stage)
                    self._set_container_visual_visibility(
                        stage,
                        f"{LIVE_TASK_TRUCK_PATH}/LoadedContainer",
                        False,
                    )
                elif work_type == WORK_TYPE_UNLOAD and flow_state == "COMPLETE":
                    self._set_container_visual_visibility(
                        stage,
                        self._last_task_source_path,
                        False,
                    )
                return

            if (
                work_type == WORK_TYPE_LOAD
                and task is not None
                and flow_state
                in (TASK_STATE_PLACED, TASK_STATE_TRUCK_EXITING, "COMPLETE")
            ):
                self._commit_load_to_task_stack(stage, task)

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
                stage,
                attachment_height,
                trolley_m=_as_float((pose or {}).get("trolleyMeter")),
            )
            if world_center is None:
                return
            if work_type == WORK_TYPE_LOAD:
                # The RTG payload hierarchy has a model-local longitudinal
                # offset.  A LOAD box starts on the task truck, so retain that
                # exact world Y after lock and only let the trolley/hoist
                # update X/Z.  Otherwise the generated blue copy can appear
                # several bays ahead of the truck it was picked from.
                world_center = self._load_carried_box_world_center(
                    stage, world_center
                )
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

    def _load_target_stack_slot(
        self, task: Dict[str, Any]
    ) -> Optional[tuple[int, int]]:
        """Return the row/tier that receives an inbound LOAD box."""

        parsed = self._parse_task_position_details(
            task.get("endPos") or task.get("targetPos")
        )
        if parsed is None:
            return None
        _block, _bay_number, row, tier = parsed
        if row is None or tier is None or not 1 <= row <= 6 or not 1 <= tier <= 6:
            return None
        return row, tier

    def _commit_load_to_task_stack(self, stage: Any, task: Dict[str, Any]) -> bool:
        """Materialize a released truck box in the live target bay map."""

        target = self._load_target_stack_slot(task)
        if target is None:
            return False
        row, tier = target
        target_path = f"{LIVE_TASK_STACK_PATH}/R{row:02d}_T{tier:02d}_Container"
        if stage.GetPrimAtPath(target_path):
            return True

        from pxr import Gf, Sdf, UsdGeom, UsdShade
        from build_live_containers import (
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
            material,
            reference_first_row_center_x_m,
        )

        stack = stage.GetPrimAtPath(LIVE_TASK_STACK_PATH)
        source = stage.GetPrimAtPath(f"{LIVE_TASK_TRUCK_PATH}/LoadedContainer")
        if not stack or not source:
            return False
        block_attr = stack.GetAttribute("live:block")
        bay_attr = stack.GetAttribute("live:bayNumber")
        size_attr = stack.GetAttribute("live:containerSizeFt")
        pattern_attr = stack.GetAttribute("live:bayMapPattern")
        block = block_attr.Get() if block_attr else None
        bay_number = bay_attr.Get() if bay_attr else None
        container_size_ft = size_attr.Get() if size_attr else None
        pattern = pattern_attr.Get() if pattern_attr else None
        if (
            not block
            or bay_number is None
            or container_size_ft not in (20, 40)
            or not isinstance(pattern, str)
            or not re.fullmatch(r"\d{6}", pattern)
        ):
            return False

        current_height = int(pattern[row - 1])
        if tier > current_height + 1:
            self._log(
                f"cannot place LOAD at row={row} tier={tier}: "
                f"bay map {pattern} has a gap"
            )
            return False
        new_height = max(current_height, tier)
        effective_pattern = (
            pattern[: row - 1] + str(new_height) + pattern[row:]
        )
        source_scale = self._get_xform_scale(source)
        scale = source_scale or Gf.Vec3f(
            CONTAINER_WIDTH_X_M,
            container_length_for_bay(block, int(bay_number), int(container_size_ft)),
            CONTAINER_VISUAL_HEIGHT_Z_M,
        )
        binding = UsdShade.MaterialBindingAPI(source).ComputeBoundMaterial()
        bound_material = binding[0] if binding else None
        if not bound_material:
            bound_material = material(
                stage,
                "TaskPlacedLoadFallback",
                Gf.Vec3f(0.04, 0.20, 0.48),
            )

        row_pitch = CONTAINER_WIDTH_X_M + ROW_GAP_X_M
        first_row_center_x = reference_first_row_center_x_m(6)
        leftmost_stack_x = first_row_center_x - 5 * row_pitch
        x = leftmost_stack_x + (6 - row) * row_pitch
        y = container_center_y_for_bay(block, int(bay_number), int(container_size_ft))
        slot_heights_m = _task_bay_slot_heights_m(task, pattern)
        support_heights_m = slot_heights_m[row - 1][: max(0, tier - 1)]
        support_visual_height_z = sum(
            _container_visual_height_z(height_m) for height_m in support_heights_m
        )
        missing_support_tiers = max(0, tier - 1 - len(support_heights_m))
        support_visual_height_z += (
            missing_support_tiers * CONTAINER_VISUAL_HEIGHT_Z_M
        )
        z = (
            PAD_TOP_Z_M
            + STACK_FLOOR_CLEARANCE_M
            + support_visual_height_z
            + (tier - 1) * TIER_GAP_Z_M
            + float(scale[2]) * 0.5
        )
        center = Gf.Vec3d(x, y, z)
        create_container_box(
            stage,
            target_path,
            center,
            scale,
            bound_material,
            bay_id=f"{block}/{int(bay_number):03d}",
            row=row,
            tier=tier,
            tiers=new_height,
        )
        create_container_details(stage, target_path, center, scale)
        placed = stage.GetPrimAtPath(target_path)
        placed.CreateAttribute("live:source", Sdf.ValueTypeNames.Token).Set(
            "load_placement"
        )
        placed.CreateAttribute("live:taskId", Sdf.ValueTypeNames.String).Set(
            str(task.get("taskId") or "")
        )
        for row_tier in range(1, new_height + 1):
            row_prim = stage.GetPrimAtPath(
                f"{LIVE_TASK_STACK_PATH}/R{row:02d}_T{row_tier:02d}_Container"
            )
            if row_prim:
                attr = row_prim.GetAttribute("live:tiers")
                if attr:
                    attr.Set(new_height)
        stack.CreateAttribute(
            "live:effectiveBayMapPattern", Sdf.ValueTypeNames.String
        ).Set(effective_pattern)
        self._log(
            f"placed LOAD into row={row} tier={tier}; "
            f"bayMap {pattern} -> {effective_pattern}"
        )
        return True

    @staticmethod
    def _load_carried_box_world_center(stage: Any, fallback_center: Any) -> Any:
        """Keep a truck-picked box on the source truck's longitudinal centre."""

        from pxr import Gf, UsdGeom

        source = stage.GetPrimAtPath(f"{LIVE_TASK_TRUCK_PATH}/LoadedContainer")
        if not source:
            return fallback_center
        source_world = UsdGeom.XformCache().GetLocalToWorldTransform(source).Transform(
            Gf.Vec3d(0.0, 0.0, 0.0)
        )
        source_y = float(source_world[1])
        if not math.isfinite(source_y):
            return fallback_center
        return Gf.Vec3d(
            float(fallback_center[0]),
            source_y,
            float(fallback_center[2]),
        )

    @staticmethod
    def _hoisted_container_world_center(
        stage: Any,
        container_height: float,
        *,
        trolley_m: Optional[float] = None,
    ) -> Any:
        """Return the carried-box centre from the rendered rope lower ends.

        The rope curve points are driven by the same trolley and hoist values
        as the visible RTG.  Averaging their lower endpoints therefore gives a
        stable attachment centre without relying on mesh transforms that may
        have been baked during USD export.
        """

        from pxr import Gf, UsdGeom
        from rtg_live_control import (
            LOWER_HEADER_SOURCE_Z,
            LOWER_ROPE_VISIBLE_OFFSET_Z,
        )

        hoist = stage.GetPrimAtPath(RTG_HOIST_PATH)
        if not hoist:
            return None
        local_center = Gf.Vec3d(
            HOISTED_LOAD_LOCAL_CENTER_X,
            HOISTED_LOAD_LOCAL_CENTER_Y,
            _hoisted_container_local_center_z(container_height),
        )
        fallback_center = UsdGeom.XformCache().GetLocalToWorldTransform(hoist).Transform(
            local_center
        )
        ropes = UsdGeom.BasisCurves.Get(stage, RTG_ROPE_SYSTEM_PATH)
        if not ropes:
            return fallback_center

        points = ropes.GetPointsAttr().Get()
        lower_points = (
            [point for index, point in enumerate(points) if index % 2 == 1]
            if points
            else []
        )
        if not lower_points:
            return fallback_center
        count = float(len(lower_points))
        lower_local = Gf.Vec3d(
            sum(float(point[0]) for point in lower_points) / count,
            sum(float(point[1]) for point in lower_points) / count,
            sum(float(point[2]) for point in lower_points) / count,
        )
        rope_world = UsdGeom.XformCache().GetLocalToWorldTransform(
            ropes.GetPrim()
        ).Transform(lower_local)
        local_vertical_drop = (
            float(LOWER_HEADER_SOURCE_Z)
            + float(LOWER_ROPE_VISIBLE_OFFSET_Z)
            - _hoisted_container_local_center_z(container_height)
        )
        calibrated_x = _carried_box_world_x(trolley_m)
        values = (
            float(rope_world[0]) if calibrated_x is None else calibrated_x,
            float(rope_world[1]),
            float(rope_world[2]) - local_vertical_drop,
        )
        if not all(math.isfinite(value) for value in values):
            return fallback_center
        return Gf.Vec3d(
            values[0],
            values[1],
            values[2],
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
            self._task_source_truck_local_center = None
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
        self._task_source_truck_local_center = None

    def _move_task_source_with_spreader(
        self,
        stage: Any,
        *,
        trolley_m: Optional[float] = None,
    ) -> None:
        """Attach the original task-stack source container to the spreader."""

        if not self._last_task_source_path:
            return
        from pxr import Gf, UsdGeom

        source = stage.GetPrimAtPath(self._last_task_source_path)
        if not source:
            self._log(
                f"cannot attach source: missing prim {self._last_task_source_path}"
            )
            return
        source_scale = self._get_xform_scale(source)
        source_translate = self._get_xform_translate(source)
        parent = source.GetParent()
        if source_scale is None or source_translate is None or not parent:
            self._log(
                f"cannot attach source: incomplete xform {self._last_task_source_path}"
            )
            return

        source_key = str(source.GetPath())
        source_origin_local = self._task_source_original_translates.get(
            source_key,
            source_translate,
        )
        parent_world = UsdGeom.XformCache().GetLocalToWorldTransform(parent)
        source_origin_world = parent_world.Transform(source_origin_local)
        spreader_world = self._hoisted_container_world_center(
            stage,
            float(source_scale[2]),
            trolley_m=trolley_m,
        )
        if spreader_world is None:
            self._log("cannot attach source: spreader transform unavailable")
            return
        # The source box already owns the authoritative bay centre.  Preserve
        # that world Y while carrying: the RTG payload/rope hierarchy has a
        # model-local longitudinal offset, and applying it to a world-authored
        # bay-map box a second time makes the box drift in depth and appear to
        # detach from the spreader as the trolley returns to the truck.
        spreader_world = Gf.Vec3d(
            float(spreader_world[0]),
            float(source_origin_world[1]),
            float(spreader_world[2]),
        )
        target_local = parent_world.GetInverse().Transform(spreader_world)
        was_moved = self._task_source_has_moved
        self._move_task_source_to_local_target(stage, source, parent, target_local)
        if not was_moved:
            self._log(
                f"attached source box {self._last_task_source_path} to spreader"
            )

    def _move_task_source_to_truck(self, stage: Any) -> None:
        """Place the original source box without a sideways unlock jump.

        At unlock, preserve the carried box's current X/Y below the spreader
        and only settle its Z onto the authored truck load height.  Capture
        that point in truck-local coordinates so the exact source box follows
        the truck during its exit animation.
        """

        if not self._last_task_source_path:
            return
        from pxr import Gf, UsdGeom

        source = stage.GetPrimAtPath(self._last_task_source_path)
        truck = stage.GetPrimAtPath(LIVE_TASK_TRUCK_PATH)
        loaded = stage.GetPrimAtPath(f"{LIVE_TASK_TRUCK_PATH}/LoadedContainer")
        if not source or not truck or not loaded:
            return
        parent = source.GetParent()
        source_local = self._get_xform_translate(source)
        if not parent or source_local is None:
            return

        xform_cache = UsdGeom.XformCache()
        truck_world = xform_cache.GetLocalToWorldTransform(truck)
        parent_world = xform_cache.GetLocalToWorldTransform(parent)
        if self._task_source_truck_local_center is None:
            source_world = parent_world.Transform(source_local)
            loaded_world = xform_cache.GetLocalToWorldTransform(loaded).Transform(
                Gf.Vec3d(0.0, 0.0, 0.0)
            )
            placed_world = Gf.Vec3d(
                float(source_world[0]),
                float(source_world[1]),
                float(loaded_world[2]),
            )
            self._task_source_truck_local_center = (
                truck_world.GetInverse().Transform(placed_world)
            )
            self._log(
                "captured placed source under spreader "
                f"truckLocal={tuple(round(float(v), 3) for v in self._task_source_truck_local_center)}"
            )

        target_world = truck_world.Transform(self._task_source_truck_local_center)
        target_local = parent_world.GetInverse().Transform(target_world)
        self._move_task_source_to_local_target(stage, source, parent, target_local)

    def _move_task_source_to_local_target(
        self,
        stage: Any,
        source: Any,
        parent: Any,
        target_local: Any,
    ) -> None:
        """Translate the source body and authored detail siblings together."""

        from pxr import Gf

        source_translate = self._get_xform_translate(source)
        if source_translate is None:
            return
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
        OmniverseRtgTwinDriver._get_or_add_xform_op(
            xform,
            "xformOp:translate",
            xform.AddTranslateOp,
        ).Set(center)
        OmniverseRtgTwinDriver._get_or_add_xform_op(
            xform,
            "xformOp:scale",
            xform.AddScaleOp,
        ).Set(scale)
        cube.CreateDisplayColorAttr([color])

    @staticmethod
    def _set_xform_translate(prim: Any, value: Any) -> None:
        from pxr import UsdGeom

        xform = UsdGeom.Xformable(prim)
        for op in xform.GetOrderedXformOps():
            if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                op.Set(value)
                return
        OmniverseRtgTwinDriver._get_or_add_xform_op(
            xform,
            "xformOp:translate",
            xform.AddTranslateOp,
        ).Set(value)

    @staticmethod
    def _set_xform_rotate_xyz(prim: Any, value: Any) -> None:
        from pxr import UsdGeom

        xform = UsdGeom.Xformable(prim)
        for op in xform.GetOrderedXformOps():
            if op.GetOpName() == "xformOp:rotateXYZ":
                op.Set(value)
                return
        OmniverseRtgTwinDriver._get_or_add_xform_op(
            xform,
            "xformOp:rotateXYZ",
            xform.AddRotateXYZOp,
        ).Set(value)

    @staticmethod
    def _get_xform_translate(prim: Any) -> Any:
        """Return the authored translate value without creating a new xform op."""

        if not prim:
            return None
        from pxr import UsdGeom

        xform = UsdGeom.Xformable(prim)
        for op in xform.GetOrderedXformOps():
            if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                return op.Get()
        return None

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
        OmniverseRtgTwinDriver._get_or_add_xform_op(
            xform,
            "xformOp:scale",
            xform.AddScaleOp,
        ).Set(value)

    @staticmethod
    def _get_or_add_xform_op(
        xform: Any,
        op_name: str,
        add_op: Any,
    ) -> Any:
        from pxr import UsdGeom

        ordered_ops = list(xform.GetOrderedXformOps())
        for op in ordered_ops:
            if op.GetOpName() == op_name:
                return op
        attr = xform.GetPrim().GetAttribute(op_name)
        if attr:
            op = UsdGeom.XformOp(attr)
            xform.SetXformOpOrder(ordered_ops + [op])
            return op
        return add_op()

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
        elif (
            len(digits) == 3
            and digits[0] != "0"
            and 1 <= int(digits[1]) <= 6
            and 1 <= int(digits[2]) <= 6
        ):
            # Accept compact task positions such as 1C423 for bay 4, row 2,
            # tier 3 as well as the zero-padded 1C0423 form used by PLCs.
            bay_text = digits[0]
            row = int(digits[1])
            tier = int(digits[2])
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
        slot_heights_m: tuple[tuple[float, ...], ...],
        *,
        include_truck_lane: bool = True,
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
            bottom_z = PAD_TOP_Z_M + STACK_FLOOR_CLEARANCE_M
            for tier in range(1, height + 1):
                actual_height_m = (
                    slot_heights_m[row_index - 1][tier - 1]
                    if len(slot_heights_m) >= row_index
                    and len(slot_heights_m[row_index - 1]) >= tier
                    else PLC_CONTAINER_TIER_HEIGHT_M
                )
                visual_height_z = _container_visual_height_z(actual_height_m)
                z = bottom_z + visual_height_z * 0.5
                container_path = f"{root_path}/R{row_index:02d}_T{tier:02d}_Container"
                mat = materials[(row_index * 3 + tier * 5) % len(materials)]
                create_container_box(
                    stage,
                    container_path,
                    Gf.Vec3d(x, bay_center_y, z),
                    Gf.Vec3f(row_width, container_length_y, visual_height_z),
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
                    Gf.Vec3f(row_width, container_length_y, visual_height_z),
                )
                container = stage.GetPrimAtPath(container_path)
                container.CreateAttribute(
                    "live:containerHeightMeters", Sdf.ValueTypeNames.Double
                ).Set(actual_height_m)
                bottom_z += visual_height_z + TIER_GAP_Z_M

        if include_truck_lane:
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
        self._spreader_task_key = None
        self._spreader_task_target_size_ft = None
        self._log("RTGController reloaded and bound to current stage")
        return self._controller

    def _sync_floating_runinfo_billboard(
        self,
        stage: Any,
        runinfo: Dict[str, Any],
        clock_text: Optional[str] = None,
    ) -> None:
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

        if not RUNINFO_FLOATING_ENABLED:
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                for path in (
                    LEGACY_RUNINFO_FLOATING_ROOT_PATH,
                    RUNINFO_FLOATING_ROOT_PATH,
                ):
                    prim = stage.GetPrimAtPath(path)
                    if prim:
                        stage.RemovePrim(prim.GetPath())
            self._floating_runinfo_text = None
            self._floating_runinfo_clock_text = None
            self._floating_runinfo_level_key = None
            return

        text = str(runinfo.get("message") or "").strip() if isinstance(runinfo, dict) else ""
        if not text:
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                legacy = stage.GetPrimAtPath(LEGACY_RUNINFO_FLOATING_ROOT_PATH)
                if legacy:
                    stage.RemovePrim(legacy.GetPath())
                root = stage.GetPrimAtPath(RUNINFO_FLOATING_ROOT_PATH)
                if root:
                    stage.RemovePrim(root.GetPath())
            self._floating_runinfo_text = None
            self._floating_runinfo_clock_text = None
            self._floating_runinfo_level_key = None
            return

        clock_text = clock_text or _runinfo_time_text(runinfo)
        level_key = _runinfo_level_key(runinfo)
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            legacy = stage.GetPrimAtPath(LEGACY_RUNINFO_FLOATING_ROOT_PATH)
            if legacy:
                stage.RemovePrim(legacy.GetPath())
            if (
                self._floating_runinfo_text == text
                and self._floating_runinfo_clock_text == clock_text
                and self._floating_runinfo_level_key == level_key
                and stage.GetPrimAtPath(RUNINFO_FLOATING_PANEL_PATH)
            ):
                root = stage.GetPrimAtPath(RUNINFO_FLOATING_ROOT_PATH)
                if root:
                    UsdGeom.Imageable(root).MakeVisible()
                    self._sync_floating_runinfo_pose(stage, root)
                return

            self._floating_runinfo_text = text
            self._floating_runinfo_clock_text = clock_text
            self._floating_runinfo_level_key = level_key
            self._floating_runinfo_bitmap_seq += 1
            texture_path = (
                Path(tempfile.gettempdir())
                / f"smart_port_rtg_runinfo_float_{os.getpid()}_{self._floating_runinfo_bitmap_seq}.png"
            )
            if not _render_floating_runinfo_bitmap(
                text,
                texture_path,
                clock_text,
                level_key,
            ):
                self._log("floating runinfo bitmap render failed")
                return

            root = UsdGeom.Xform.Define(stage, RUNINFO_FLOATING_ROOT_PATH)
            root_prim = root.GetPrim()
            UsdGeom.Imageable(root_prim).MakeVisible()
            self._sync_floating_runinfo_pose(stage, root_prim)

            panel = UsdGeom.Mesh.Define(stage, RUNINFO_FLOATING_PANEL_PATH)
            panel_prim = panel.GetPrim()
            half_w = RUNINFO_FLOATING_PANEL_WIDTH_M * 0.5
            half_h = RUNINFO_FLOATING_PANEL_HEIGHT_M * 0.5
            panel.CreatePointsAttr(
                [
                    Gf.Vec3f(half_w, 0.0, -half_h),
                    Gf.Vec3f(-half_w, 0.0, -half_h),
                    Gf.Vec3f(-half_w, 0.0, half_h),
                    Gf.Vec3f(half_w, 0.0, half_h),
                ]
            )
            panel.CreateFaceVertexCountsAttr([4])
            panel.CreateFaceVertexIndicesAttr(list(RUNINFO_FLOATING_FACE_VERTEX_INDICES))
            panel.CreateDoubleSidedAttr(True)
            panel.CreateExtentAttr(
                [
                    Gf.Vec3f(-half_w, 0.0, -half_h),
                    Gf.Vec3f(half_w, 0.0, half_h),
                ]
            )
            st = UsdGeom.PrimvarsAPI(panel_prim).CreatePrimvar(
                "st",
                Sdf.ValueTypeNames.TexCoord2fArray,
                UsdGeom.Tokens.faceVarying,
            )
            st.Set(
                [Gf.Vec2f(u, v) for u, v in RUNINFO_FLOATING_UV_COORDS]
            )
            panel_prim.CreateAttribute("rtg:runinfoText", Sdf.ValueTypeNames.String).Set(
                text
            )
            panel_prim.CreateAttribute(
                "rtg:runinfoTexture", Sdf.ValueTypeNames.Asset
            ).Set(Sdf.AssetPath(str(texture_path)))

            material = UsdShade.Material.Define(stage, RUNINFO_FLOATING_MATERIAL_PATH)
            shader = UsdShade.Shader.Define(
                stage, f"{RUNINFO_FLOATING_MATERIAL_PATH}/PreviewSurface"
            )
            shader.CreateIdAttr("UsdPreviewSurface")
            shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.42)
            shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
            texture = UsdShade.Shader.Define(
                stage, f"{RUNINFO_FLOATING_MATERIAL_PATH}/Texture"
            )
            texture.CreateIdAttr("UsdUVTexture")
            texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(
                Sdf.AssetPath(str(texture_path))
            )
            texture.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
            reader = UsdShade.Shader.Define(
                stage, f"{RUNINFO_FLOATING_MATERIAL_PATH}/Primvar_st"
            )
            reader.CreateIdAttr("UsdPrimvarReader_float2")
            reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
            texture.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(
                reader.ConnectableAPI(), "result"
            )
            shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
                texture.ConnectableAPI(), "rgb"
            )
            shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).ConnectToSource(
                texture.ConnectableAPI(), "a"
            )
            material.CreateSurfaceOutput().ConnectToSource(
                shader.ConnectableAPI(), "surface"
            )
            UsdShade.MaterialBindingAPI(panel_prim).Bind(material)

    def _sync_floating_runinfo_pose(self, stage: Any, root_prim: Any) -> None:
        from pxr import Gf, Usd, UsdGeom

        attach = stage.GetPrimAtPath(RUNINFO_FLOATING_ATTACH_PATH.rstrip("/"))
        translate = Gf.Vec3d(*RUNINFO_FLOATING_LOCAL_TRANSLATE)
        rtg = stage.GetPrimAtPath(RUNINFO_FLOATING_ABOVE_RTG_PATH)
        if rtg:
            try:
                cache = UsdGeom.BBoxCache(
                    Usd.TimeCode.Default(),
                    [UsdGeom.Tokens.default_],
                )
                aligned = cache.ComputeWorldBound(rtg).ComputeAlignedBox()
                bounds_min = aligned.GetMin()
                bounds_max = aligned.GetMax()
                translate = Gf.Vec3d(
                    (float(bounds_min[0]) + float(bounds_max[0])) * 0.5,
                    (float(bounds_min[1]) + float(bounds_max[1])) * 0.5,
                    float(bounds_max[2])
                    + RUNINFO_FLOATING_ABOVE_GAP_M
                    + RUNINFO_FLOATING_PANEL_HEIGHT_M * 0.5,
                )
            except Exception:
                pass
        if rtg is None and attach:
            try:
                cache = UsdGeom.XformCache(Usd.TimeCode.Default())
                matrix = cache.GetLocalToWorldTransform(attach)
                translate = matrix.Transform(Gf.Vec3d(*RUNINFO_FLOATING_LOCAL_TRANSLATE))
            except Exception:
                pass
        translate = Gf.Vec3d(
            float(translate[0]) + RUNINFO_FLOATING_RIGHT_X_OFFSET_M,
            float(translate[1]) + RUNINFO_FLOATING_BACK_Y_OFFSET_M,
            float(translate[2]) + RUNINFO_FLOATING_UP_Z_OFFSET_M,
        )
        self._set_xform_translate(root_prim, translate)
        rotate = camera_facing_rotate_xyz(
            stage,
            translate,
            RUNINFO_FLOATING_ROTATE_XYZ,
        )
        self._set_xform_rotate_xyz(root_prim, Gf.Vec3f(*rotate))

    @staticmethod
    def _clear_runinfo_viewport_selection() -> None:
        try:
            import omni.usd  # type: ignore

            selection = omni.usd.get_context().get_selection()
            paths = list(selection.get_selected_prim_paths())
            if not any(
                path == RUNINFO_FLOATING_ROOT_PATH
                or path.startswith(f"{RUNINFO_FLOATING_ROOT_PATH}/")
                for path in paths
            ):
                return
            try:
                selection.clear_selected_prim_paths()
            except Exception:
                selection.set_selected_prim_paths([], False)
        except Exception:
            return

    def _sync_visible_panels_facing_camera_if_due(self) -> None:
        """Update every 3D info panel from one camera read, capped at 30 Hz."""

        now = time.monotonic()
        last_update = float(
            getattr(self, "_last_panel_facing_update_at", 0.0) or 0.0
        )
        if now - last_update < LIVE_INFO_CAMERA_FACING_UPDATE_INTERVAL_SECONDS:
            return
        self._last_panel_facing_update_at = now

        stage = self._current_stage()
        if stage is None:
            return
        camera_position = active_viewport_camera_position(stage)
        self._sync_visible_task_panel_facing_camera(stage, camera_position)
        self._sync_visible_live_info_panels_facing_camera(stage, camera_position)

    def _sync_floating_task_panel(self, stage: Any, task: Dict[str, Any]) -> None:
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

        if not task or not task.get("taskId"):
            self._log_task_panel_status(
                "hidden_no_task",
                "task panel hidden: TwinState has no active task",
            )
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                root = stage.GetPrimAtPath(TASK_PANEL_ROOT_PATH)
                if root:
                    UsdGeom.Imageable(root).MakeInvisible()
            self._floating_task_panel_key = None
            return

        stack_key = self._task_stack_key(task)
        if stack_key is None:
            self._log_task_panel_status(
                "hidden_invalid_task",
                "task panel hidden: task has no valid bayMap/position "
                f"task={task.get('taskId') or '--'} "
                f"work={task.get('workType') or task.get('taskType') or '--'} "
                f"start={task.get('startPos') or task.get('sourcePos') or '--'} "
                f"end={task.get('endPos') or task.get('targetPos') or '--'} "
                f"bayMap={task.get('bayMap') or '--'}",
            )
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                root = stage.GetPrimAtPath(TASK_PANEL_ROOT_PATH)
                if root:
                    UsdGeom.Imageable(root).MakeInvisible()
            self._floating_task_panel_key = None
            return

        panel_key = (
            str(task.get("taskId") or ""),
            str(task.get("taskState") or task.get("state") or ""),
            str(task.get("workType") or task.get("taskType") or ""),
            str(task.get("startPos") or task.get("sourcePos") or ""),
            str(task.get("endPos") or task.get("targetPos") or ""),
            str(task.get("containerNo") or ""),
            str(task.get("containerType") or ""),
            str(task.get("containerHeight") or ""),
            str(task.get("spreaderPos") or ""),
            str(task.get("carType") or ""),
            str(task.get("carNo") or ""),
            str(task.get("bayMap") or ""),
            str(task.get("operationState") or task.get("visualPhase") or ""),
        )
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            if (
                panel_key == self._floating_task_panel_key
                and stage.GetPrimAtPath(TASK_PANEL_MESH_PATH)
            ):
                root = stage.GetPrimAtPath(TASK_PANEL_ROOT_PATH)
                if root:
                    UsdGeom.Imageable(root).MakeVisible()
                    self._sync_floating_task_panel_pose(root, stack_key)
                return

            self._floating_task_panel_key = panel_key
            self._floating_task_panel_bitmap_seq += 1
            texture_path = (
                Path(tempfile.gettempdir())
                / f"smart_port_rtg_task_panel_{os.getpid()}_{self._floating_task_panel_bitmap_seq}.png"
            )
            if not _render_task_info_panel_bitmap(task, texture_path):
                self._log("task info panel bitmap render failed")
                return

            root = UsdGeom.Xform.Define(stage, TASK_PANEL_ROOT_PATH)
            root_prim = root.GetPrim()
            UsdGeom.Imageable(root_prim).MakeVisible()
            self._sync_floating_task_panel_pose(root_prim, stack_key)

            panel = UsdGeom.Mesh.Define(stage, TASK_PANEL_MESH_PATH)
            panel_prim = panel.GetPrim()
            half_w = TASK_PANEL_WIDTH_M * 0.5
            half_h = TASK_PANEL_HEIGHT_M * 0.5
            panel.CreatePointsAttr(
                [
                    Gf.Vec3f(half_w, 0.0, -half_h),
                    Gf.Vec3f(-half_w, 0.0, -half_h),
                    Gf.Vec3f(-half_w, 0.0, half_h),
                    Gf.Vec3f(half_w, 0.0, half_h),
                ]
            )
            panel.CreateFaceVertexCountsAttr([4])
            panel.CreateFaceVertexIndicesAttr(list(RUNINFO_FLOATING_FACE_VERTEX_INDICES))
            panel.CreateDoubleSidedAttr(True)
            panel.CreateExtentAttr(
                [
                    Gf.Vec3f(-half_w, 0.0, -half_h),
                    Gf.Vec3f(half_w, 0.0, half_h),
                ]
            )
            st = UsdGeom.PrimvarsAPI(panel_prim).CreatePrimvar(
                "st",
                Sdf.ValueTypeNames.TexCoord2fArray,
                UsdGeom.Tokens.faceVarying,
            )
            st.Set([Gf.Vec2f(u, v) for u, v in TASK_PANEL_UV_COORDS])
            panel_prim.CreateAttribute("rtg:taskPanelTexture", Sdf.ValueTypeNames.Asset).Set(
                Sdf.AssetPath(str(texture_path))
            )
            panel_prim.CreateAttribute("rtg:taskId", Sdf.ValueTypeNames.String).Set(
                str(task.get("taskId") or "")
            )
            self._log_task_panel_status(
                "shown",
                "task panel shown "
                f"task={task.get('taskId') or '--'} "
                f"work={task.get('workType') or task.get('taskType') or '--'} "
                f"start={task.get('startPos') or task.get('sourcePos') or '--'} "
                f"end={task.get('endPos') or task.get('targetPos') or '--'} "
                f"bayMap={task.get('bayMap') or '--'} "
                f"path={TASK_PANEL_ROOT_PATH}",
            )

            material = UsdShade.Material.Define(stage, TASK_PANEL_MATERIAL_PATH)
            shader = UsdShade.Shader.Define(
                stage, f"{TASK_PANEL_MATERIAL_PATH}/PreviewSurface"
            )
            shader.CreateIdAttr("UsdPreviewSurface")
            shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.42)
            shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
            texture = UsdShade.Shader.Define(
                stage, f"{TASK_PANEL_MATERIAL_PATH}/Texture"
            )
            texture.CreateIdAttr("UsdUVTexture")
            texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(
                Sdf.AssetPath(str(texture_path))
            )
            texture.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
            reader = UsdShade.Shader.Define(
                stage, f"{TASK_PANEL_MATERIAL_PATH}/Primvar_st"
            )
            reader.CreateIdAttr("UsdPrimvarReader_float2")
            reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
            texture.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(
                reader.ConnectableAPI(), "result"
            )
            shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
                texture.ConnectableAPI(), "rgb"
            )
            shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).ConnectToSource(
                texture.ConnectableAPI(), "a"
            )
            material.CreateSurfaceOutput().ConnectToSource(
                shader.ConnectableAPI(), "surface"
            )
            UsdShade.MaterialBindingAPI(panel_prim).Bind(material)

    def _log_task_panel_status(self, status: str, message: str) -> None:
        if not TASK_PANEL_DIAGNOSTIC_LOG_ENABLED:
            return
        key = (status, message)
        if key == self._floating_task_panel_diag_key:
            return
        self._floating_task_panel_diag_key = key
        self._log(message)

    def _sync_floating_task_panel_pose(
        self,
        root_prim: Any,
        stack_key: tuple[str, int, str, int],
    ) -> None:
        from pxr import Gf
        from build_live_containers import container_center_y_for_bay

        block, bay_number, _pattern, container_size_ft = stack_key
        bay_center_y = container_center_y_for_bay(block, bay_number, container_size_ft)
        self._set_xform_translate(
            root_prim,
            Gf.Vec3d(
                TASK_PANEL_WORLD_X,
                bay_center_y + TASK_PANEL_Y_OFFSET_M,
                TASK_PANEL_WORLD_Z,
            ),
        )
        rotate = camera_facing_rotate_xyz(
            self._current_stage(),
            (
                TASK_PANEL_WORLD_X,
                bay_center_y + TASK_PANEL_Y_OFFSET_M,
                TASK_PANEL_WORLD_Z,
            ),
            TASK_PANEL_ROTATE_XYZ,
        )
        self._set_xform_rotate_xyz(root_prim, Gf.Vec3f(*rotate))

    def _sync_visible_task_panel_facing_camera(
        self,
        stage: Any = None,
        camera_position: Any = None,
    ) -> None:
        stage = stage or self._current_stage()
        if stage is None:
            return
        root = stage.GetPrimAtPath(TASK_PANEL_ROOT_PATH)
        if not root:
            return
        translate = self._get_xform_translate(root)
        if translate is None:
            return
        try:
            from pxr import Gf

            rotate = (
                camera_facing_rotate_xyz_from_camera(
                    translate,
                    camera_position,
                    TASK_PANEL_ROTATE_XYZ,
                )
                if camera_position is not None
                else camera_facing_rotate_xyz(stage, translate, TASK_PANEL_ROTATE_XYZ)
            )
            self._set_xform_rotate_xyz(root, Gf.Vec3f(*rotate))
        except Exception:
            return

    def _sync_visible_live_info_panels_facing_camera(
        self,
        stage: Any = None,
        camera_position: Any = None,
    ) -> None:
        """Keep click-to-inspect scene panels facing the camera each frame."""

        stage = stage or self._current_stage()
        if stage is None:
            return
        self._clear_runinfo_viewport_selection()
        try:
            from pxr import Gf
        except Exception:
            return
        for root_path in LIVE_INFO_CAMERA_FACING_PANEL_ROOT_PATHS:
            root = stage.GetPrimAtPath(root_path)
            if not root:
                continue
            translate = self._get_xform_translate(root)
            if translate is None:
                continue
            try:
                fallback_rotate = LIVE_INFO_CAMERA_FACING_PANEL_ROTATE_XYZ.get(
                    root_path,
                    LIVE_INFO_PANEL_ROTATE_XYZ,
                )
                rotate = (
                    camera_facing_rotate_xyz_from_camera(
                        translate,
                        camera_position,
                        fallback_rotate,
                    )
                    if camera_position is not None
                    else camera_facing_rotate_xyz(stage, translate, fallback_rotate)
                )
                self._set_xform_rotate_xyz(root, Gf.Vec3f(*rotate))
            except Exception:
                continue

    def _sync_user_side_view_camera(self, stage: Any) -> None:
        from pxr import Gf, Sdf, Usd, UsdGeom

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            camera = UsdGeom.Camera.Define(stage, USER_SIDE_VIEW_CAMERA_PATH)
            camera.CreateProjectionAttr(UsdGeom.Tokens.perspective)
            camera.CreateFocalLengthAttr(14.0)
            camera.CreateHorizontalApertureAttr(36.0)
            camera.CreateVerticalApertureAttr(20.25)
            camera.CreateClippingRangeAttr(Gf.Vec2f(0.03, 2500.0))
            prim = camera.GetPrim()
            UsdGeom.Imageable(prim).MakeInvisible()
            prim.CreateAttribute("omni:kit:cameraLock", Sdf.ValueTypeNames.Bool).Set(
                False
            )
            prim.CreateAttribute(
                "omni:kit:centerOfInterest",
                Sdf.ValueTypeNames.Vector3d,
                custom=True,
            ).Set(Gf.Vec3d(*USER_SIDE_VIEW_CAMERA_CENTER_OF_INTEREST))
            prim.CreateAttribute(
                "xformOp:translate",
                Sdf.ValueTypeNames.Double3,
            ).Set(Gf.Vec3d(*USER_SIDE_VIEW_CAMERA_TRANSLATE))
            prim.CreateAttribute(
                "xformOp:rotateYXZ",
                Sdf.ValueTypeNames.Float3,
            ).Set(Gf.Vec3f(*USER_SIDE_VIEW_CAMERA_ROTATE_YXZ))
            prim.CreateAttribute("xformOp:scale", Sdf.ValueTypeNames.Float3).Set(
                Gf.Vec3f(1.0, 1.0, 1.0)
            )
            prim.CreateAttribute("xformOpOrder", Sdf.ValueTypeNames.TokenArray).Set(
                ["xformOp:translate", "xformOp:rotateYXZ", "xformOp:scale"]
            )

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
            current_field = str(pose.get("currentField") or "").strip()
            current_bay = _as_int(pose.get("currentBay"), 0)
            self._set_attr(
                prim,
                "rtg:currentBayId",
                Sdf.ValueTypeNames.String,
                f"{current_field}/{current_bay:03d}"
                if current_field and current_bay > 0
                else "",
            )
            self._set_attr(
                prim,
                "rtg:controlMode",
                Sdf.ValueTypeNames.String,
                applied.get("controlMode") or "LIVE",
            )
            self._set_attr(
                prim,
                "rtg:manualPayload",
                Sdf.ValueTypeNames.String,
                state.get("manualPayload"),
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
            alarm_status = _as_int(pose.get("alarmStatus"), 0)
            alarm_message = (
                pose.get("alarmMessage")
                or pose.get("alarmText")
                or state.get("alarmMessage")
                or state.get("alarmText")
                or ("设备报警触发！" if alarm_status else "")
            )
            self._set_attr(
                prim, "rtg:alarmStatus", Sdf.ValueTypeNames.Int, alarm_status
            )
            self._set_attr(
                prim, "rtg:alarmActive", Sdf.ValueTypeNames.Bool, alarm_status != 0
            )
            self._set_attr(
                prim, "rtg:alarmMessage", Sdf.ValueTypeNames.String, alarm_message
            )
            self._set_attr(
                prim,
                "rtg:alarmSeverity",
                Sdf.ValueTypeNames.String,
                pose.get("alarmSeverity") or state.get("alarmSeverity") or "ALARM",
            )
            self._set_attr(
                prim,
                "rtg:unlockState",
                Sdf.ValueTypeNames.Int,
                _as_int(pose.get("unlockState"), 0),
            )
            self._set_attr(
                prim,
                "rtg:spreaderLocked",
                Sdf.ValueTypeNames.Bool,
                self._spreader_locked,
            )
            self._set_attr(
                prim,
                "rtg:tool20f",
                Sdf.ValueTypeNames.Int,
                _as_int(pose.get("tool20f"), 0),
            )
            self._set_attr(
                prim,
                "rtg:tool40f",
                Sdf.ValueTypeNames.Int,
                _as_int(pose.get("tool40f"), 0),
            )
            self._set_attr(
                prim,
                "rtg:spreaderSizeFt",
                Sdf.ValueTypeNames.Double,
                applied.get("spreaderSizeFt"),
            )
            self._set_attr(
                prim,
                "rtg:spreaderTargetSizeFt",
                Sdf.ValueTypeNames.Int,
                applied.get("spreaderTargetSizeFt"),
            )
            self._set_attr(
                prim, "rtg:taskId", Sdf.ValueTypeNames.String, task.get("taskId")
            )
            self._set_attr(
                prim,
                "rtg:taskState",
                Sdf.ValueTypeNames.Int,
                _as_int(task.get("taskState"), 0),
            )
            self._set_attr(
                prim,
                "rtg:taskType",
                Sdf.ValueTypeNames.Int,
                _as_int(task.get("taskType"), 0),
            )
            self._set_attr(
                prim, "rtg:workType", Sdf.ValueTypeNames.String, task.get("workType")
            )
            self._set_attr(
                prim, "rtg:bayMap", Sdf.ValueTypeNames.String, task.get("bayMap")
            )
            self._set_attr(
                prim,
                "rtg:operationState",
                Sdf.ValueTypeNames.String,
                task.get("operationState"),
            )
            self._set_attr(
                prim,
                "rtg:visualPhase",
                Sdf.ValueTypeNames.String,
                task.get("visualPhase"),
            )
            self._set_attr(
                prim, "rtg:startPos", Sdf.ValueTypeNames.String, task.get("startPos")
            )
            self._set_attr(
                prim, "rtg:endPos", Sdf.ValueTypeNames.String, task.get("endPos")
            )
            self._set_attr(
                prim, "rtg:sourcePos", Sdf.ValueTypeNames.String, task.get("sourcePos")
            )
            self._set_attr(
                prim, "rtg:targetPos", Sdf.ValueTypeNames.String, task.get("targetPos")
            )
            self._set_attr(
                prim,
                "rtg:containerNo",
                Sdf.ValueTypeNames.String,
                task.get("containerNo"),
            )
            self._set_attr(
                prim,
                "rtg:containerType",
                Sdf.ValueTypeNames.String,
                task.get("containerType"),
            )
            self._set_attr(
                prim,
                "rtg:containerHeight",
                Sdf.ValueTypeNames.String,
                task.get("containerHeight"),
            )
            self._set_attr(
                prim,
                "rtg:containerSize",
                Sdf.ValueTypeNames.Int,
                _as_int(task.get("containerSize"), 0),
            )
            self._set_attr(
                prim,
                "rtg:spreaderPos",
                Sdf.ValueTypeNames.String,
                task.get("spreaderPos"),
            )
            self._set_attr(
                prim, "rtg:carType", Sdf.ValueTypeNames.String, task.get("carType")
            )
            self._set_attr(
                prim, "rtg:carNo", Sdf.ValueTypeNames.String, task.get("carNo")
            )
            self._set_attr(
                prim,
                "rtg:inventoryMode",
                Sdf.ValueTypeNames.String,
                task.get("inventoryMode") or self._inventory_mode,
            )
            self._set_attr(
                prim,
                "rtg:inventorySyncState",
                Sdf.ValueTypeNames.String,
                task.get("inventorySyncState"),
            )
            self._set_attr(
                prim,
                "rtg:inventorySyncReason",
                Sdf.ValueTypeNames.String,
                task.get("inventorySyncReason"),
            )
            runinfo = state.get("runinfo") or {}
            self._set_attr(
                prim,
                "rtg:runinfo",
                Sdf.ValueTypeNames.String,
                runinfo.get("message"),
            )
            self._sync_floating_runinfo_billboard(stage, runinfo)
            self._sync_floating_task_panel(stage, task)
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


def _start_live_info_popups() -> None:
    """Enable click-to-inspect live RTG and container HUD popups."""

    for module_name, starter_name in (
        ("rtg_device_info_popup", "start_rtg_device_info_popup"),
        ("rtg_container_info_popup", "start_rtg_container_info_popup"),
    ):
        try:
            module = importlib.import_module(module_name)
            module = importlib.reload(module)
            starter = getattr(module, starter_name)
            starter()
            print(f"[RTG Twin Driver] enabled {module_name}")
        except Exception as exc:  # noqa: BLE001 - live driver can run without UI
            print(f"[RTG Twin Driver] unable to enable {module_name}: {exc}")


def _stop_live_info_popups() -> None:
    """Stop click-to-inspect live UI services when live mode is stopped."""

    for module_name, stopper_name in (
        ("rtg_container_info_popup", "stop_rtg_container_info_popup"),
        ("rtg_device_info_popup", "stop_rtg_device_info_popup"),
    ):
        try:
            module = importlib.import_module(module_name)
            stopper = getattr(module, stopper_name)
            stopper()
        except Exception as exc:  # noqa: BLE001 - shutdown should be best-effort
            print(f"[RTG Twin Driver] unable to stop {module_name}: {exc}")


def _enable_live_viewport_selection() -> None:
    """Best-effort restore of viewport picking for click-to-inspect live UI."""

    try:
        import carb.settings  # type: ignore

        settings = carb.settings.get_settings()
        for path, value in (
            ("/app/window/drawMouse", True),
            ("/persistent/app/viewport/coiDoubleClick", True),
            ("/persistent/app/viewport/coiDoubleClickObjectOnly", True),
            ("/persistent/app/viewport/pickingMode", "type:ALL"),
            ("/persistent/app/viewport/pickingModeNoKinds", True),
            ("/persistent/app/viewport/pickingModeIncludeRef", True),
            ("/exts/omni.kit.viewport.window/startup/cameraManipulator/enabled", True),
            ("/exts/omni.kit.viewport.window/startup/selectionManipulator/enabled", True),
        ):
            settings.set(path, value)
    except Exception as exc:  # noqa: BLE001 - selection may still work
        print(f"[RTG Twin Driver] viewport selection settings unavailable: {exc}")

    previous = getattr(builtins, "_rtg_viewport_read_only_sub", None)
    if previous is not None:
        try:
            previous.unsubscribe()
        except Exception:
            pass
        builtins._rtg_viewport_read_only_sub = None

    try:
        from omni.kit.viewport.utility import get_active_viewport_window  # type: ignore

        window = get_active_viewport_window()
        if window is not None:
            for attr, value in (
                ("enabled", True),
                ("visible", True),
                ("mouse_enabled", True),
                ("keyboard_enabled", True),
            ):
                try:
                    setattr(window, attr, value)
                except Exception:
                    pass
    except Exception:
        pass

    try:
        import omni.kit.viewport_legacy as vp  # type: ignore

        interface = vp.acquire_viewport_interface()
        for instance in interface.get_instance_list():
            window = interface.get_viewport_window(instance)
            if not window:
                continue
            for method_name, value in (
                ("disable_selection_rect", False),
                ("set_selection_enabled", True),
                ("set_picking_enabled", True),
                ("set_mouse_enabled", True),
                ("set_keyboard_enabled", True),
            ):
                method = getattr(window, method_name, None)
                if callable(method):
                    try:
                        method(value)
                    except Exception:
                        pass
    except Exception:
        pass

    print("[RTG Twin Driver] viewport picking enabled for live info popups")


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
    _driver = OmniverseRtgTwinDriver(inventory_mode=INVENTORY_MODE_LIVE)
    _driver.start()
    _enable_live_viewport_selection()
    _start_live_info_popups()
    return start_receiver(host, port, on_state=_driver.enqueue)


def stop_receiver() -> None:
    global _receiver
    if _receiver is not None:
        _receiver.stop()
        _receiver = None


def stop_live_driver() -> None:
    global _driver
    stop_receiver()
    _stop_live_info_popups()
    if _driver is not None:
        _driver.stop()
        _driver = None
    cleanup_runinfo_billboards()


builtins._smart_port_rtg_twin_stop_live_driver = stop_live_driver


def get_latest_state() -> Optional[Dict[str, Any]]:
    return _receiver.latest_state if _receiver else None


def apply_latest_state_once() -> Optional[Dict[str, Any]]:
    """Manual helper for debugging in Script Editor."""
    if _receiver is None or _receiver.latest_state is None:
        return None
    driver = _driver or OmniverseRtgTwinDriver()
    return driver.apply_state(_receiver.latest_state)


def report_live_crossbeam_alignment() -> Optional[Dict[str, Any]]:
    """Print the actual lower-crossbeam and R02/T02 box centre difference."""

    driver = _driver or OmniverseRtgTwinDriver()
    return driver.report_crossbeam_alignment()


def get_yard_inventory_status() -> Dict[str, Any]:
    """Return inventory path, statistics, and the completed-task ledger."""

    driver = _driver or OmniverseRtgTwinDriver()
    snapshot = driver._inventory.to_dict()
    return {
        "path": str(driver._inventory_path),
        "mode": driver._inventory_mode,
        "startupSyncReady": driver._inventory_full_sync_ready,
        "syncReason": driver._inventory_sync_reason,
        "loadError": driver._inventory_load_error,
        "activeTaskSyncState": driver._active_inventory_sync_state,
        "activeTaskSyncReason": driver._active_inventory_sync_reason,
        "stats": snapshot["stats"],
        "completedTasks": snapshot["completedTasks"],
    }


def reload_yard_inventory() -> Optional[Dict[str, Any]]:
    """Reload the durable snapshot and rebuild the inventory scene."""

    global _driver
    driver = _driver or OmniverseRtgTwinDriver()
    try:
        driver._inventory = YardInventory.load(driver._inventory_path)
        driver._inventory_load_error = None
    except Exception as exc:  # noqa: BLE001 - report cache failure without crash
        driver._inventory = YardInventory.empty()
        driver._inventory_load_error = str(exc)
    if driver._inventory_mode == INVENTORY_MODE_LIVE:
        driver._inventory_full_sync_ready = False
        driver._inventory_sync_reason = (
            driver._inventory_load_error
            or "reloaded live cache requires authoritative synchronization"
        )
        driver._active_inventory_task_key = None
    driver._inventory_stage_id = None
    stage = driver._current_stage()
    if stage is None:
        return None
    driver._sync_inventory_scene(stage)
    if _driver is None:
        _driver = driver
    return get_yard_inventory_status()


def synchronize_live_yard_inventory(
    authoritative_snapshot_path: str,
) -> Optional[Dict[str, Any]]:
    """Replace the live cache with one authoritative whole-yard snapshot."""

    global _driver
    driver = _driver or OmniverseRtgTwinDriver(
        inventory_mode=INVENTORY_MODE_LIVE
    )
    if driver._inventory_mode != INVENTORY_MODE_LIVE:
        raise RuntimeError("Cannot load a live snapshot into simulation mode")
    authoritative = YardInventory.load(Path(authoritative_snapshot_path))
    driver._inventory = authoritative
    if driver._inventory_autosave:
        driver._inventory.save(driver._inventory_path)
    driver._inventory_load_error = None
    driver._inventory_full_sync_ready = True
    driver._inventory_sync_reason = None
    driver._active_inventory_sync_state = INVENTORY_SYNC_READY
    driver._active_inventory_sync_reason = None
    driver._active_inventory_task_key = None
    driver._inventory_stage_id = None
    stage = driver._current_stage()
    if stage is not None:
        driver._sync_inventory_scene(stage)
    if _driver is None:
        _driver = driver
    return get_yard_inventory_status()


if __name__ == "__main__":
    start_live_driver()
