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
import traceback
import types
from dataclasses import dataclass
from datetime import datetime
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

from yard_coordinate_mapping import TRUCK_OPERATING_LANE_CENTER_X_M

def _ensure_pillow_available() -> bool:
    """Load Pillow from Kit's pip archive when it is not on ``sys.path``."""

    try:
        import PIL  # noqa: F401, PLC0415

        return True
    except ImportError:
        pass

    local_app_data = Path(
        os.environ.get("LOCALAPPDATA")
        or (Path.home() / "AppData" / "Local")
    )
    extension_root = local_app_data / "ov" / "data" / "exts" / "v2"
    try:
        archives = sorted(
            extension_root.glob("omni.kit.pip_archive-*"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        archives = []

    python_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    for archive in archives:
        prebundle = archive / "pip_prebundle"
        pil_dir = prebundle / "PIL"
        if not (pil_dir / "__init__.py").is_file():
            continue
        if not any(pil_dir.glob(f"_imaging.{python_tag}-*.pyd")):
            continue
        prebundle_text = str(prebundle)
        if prebundle_text not in sys.path:
            sys.path.insert(0, prebundle_text)
        importlib.invalidate_caches()
        try:
            import PIL  # noqa: F401, PLC0415

            return True
        except ImportError:
            sys.modules.pop("PIL", None)
            if prebundle_text in sys.path:
                sys.path.remove(prebundle_text)
    return False


PILLOW_AVAILABLE = _ensure_pillow_available()

# The RTG payload contains two feeder-ship reference sets as direct children
# of PortAndRTG.  They are authored inside the payload, so the static scene
# replacement layer is not sufficient when Kit keeps an already-open stage.
FOUR_C_FEEDER_SHIP_ASSET_PATH = (
    SCRIPT_DIR.parent / "assets" / "ship" / "Custom_Feeder_Ship.usdc"
)
FOUR_C_FEEDER_SHIP_OLD_PREFIXES = (
    "CODEx_left_reference_feeder_ship_clone",
    "CODEx_right_reference_feeder_ship_clone",
)
FOUR_C_LEGACY_SHIP_ROOT_NAMES = (
    "SMART_PORT_LOCAL_SHIP_4c40_shoreline_east_ROOT",
    "SMART_PORT_LOCAL_SHIP_4c40_shoreline_west_ROOT",
    "SMART_PORT_LOCAL_SHIP_4c40_near_sea_mid_ROOT",
    "SMART_PORT_LOCAL_SHIP_4c40_near_sea_east_ROOT",
)
FOUR_C_FEEDER_SHIP_RUNTIME_TRANSFORMS = {
    "SMART_PORT_4C_FEEDER_SHIP_LEFT_IMPORTED": (
        (-249.6, 40.0, -7.5),
    ),
    "SMART_PORT_4C_FEEDER_SHIP_RIGHT_IMPORTED": (
        (-249.6, 125.0, -7.5),
    ),
}
FOUR_C_FEEDER_SHIP_RUNTIME_SCALE = (1.0, 1.0, 1.0)
LIVE_DRIVER_BUILD_ID = "2026-08-17-remote-mode-signal-link-v4"
FOUR_C_FEEDER_SHIP_STALE_RUNTIME_NAMES = (
    "SMART_PORT_4C_FEEDER_SHIP_LEFT_RUNTIME",
    "SMART_PORT_4C_FEEDER_SHIP_RIGHT_RUNTIME",
)


def ensure_4c_feeder_ship_replacements(stage: Any) -> tuple[bool, int, int]:
    """Hide payload feeder ships and mount the replacement models in-place.

    Returns ``(old_found, hidden_old_count, created_or_reused_new_count)``.
    This edits only the session layer, so it remains safe for a live stage and
    does not rewrite the authored RTG asset.
    """

    from pxr import Usd, UsdGeom

    port_path = "/World/PortAndRTG"
    port = stage.GetPrimAtPath(port_path)
    if not port:
        return False, 0, 0
    asset_path = FOUR_C_FEEDER_SHIP_ASSET_PATH.as_posix()
    if not FOUR_C_FEEDER_SHIP_ASSET_PATH.is_file():
        return False, 0, 0

    old_paths: set[str] = set()
    for prim in port.GetChildren():
        name = str(prim.GetName())
        if any(name.startswith(prefix) for prefix in FOUR_C_FEEDER_SHIP_OLD_PREFIXES):
            old_paths.add(str(prim.GetPath()))
    old_found = bool(old_paths)
    hidden = 0
    mounted = 0
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for name in FOUR_C_LEGACY_SHIP_ROOT_NAMES:
            legacy = stage.GetPrimAtPath(f"{port_path}/{name}")
            if legacy and legacy.IsActive():
                legacy.SetActive(False)
                hidden += 1
        for name in FOUR_C_FEEDER_SHIP_STALE_RUNTIME_NAMES:
            stale = stage.GetPrimAtPath(f"{port_path}/{name}")
            if stale and stale.IsActive():
                stale.SetActive(False)
        for path in _top_level_paths(old_paths):
            prim = stage.GetPrimAtPath(path)
            if prim and prim.IsActive():
                prim.SetActive(False)
                hidden += 1

        for name, (translate,) in FOUR_C_FEEDER_SHIP_RUNTIME_TRANSFORMS.items():
            path = f"{port_path}/{name}"
            prim = stage.GetPrimAtPath(path)
            if not prim:
                prim = UsdGeom.Xform.Define(stage, path).GetPrim()
            references = prim.GetReferences()
            if not prim.HasAuthoredReferences():
                references.AddReference(asset_path)
            prim.SetInstanceable(True)
            xform = UsdGeom.Xformable(prim)
            translate_op = _billboard_utils.get_or_add_xform_op(
                xform,
                "xformOp:translate",
                xform.AddTranslateOp,
            )
            translate_op.Set(translate)
            rotate_op = _billboard_utils.get_or_add_xform_op(
                xform,
                "xformOp:rotateXYZ",
                xform.AddRotateXYZOp,
            )
            rotate_op.Set((0.0, 0.0, -90.0))
            scale_op = _billboard_utils.get_or_add_xform_op(
                xform,
                "xformOp:scale",
                xform.AddScaleOp,
            )
            scale_op.Set(FOUR_C_FEEDER_SHIP_RUNTIME_SCALE)
            xform.SetXformOpOrder([translate_op, rotate_op, scale_op])
            mounted += 1
    return old_found, hidden, mounted

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
import rtg_billboard_utils as _billboard_utils  # noqa: E402

_billboard_utils = importlib.reload(_billboard_utils)
active_viewport_camera_position = _billboard_utils.active_viewport_camera_position
camera_facing_rotate_xyz = _billboard_utils.camera_facing_rotate_xyz
camera_facing_rotate_xyz_from_camera = (
    _billboard_utils.camera_facing_rotate_xyz_from_camera
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 48700
RECEIVER_STATE_LOG_ENABLED = False
STATUS_PRIM_PATH = "/World/LiveTwinState"
AUTOMATIC_SIGNAL_ROOT_PATH = "/World/LiveHUD/RTG_AUTOMATIC_SIGNAL_LINK"
AUTOMATIC_SIGNAL_OUTER_CURVE_PATH = f"{AUTOMATIC_SIGNAL_ROOT_PATH}/OuterGlow"
AUTOMATIC_SIGNAL_CORE_CURVE_PATH = f"{AUTOMATIC_SIGNAL_ROOT_PATH}/CoreBeam"
AUTOMATIC_SIGNAL_LEFT_CURVE_PATH = f"{AUTOMATIC_SIGNAL_ROOT_PATH}/LeftDataRail"
AUTOMATIC_SIGNAL_RIGHT_CURVE_PATH = f"{AUTOMATIC_SIGNAL_ROOT_PATH}/RightDataRail"
AUTOMATIC_SIGNAL_PULSE_ROOT_PATH = f"{AUTOMATIC_SIGNAL_ROOT_PATH}/DataPulses"
AUTOMATIC_SIGNAL_ANTENNA_PATH = (
    "/World/PortAndRTG/SMART_PORT_tower_main_antenna_mast"
)
AUTOMATIC_SIGNAL_RTG_ATTACH_PATH = (
    "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL"
)
# Anchor above the RTG portal centerline.  X/Z come from the upper-girder LED
# measurements; Y follows the calibrated portal crossbeam centerline.
AUTOMATIC_SIGNAL_RTG_TARGET_LOCAL_POINT = (-0.797093, 5.0, 9.2)
AUTOMATIC_SIGNAL_MATERIAL_ROOT_PATH = "/World/Looks/RTG_Automatic_Signal_Link"
AUTOMATIC_SIGNAL_POINT_COUNT = 41
AUTOMATIC_SIGNAL_PULSE_COUNT = 9
AUTOMATIC_SIGNAL_UPDATE_INTERVAL_SECONDS = 1.0 / 30.0
AUTOMATIC_SIGNAL_PACKET_SPEED = 0.16
AUTOMATIC_SIGNAL_MIN_ARC_LIFT_M = 16.0
AUTOMATIC_SIGNAL_MAX_ARC_LIFT_M = 52.0
AUTOMATIC_SIGNAL_RAIL_OFFSET_M = 0.24
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
PRIMARY_RTG_SELECTION_ROOT_PATH = RUNINFO_FLOATING_ABOVE_RTG_PATH
RTG_CAMERA_MARKER_NAME_RE = re.compile(
    r"^(?:(?:CAMERA|CAM|MARKER)[_-]?C?|C)[_-]?([0-9]{1,2})(?:[_-].*)?$",
    re.IGNORECASE,
)
RUNINFO_FLOATING_ABOVE_GAP_M = 1.2
RUNINFO_FLOATING_RIGHT_X_OFFSET_M = 1.2
RUNINFO_FLOATING_BACK_Y_OFFSET_M = 7.0
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
RUNINFO_FLOATING_TIME_OFFSET_HOURS = 0
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
TASK_PANEL_WIDTH_M = 8.8
TASK_PANEL_HEIGHT_M = 5.7
TASK_PANEL_CONTAINER_WIDTH_X_M = 1.306287
TASK_PANEL_ONE_TIER_VISUAL_HEIGHT_M = 0.904353
TASK_PANEL_WORLD_X = 12.0 + TASK_PANEL_WIDTH_M * 0.25 + TASK_PANEL_CONTAINER_WIDTH_X_M
TASK_PANEL_BASE_WORLD_Z = TASK_PANEL_HEIGHT_M * 0.5 + TASK_PANEL_ONE_TIER_VISUAL_HEIGHT_M
# The task panel now occupies the former device-panel position above the RTG.
TASK_PANEL_WORLD_Z = (
    TASK_PANEL_BASE_WORLD_Z
    + TASK_PANEL_HEIGHT_M
    + 0.35
    + TASK_PANEL_WIDTH_M / 5.0
    - TASK_PANEL_WIDTH_M / 4.0
)
TASK_PANEL_Y_OFFSET_M = -2.2
TASK_PANEL_ROTATE_XYZ = (0.0, 0.0, 180.0)
TASK_PANEL_UV_COORDS = (
    (0.0, 0.0),
    (1.0, 0.0),
    (1.0, 1.0),
    (0.0, 1.0),
)
TASK_PANEL_DIAGNOSTIC_LOG_ENABLED = True
TASK_PANEL_ROUTE_DASH_LENGTH_PX = 18
TASK_PANEL_ROUTE_GAP_LENGTH_PX = 12
TASK_PANEL_TRUCK_ROUTE_ANCHOR_FROM_BOTTOM_PX = 68.0
TASK_PANEL_BAY_MAP_LEFT_PX = 500
TASK_PANEL_TRUCK_ICON_PATH = SCRIPT_DIR.parent / "assets" / "task_truck_lane.png"
DEVICE_LOCATION_PANEL_ROOT_PATH = "/World/LiveHUD/RTG_DEVICE_LOCATION_PANEL"
DEVICE_LOCATION_PANEL_MESH_PATH = f"{DEVICE_LOCATION_PANEL_ROOT_PATH}/Panel"
DEVICE_LOCATION_PANEL_MATERIAL_PATH = "/World/Looks/RTG_Device_Location_Panel_Live"
DEVICE_LOCATION_PANEL_VALUES_MESH_PATH = f"{DEVICE_LOCATION_PANEL_ROOT_PATH}/Values"
DEVICE_LOCATION_PANEL_VALUES_MATERIAL_PATH = "/World/Looks/RTG_Device_Location_Panel_Values_Live"
DEVICE_LOCATION_PANEL_IMAGE_WIDTH = 1024
DEVICE_LOCATION_PANEL_IMAGE_HEIGHT = 540
DEVICE_LOCATION_PANEL_WIDTH_M = 7.6
DEVICE_LOCATION_PANEL_HEIGHT_M = 4.0
DEVICE_LOCATION_PANEL_RENDER_WIDTH = 768
DEVICE_LOCATION_PANEL_RENDER_HEIGHT = 360
DEVICE_LOCATION_PANEL_GAP_M = 0.35
DEVICE_LOCATION_PANEL_VERTICAL_LIFT_M = DEVICE_LOCATION_PANEL_WIDTH_M / 5.0
DEVICE_LOCATION_PANEL_LEFT_GAP_M = 0.0
DEVICE_LOCATION_PANEL_UP_OFFSET_M = 1.0
DEVICE_LOCATION_PANEL_RENDER_INTERVAL_SECONDS = 0.5
# Mirror the task-panel anchor around the RTG's x=12.0 centreline so the
# device panel sits on the RTG's left side.
DEVICE_LOCATION_PANEL_WORLD_X = (
    12.0 - TASK_PANEL_WIDTH_M * 2.25 - TASK_PANEL_CONTAINER_WIDTH_X_M
)
DEVICE_LOCATION_PANEL_WORLD_Z = (
    TASK_PANEL_BASE_WORLD_Z
    + TASK_PANEL_HEIGHT_M
    + 0.35
    + TASK_PANEL_WIDTH_M / 5.0
)
# Keep the device-location panel and work-mode badge on the same Y plane as
# the task/message panel.  Their X positions remain independently laid out.
DEVICE_LOCATION_PANEL_Y_OFFSET_M = TASK_PANEL_Y_OFFSET_M
DEVICE_LOCATION_PANEL_ROTATE_XYZ = TASK_PANEL_ROTATE_XYZ
DEVICE_LOCATION_PANEL_UV_COORDS = TASK_PANEL_UV_COORDS
DEVICE_LOCATION_PANEL_VALUE_Y = {
    "Device": 132,
    "Field": 196,
    "Bay": 260,
    "WorkMode": 324,
}
# Independent always-on work-mode badge. It is not governed by RTG selection;
# only its bay-relative position follows the current RTG.
WORK_MODE_INDICATOR_ROOT_PATH = "/World/LiveHUD/RTG_WORK_MODE_INDICATOR"
WORK_MODE_INDICATOR_SPINNER_PATH = f"{WORK_MODE_INDICATOR_ROOT_PATH}/Spinner"
WORK_MODE_INDICATOR_MESH_PATH = f"{WORK_MODE_INDICATOR_SPINNER_PATH}/Panel"
WORK_MODE_INDICATOR_MATERIAL_PATH = "/World/Looks/RTG_Work_Mode_Indicator"
WORK_MODE_INDICATOR_IMAGE_WIDTH = 420
WORK_MODE_INDICATOR_IMAGE_HEIGHT = 420
WORK_MODE_INDICATOR_WIDTH_M = 2.35
WORK_MODE_INDICATOR_HEIGHT_M = 2.35
WORK_MODE_INDICATOR_GAP_M = 0.30
WORK_MODE_INDICATOR_RENDER_INTERVAL_SECONDS = 0.20
LIVE_INFO_PANEL_ROTATE_XYZ = (0.0, 0.0, 180.0)
DEVICE_LOCATION_PANEL_RIGHT_ROOT_PATH = (
    "/World/LiveHUD/RTG_DEVICE_LOCATION_PANEL_RIGHT"
)
CONTAINER_INFO_PANEL_ROOT_PATH = "/World/LiveHUD/RTG_CONTAINER_INFO_PANEL"
CONTAINER_INFO_PANEL_MESH_PATH = f"{CONTAINER_INFO_PANEL_ROOT_PATH}/Panel"
CONTAINER_INFO_PANEL_MATERIAL_PATH = "/World/Looks/RTG_Container_Info_Panel_Live"
CONTAINER_INFO_PANEL_IMAGE_WIDTH = TASK_PANEL_IMAGE_WIDTH
CONTAINER_INFO_PANEL_IMAGE_HEIGHT = TASK_PANEL_IMAGE_HEIGHT
CONTAINER_INFO_PANEL_WIDTH_M = DEVICE_LOCATION_PANEL_WIDTH_M
CONTAINER_INFO_PANEL_HEIGHT_M = TASK_PANEL_HEIGHT_M
# Align the container panel's left edge with the task panel's left edge.
CONTAINER_INFO_PANEL_HORIZONTAL_OFFSET_M = (
    CONTAINER_INFO_PANEL_WIDTH_M - TASK_PANEL_WIDTH_M
) * 0.5
CONTAINER_INFO_PANEL_VERTICAL_OFFSET_M = -0.8
CONTAINER_INFO_PANEL_WORLD_X = (
    TASK_PANEL_WORLD_X + CONTAINER_INFO_PANEL_HORIZONTAL_OFFSET_M
)
CONTAINER_INFO_PANEL_WORLD_Z = TASK_PANEL_BASE_WORLD_Z + CONTAINER_INFO_PANEL_VERTICAL_OFFSET_M
CONTAINER_INFO_PANEL_Y_OFFSET_M = DEVICE_LOCATION_PANEL_Y_OFFSET_M
CONTAINER_INFO_PANEL_Z_OFFSET_M = 4.4
CONTAINER_INFO_PANEL_MIN_BOTTOM_WORLD_Z = (
    0.12 + 6 * 0.904353 + 5 * 0.035
)
CONTAINER_INFO_PANEL_ROTATE_XYZ = (0.0, 0.0, 180.0)
CONTAINER_INFO_PANEL_ROW_Y = {
    "Container": 174,
    "Size": 260,
    "Type": 346,
    "Height": 432,
    "Bay": 518,
    "Task ID": 604,
    "Locked": 690,
}
CONTAINER_INFO_PANEL_LABELS = {
    "Container": "箱号",
    "Size": "箱尺寸",
    "Type": "箱型",
    "Height": "箱高",
    "Bay": "当前位置",
    "Task ID": "关联任务",
    "Locked": "锁闭",
}
TRUCK_INFO_PANEL_ROOT_PATH = "/World/LiveHUD/RTG_TRUCK_INFO_PANEL"
TRUCK_INFO_PANEL_MESH_PATH = f"{TRUCK_INFO_PANEL_ROOT_PATH}/Panel"
TRUCK_INFO_PANEL_MATERIAL_PATH = "/World/Looks/RTG_Truck_Info_Panel_Live"
TRUCK_INFO_PANEL_IMAGE_WIDTH = 820
TRUCK_INFO_PANEL_IMAGE_HEIGHT = 420
TRUCK_INFO_PANEL_WIDTH_M = 6.8
TRUCK_INFO_PANEL_HEIGHT_M = 3.5
TRUCK_INFO_PANEL_Y_OFFSET_M = -3.2
TRUCK_INFO_PANEL_Z_OFFSET_M = 2.5
TRUCK_INFO_PANEL_ROTATE_XYZ = LIVE_INFO_PANEL_ROTATE_XYZ
TRUCK_INFO_PANEL_UV_COORDS = TASK_PANEL_UV_COORDS
TRUCK_INFO_PANEL_LABELS = {
    "Type": "车类型",
    "Number": "车号",
}
LIVE_INFO_CAMERA_FACING_PANEL_ROOT_PATHS = (
    RUNINFO_FLOATING_ROOT_PATH,
    DEVICE_LOCATION_PANEL_ROOT_PATH,
    DEVICE_LOCATION_PANEL_RIGHT_ROOT_PATH,
    CONTAINER_INFO_PANEL_ROOT_PATH,
    TRUCK_INFO_PANEL_ROOT_PATH,
)
LIVE_INFO_CAMERA_FACING_PANEL_ROTATE_XYZ = {
    RUNINFO_FLOATING_ROOT_PATH: RUNINFO_FLOATING_ROTATE_XYZ,
    DEVICE_LOCATION_PANEL_ROOT_PATH: LIVE_INFO_PANEL_ROTATE_XYZ,
    DEVICE_LOCATION_PANEL_RIGHT_ROOT_PATH: LIVE_INFO_PANEL_ROTATE_XYZ,
    CONTAINER_INFO_PANEL_ROOT_PATH: LIVE_INFO_PANEL_ROTATE_XYZ,
    TRUCK_INFO_PANEL_ROOT_PATH: LIVE_INFO_PANEL_ROTATE_XYZ,
}
LIVE_INFO_CAMERA_FACING_UPDATE_INTERVAL_SECONDS = 1.0 / 30.0
DEVICE_PANEL_SELECTION_POLL_INTERVAL_SECONDS = 1.0 / 30.0
CONTAINER_INFO_DATA_REFRESH_INTERVAL_SECONDS = 0.1
WEATHER_ATTRIBUTE_SYNC_INTERVAL_SECONDS = 1.0 / 30.0
TASK_ROUTE_INDICATOR_ROOT_PATH = "/World/LiveHUD/RTG_TASK_ROUTE_INDICATOR"
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
LIVE_HOISTED_LOAD_PATH = "/World/LiveContainers/LiveTwinHoistedContainer"
LIVE_TASK_TRUCK_VISUAL_PATH = f"{LIVE_TASK_TRUCK_PATH}/ImportedRtgModelLaneTruck"
LIVE_TASK_TRUCK_VISUAL_ASSET_PATH = (
    SCRIPT_DIR.parent
    / "assets"
    / "resource"
    / "truck"
    / "RTG_Model_Lane_Truck.usdc"
)
# This compact vehicle is exported from blender/RTG_Model.blend with the
# skeletal-trailer centre at the origin. A small longitudinal stretch lets the
# 40 ft task box sit entirely over the trailer while preserving its proportions.
LIVE_TASK_TRUCK_VISUAL_SCALE = (1.0, 1.22, 1.0)
LIVE_TASK_TRUCK_ASSET_CAB_CENTER_Y_M = -2.65 * LIVE_TASK_TRUCK_VISUAL_SCALE[1]
LIVE_TASK_TRUCK_ASSET_GROUND_Z_M = 0.14
LIVE_TASK_TRUCK_WHITE_CAB_PART_NAMES = (
    "TrailerMainFrame",
    "TrailerDeck",
    "TrailerLeftRail",
    "TrailerRightRail",
    "CabBody",
    "CabWindshield",
    "CabBumper",
)
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
CONTAINER_WORK_TYPES = frozenset(
    (WORK_TYPE_LOAD, WORK_TYPE_UNLOAD, WORK_TYPE_SWITCH)
)

TRUCK_APPROACH_SECONDS = 5.0
TRUCK_SETTLE_SECONDS = 1.5
TRUCK_EXIT_SECONDS = 4.0
# Move the task truck and its painted lane 0.90 m toward the container side.
# The companion trolley calibration puts the visible spreader at -3.75 m too.
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


def _task_is_cancelled(task: Dict[str, Any]) -> bool:
    """Return whether an upstream task snapshot represents cancellation."""

    for key in (
        "operationState",
        "taskState",
        "state",
        "status",
        "taskStatus",
    ):
        value = str(task.get(key) or "").strip().upper()
        if value in {
            "CANCEL",
            "CANCELLED",
            "CANCELED",
            "ABORT",
            "ABORTED",
            "取消",
            "撤销",
            "中止",
        }:
            return True
    return False


def _runinfo_is_task_completion(raw_runinfo: Any) -> bool:
    """Recognize terminal messages even if the task snapshot lags one frame."""

    if isinstance(raw_runinfo, dict):
        message = str(raw_runinfo.get("message") or "").strip()
    else:
        message = str(raw_runinfo or "").strip()
    normalized = message.upper().replace(" ", "")
    return any(
        marker in normalized
        for marker in (
            "任务完成",
            "作业完成",
            "TASKCOMPLETE",
            "TASKCOMPLETED",
        )
    )


def _runinfo_is_task_cancellation(raw_runinfo: Any) -> bool:
    """Recognize terminal cancellation messages from the upstream system."""

    if isinstance(raw_runinfo, dict):
        message = str(raw_runinfo.get("message") or "").strip()
    else:
        message = str(raw_runinfo or "").strip()
    normalized = message.upper().replace(" ", "")
    return any(
        marker in normalized
        for marker in (
            "任务取消",
            "作业取消",
            "任务撤销",
            "作业撤销",
            "TASKCANCEL",
            "TASKCANCELLED",
            "TASKCANCELED",
            "TASKABORT",
            "TASKABORTED",
        )
    )


def _runinfo_should_be_visible(
    task: Dict[str, Any],
    raw_runinfo: Any = None,
) -> bool:
    """Show RunInfo only while a named task is actively executing."""

    return bool(str(task.get("taskId") or "").strip()) and not (
        _task_is_complete(task)
        or _task_is_cancelled(task)
        or _runinfo_is_task_completion(raw_runinfo)
        or _runinfo_is_task_cancellation(raw_runinfo)
    )


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


def _is_supported_yard_bay(field: Any, bay: Any) -> bool:
    """Return whether a PLC field/bay identifies a real modelled yard bay."""

    normalized_field = _field_name(field)
    bay_number = _as_int(bay, 0)
    if not normalized_field or bay_number <= 0:
        return False
    try:
        from yard_coordinate_mapping import validate_bay

        validate_bay(normalized_field, bay_number)
        return True
    except (TypeError, ValueError):
        return False


def _is_container_work_task(task: Dict[str, Any]) -> bool:
    """Return whether a task owns a box/stack/集卡 visual transaction."""

    return _task_work_type(task) in CONTAINER_WORK_TYPES


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


WORK_MODE_LABELS = {
    0: "未知",
    1: "自动模式",
    2: "辅助模式",
    3: "远程手动",
    4: "本地模式",
    5: "维修模式",
}


def _work_mode_text(value: Any) -> str:
    """Return the Chinese work-mode label from the RCMS heartbeat code."""

    try:
        mode = int(value)
    except (TypeError, ValueError):
        return "未知"
    return WORK_MODE_LABELS.get(mode, "未知")


def _work_mode_code(value: Any) -> int:
    """Normalize a heartbeat work_mode for the always-on mode badge."""

    try:
        mode = int(value)
    except (TypeError, ValueError):
        return 0
    return mode if mode in WORK_MODE_LABELS else 0


def _state_work_mode(state: Any) -> int:
    """Read the RCMS work mode from either the pose or top-level snapshot."""

    if not isinstance(state, dict):
        return 0
    pose = state.get("pose")
    sources = (pose, state) if isinstance(pose, dict) else (state,)
    for source in sources:
        for key in ("workMode", "work_mode"):
            value = source.get(key)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return 0


def _quadratic_signal_point(
    start: tuple[float, float, float],
    control: tuple[float, float, float],
    end: tuple[float, float, float],
    progress: float,
) -> tuple[float, float, float]:
    """Sample one point on the antenna-to-RTG quadratic signal arc."""

    t = max(0.0, min(1.0, float(progress)))
    inverse = 1.0 - t
    return tuple(
        inverse * inverse * start[index]
        + 2.0 * inverse * t * control[index]
        + t * t * end[index]
        for index in range(3)
    )


def _automatic_signal_arc(
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    point_count: int = AUTOMATIC_SIGNAL_POINT_COUNT,
) -> tuple[
    list[tuple[float, float, float]],
    tuple[float, float, float],
]:
    """Build a smooth, raised communication arc and return its control point."""

    horizontal_distance = math.hypot(end[0] - start[0], end[1] - start[1])
    lift = max(
        AUTOMATIC_SIGNAL_MIN_ARC_LIFT_M,
        min(AUTOMATIC_SIGNAL_MAX_ARC_LIFT_M, horizontal_distance * 0.18),
    )
    control = (
        (start[0] + end[0]) * 0.5,
        (start[1] + end[1]) * 0.5,
        max(start[2], end[2]) + lift,
    )
    count = max(2, int(point_count))
    points = [
        _quadratic_signal_point(start, control, end, index / (count - 1))
        for index in range(count)
    ]
    return points, control


def _automatic_signal_rail_points(
    points: list[tuple[float, float, float]],
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    signed_offset: float,
) -> list[tuple[float, float, float]]:
    """Offset an arc sideways while converging both data rails at its ends."""

    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    if length <= 1e-6:
        return list(points)
    side_x = -dy / length
    side_y = dx / length
    last_index = max(1, len(points) - 1)
    return [
        (
            point[0] + side_x * signed_offset * math.sin(math.pi * index / last_index),
            point[1] + side_y * signed_offset * math.sin(math.pi * index / last_index),
            point[2],
        )
        for index, point in enumerate(points)
    ]


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
            return datetime.fromtimestamp(timestamp).strftime(RUNINFO_FLOATING_CLOCK_FORMAT)
        except (OSError, OverflowError, ValueError):
            return text
    normalized = text.replace("T", " ").replace("/", "-")
    if normalized.endswith("Z"):
        normalized = normalized[:-1]
    if "." in normalized:
        normalized = normalized.split(".", 1)[0]
    try:
        return datetime.fromisoformat(normalized).strftime(RUNINFO_FLOATING_CLOCK_FORMAT)
    except ValueError:
        return text


def _runinfo_time_text(runinfo: Dict[str, Any]) -> str:
    for key in ("happentime", "timespan", "timeSpan", "msg_timespan", "msgTimeSpan"):
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


def _is_rtg_camera_marker_selection_path(path: str) -> bool:
    """Return True for a selected C1-C33 marker or one of its mesh children."""

    text = str(path or "").strip()
    if not _is_descendant_path(text, PRIMARY_RTG_SELECTION_ROOT_PATH):
        return False
    if text == PRIMARY_RTG_SELECTION_ROOT_PATH:
        return False
    for component in reversed([part for part in text.split("/") if part]):
        match = RTG_CAMERA_MARKER_NAME_RE.match(component)
        if match and 1 <= int(match.group(1)) <= 33:
            return True
    return False


def _container_component_index(parts: tuple[str, ...]) -> Optional[int]:
    for index, part in enumerate(parts):
        if part in {"LiveContainers", "YardInventory"}:
            continue
        if part in {"LiveTwinHoistedContainer", "LIVE_TwinHoistedContainer", "LoadedContainer"}:
            return index
        if part.endswith("_Container") or "_Container_" in part:
            return index
        if part.lower().endswith("container") and part.lower() != "livecontainers":
            return index
    return None


def _container_root_path_from_selection(path: Any) -> Optional[str]:
    """Normalize a clicked box/detail prim to its physical container root."""

    text = str(path or "").strip()
    if not text.startswith("/"):
        return None
    if text == LIVE_HOISTED_LOAD_PATH or text.startswith(f"{LIVE_HOISTED_LOAD_PATH}/"):
        return LIVE_HOISTED_LOAD_PATH
    parts = tuple(part for part in text.split("/") if part)
    for index, part in enumerate(parts):
        logical_name = part
        if logical_name.endswith("_ImportedModel"):
            logical_name = logical_name[: -len("_ImportedModel")]
        if "_Container_" in logical_name:
            logical_name = logical_name.partition("_Container_")[0] + "_Container"
        if logical_name != part and (
            logical_name in {
                "LiveTwinHoistedContainer",
                "LIVE_TwinHoistedContainer",
                "LoadedContainer",
            }
            or logical_name.endswith("_Container")
            or logical_name.lower().endswith("container")
        ):
            return "/" + "/".join((*parts[:index], logical_name))
    index = _container_component_index(parts)
    if index is None:
        return None
    return "/" + "/".join(parts[: index + 1])


def _truck_root_path_from_selection(path: Any) -> Optional[str]:
    """Normalize a clicked truck/detail prim to the live task truck root."""

    text = str(path or "").strip()
    loaded_container_path = f"{LIVE_TASK_TRUCK_PATH}/LoadedContainer"
    if _is_descendant_path(text, loaded_container_path):
        return None
    if _is_descendant_path(text, LIVE_TASK_TRUCK_PATH):
        return LIVE_TASK_TRUCK_PATH
    parts = tuple(part for part in text.split("/") if part)
    for index, part in enumerate(parts):
        if part.startswith("LIVE_internal_yard_truck"):
            return "/" + "/".join(parts[: index + 1])
        if part.startswith("LIVE_internal_lane_truck") and index > 0:
            return "/" + "/".join(parts[:index])
    return None


def _selected_rtg_path() -> Optional[str]:
    """Return the selected RTG prim, or None when the click is elsewhere."""

    try:
        import omni.usd  # type: ignore

        selection = omni.usd.get_context().get_selection()
        for raw_path in selection.get_selected_prim_paths():
            path = str(raw_path)
            if _is_descendant_path(path, PRIMARY_RTG_SELECTION_ROOT_PATH):
                return path
    except Exception:
        return None
    return None


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
    """Map the upstream runinfo state/type to one of the three visual panels."""

    if not isinstance(runinfo, dict):
        return RUNINFO_LEVEL_PROMPT
    raw_type = None
    for key in ("runState", "run_state"):
        raw_type = runinfo.get(key)
        if raw_type is not None:
            break
    try:
        run_state = int(raw_type)
    except (TypeError, ValueError):
        run_state = 1
    return {
        1: RUNINFO_LEVEL_PROMPT,
        2: RUNINFO_LEVEL_ALERT,
        3: RUNINFO_LEVEL_EXCEPTION,
    }.get(run_state, RUNINFO_LEVEL_PROMPT)


@dataclass(frozen=True)
class TruckRunInfo:
    car_type: str
    car_no: str


def _runinfo_message_text(runinfo: Any) -> str:
    if isinstance(runinfo, dict):
        return str(runinfo.get("message") or "").strip()
    return str(runinfo or "").strip()


def _runinfo_truck_type_from_message(message: str) -> str:
    if re.search(r"(?:校验|检验)外集卡车号(?:通过|成功)", message):
        return "外集卡"
    if re.search(r"(?:校验|检验)内集卡车号(?:通过|成功)", message):
        return "内集卡"
    if re.search(r"(?:校验|检验)AIV车号(?:通过|成功)", message):
        return "AIV"
    if re.search(r"(?:校验|检验)ITK车号(?:通过|成功)", message):
        return "ITK"
    if "进箱集卡" in message:
        return "进箱集卡"
    if "外集卡" in message:
        return "外集卡"
    if "内集卡" in message:
        return "内集卡"
    if "集卡" in message:
        return "集卡"
    if "AIV" in message:
        return "AIV"
    if "ITK" in message:
        return "ITK"
    return "--"


def _runinfo_field_value(message: str, label_pattern: str) -> Optional[str]:
    labels = (
        r"算法检测车号",
        r"侧面车号",
        r"任务车号",
        r"排队车号",
        r"引导识别到(?:ITK/AIV|AIV|ITK)?车号",
        r"车号",
        r"箱号",
        r"时间[:：]",
    )
    pattern = (
        rf"{label_pattern}\s*[:：]?\s*"
        rf"(.+?)(?=(?:{'|'.join(labels)})|[,，;；\s]|$)"
    )
    match = re.search(pattern, message)
    if not match:
        return None
    value = re.sub(r"[\s,，;；:：]+", "", match.group(1))
    if value.startswith("时间"):
        return None
    return value or None


def _truck_runinfo_from_payload(runinfo: Any) -> Optional[TruckRunInfo]:
    """Parse validated truck type and number from the latest RunInfo message."""

    message = _runinfo_message_text(runinfo)
    if not message:
        return None
    if not re.search(r"(?:校验|检验).*(?:通过|成功)", message):
        return None
    car_type = _runinfo_truck_type_from_message(message)
    car_no_candidates: list[tuple[int, int, str]] = []
    for priority, label_pattern in enumerate(
        (
        r"任务车号",
        r"侧面车号",
        r"算法检测车号",
        r"引导识别到(?:ITK/AIV|AIV|ITK)?车号",
        r"车号",
        )
    ):
        candidate = _runinfo_field_value(message, label_pattern)
        if candidate:
            # RCMS can publish a truncated side-camera value (for example
            # ``侧面车号H``) while ``算法检测车号`` still contains ``H327``.
            # Prefer the longest candidate, retaining the existing label
            # priority when candidates have equal length.
            car_no_candidates.append((len(candidate), -priority, candidate))
    car_no = max(car_no_candidates)[2] if car_no_candidates else None
    if not car_no:
        return None
    return TruckRunInfo(car_type=car_type, car_no=car_no)


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


def _task_panel_content_key(task: Dict[str, Any]) -> tuple[str, ...]:
    """Return only the task fields that are rendered into the task panel bitmap."""

    return (
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
    )


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


def _task_panel_slot_markers(
    task: Dict[str, Any],
) -> tuple[Optional[tuple[str, int, int]], Optional[tuple[str, int, int]]]:
    """Return the solid source and dashed destination markers for the bay map."""

    endpoints = _task_route_endpoints(task)
    if endpoints is None:
        return None, None
    source, target = endpoints
    work_type = _task_work_type(task)
    source_marker = None
    pattern = str(task.get("bayMap") or "").strip()
    if (
        work_type in (WORK_TYPE_UNLOAD, WORK_TYPE_SWITCH)
        and source[0] == "slot"
        and re.fullmatch(r"\d{6}", pattern)
        and 1 <= source[1] <= 6
    ):
        stack_height = int(pattern[source[1] - 1])
        if stack_height > 0:
            # Preserve the established out-box display rule: RCMS can publish
            # a stale tier in startPos, while bayMap identifies the visible
            # top box that the operator will actually lift.
            source_marker = ("slot", source[1], stack_height)
    target_marker = (
        target
        if work_type in (WORK_TYPE_LOAD, WORK_TYPE_SWITCH)
        and target[0] == "slot"
        else None
    )
    return source_marker, target_marker


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


def _task_panel_route_map_points(
    task: Dict[str, Any],
    *,
    map_left: int,
    bottom: int,
    cell_w: int,
    cell_h: int,
    gap: int,
    lane_left: int,
    lane_w: int,
) -> Optional[tuple[tuple[float, float], tuple[float, float], str, Optional[float]]]:
    """Map semantic task endpoints onto the small bay-map drawing."""

    pattern = str(task.get("bayMap") or "").strip()
    if not re.fullmatch(r"\d{6}", pattern):
        return None
    endpoints = _task_route_endpoints(task)
    if endpoints is None:
        return None

    left = lane_left + lane_w + gap
    row_columns = {
        row: column for column, row in enumerate(TASK_PANEL_BAY_MAP_ROWS_LEFT_TO_RIGHT)
    }

    def point(endpoint: tuple[str, int, int]) -> Optional[tuple[float, float]]:
        if endpoint[0] == "truck":
            # Anchor the route just above the truck-cab icon instead of at
            # its centre, so both inbound and outbound arrows stay readable.
            return (
                lane_left + lane_w * 0.5,
                bottom - TASK_PANEL_TRUCK_ROUTE_ANCHOR_FROM_BOTTOM_PX,
            )
        row, tier = endpoint[1], endpoint[2]
        column = row_columns.get(row)
        # A LOAD target can intentionally be an empty slot.  The bay map
        # describes the current stack, so do not reject an arrow just because
        # its destination tier has not been materialized yet.
        if column is None or tier < 1 or tier > 6:
            return None
        x = left + column * (cell_w + gap) + cell_w * 0.5
        y = bottom - tier * (cell_h + 9) + cell_h * 0.5
        return (float(x), float(y))

    source = point(endpoints[0])
    target = point(endpoints[1])
    if source is None or target is None:
        return None
    work_type = _task_work_type(task)
    clearance_y = None
    if work_type in CONTAINER_WORK_TYPES:
        # The trolley must clear the tallest visible stack before traveling
        # horizontally toward the truck lane.
        highest_tier = max(int(height_text) for height_text in pattern)
        clearance_y = float(bottom - highest_tier * (cell_h + 9) - 18)
    return source, target, work_type, clearance_y


def _task_panel_route_polyline(
    route: tuple[tuple[float, float], tuple[float, float], str, Optional[float]],
) -> tuple[tuple[float, float], ...]:
    """Return an orthogonal route with a final segment aimed at the target."""

    source, target, work_type = route[:3]
    clearance_y = route[3] if len(route) > 3 else None
    if work_type in CONTAINER_WORK_TYPES and clearance_y is not None:
        return (
            source,
            (source[0], clearance_y),
            (target[0], clearance_y),
            target,
        )
    if abs(target[0] - source[0]) < 0.5 or abs(target[1] - source[1]) < 0.5:
        return source, target
    midpoint_x = (source[0] + target[0]) * 0.5
    return (
        source,
        (midpoint_x, source[1]),
        (midpoint_x, target[1]),
        target,
    )


def _draw_task_panel_route_indicator(
    draw: Any,
    route: tuple[tuple[float, float], tuple[float, float], str, Optional[float]],
    phase: float,
) -> None:
    """Draw a moving dashed arrow for the task's direction on the bay map."""

    source, target, work_type = route[:3]
    colors = {
        WORK_TYPE_LOAD: (88, 222, 248, 255),
        WORK_TYPE_UNLOAD: (255, 190, 78, 255),
        WORK_TYPE_SWITCH: (91, 238, 178, 255),
    }
    color = colors.get(work_type, (246, 252, 255, 255))
    polyline = _task_panel_route_polyline(route)
    dash = float(TASK_PANEL_ROUTE_DASH_LENGTH_PX)
    gap = float(TASK_PANEL_ROUTE_GAP_LENGTH_PX)
    stride = dash + gap
    del phase
    final_unit = (0.0, 0.0)
    for start, end in zip(polyline, polyline[1:]):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = math.hypot(dx, dy)
        if length < 1.0:
            continue
        unit_x = dx / length
        unit_y = dy / length
        final_unit = (unit_x, unit_y)
        cursor = 0.0
        while cursor < length:
            segment_start = cursor
            segment_end = min(length, cursor + dash)
            draw.line(
                (
                    int(start[0] + unit_x * segment_start),
                    int(start[1] + unit_y * segment_start),
                    int(start[0] + unit_x * segment_end),
                    int(start[1] + unit_y * segment_end),
                ),
                fill=color,
                width=5,
            )
            cursor += stride

    unit_x, unit_y = final_unit
    if unit_x == 0.0 and unit_y == 0.0:
        return
    head_length = 22.0
    head_width = 11.0
    back_x = target[0] - unit_x * head_length
    back_y = target[1] - unit_y * head_length
    perp_x = -unit_y * head_width
    perp_y = unit_x * head_width
    draw.polygon(
        [
            (int(target[0]), int(target[1])),
            (int(back_x + perp_x), int(back_y + perp_y)),
            (int(back_x - perp_x), int(back_y - perp_y)),
        ],
        fill=color,
    )
    draw.ellipse(
        (int(source[0] - 6), int(source[1] - 6), int(source[0] + 6), int(source[1] + 6)),
        fill=(10, 38, 58, 255),
        outline=color,
        width=3,
    )
    draw.ellipse(
        (int(target[0] - 6), int(target[1] - 6), int(target[0] + 6), int(target[1] + 6)),
        outline=color,
        width=3,
    )


def _draw_task_panel_dashed_slot(
    draw: Any,
    rect: tuple[int, int, int, int],
    color: tuple[int, int, int, int],
    *,
    width: int = 3,
    dash: int = 8,
    gap: int = 5,
) -> None:
    """Draw a dashed target-slot outline without filling the empty position."""

    left, top, right, bottom = rect
    stride = dash + gap
    for x in range(left, right + 1, stride):
        x_end = min(right, x + dash)
        draw.line((x, top, x_end, top), fill=color, width=width)
        draw.line((x, bottom, x_end, bottom), fill=color, width=width)
    for y in range(top, bottom + 1, stride):
        y_end = min(bottom, y + dash)
        draw.line((left, y, left, y_end), fill=color, width=width)
        draw.line((right, y, right, y_end), fill=color, width=width)


def _read_status_value(stage: Any, name: str, default: Any = None) -> Any:
    try:
        status = stage.GetPrimAtPath(STATUS_PRIM_PATH)
        if not status:
            return default
        attr = status.GetAttribute(name)
        value = attr.Get() if attr else None
        return default if value is None else value
    except Exception:
        return default


def _device_location_panel_values(stage: Any) -> dict[str, str]:
    """Return the live device values shown by the always-visible panel."""

    return {
        "Device": _fmt_text(_read_status_value(stage, "rtg:rtgName")),
        "Field": _fmt_text(_read_status_value(stage, "rtg:currentField")),
        "Bay": _fmt_int(_read_status_value(stage, "rtg:currentBay")),
        "WorkMode": _work_mode_text(_read_status_value(stage, "rtg:workMode")),
        "Gantry": _fmt_number(
            _read_status_value(stage, "rtg:gantryMeter"),
            2,
            " m",
        ),
        "Trolley": _fmt_number(
            _read_status_value(stage, "rtg:trolleyMeter"),
            2,
            " m",
        ),
        "Hoist": _fmt_number(
            _read_status_value(stage, "rtg:hoistMeter"),
            2,
            " m",
        ),
    }


def _device_location_panel_value_key(values: dict[str, str]) -> tuple[tuple[str, str], ...]:
    """Return only the stable values that remain visible in the panel."""

    return tuple((key, str(values.get(key) or "--")) for key in DEVICE_LOCATION_PANEL_VALUE_Y)


def _save_bitmap_atomically(image: Any, output_path: Path) -> None:
    """Publish a completed PNG so Kit never reads a partially written file."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{output_path.stem}_",
        suffix=".tmp",
        dir=str(output_path.parent),
    )
    os.close(fd)
    try:
        image.save(temp_name, format="PNG")
        os.replace(temp_name, output_path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _render_device_location_panel_bitmap(
    values: dict[str, str],
    output_path: Path,
    *,
    draw_values: bool = True,
) -> bool:
    """Render the persistent device location panel with the task-panel flow."""

    font_path = _find_cjk_font_path()
    if font_path is None:
        return False
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont  # type: ignore

        draw_width = DEVICE_LOCATION_PANEL_RENDER_WIDTH
        draw_height = DEVICE_LOCATION_PANEL_RENDER_HEIGHT
        image = Image.new("RGBA", (draw_width, draw_height), (0, 0, 0, 0))
        glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        panel_box = (1, 1, draw_width - 2, draw_height - 2)
        glow_draw.rounded_rectangle(
            panel_box,
            radius=12,
            fill=(82, 175, 216, 34),
            outline=(128, 210, 236, 148),
            width=4,
        )
        image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(10)))

        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            panel_box,
            radius=10,
            fill=(10, 38, 58, 248),
            outline=(130, 207, 232, 176),
            width=2,
        )
        draw.line((14, 14, draw_width - 15, 14), fill=(255, 156, 22, 232), width=4)
        draw.rectangle(
            (12, 12, draw_width - 13, draw_height - 13),
            outline=(142, 219, 238, 50),
        )
        draw.polygon(
            [(draw_width - 68, draw_height - 1), (draw_width - 1, draw_height - 68), (draw_width - 1, draw_height - 1)],
            fill=(153, 206, 218, 112),
        )
        draw.rectangle((0, 0, draw_width, 86), fill=(22, 68, 91, 250))

        title_font = ImageFont.truetype(str(font_path), 42)
        label_font = ImageFont.truetype(str(font_path), 31)
        value_font = ImageFont.truetype(str(font_path), 35)
        device_font = ImageFont.truetype(str(font_path), 46)
        draw.text((36, 25), "设备定位", fill=(248, 252, 255, 255), font=title_font)

        labels = {
            "Device": "设备编号",
            "Field": "当前堆场",
            "Bay": "当前贝位",
            "WorkMode": "工作模式",
            "Gantry": "大车位置",
            "Trolley": "小车位置",
            "Hoist": "吊具高度",
        }
        for key, y in DEVICE_LOCATION_PANEL_VALUE_Y.items():
            text = str(values.get(key) or "--")
            font = device_font if key == "Device" else value_font
            color = (
                (88, 222, 248, 255)
                if key == "Device"
                else (246, 252, 255, 255)
            )
            draw.text(
                (40, y - 18),
                labels.get(key, key),
                font=label_font,
                fill=(198, 221, 232, 255),
            )
            if not draw_values:
                continue
            max_width = 260 if key == "Device" else 220
            while (
                len(text) > 2
                and draw.textbbox((0, 0), text, font=font)[2] > max_width
            ):
                text = text[:-2] + "..."
            bbox = draw.textbbox((0, 0), text, font=font)
            x = draw_width - 44 - (bbox[2] - bbox[0])
            draw.text(
                (x, y - (bbox[3] - bbox[1]) // 2),
                text,
                font=font,
                fill=color,
            )
        image = image.resize(
            (DEVICE_LOCATION_PANEL_IMAGE_WIDTH, DEVICE_LOCATION_PANEL_IMAGE_HEIGHT),
            Image.Resampling.LANCZOS,
        )
        _save_bitmap_atomically(image, output_path)
        return True
    except Exception:
        return False


def _render_device_location_values_bitmap(
    values: dict[str, str],
    output_path: Path,
) -> bool:
    """Render only changing values onto a transparent overlay."""

    font_path = _find_cjk_font_path()
    if font_path is None:
        return False
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore

        draw_width = DEVICE_LOCATION_PANEL_IMAGE_WIDTH
        draw_height = DEVICE_LOCATION_PANEL_IMAGE_HEIGHT
        scale_x = draw_width / DEVICE_LOCATION_PANEL_RENDER_WIDTH
        scale_y = draw_height / DEVICE_LOCATION_PANEL_RENDER_HEIGHT
        image = Image.new(
            "RGBA",
            (draw_width, draw_height),
            (0, 0, 0, 0),
        )
        draw = ImageDraw.Draw(image)
        value_font = ImageFont.truetype(str(font_path), int(35 * scale_y))
        device_font = ImageFont.truetype(str(font_path), int(46 * scale_y))
        for key, y in DEVICE_LOCATION_PANEL_VALUE_Y.items():
            text = str(values.get(key) or "--")
            font = device_font if key == "Device" else value_font
            max_width = int((260 if key == "Device" else 220) * scale_x)
            while len(text) > 2 and draw.textbbox((0, 0), text, font=font)[2] > max_width:
                text = text[:-2] + "..."
            bbox = draw.textbbox((0, 0), text, font=font)
            x = draw_width - int(44 * scale_x) - (bbox[2] - bbox[0])
            y_px = int(y * scale_y)
            draw.text(
                (x, y_px - (bbox[3] - bbox[1]) // 2),
                text,
                font=font,
                fill=(88, 222, 248, 255) if key == "Device" else (246, 252, 255, 255),
            )
        _save_bitmap_atomically(image, output_path)
        return True
    except Exception:
        return False


def _render_work_mode_indicator_bitmap(mode: int, output_path: Path) -> bool:
    """Render one circular work-mode badge for the independent HUD indicator."""

    font_path = _find_cjk_font_path()
    if font_path is None:
        return False
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont  # type: ignore

        size = WORK_MODE_INDICATOR_IMAGE_WIDTH
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        center = size // 2
        outer = int(size * 0.43)
        glow_draw.ellipse(
            (center - outer, center - outer, center + outer, center + outer),
            fill=(54, 214, 245, 88),
            outline=(78, 229, 249, 180),
            width=10,
        )
        image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(18)))

        draw = ImageDraw.Draw(image)
        # Match the RCMS 3D display control-mode icons: a dark concentric
        # circular frame, saturated mode color, and the same six symbols.
        mode_colors = {
            0: ((55, 71, 80, 235), (119, 137, 153, 255)),
            1: ((34, 190, 112, 255), (67, 245, 130, 255)),
            2: ((24, 166, 202, 255), (56, 213, 255, 255)),
            3: ((38, 104, 202, 255), (96, 165, 250, 255)),
            4: ((190, 146, 12, 255), (250, 204, 21, 255)),
            5: ((178, 42, 66, 255), (255, 77, 90, 255)),
        }
        fill, edge = mode_colors.get(mode, mode_colors[0])
        draw.ellipse(
            (center - outer, center - outer, center + outer, center + outer),
            fill=(3, 18, 30, 250),
            outline=(16, 41, 55, 255),
            width=18,
        )
        ring = outer - 19
        draw.ellipse(
            (center - ring, center - ring, center + ring, center + ring),
            fill=(4, 23, 34, 255),
            outline=edge,
            width=8,
        )
        inner = outer - 42
        draw.ellipse(
            (center - inner, center - inner, center + inner, center + inner),
            fill=(5, 27, 39, 255),
            outline=edge,
            width=5,
        )
        # Small broken arcs reproduce the RCMS neon ring without making the
        # center wash out the mode glyph.
        arc_box = (center - outer + 9, center - outer + 9, center + outer - 9, center + outer - 9)
        for start in (18, 138, 258):
            draw.arc(arc_box, start, start + 62, fill=edge, width=7)

        title_font = ImageFont.truetype(str(font_path), 42)
        code_font = ImageFont.truetype(str(font_path), 20)
        title = WORK_MODE_LABELS.get(mode, "未知")
        icon_stroke = max(5, size // 58)
        icon_fill = (234, 250, 255, 245)
        icon_color = edge
        icon_box = (center - 76, center - 82, center + 76, center + 70)
        if mode == 0:
            draw.ellipse((center - 53, center - 53, center + 53, center + 53), outline=icon_color, width=8)
            draw.text((center, center - 5), "?", font=title_font, fill=icon_fill, anchor="mm")
        elif mode == 1:
            draw.arc(icon_box, 35, 315, fill=icon_color, width=icon_stroke)
            draw.polygon(
                [(center + 62, center + 36), (center + 76, center + 36), (center + 69, center + 51)],
                fill=icon_color,
            )
            draw.ellipse((center - 16, center - 16, center + 16, center + 16), fill=icon_color)
            draw.line((center, center - 63, center, center - 78), fill=icon_color, width=icon_stroke // 2)
        elif mode == 2:
            draw.ellipse((center - 57, center - 57, center + 57, center + 57), outline=icon_color, width=icon_stroke)
            draw.line((center - 38, center, center + 38, center), fill=icon_color, width=icon_stroke)
            draw.line((center, center - 38, center, center + 38), fill=icon_color, width=icon_stroke)
            draw.ellipse((center - 72, center - 14, center - 44, center + 14), fill=icon_fill)
            draw.ellipse((center + 44, center - 14, center + 72, center + 14), fill=icon_fill)
        elif mode == 3:
            draw.rounded_rectangle((center - 63, center - 50, center + 63, center + 24), radius=12, outline=icon_color, width=icon_stroke, fill=(23, 68, 126, 120))
            draw.line((center - 23, center + 47, center + 23, center + 47), fill=icon_color, width=icon_stroke)
            draw.line((center, center + 24, center, center + 47), fill=icon_color, width=icon_stroke)
            draw.arc((center - 47, center - 38, center + 47, center + 28), 205, 335, fill=icon_fill, width=icon_stroke // 2)
            draw.arc((center - 30, center - 25, center + 30, center + 18), 205, 335, fill=icon_fill, width=icon_stroke // 2)
        elif mode == 4:
            draw.polygon([(center - 70, center - 8), (center, center - 70), (center + 70, center - 8)], outline=icon_color, fill=(112, 94, 20, 100))
            draw.rounded_rectangle((center - 52, center - 8, center + 52, center + 55), radius=8, outline=icon_color, width=icon_stroke, fill=(113, 94, 20, 90))
            draw.ellipse((center - 12, center + 13, center + 12, center + 37), fill=icon_fill)
        else:
            draw.line((center + 40, center - 58, center + 69, center - 29), fill=icon_color, width=icon_stroke + 5)
            draw.line((center - 54, center + 58, center + 42, center - 36), fill=icon_color, width=icon_stroke + 13)
            draw.line((center - 69, center + 51, center - 45, center + 75), fill=icon_color, width=icon_stroke + 8)
            draw.ellipse((center - 68, center + 42, center - 38, center + 72), fill=icon_color)
            draw.arc((center + 28, center - 78, center + 78, center - 28), 270, 90, fill=icon_fill, width=icon_stroke)
        draw.text(
            (center, center + outer - 8),
            title,
            font=code_font,
            fill=(236, 251, 255, 255),
            anchor="mm",
        )
        _save_bitmap_atomically(image, output_path)
        return True
    except Exception:
        return False


@dataclass(frozen=True)
class ContainerInfoRow:
    label: str
    value: str


def _container_read_attr(prim: Any, name: str, default: Any = None) -> Any:
    if not prim:
        return default
    try:
        attr = prim.GetAttribute(name)
        value = attr.Get() if attr else None
        return default if value is None else value
    except Exception:
        return default


def _container_first_attr(
    prims: tuple[Any, ...],
    names: tuple[str, ...],
    default: Any = None,
) -> Any:
    for prim in prims:
        for name in names:
            value = _container_read_attr(prim, name)
            if value not in (None, ""):
                return value
    return default


def _container_fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "--"
    if isinstance(value, float):
        text = f"{value:.3f}".rstrip("0").rstrip(".")
    else:
        text = str(value).strip()
    return f"{text}{suffix}" if text else "--"


def _container_fmt_bool(value: Any) -> str:
    if value is None:
        return "--"
    return "YES" if bool(value) else "NO"


def _container_context_prims(stage: Any, root_path: str) -> tuple[Any, ...]:
    paths = [root_path, f"{root_path}/Body"]
    if root_path.startswith(f"{LIVE_TASK_STACK_PATH}/"):
        paths.append(LIVE_TASK_STACK_PATH)
    if root_path.startswith(f"{YARD_INVENTORY_ROOT_PATH}/"):
        parts = tuple(part for part in root_path.split("/") if part)
        if len(parts) >= 4:
            paths.append("/" + "/".join(parts[:4]))
    prims = []
    for path in paths:
        prim = stage.GetPrimAtPath(path)
        if prim:
            prims.append(prim)
    return tuple(prims)


def _collect_container_info_rows(stage: Any, root_path: str) -> tuple[ContainerInfoRow, ...]:
    status = stage.GetPrimAtPath(STATUS_PRIM_PATH)
    prims = _container_context_prims(stage, root_path)
    bay_id = _container_first_attr(prims, ("live:bayId", "inventory:bayId"))
    row = _container_first_attr(prims, ("live:row",))
    tier = _container_first_attr(prims, ("live:tier",))
    tiers = _container_first_attr(prims, ("live:tiers",))
    size = _container_first_attr(
        prims,
        ("live:containerSizeFt", "inventory:containerSizeFt"),
        _container_read_attr(status, "rtg:containerSize"),
    )
    height = _container_first_attr(
        prims,
        ("live:containerHeightMeters", "live:taskContainerHeightMeters"),
        _container_read_attr(status, "rtg:containerHeight"),
    )
    container_no = _container_first_attr(
        prims,
        ("live:containerNo", "inventory:containerNo", "containerNo"),
        _container_read_attr(status, "rtg:containerNo"),
    )
    container_type = _container_first_attr(
        prims,
        ("live:containerType", "inventory:containerType", "containerType"),
        _container_read_attr(status, "rtg:containerType"),
    )
    row_tier = "--"
    if row not in (None, "") and tier not in (None, ""):
        row_tier = f"R{int(row):02d} / T{int(tier):02d}"
        if tiers not in (None, ""):
            row_tier += f" of {int(tiers)}"
    return (
        ContainerInfoRow("Container", _container_fmt(container_no)),
        ContainerInfoRow("Type", _container_fmt(container_type)),
        ContainerInfoRow(
            "Size",
            _container_fmt(size, " ft") if size not in (None, "", 0, "0") else "--",
        ),
        ContainerInfoRow(
            "Height",
            _container_fmt(height, " m") if height not in (None, "") else "--",
        ),
        ContainerInfoRow("Bay", " / ".join(part for part in (str(bay_id or "--"), row_tier) if part != "--") or "--"),
        ContainerInfoRow("Task ID", _container_fmt(_container_read_attr(status, "rtg:taskId"))),
        ContainerInfoRow("Locked", _container_fmt_bool(_container_read_attr(status, "rtg:spreaderLocked"))),
    )


def _render_container_info_panel_bitmap(
    rows: tuple[ContainerInfoRow, ...],
    output_path: Path,
) -> bool:
    font_path = _find_cjk_font_path()
    if font_path is None:
        return False
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont  # type: ignore

        width = CONTAINER_INFO_PANEL_IMAGE_WIDTH
        height = CONTAINER_INFO_PANEL_IMAGE_HEIGHT
        values = {row.label: row.value for row in rows}
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        box = (1, 1, width - 2, height - 2)
        glow_draw.rounded_rectangle(box, radius=12, fill=(54, 151, 190, 44), outline=(150, 228, 248, 222), width=4)
        image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(4)))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(box, radius=10, fill=(9, 31, 46, 250), outline=(154, 230, 250, 238), width=2)
        draw.line((14, 14, width - 15, 14), fill=(255, 172, 44, 255), width=4)
        draw.rectangle((12, 12, width - 13, height - 13), outline=(142, 219, 238, 120))
        draw.rectangle((0, 0, width, 104), fill=(16, 55, 76, 252))

        title_font = ImageFont.truetype(str(font_path), 42)
        label_font = ImageFont.truetype(str(font_path), 31)
        value_font = ImageFont.truetype(str(font_path), 35)
        big_font = ImageFont.truetype(str(font_path), 46)
        draw.text((12, 15), "箱信息", fill=(255, 255, 255, 255), font=title_font)
        badge = "选中箱"
        badge_font = ImageFont.truetype(str(font_path), 26)
        badge_bbox = draw.textbbox((0, 0), badge, font=badge_font)
        badge_w = badge_bbox[2] - badge_bbox[0] + 36
        badge_x2 = width - 40
        badge_x1 = badge_x2 - badge_w
        draw.rounded_rectangle((badge_x1, 36, badge_x2, 76), radius=18, fill=(58, 74, 50, 218), outline=(255, 197, 72, 230), width=2)
        draw.text((badge_x1 + 18, 39), badge, fill=(255, 211, 92, 255), font=badge_font)
        for key, y in CONTAINER_INFO_PANEL_ROW_Y.items():
            label = CONTAINER_INFO_PANEL_LABELS[key]
            value = str(values.get(key) or "--")
            if key == "Locked":
                value = {"YES": "已锁闭", "NO": "未锁闭"}.get(value, value)
            font = big_font if key == "Container" else value_font
            while len(value) > 2 and draw.textbbox((0, 0), value, font=font)[2] > 560:
                value = value[:-2] + "..."
            draw.text((44, y - 23), label, fill=(198, 221, 232, 255), font=label_font)
            bbox = draw.textbbox((0, 0), value, font=font)
            color = (88, 222, 248, 255) if key == "Container" else (246, 252, 255, 255)
            if key == "Locked" and value == "未锁闭":
                color = (255, 86, 86, 255)
            draw.text((width - 44 - (bbox[2] - bbox[0]), y - 28), value, fill=color, font=font)
        _save_bitmap_atomically(image, output_path)
        return True
    except Exception:
        return False


def _render_truck_info_panel_bitmap(
    info: TruckRunInfo,
    output_path: Path,
) -> bool:
    font_path = _find_cjk_font_path()
    if font_path is None:
        return False
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont  # type: ignore

        width = TRUCK_INFO_PANEL_IMAGE_WIDTH
        height = TRUCK_INFO_PANEL_IMAGE_HEIGHT
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        box = (1, 1, width - 2, height - 2)
        glow_draw.rounded_rectangle(
            box,
            radius=14,
            fill=(38, 190, 230, 46),
            outline=(124, 235, 255, 230),
            width=4,
        )
        image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(5)))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            box,
            radius=12,
            fill=(7, 28, 46, 248),
            outline=(138, 231, 255, 240),
            width=2,
        )
        draw.rectangle((0, 0, width, 88), fill=(14, 66, 94, 252))
        draw.line((20, 100, width - 20, 100), fill=(61, 220, 255, 155), width=2)
        draw.rectangle((16, 16, width - 17, height - 17), outline=(126, 220, 244, 105))

        title_font = ImageFont.truetype(str(font_path), 42)
        label_font = ImageFont.truetype(str(font_path), 30)
        value_font = ImageFont.truetype(str(font_path), 46)
        badge_font = ImageFont.truetype(str(font_path), 25)
        draw.text((24, 18), "集卡信息", fill=(245, 252, 255, 255), font=title_font)
        badge = "校验通过"
        badge_bbox = draw.textbbox((0, 0), badge, font=badge_font)
        badge_w = badge_bbox[2] - badge_bbox[0] + 38
        badge_x2 = width - 30
        badge_x1 = badge_x2 - badge_w
        draw.rounded_rectangle(
            (badge_x1, 28, badge_x2, 68),
            radius=18,
            fill=(20, 114, 89, 225),
            outline=(88, 255, 205, 230),
            width=2,
        )
        draw.text((badge_x1 + 19, 31), badge, fill=(204, 255, 235, 255), font=badge_font)

        rows = (
            ("Type", info.car_type),
            ("Number", info.car_no),
        )
        for index, (key, value) in enumerate(rows):
            y = 170 + index * 105
            draw.text(
                (44, y - 23),
                TRUCK_INFO_PANEL_LABELS[key],
                fill=(192, 221, 235, 255),
                font=label_font,
            )
            text = str(value or "--")
            while len(text) > 2 and draw.textbbox((0, 0), text, font=value_font)[2] > 410:
                text = text[:-2] + "..."
            bbox = draw.textbbox((0, 0), text, font=value_font)
            color = (92, 232, 255, 255) if key == "Number" else (246, 252, 255, 255)
            draw.text(
                (width - 52 - (bbox[2] - bbox[0]), y - 34),
                text,
                fill=color,
                font=value_font,
            )

        _save_bitmap_atomically(image, output_path)
        return True
    except Exception:
        return False


def _container_info_panel_world_z(origin_z: Any) -> float:
    try:
        z = float(origin_z) + CONTAINER_INFO_PANEL_Z_OFFSET_M
    except Exception:
        z = 4.0
    minimum_center_z = (
        CONTAINER_INFO_PANEL_MIN_BOTTOM_WORLD_Z
        + CONTAINER_INFO_PANEL_HEIGHT_M * 0.5
    )
    return max(z, minimum_center_z)


def _render_task_info_panel_bitmap(
    task: Dict[str, Any],
    output_path: Path,
    *,
    route_phase: float = 0.0,
) -> bool:
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
        map_left = TASK_PANEL_BAY_MAP_LEFT_PX
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
            try:
                truck_icon = Image.open(TASK_PANEL_TRUCK_ICON_PATH).convert("RGBA")
                if truck_icon.getbbox() is not None:
                    truck_icon = truck_icon.crop(truck_icon.getbbox())
                truck_icon.thumbnail((lane_w + 10, lane_w + 10), Image.Resampling.LANCZOS)
                icon_pixels = truck_icon.load()
                for icon_y in range(truck_icon.height):
                    for icon_x in range(truck_icon.width):
                        red, green, blue, alpha = icon_pixels[icon_x, icon_y]
                        if alpha:
                            icon_pixels[icon_x, icon_y] = (220, 226, 230, alpha)
                icon_left = int(lane_left + (lane_w - truck_icon.width) * 0.5)
                icon_top = int(bottom - 39 - truck_icon.height * 0.5)
                image.alpha_composite(truck_icon, (icon_left, icon_top))
            except Exception:
                draw.rectangle(
                    (lane_left, bottom - 78, lane_left + lane_w, bottom),
                    fill=(255, 190, 78, 132),
                    outline=(255, 190, 78, 132),
                )
            draw.text((lane_left - 4, bottom + 12), "车道", fill=(198, 221, 232, 230), font=map_label_font)
            source_marker, target_marker = _task_panel_slot_markers(task)
            for column_index, row_index in enumerate(TASK_PANEL_BAY_MAP_ROWS_LEFT_TO_RIGHT):
                height_text = pattern[row_index - 1]
                x = left + column_index * (cell_w + gap)
                stack_height = max(0, int(height_text))
                draw.text((x + 16, bottom + 12), f"R{row_index}", fill=(198, 221, 232, 230), font=map_label_font)
                for tier in range(1, stack_height + 1):
                    y0 = bottom - tier * (cell_h + 9)
                    is_source = source_marker == ("slot", row_index, tier)
                    fill = (255, 190, 78, 210) if is_source else (63, 132, 154, 205)
                    outline = (255, 190, 78, 232) if is_source else (150, 224, 240, 118)
                    draw.rectangle((x, y0, x + cell_w, y0 + cell_h), fill=fill, outline=outline)
            if (
                target_marker is not None
                and target_marker[1] in TASK_PANEL_BAY_MAP_ROWS_LEFT_TO_RIGHT
                and 1 <= target_marker[2] <= 6
            ):
                _kind, target_row, target_tier = target_marker
                target_column = TASK_PANEL_BAY_MAP_ROWS_LEFT_TO_RIGHT.index(target_row)
                target_x = left + target_column * (cell_w + gap)
                target_y = bottom - target_tier * (cell_h + 9)
                _draw_task_panel_dashed_slot(
                    draw,
                    (target_x, target_y, target_x + cell_w, target_y + cell_h),
                    (88, 222, 248, 255),
                )
            route = _task_panel_route_map_points(
                task,
                map_left=map_left,
                bottom=bottom,
                cell_w=cell_w,
                cell_h=cell_h,
                gap=gap,
                lane_left=lane_left,
                lane_w=lane_w,
            )
            if route is not None:
                _draw_task_panel_route_indicator(draw, route, route_phase)
        else:
            draw.text((map_left + 6, map_top + 54), "--", fill=(246, 252, 255, 255), font=value_font)

        _save_bitmap_atomically(image, output_path)
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


def _task_truck_imported_visual_translate_y(cab_center_y: float) -> float:
    """Align the Blender lane-truck cab with the task-specific cab position."""

    return float(cab_center_y) - LIVE_TASK_TRUCK_ASSET_CAB_CENTER_Y_M


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
        self._task_route_cleanup_stage_id: Optional[int] = None
        self._feeder_ship_replacement_stage_id: Optional[int] = None
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
        self._last_runinfo_payload: Dict[str, Any] = {}
        # Keep the last successfully validated truck for the active LOAD /
        # UNLOAD task.  Ordinary RunInfo messages continue to update the HUD,
        # but must not erase the truck data used by the click-to-inspect panel.
        self._validated_truck_runinfo: Optional[TruckRunInfo] = None
        self._validated_truck_task_id: Optional[str] = None
        self._floating_task_panel_key: Optional[tuple[Any, ...]] = None
        self._floating_task_panel_bitmap_seq = 0
        self._floating_task_panel_diag_key: Optional[tuple[Any, ...]] = None
        self._floating_device_panel_key: Optional[tuple[Any, ...]] = None
        self._floating_device_panel_value_key: Optional[tuple[Any, ...]] = None
        self._floating_device_panel_bitmap_seq = 0
        self._last_device_panel_render_at = 0.0
        self._pending_device_panel_key: Optional[tuple[Any, ...]] = None
        self._pending_device_panel_since = 0.0
        self._device_panel_selection_sub = None
        self._device_panel_selected = False
        self._device_panel_visible = False
        self._device_panel_last_selection_signature: tuple[str, ...] = ()
        self._last_device_selection_poll_at = 0.0
        self._container_info_selected_root_path: Optional[str] = None
        self._container_info_panel_visible = False
        self._container_info_panel_key: Optional[tuple[Any, ...]] = None
        self._container_info_panel_bitmap_seq = 0
        self._last_container_info_data_refresh_at = 0.0
        self._container_info_panel_position_key: Optional[tuple[Any, ...]] = None
        self._truck_info_selected_root_path: Optional[str] = None
        self._truck_info_panel_visible = False
        self._truck_info_panel_key: Optional[tuple[Any, ...]] = None
        self._truck_info_panel_bitmap_seq = 0
        self._truck_info_panel_position_key: Optional[tuple[Any, ...]] = None
        self._truck_info_panel_diag_key: Optional[tuple[Any, ...]] = None
        self._last_panel_facing_update_at = 0.0
        self._last_error_at = 0.0
        self._weather_stage_id: Optional[int] = None
        self._weather_signature: Optional[tuple[Any, ...]] = None
        self._automatic_signal_work_mode = 0
        self._automatic_signal_stage_id: Optional[int] = None
        self._automatic_signal_antenna_world: Optional[
            tuple[float, float, float]
        ] = None
        self._automatic_signal_last_update_at = 0.0
        self._automatic_signal_visible: Optional[bool] = None
        self._work_mode_indicator_mode = 0
        self._work_mode_indicator_rendered_mode: Optional[int] = None
        self._work_mode_indicator_bitmap_seq = 0
        self._work_mode_indicator_last_render_at = 0.0
        self._work_mode_indicator_stage_id: Optional[int] = None
        self._work_mode_indicator_transform_key: Optional[tuple[Any, ...]] = None
        self._work_mode_indicator_task: Dict[str, Any] = {}

    def start(self) -> None:
        if self._subscription is not None:
            return
        startup_step = "initializing"
        self._log(f"loading live driver build={LIVE_DRIVER_BUILD_ID} scripts={SCRIPT_DIR}")
        try:
            import omni.kit.app  # type: ignore

            startup_step = "dismiss legacy runinfo"
            _dismiss_legacy_runinfo_window()
            startup_step = "pause timeline"
            pause_timeline_for_live_control(self._log)
            stage = self._current_stage()
            if stage is not None:
                startup_step = "disable demo artifacts"
                self._disable_demo_artifacts_for_stage(stage)
                startup_step = "ensure 4C feeder ship replacements"
                self._ensure_4c_feeder_ship_replacements(stage)
                startup_step = "sync user side view camera"
                self._sync_user_side_view_camera(stage)
                startup_step = "remove runinfo billboards"
                _remove_runinfo_billboards(stage, keep_current=False)
                startup_step = "hide floating device panel"
                self._hide_floating_device_panel(stage)
                startup_step = "hide container info panel"
                self._hide_container_info_panel(stage)
                startup_step = "hide truck info panel"
                self._hide_truck_info_panel(stage)
                startup_step = "show work mode indicator"
                # Reuse the last applied heartbeat mode immediately after a
                # hot reload; the next TCP snapshot will still replace it.
                self._automatic_signal_work_mode = _work_mode_code(
                    _read_status_value(stage, "rtg:workMode", 0)
                )
                self._sync_work_mode_indicator(
                    stage,
                    self._automatic_signal_work_mode,
                    {},
                )
                self._device_panel_selected = _selected_rtg_path() is not None
                startup_step = "sync floating runinfo billboard"
                self._sync_floating_runinfo_billboard(stage, {}, visible=False)
                startup_step = "sync floating device panel"
                self._sync_floating_device_panel(stage, {})
                startup_step = "sync floating task panel"
                self._sync_floating_task_panel(stage, {})
            startup_step = "subscribe device panel selection"
            self._subscribe_device_panel_selection()
            if self._status_overlay is None:
                startup_step = "create status overlay"
                self._status_overlay = RtgTwinStatusOverlay()
            startup_step = "subscribe Omniverse update stream"
            stream = omni.kit.app.get_app().get_update_event_stream()
            self._subscription = stream.create_subscription_to_pop(
                self._on_update, name="SmartPortRtgTwinDriver"
            )
            builtins._smart_port_rtg_panel_facing_driver = self
            self._log("Omniverse main-thread driver started")
        except Exception as exc:  # noqa: BLE001 - Kit may not be loaded in tests
            self._log(
                f"unable to start Omniverse update driver at {startup_step}: {exc}"
            )
            self._log(traceback.format_exc().rstrip())

    def stop(self) -> None:
        selection_sub = self._device_panel_selection_sub
        if selection_sub is not None:
            try:
                selection_sub.unsubscribe()
            except Exception:
                pass
            self._device_panel_selection_sub = None
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
            try:
                self._set_automatic_signal_visible(stage, False, force=True)
            except Exception:
                pass
            try:
                self._hide_work_mode_indicator(stage)
            except Exception:
                pass
        if getattr(builtins, "_smart_port_rtg_panel_facing_driver", None) is self:
            builtins._smart_port_rtg_panel_facing_driver = None
        _dismiss_legacy_runinfo_window()
        self._controller = None
        self._controller_stage_id = None
        self._demo_artifacts_stage_id = None
        self._task_route_cleanup_stage_id = None
        self._feeder_ship_replacement_stage_id = None
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
        self._last_runinfo_payload = {}
        self._validated_truck_runinfo = None
        self._validated_truck_task_id = None
        self._floating_task_panel_key = None
        self._floating_task_panel_bitmap_seq = 0
        self._floating_task_panel_diag_key = None
        self._floating_device_panel_key = None
        self._floating_device_panel_value_key = None
        self._floating_device_panel_bitmap_seq = 0
        self._last_device_panel_render_at = 0.0
        self._pending_device_panel_key = None
        self._pending_device_panel_since = 0.0
        self._device_panel_selected = False
        self._device_panel_visible = False
        self._device_panel_last_selection_signature = ()
        self._last_device_selection_poll_at = 0.0
        self._container_info_selected_root_path = None
        self._container_info_panel_visible = False
        self._container_info_panel_key = None
        self._container_info_panel_bitmap_seq = 0
        self._last_container_info_data_refresh_at = 0.0
        self._container_info_panel_position_key = None
        self._truck_info_selected_root_path = None
        self._truck_info_panel_visible = False
        self._truck_info_panel_key = None
        self._truck_info_panel_bitmap_seq = 0
        self._truck_info_panel_position_key = None
        self._truck_info_panel_diag_key = None
        self._weather_stage_id = None
        self._weather_signature = None
        self._automatic_signal_work_mode = 0
        self._automatic_signal_stage_id = None
        self._automatic_signal_antenna_world = None
        self._automatic_signal_last_update_at = 0.0
        self._automatic_signal_visible = None
        self._work_mode_indicator_mode = 0
        self._work_mode_indicator_rendered_mode = None
        self._work_mode_indicator_bitmap_seq = 0
        self._work_mode_indicator_last_render_at = 0.0
        self._work_mode_indicator_stage_id = None
        self._work_mode_indicator_transform_key = None
        self._work_mode_indicator_task = {}
        self._last_weather_attribute_sync_at = 0.0
        self._weather_mode = "day"
        if stage is not None:
            self._hide_floating_device_panel(stage)
            self._hide_container_info_panel(stage)
            self._hide_truck_info_panel(stage)
            self._set_automatic_signal_visible(stage, False, force=True)
            self._hide_work_mode_indicator(stage)
        if self._status_overlay is not None:
            self._status_overlay.destroy()
            self._status_overlay = None

    def enqueue(self, state: Dict[str, Any]) -> None:
        with self._latest_lock:
            self._latest_state = state

    def _on_update(self, _event: Any) -> None:
        stage = self._current_stage()
        if stage is not None:
            self._sync_weather_environment(stage)
        self._sync_visible_panels_facing_camera_if_due()
        if self._refresh_device_panel_selection("poll"):
            stage = self._current_stage()
            if stage is not None:
                with self._latest_lock:
                    pending_state = dict(self._latest_state or {})
                if self._container_info_selected_root_path:
                    self._show_container_info_panel(
                        stage,
                        self._container_info_selected_root_path,
                    )
                elif self._container_info_panel_visible:
                    self._hide_container_info_panel(stage)
                if self._truck_info_selected_root_path:
                    self._show_truck_info_panel(
                        stage,
                        self._truck_info_selected_root_path,
                    )
                elif self._truck_info_panel_visible:
                    self._hide_truck_info_panel(stage)
                self._sync_floating_device_panel(
                    stage,
                    dict(pending_state.get("task") or {}),
                )
        elif (
            self._container_info_selected_root_path
            and time.monotonic() - self._last_container_info_data_refresh_at
            >= CONTAINER_INFO_DATA_REFRESH_INTERVAL_SECONDS
        ):
            stage = self._current_stage()
            if stage is not None:
                self._last_container_info_data_refresh_at = time.monotonic()
                self._show_container_info_panel(
                    stage,
                    self._container_info_selected_root_path,
                )
        elif (
            self._truck_info_selected_root_path
            and time.monotonic() - self._last_container_info_data_refresh_at
            >= CONTAINER_INFO_DATA_REFRESH_INTERVAL_SECONDS
        ):
            stage = self._current_stage()
            if stage is not None:
                self._last_container_info_data_refresh_at = time.monotonic()
                self._show_truck_info_panel(
                    stage,
                    self._truck_info_selected_root_path,
                )
        with self._latest_lock:
            state = self._latest_state
            self._latest_state = None
        if isinstance(state, dict):
            self._automatic_signal_work_mode = _state_work_mode(state)
            self._work_mode_indicator_task = dict(state.get("task") or {})
        if stage is not None:
            self._sync_automatic_mode_signal(stage, time.monotonic())
            # A render update can arrive between TCP snapshots.  Keep the
            # last task anchor during that gap; switching to an empty task
            # would make the badge alternate between task-bay Y and gantry Y.
            indicator_task = (
                dict(state.get("task") or {})
                if isinstance(state, dict)
                else dict(self._work_mode_indicator_task)
            )
            indicator_mode = (
                self._automatic_signal_work_mode
                if isinstance(state, dict)
                else _read_status_value(stage, "rtg:workMode", self._work_mode_indicator_mode)
            )
            self._sync_work_mode_indicator(stage, indicator_mode, indicator_task)
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
                        raw_task = dict(state.get("task") or {})
                        raw_runinfo = state.get("runinfo")
                        self._update_validated_truck_runinfo_cache(
                            raw_runinfo,
                            raw_task,
                        )
                        runinfo_visible = _runinfo_should_be_visible(
                            raw_task,
                            raw_runinfo,
                        )
                        self._sync_floating_runinfo_billboard(
                            stage,
                            self._runinfo_payload_for_task(
                                raw_runinfo,
                                raw_task,
                            ),
                            visible=runinfo_visible,
                        )
                        self._sync_floating_device_panel(
                            stage,
                            state.get("task") or {},
                        )
                        self._sync_floating_task_panel(
                            stage,
                            dict(state.get("task") or {}),
                        )
                    except Exception as exc:  # noqa: BLE001 - keep manual control alive
                        self._log(f"live panel update failed: {exc}")
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
    def _sync_weather_environment(
        self,
        stage: Any,
    ) -> None:
        """Keep RTG Control weather authoritative while Live mode is running.

        RTG Control and the Property panel author the same USD attributes.
        Live task/pose updates never replace them; this cheap signature check
        applies Day/Night/Rain/Fog plus rain, fog, and wind changes in the
        existing Live update loop without adding another timer.  Rain motion
        also stays active when the Kit timeline is paused.
        """

        try:
            import smart_port_environment as weather

            environment = stage.GetPrimAtPath(weather.ENVIRONMENT_ROOT_PATH)
            if environment is None or not environment.IsValid():
                return

            now = time.monotonic()
            stage_changed = getattr(self, "_weather_stage_id", None) != id(stage)
            attributes_due = (
                stage_changed
                or now - getattr(self, "_last_weather_attribute_sync_at", 0.0)
                >= WEATHER_ATTRIBUTE_SYNC_INTERVAL_SECONDS
            )
            mode = getattr(self, "_weather_mode", "day")

            def attr(name: str, default: Any) -> Any:
                value = environment.GetAttribute(name).Get()
                return default if value is None else value

            if attributes_due:
                # A previous Kit session can leave a night preset in the
                # anonymous session layer.  Live RTG startup should always
                # begin in the authored daytime view; the environment
                # controls can still switch to night/rain/fog afterwards.
                mode = (
                    "day"
                    if stage_changed
                    else weather.current_environment_mode(stage)
                )
                rain = weather.normalize_rain_amount(
                    attr(weather.RAIN_AMOUNT_ATTRIBUTE, weather.DEFAULT_RAIN_AMOUNT)
                )
                fog = weather.normalize_fog_amount(
                    attr(weather.FOG_AMOUNT_ATTRIBUTE, weather.DEFAULT_FOG_AMOUNT)
                )
                wind = float(attr(weather.WIND_SPEED_ATTRIBUTE, weather.DEFAULT_WIND_SPEED_MPS))
                signature = (id(stage), mode, rain, fog, round(wind, 4))
                if signature != self._weather_signature:
                    weather.apply_environment_mode(stage, mode)
                    self._weather_signature = signature
                self._weather_stage_id = id(stage)
                self._weather_mode = mode
                self._last_weather_attribute_sync_at = now

            if mode == "rain":
                weather.update_weather_animation(stage, now)
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError) as exc:
            now = time.time()
            if now - self._last_error_at > 1.0:
                self._last_error_at = now
                self._log(f"weather sync failed: {exc}")

    def _sync_automatic_mode_signal(self, stage: Any, now: float) -> None:
        """Animate the RCMS antenna link while heartbeat work_mode is remote manual."""

        try:
            stage_id = id(stage)
            if self._automatic_signal_stage_id != stage_id:
                self._automatic_signal_stage_id = stage_id
                self._automatic_signal_antenna_world = None
                self._automatic_signal_last_update_at = 0.0
                self._automatic_signal_visible = None

            if self._automatic_signal_work_mode != 3:
                self._set_automatic_signal_visible(stage, False)
                return
            if (
                now - self._automatic_signal_last_update_at
                < AUTOMATIC_SIGNAL_UPDATE_INTERVAL_SECONDS
            ):
                return

            endpoints = self._automatic_signal_endpoints(stage)
            if endpoints is None:
                self._set_automatic_signal_visible(stage, False)
                return
            start, end = endpoints
            points, control = _automatic_signal_arc(start, end)
            left_points = _automatic_signal_rail_points(
                points,
                start,
                end,
                AUTOMATIC_SIGNAL_RAIL_OFFSET_M,
            )
            right_points = _automatic_signal_rail_points(
                points,
                start,
                end,
                -AUTOMATIC_SIGNAL_RAIL_OFFSET_M,
            )

            from pxr import Gf, Sdf, Usd, UsdGeom

            with Usd.EditContext(stage, stage.GetSessionLayer()):
                self._ensure_automatic_signal_geometry(stage)

                def set_curve_points(path: str, values: list[tuple[float, float, float]]) -> None:
                    curve = UsdGeom.BasisCurves.Get(stage, path)
                    curve.GetPointsAttr().Set([Gf.Vec3f(*value) for value in values])

                set_curve_points(AUTOMATIC_SIGNAL_OUTER_CURVE_PATH, points)
                set_curve_points(AUTOMATIC_SIGNAL_CORE_CURVE_PATH, points)
                set_curve_points(AUTOMATIC_SIGNAL_LEFT_CURVE_PATH, left_points)
                set_curve_points(AUTOMATIC_SIGNAL_RIGHT_CURVE_PATH, right_points)

                breath = 0.5 + 0.5 * math.sin(now * 3.2)
                UsdGeom.BasisCurves.Get(
                    stage, AUTOMATIC_SIGNAL_OUTER_CURVE_PATH
                ).GetWidthsAttr().Set([0.34 + breath * 0.12])
                UsdGeom.BasisCurves.Get(
                    stage, AUTOMATIC_SIGNAL_CORE_CURVE_PATH
                ).GetWidthsAttr().Set([0.075 + breath * 0.025])

                for index in range(AUTOMATIC_SIGNAL_PULSE_COUNT):
                    progress = (
                        now * AUTOMATIC_SIGNAL_PACKET_SPEED
                        + index / AUTOMATIC_SIGNAL_PULSE_COUNT
                    ) % 1.0
                    point = _quadratic_signal_point(start, control, end, progress)
                    prim = stage.GetPrimAtPath(
                        f"{AUTOMATIC_SIGNAL_PULSE_ROOT_PATH}/Packet_{index:02d}"
                    )
                    self._set_xform_op_attr_value(
                        prim,
                        "xformOp:translate",
                        Sdf.ValueTypeNames.Double3,
                        Gf.Vec3d(*point),
                    )
                    packet_scale = 0.72 + 0.32 * math.sin(
                        math.pi * max(0.0, min(1.0, progress))
                    )
                    self._set_xform_op_attr_value(
                        prim,
                        "xformOp:scale",
                        Sdf.ValueTypeNames.Float3,
                        Gf.Vec3f(packet_scale, packet_scale, packet_scale),
                    )

                for name, point, phase in (
                    ("AntennaBeacon", start, 0.0),
                    ("RtgBeacon", end, math.pi),
                ):
                    prim = stage.GetPrimAtPath(f"{AUTOMATIC_SIGNAL_ROOT_PATH}/{name}")
                    self._set_xform_op_attr_value(
                        prim,
                        "xformOp:translate",
                        Sdf.ValueTypeNames.Double3,
                        Gf.Vec3d(*point),
                    )
                    scale = 0.82 + 0.22 * math.sin(now * 4.0 + phase)
                    self._set_xform_op_attr_value(
                        prim,
                        "xformOp:scale",
                        Sdf.ValueTypeNames.Float3,
                        Gf.Vec3f(scale, scale, scale),
                    )

                for index in range(3):
                    prim = stage.GetPrimAtPath(
                        f"{AUTOMATIC_SIGNAL_ROOT_PATH}/RtgTargetRing_{index + 1:02d}"
                    )
                    try:
                        prim_valid = bool(prim and prim.IsValid())
                    except Exception:
                        prim_valid = False
                    if not prim_valid:
                        # A stale session layer may have removed a ring while
                        # the live update callback still holds the old path.
                        # Rebuild the compatible BasisCurves reticle once and
                        # skip this frame if Kit still cannot author it.
                        self._ensure_automatic_signal_geometry(stage)
                        prim = stage.GetPrimAtPath(
                            f"{AUTOMATIC_SIGNAL_ROOT_PATH}/RtgTargetRing_{index + 1:02d}"
                        )
                        try:
                            prim_valid = bool(prim and prim.IsValid())
                        except Exception:
                            prim_valid = False
                    if not prim_valid:
                        continue
                    self._set_xform_op_attr_value(
                        prim,
                        "xformOp:translate",
                        Sdf.ValueTypeNames.Double3,
                        Gf.Vec3d(end[0], end[1], end[2] + 0.05),
                    )
                    ring_phase = (now * 0.9 + index / 3.0) % 1.0
                    ring_scale = 0.7 + ring_phase * 1.15
                    self._set_xform_op_attr_value(
                        prim,
                        "xformOp:scale",
                        Sdf.ValueTypeNames.Float3,
                        Gf.Vec3f(ring_scale, ring_scale, 1.0),
                    )

                root = stage.GetPrimAtPath(AUTOMATIC_SIGNAL_ROOT_PATH)
                self._set_attr(
                    root,
                    "rtg:workMode",
                    Sdf.ValueTypeNames.Int,
                    self._automatic_signal_work_mode,
                )
                self._set_automatic_signal_visible(stage, True)
            self._automatic_signal_last_update_at = now
        except Exception as exc:  # noqa: BLE001 - visual effect must not stop live control
            error_time = time.time()
            if error_time - self._last_error_at > 1.0:
                self._last_error_at = error_time
                self._log(f"remote-mode signal link update failed: {exc}")

    def _automatic_signal_endpoints(
        self,
        stage: Any,
    ) -> Optional[
        tuple[
            tuple[float, float, float],
            tuple[float, float, float],
        ]
    ]:
        """Resolve a fixed antenna tip and a gantry-local moving RTG roof point."""

        from pxr import Gf, Usd, UsdGeom

        antenna = stage.GetPrimAtPath(AUTOMATIC_SIGNAL_ANTENNA_PATH)
        rtg_attach = stage.GetPrimAtPath(AUTOMATIC_SIGNAL_RTG_ATTACH_PATH)
        if not antenna or not rtg_attach:
            return None

        if self._automatic_signal_antenna_world is None:
            antenna_top = self._automatic_signal_world_top(antenna, extra_z=0.35)
            if antenna_top is None:
                return None
            self._automatic_signal_antenna_world = antenna_top

        xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
        rtg_world = xform_cache.GetLocalToWorldTransform(rtg_attach)
        rtg_target = rtg_world.Transform(
            Gf.Vec3d(*AUTOMATIC_SIGNAL_RTG_TARGET_LOCAL_POINT)
        )
        return (
            self._automatic_signal_antenna_world,
            tuple(float(rtg_target[index]) for index in range(3)),
        )

    @staticmethod
    def _automatic_signal_world_top(
        prim: Any,
        *,
        extra_z: float,
    ) -> Optional[tuple[float, float, float]]:
        """Return the top-centre of a prim's rendered world bound."""

        from pxr import Gf, Usd, UsdGeom

        try:
            cache = UsdGeom.BBoxCache(
                Usd.TimeCode.Default(),
                [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
            )
            world_range = cache.ComputeWorldBound(prim).ComputeAlignedRange()
            if not world_range.IsEmpty():
                minimum = world_range.GetMin()
                maximum = world_range.GetMax()
                return (
                    float((minimum[0] + maximum[0]) * 0.5),
                    float((minimum[1] + maximum[1]) * 0.5),
                    float(maximum[2] + extra_z),
                )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass

        try:
            matrix = UsdGeom.XformCache(Usd.TimeCode.Default()).GetLocalToWorldTransform(prim)
            origin = matrix.Transform(Gf.Vec3d(0.0, 0.0, 0.0))
            return (float(origin[0]), float(origin[1]), float(origin[2] + extra_z))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return None

    def _ensure_automatic_signal_geometry(self, stage: Any) -> None:
        """Create the layered beam, packets, beacons, and target reticle once."""

        from pxr import Gf, Sdf, UsdGeom, UsdShade

        existing = stage.GetPrimAtPath(
            f"{AUTOMATIC_SIGNAL_PULSE_ROOT_PATH}/Packet_00"
        )
        required_paths = (
            AUTOMATIC_SIGNAL_CORE_CURVE_PATH,
            *(f"{AUTOMATIC_SIGNAL_PULSE_ROOT_PATH}/Packet_{index:02d}" for index in range(AUTOMATIC_SIGNAL_PULSE_COUNT)),
            *(f"{AUTOMATIC_SIGNAL_ROOT_PATH}/{name}" for name in ("AntennaBeacon", "RtgBeacon")),
        )
        ring_paths = tuple(
            f"{AUTOMATIC_SIGNAL_ROOT_PATH}/RtgTargetRing_{index + 1:02d}"
            for index in range(3)
        )
        rings_ready = all(
            stage.GetPrimAtPath(path)
            and stage.GetPrimAtPath(path).GetTypeName() == "BasisCurves"
            for path in ring_paths
        )
        if existing and all(stage.GetPrimAtPath(path) for path in required_paths) and rings_ready:
            return

        root = UsdGeom.Xform.Define(stage, AUTOMATIC_SIGNAL_ROOT_PATH).GetPrim()
        self._set_attr(
            root,
            "rtg:effect",
            Sdf.ValueTypeNames.String,
            "RCMS remote-mode antenna communication link",
        )

        def material(
            name: str,
            color: tuple[float, float, float],
            emissive: tuple[float, float, float],
            opacity: float,
        ) -> Any:
            path = f"{AUTOMATIC_SIGNAL_MATERIAL_ROOT_PATH}/{name}"
            result = UsdShade.Material.Define(stage, path)
            shader = UsdShade.Shader.Define(stage, f"{path}/PreviewSurface")
            shader.CreateIdAttr("UsdPreviewSurface")
            shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
                Gf.Vec3f(*color)
            )
            shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(
                Gf.Vec3f(*emissive)
            )
            shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(opacity)
            shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.12)
            shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.18)
            result.CreateSurfaceOutput().ConnectToSource(
                shader.ConnectableAPI(), "surface"
            )
            return result

        glow_material = material(
            "OuterGlow",
            (0.0, 0.20, 0.36),
            (0.0, 3.5, 8.0),
            0.24,
        )
        core_material = material(
            "CoreCyan",
            (0.10, 0.92, 1.0),
            (2.0, 18.0, 28.0),
            0.96,
        )
        pulse_material = material(
            "DataPacket",
            (0.72, 1.0, 1.0),
            (12.0, 35.0, 42.0),
            1.0,
        )

        def curve(path: str, width: float, bound_material: Any) -> None:
            value = UsdGeom.BasisCurves.Define(stage, path)
            value.CreateTypeAttr(UsdGeom.Tokens.linear)
            value.CreateWrapAttr(UsdGeom.Tokens.nonperiodic)
            value.CreateCurveVertexCountsAttr([AUTOMATIC_SIGNAL_POINT_COUNT])
            value.CreatePointsAttr(
                [Gf.Vec3f(0.0, 0.0, 0.0)] * AUTOMATIC_SIGNAL_POINT_COUNT
            )
            value.CreateWidthsAttr([width])
            value.SetWidthsInterpolation(UsdGeom.Tokens.constant)
            UsdShade.MaterialBindingAPI.Apply(value.GetPrim()).Bind(bound_material)

        curve(AUTOMATIC_SIGNAL_OUTER_CURVE_PATH, 0.42, glow_material)
        curve(AUTOMATIC_SIGNAL_CORE_CURVE_PATH, 0.09, core_material)
        curve(AUTOMATIC_SIGNAL_LEFT_CURVE_PATH, 0.035, core_material)
        curve(AUTOMATIC_SIGNAL_RIGHT_CURVE_PATH, 0.035, core_material)

        UsdGeom.Xform.Define(stage, AUTOMATIC_SIGNAL_PULSE_ROOT_PATH)
        for index in range(AUTOMATIC_SIGNAL_PULSE_COUNT):
            sphere = UsdGeom.Sphere.Define(
                stage,
                f"{AUTOMATIC_SIGNAL_PULSE_ROOT_PATH}/Packet_{index:02d}",
            )
            sphere.CreateRadiusAttr(0.32)
            UsdShade.MaterialBindingAPI.Apply(sphere.GetPrim()).Bind(pulse_material)

        for name in ("AntennaBeacon", "RtgBeacon"):
            sphere = UsdGeom.Sphere.Define(
                stage, f"{AUTOMATIC_SIGNAL_ROOT_PATH}/{name}"
            )
            sphere.CreateRadiusAttr(0.52)
            UsdShade.MaterialBindingAPI.Apply(sphere.GetPrim()).Bind(pulse_material)

        # USD's Python bindings in Kit do not expose UsdGeom.Torus.  Build
        # the target reticle as periodic BasisCurves instead; the ring paths
        # remain Xformable, so the update loop can keep translating them to
        # the moving RTG endpoint exactly as before.
        ring_point_count = 48
        for index in range(3):
            ring_path = f"{AUTOMATIC_SIGNAL_ROOT_PATH}/RtgTargetRing_{index + 1:02d}"
            existing_ring = stage.GetPrimAtPath(ring_path)
            if existing_ring and existing_ring.GetTypeName() not in ("", "BasisCurves"):
                stage.RemovePrim(ring_path)
            ring = UsdGeom.BasisCurves.Define(stage, ring_path)
            radius = 0.72 + index * 0.18
            ring.CreateTypeAttr(UsdGeom.Tokens.linear)
            ring.CreateWrapAttr(UsdGeom.Tokens.periodic)
            ring.CreateCurveVertexCountsAttr([ring_point_count])
            ring.CreatePointsAttr(
                [
                    Gf.Vec3f(
                        math.cos(math.tau * point / ring_point_count) * radius,
                        math.sin(math.tau * point / ring_point_count) * radius,
                        0.0,
                    )
                    for point in range(ring_point_count)
                ]
            )
            ring.CreateWidthsAttr([0.045])
            ring.SetWidthsInterpolation(UsdGeom.Tokens.constant)
            UsdShade.MaterialBindingAPI.Apply(ring.GetPrim()).Bind(core_material)

    def _set_automatic_signal_visible(
        self,
        stage: Any,
        visible: bool,
        *,
        force: bool = False,
    ) -> None:
        """Toggle the whole link without deleting its cached USD geometry."""

        root = stage.GetPrimAtPath(AUTOMATIC_SIGNAL_ROOT_PATH)
        if not root:
            self._automatic_signal_visible = False
            return
        if (
            not force
            and
            self._automatic_signal_visible is not None
            and self._automatic_signal_visible == bool(visible)
        ):
            return
        from pxr import Usd, UsdGeom

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            imageable = UsdGeom.Imageable(root)
            if visible:
                imageable.MakeVisible()
            else:
                imageable.MakeInvisible()
        self._automatic_signal_visible = bool(visible)

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

    def _ensure_4c_feeder_ship_replacements(self, stage: Any) -> None:
        stage_id = id(stage)
        if self._feeder_ship_replacement_stage_id == stage_id:
            return
        try:
            _old_found, hidden, mounted = ensure_4c_feeder_ship_replacements(stage)
            if hidden or mounted:
                self._log(
                    f"4C feeder ships: hidden_old={hidden} mounted_new={mounted}"
                )
            # Ship replacement is a one-time stage setup. Repeating the USD
            # traversal and transform writes on every PLC frame stalls RTX.
            self._feeder_ship_replacement_stage_id = stage_id
        except Exception as exc:  # noqa: BLE001 - ship visuals must not stop live control
            self._log(f"unable to replace 4C feeder ships: {exc}")

    def apply_state(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        stage = self._current_stage()
        if stage is None:
            return None

        # The world-space 3D route guide was removed for performance. Clean
        # up an indicator authored by an older hot-loaded driver exactly once
        # per stage; the task-panel route arrow is rendered independently.
        self._remove_task_route_indicator_once(stage)

        # HUD messages are independent from inventory/model synchronization.
        # Render them first so a malformed bay or asset cannot suppress the
        # operator-facing RunInfo and task panels.
        raw_task = dict(state.get("task") or {})
        raw_runinfo = state.get("runinfo")
        self._update_validated_truck_runinfo_cache(raw_runinfo, raw_task)
        try:
            self._sync_floating_runinfo_billboard(
                stage,
                self._runinfo_payload_for_task(raw_runinfo, raw_task),
                visible=_runinfo_should_be_visible(raw_task, raw_runinfo),
            )
        except Exception as exc:  # noqa: BLE001 - physical state can still apply
            self._log(f"runinfo panel update failed: {exc}")
        try:
            self._sync_floating_task_panel(stage, raw_task)
        except Exception as exc:  # noqa: BLE001 - physical state can still apply
            self._log(f"task panel update failed: {exc}")

        self._disable_demo_artifacts_for_stage(stage)
        self._ensure_4c_feeder_ship_replacements(stage)
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
            if _is_container_work_task(task):
                if self._settle_inventory_task(stage, task):
                    self._clear_completed_task_visuals(stage, task)
            elif task:
                # BAYCHANGE and other equipment-only tasks do not own a
                # container transaction, but must still release stale visuals.
                self._discard_task_visuals(stage, remove_stack=True)
        else:
            self._cleared_completed_task_key = None
            # Create the physical stack before positioning the RTG so the
            # rendered container centre is available as the final reference.
            if self._sync_task_stack(stage, task):
                self._hide_inventory_bay_for_task(stage, task)

        if (
            field
            and bay > 0
            and _is_supported_yard_bay(field, bay)
            and not task_is_gantry_traveling
        ):
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
                and _is_container_work_task(task)
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
                is_container_task = _is_container_work_task(task)
                if (
                    is_container_task
                    and self._inventory_mode == INVENTORY_MODE_LIVE
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

    def _remove_task_route_indicator(self, stage: Any) -> None:
        from pxr import Usd

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            root = stage.GetPrimAtPath(TASK_ROUTE_INDICATOR_ROOT_PATH)
            if root:
                stage.RemovePrim(root.GetPath())

    def _remove_task_route_indicator_once(self, stage: Any) -> None:
        stage_id = id(stage)
        if self._task_route_cleanup_stage_id == stage_id:
            return
        self._remove_task_route_indicator(stage)
        self._task_route_cleanup_stage_id = stage_id

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
        now = time.time()
        transitions = []
        transition = machine.update(plc, now)
        if transition is not None:
            transitions.append(transition)
            if (
                transition.current is OperationState.GANTRY_TO_SOURCE
                and machine.uses_truck
                and machine._at_slot(plc, machine.task.source)
            ):
                immediate = machine.update(plc, now)
                if immediate is not None:
                    transitions.append(immediate)
        self._set_operation_state_attr(stage, machine.state.value)
        for transition in transitions:
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

            imported_visual_created = self._create_imported_task_truck_visual(
                stage,
                cab_center_y=cab_center_y,
            )
            root.CreateAttribute(
                "live:truckVisualSource", Sdf.ValueTypeNames.Token
            ).Set(
                "rtg_model_lane_truck_usdc"
                if imported_visual_created
                else "procedural_fallback"
            )
            if not imported_visual_created:
                steel = material(
                    stage,
                    "TaskTruckSteel",
                    Gf.Vec3f(0.075, 0.070, 0.065),
                )
                deck = material(
                    stage,
                    "TaskTruckDeck",
                    Gf.Vec3f(0.30, 0.24, 0.16),
                )
                cab_white = material(
                    stage,
                    "TaskTruckCabWhite",
                    Gf.Vec3f(0.90, 0.88, 0.80),
                )
                glass = material(
                    stage,
                    "TaskTruckGlass",
                    Gf.Vec3f(0.02, 0.04, 0.055),
                )
                black = material(
                    stage,
                    "TaskTruckBlack",
                    Gf.Vec3f(0.015, 0.015, 0.013),
                )
                light = material(
                    stage,
                    "TaskTruckHeadlight",
                    Gf.Vec3f(1.0, 0.88, 0.42),
                )
                self._create_white_cab_task_truck_visual(
                    stage,
                    trailer_length_y=trailer_length_y,
                    trailer_center_y=trailer_center_y,
                    trailer_center_z=trailer_center_z,
                    cab_center_y=cab_center_y,
                    steel=steel,
                    deck=deck,
                    cab_white=cab_white,
                    glass=glass,
                    black=black,
                    light=light,
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
                truck_box_material,
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
            bound_material,
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
            row_center_x_m,
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

        x = row_center_x_m(row, 6)
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
        create_container_details(stage, target_path, center, scale, bound_material)
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
    def _create_imported_task_truck_visual(
        stage: Any,
        *,
        cab_center_y: float,
    ) -> bool:
        """Reference the compact lane truck exported from the Blender scene."""

        from pxr import Gf, Sdf, UsdGeom

        asset_path = LIVE_TASK_TRUCK_VISUAL_ASSET_PATH
        if not asset_path.is_file():
            return False

        visual = UsdGeom.Xform.Define(stage, LIVE_TASK_TRUCK_VISUAL_PATH).GetPrim()
        visual.GetReferences().AddReference(asset_path.as_posix())
        visual.SetInstanceable(False)
        visual.CreateAttribute("live:role", Sdf.ValueTypeNames.Token).Set(
            "task_truck_visual"
        )
        visual.CreateAttribute("live:asset", Sdf.ValueTypeNames.Asset).Set(
            Sdf.AssetPath(asset_path.as_posix())
        )

        xform = UsdGeom.Xformable(visual)
        translate_op = OmniverseRtgTwinDriver._get_or_add_xform_op(
            xform,
            "xformOp:translate",
            xform.AddTranslateOp,
        )
        translate_op.Set(
            Gf.Vec3d(
                0.0,
                _task_truck_imported_visual_translate_y(cab_center_y),
                -LIVE_TASK_TRUCK_ASSET_GROUND_Z_M,
            )
        )
        scale_op = OmniverseRtgTwinDriver._get_or_add_xform_op(
            xform,
            "xformOp:scale",
            xform.AddScaleOp,
        )
        scale_op.Set(Gf.Vec3f(*LIVE_TASK_TRUCK_VISUAL_SCALE))
        xform.SetXformOpOrder([translate_op, scale_op])
        return True

    @staticmethod
    def _create_white_cab_task_truck_visual(
        stage: Any,
        *,
        trailer_length_y: float,
        trailer_center_y: float,
        trailer_center_z: float,
        cab_center_y: float,
        steel: Any,
        deck: Any,
        cab_white: Any,
        glass: Any,
        black: Any,
        light: Any,
    ) -> None:
        from pxr import Gf

        def cube(name: str, center: Any, scale: Any, mat: Any) -> None:
            OmniverseRtgTwinDriver._create_task_material_cube(
                stage,
                f"{LIVE_TASK_TRUCK_PATH}/{name}",
                center,
                scale,
                mat,
            )

        wheel_x = min(TASK_TRUCK_TRAILER_WHEEL_X_M, 0.88)
        trailer_width_x = min(TASK_TRUCK_TRAILER_WIDTH_X_M, 1.28)
        rail_x = trailer_width_x * 0.5 - 0.07
        frame_z = trailer_center_z - 0.02
        deck_z = trailer_center_z + 0.07

        cube(
            "TrailerMainFrame",
            Gf.Vec3d(0.0, trailer_center_y, frame_z),
            Gf.Vec3f(0.72, trailer_length_y, 0.12),
            steel,
        )
        cube(
            "TrailerDeck",
            Gf.Vec3d(0.0, trailer_center_y, deck_z),
            Gf.Vec3f(1.18, trailer_length_y, 0.04),
            deck,
        )
        cube(
            "TrailerLeftRail",
            Gf.Vec3d(-rail_x, trailer_center_y, deck_z + 0.03),
            Gf.Vec3f(0.07, trailer_length_y, 0.08),
            steel,
        )
        cube(
            "TrailerRightRail",
            Gf.Vec3d(rail_x, trailer_center_y, deck_z + 0.03),
            Gf.Vec3f(0.07, trailer_length_y, 0.08),
            steel,
        )
        for index, offset in enumerate((-0.46, -0.23, 0.0, 0.23, 0.46), start=1):
            cube(
                f"TrailerCrossbar_{index:02d}",
                Gf.Vec3d(
                    0.0,
                    trailer_center_y + trailer_length_y * offset,
                    deck_z,
                ),
                Gf.Vec3f(1.22, 0.08, 0.08),
                steel,
            )

        cube(
            "CabBody",
            Gf.Vec3d(0.0, cab_center_y, 0.70),
            Gf.Vec3f(1.02, 1.00, 0.72),
            cab_white,
        )
        cube(
            "CabWindshield",
            Gf.Vec3d(0.0, cab_center_y - 0.51, 0.88),
            Gf.Vec3f(0.78, 0.035, 0.30),
            glass,
        )
        cube(
            "CabSideWindowLeft",
            Gf.Vec3d(-0.53, cab_center_y - 0.12, 0.88),
            Gf.Vec3f(0.035, 0.42, 0.24),
            glass,
        )
        cube(
            "CabSideWindowRight",
            Gf.Vec3d(0.53, cab_center_y - 0.12, 0.88),
            Gf.Vec3f(0.035, 0.42, 0.24),
            glass,
        )
        cube(
            "CabBumper",
            Gf.Vec3d(0.0, cab_center_y - 0.55, 0.38),
            Gf.Vec3f(1.05, 0.07, 0.12),
            black,
        )
        for side_name, x in (("Left", -0.34), ("Right", 0.34)):
            cube(
                f"Headlight{side_name}",
                Gf.Vec3d(x, cab_center_y - 0.60, 0.51),
                Gf.Vec3f(0.14, 0.025, 0.08),
                light,
            )

        wheel_y_positions = (
            cab_center_y - 0.07,
            trailer_center_y - trailer_length_y * 0.34,
            trailer_center_y + trailer_length_y * 0.26,
            trailer_center_y + trailer_length_y * 0.42,
        )
        for axle_index, wheel_y in enumerate(wheel_y_positions, start=1):
            for side_name, x in (("L", -wheel_x), ("R", wheel_x)):
                cube(
                    f"Wheel_{axle_index}_{side_name}",
                    Gf.Vec3d(x, wheel_y, 0.36),
                    Gf.Vec3f(0.18, 0.44, 0.44),
                    black,
                )

    @staticmethod
    def _create_task_material_cube(
        stage: Any,
        path: str,
        center: Any,
        scale: Any,
        mat: Any,
    ) -> None:
        from pxr import UsdGeom, UsdShade

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
        UsdShade.MaterialBindingAPI.Apply(cube.GetPrim()).Bind(mat)

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
    def _set_xform_op_attr_value(
        prim: Any,
        op_name: str,
        type_name: Any,
        value: Any,
    ) -> Any:
        """Set a transform attribute without relying on AddXformOp."""

        attr = prim.GetAttribute(op_name)
        try:
            attr_valid = bool(attr and attr.IsValid())
        except Exception:
            attr_valid = bool(attr)
        if not attr_valid:
            attr = prim.CreateAttribute(op_name, type_name)
        try:
            if attr.Get() != value:
                attr.Set(value)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            attr.Set(value)

        order_attr = prim.GetAttribute("xformOpOrder")
        try:
            order_valid = bool(order_attr and order_attr.IsValid())
        except Exception:
            order_valid = bool(order_attr)
        if not order_valid:
            from pxr import Sdf

            order_attr = prim.CreateAttribute(
                "xformOpOrder",
                Sdf.ValueTypeNames.TokenArray,
            )
        order = list(order_attr.Get() or [])
        if op_name not in order:
            order_attr.Set([*order, op_name])
        return attr

    @staticmethod
    def _set_xform_translate(prim: Any, value: Any) -> None:
        from pxr import Sdf

        OmniverseRtgTwinDriver._set_xform_op_attr_value(
            prim,
            "xformOp:translate",
            Sdf.ValueTypeNames.Double3,
            value,
        )

    @staticmethod
    def _set_xform_rotate_xyz(prim: Any, value: Any) -> None:
        from pxr import Sdf

        OmniverseRtgTwinDriver._set_xform_op_attr_value(
            prim,
            "xformOp:rotateXYZ",
            Sdf.ValueTypeNames.Float3,
            value,
        )

    @staticmethod
    def _normalize_panel_xform_op_order(prim: Any) -> None:
        """Keep a billboard anchored while rotating around its own center."""

        try:
            order_attr = prim.GetAttribute("xformOpOrder")
            ordered_names = list(order_attr.Get() or [])
        except Exception:
            return
        translate_name = "xformOp:translate"
        rotate_name = "xformOp:rotateXYZ"
        if translate_name not in ordered_names or rotate_name not in ordered_names:
            return
        extras = [
            name
            for name in ordered_names
            if name not in (translate_name, rotate_name)
        ]
        order_attr.Set([translate_name, rotate_name, *extras])

    @staticmethod
    def _get_xform_translate(prim: Any) -> Any:
        """Return the authored translate value without creating a new xform op."""

        if not prim:
            return None
        try:
            attr = prim.GetAttribute("xformOp:translate")
            return attr.Get() if attr and attr.IsValid() else None
        except Exception:
            return None

    def _get_xform_world_translate(self, stage: Any, prim: Any) -> Any:
        """Return an info panel origin in world space for camera-facing math."""

        if not prim:
            return None
        try:
            from pxr import Gf, Usd, UsdGeom

            matrix = UsdGeom.XformCache(Usd.TimeCode.Default()).GetLocalToWorldTransform(
                prim
            )
            for name in ("ExtractTranslation", "GetTranslation"):
                method = getattr(matrix, name, None)
                if callable(method):
                    return method()
            return matrix.Transform(Gf.Vec3d(0.0, 0.0, 0.0))
        except Exception:
            return self._get_xform_translate(prim)

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
        from pxr import Sdf

        OmniverseRtgTwinDriver._set_xform_op_attr_value(
            prim,
            "xformOp:scale",
            Sdf.ValueTypeNames.Float3,
            value,
        )

    @staticmethod
    def _get_or_add_xform_op(
        xform: Any,
        op_name: str,
        add_op: Any,
    ) -> Any:
        return _billboard_utils.get_or_add_xform_op(xform, op_name, add_op)

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
                    mat,
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
        visible: bool = True,
    ) -> None:
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

        if not RUNINFO_FLOATING_ENABLED or not visible:
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                legacy = stage.GetPrimAtPath(LEGACY_RUNINFO_FLOATING_ROOT_PATH)
                if legacy:
                    stage.RemovePrim(legacy.GetPath())
                current = stage.GetPrimAtPath(RUNINFO_FLOATING_ROOT_PATH)
                if current:
                    if RUNINFO_FLOATING_ENABLED:
                        UsdGeom.Imageable(current).MakeInvisible()
                    else:
                        stage.RemovePrim(current.GetPath())
            self._floating_runinfo_text = None
            self._floating_runinfo_clock_text = None
            self._floating_runinfo_level_key = None
            return

        text = (
            str(runinfo.get("message") or "").strip()
            if isinstance(runinfo, dict)
            else ""
        ) or RUNINFO_FLOATING_DEFAULT_TEXT

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
            shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
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
        self._normalize_panel_xform_op_order(root_prim)

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

    def _device_panel_bay_center_y(
        self,
        stage: Any,
        task: Dict[str, Any],
    ) -> float:
        field = _field_name(_read_status_value(stage, "rtg:currentField")) or "4C"
        bay_number = _as_int(_read_status_value(stage, "rtg:currentBay"), 0)
        if not _is_supported_yard_bay(field, bay_number):
            field = ""
        stack_key = self._task_stack_key(task) if task else None
        try:
            from build_live_containers import container_center_y_for_bay

            if field:
                size_ft = 40 if bay_number % 2 == 0 else 20
                return float(container_center_y_for_bay(field, bay_number, size_ft))
        except Exception:
            pass

        if stack_key is not None:
            block, bay_number, _pattern, container_size_ft = stack_key
            try:
                return float(
                    container_center_y_for_bay(
                        block,
                        bay_number,
                        container_size_ft,
                    )
                )
            except Exception:
                pass
        return 0.0

    def _subscribe_device_panel_selection(self) -> None:
        """Track RTG selection changes without rebuilding the panel per frame."""

        try:
            import omni.usd  # type: ignore

            stream = omni.usd.get_context().get_selection().get_changed_event_stream()
            self._device_panel_selection_sub = stream.create_subscription_to_pop(
                self._on_device_panel_selection_changed,
                name="SmartPortRtgDevicePanelSelection",
            )
        except Exception as exc:  # noqa: BLE001 - selection is optional in tests
            self._device_panel_selection_sub = None
            self._log(f"device panel selection subscription unavailable: {exc}")

    @staticmethod
    def _viewport_selection_paths() -> list[str]:
        try:
            import omni.usd  # type: ignore

            return [
                str(path)
                for path in (
                omni.usd.get_context()
                .get_selection()
                .get_selected_prim_paths()
                )
            ]
        except Exception:
            return []

    def _refresh_device_panel_selection(self, source: str) -> bool:
        if source == "poll":
            now = time.monotonic()
            last_poll = getattr(self, "_last_device_selection_poll_at", 0.0)
            if now - last_poll < DEVICE_PANEL_SELECTION_POLL_INTERVAL_SECONDS:
                return False
            self._last_device_selection_poll_at = now
        paths = self._viewport_selection_paths()
        signature = tuple(paths)
        if signature == self._device_panel_last_selection_signature:
            return False
        del source
        self._device_panel_last_selection_signature = signature
        if any(_is_rtg_camera_marker_selection_path(path) for path in paths):
            # Camera markers are handled by rtg_camera_popup. Do not let the
            # generic RTG device-location panel consume the same click.
            self._device_panel_selected = False
            self._container_info_selected_root_path = None
            self._truck_info_selected_root_path = None
            return True
        matched_rtg = any(
            _is_descendant_path(path, PRIMARY_RTG_SELECTION_ROOT_PATH)
            for path in paths
        )
        container_root = next(
            (
                root
                for root in (_container_root_path_from_selection(path) for path in paths)
                if root is not None
            ),
            None,
        )
        truck_root = None
        if container_root is None:
            truck_root = next(
                (
                    root
                    for root in (_truck_root_path_from_selection(path) for path in paths)
                    if root is not None
                ),
                None,
            )
        # Selecting a box opens its own info panel but must not close the
        # device panel that was opened by the preceding RTG selection.
        if (not container_root and not truck_root) or not self._device_panel_selected:
            self._device_panel_selected = matched_rtg
        self._container_info_selected_root_path = container_root
        self._truck_info_selected_root_path = truck_root
        return True

    def _on_device_panel_selection_changed(self, *_args: Any) -> None:
        """Show or hide immediately when the viewport selection changes."""

        if not self._refresh_device_panel_selection("event"):
            return

        stage = self._current_stage()
        if stage is None:
            self._log("device panel selection changed but stage is unavailable")
            return
        if self._container_info_selected_root_path:
            self._show_container_info_panel(
                stage,
                self._container_info_selected_root_path,
            )
        elif self._container_info_panel_visible:
            self._hide_container_info_panel(stage)
        if self._truck_info_selected_root_path:
            self._show_truck_info_panel(
                stage,
                self._truck_info_selected_root_path,
            )
        elif self._truck_info_panel_visible:
            self._hide_truck_info_panel(stage)
        if not self._device_panel_selected:
            if self._device_panel_visible:
                self._hide_floating_device_panel(stage)
                self._device_panel_visible = False
            return

        with self._latest_lock:
            state = dict(self._latest_state or {})
        self._sync_floating_device_panel(stage, dict(state.get("task") or {}))

    @staticmethod
    def _hide_container_info_panel(stage: Any) -> None:
        try:
            from pxr import Usd, UsdGeom

            with Usd.EditContext(stage, stage.GetSessionLayer()):
                root = stage.GetPrimAtPath(CONTAINER_INFO_PANEL_ROOT_PATH)
                if root:
                    UsdGeom.Imageable(root).MakeInvisible()
        except Exception:
            return

    @staticmethod
    def _container_info_panel_world_translate(stage: Any, root_path: str) -> Any:
        from pxr import Gf, Usd, UsdGeom

        selected = stage.GetPrimAtPath(root_path)
        if not selected:
            return Gf.Vec3d(
                CONTAINER_INFO_PANEL_WORLD_X,
                0.0,
                CONTAINER_INFO_PANEL_WORLD_Z,
            )
        try:
            cache = UsdGeom.BBoxCache(
                Usd.TimeCode.Default(),
                [UsdGeom.Tokens.default_],
            )
            bounds = cache.ComputeWorldBound(selected).ComputeAlignedBox()
            minimum = bounds.GetMin()
            maximum = bounds.GetMax()
            origin_y = (float(minimum[1]) + float(maximum[1])) * 0.5
            return Gf.Vec3d(
                CONTAINER_INFO_PANEL_WORLD_X,
                origin_y + CONTAINER_INFO_PANEL_Y_OFFSET_M,
                CONTAINER_INFO_PANEL_WORLD_Z,
            )
        except Exception:
            return Gf.Vec3d(
                CONTAINER_INFO_PANEL_WORLD_X,
                0.0,
                CONTAINER_INFO_PANEL_WORLD_Z,
            )

    def _sync_container_info_panel_pose(self, stage: Any, root: Any, root_path: str) -> None:
        from pxr import Gf

        translate = self._sync_container_info_panel_position(stage, root, root_path)
        rotate = camera_facing_rotate_xyz(
            stage,
            translate,
            CONTAINER_INFO_PANEL_ROTATE_XYZ,
        )
        self._set_xform_rotate_xyz(root, Gf.Vec3f(*rotate))
        self._normalize_panel_xform_op_order(root)

    def _sync_container_info_panel_position(
        self,
        stage: Any,
        root: Any,
        root_path: str,
    ) -> Any:
        translate = self._container_info_panel_world_translate(stage, root_path)
        position_key = (
            root_path,
            round(float(translate[0]), 4),
            round(float(translate[1]), 4),
            round(float(translate[2]), 4),
        )
        if position_key != self._container_info_panel_position_key:
            self._set_xform_translate(root, translate)
            self._container_info_panel_position_key = position_key
        return translate

    def _show_container_info_panel(self, stage: Any, root_path: str) -> None:
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

        rows = _collect_container_info_rows(stage, root_path)
        panel_key = (root_path, tuple((row.label, row.value) for row in rows))
        mesh = stage.GetPrimAtPath(CONTAINER_INFO_PANEL_MESH_PATH)
        if (
            mesh
            and panel_key == self._container_info_panel_key
            and stage.GetPrimAtPath(CONTAINER_INFO_PANEL_ROOT_PATH)
        ):
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                root = stage.GetPrimAtPath(CONTAINER_INFO_PANEL_ROOT_PATH)
                UsdGeom.Imageable(root).MakeVisible()
                self._sync_container_info_panel_pose(stage, root, root_path)
            self._container_info_panel_visible = True
            return

        self._container_info_panel_bitmap_seq += 1
        texture_path = (
            Path(tempfile.gettempdir())
            / f"smart_port_rtg_container_info_{os.getpid()}_{self._container_info_panel_bitmap_seq}.png"
        )
        if not _render_container_info_panel_bitmap(rows, texture_path):
            self._log("container info panel bitmap render failed")
            return

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            root = UsdGeom.Xform.Define(stage, CONTAINER_INFO_PANEL_ROOT_PATH)
            root_prim = root.GetPrim()
            UsdGeom.Imageable(root_prim).MakeVisible()
            self._sync_container_info_panel_pose(stage, root_prim, root_path)

            panel = UsdGeom.Mesh.Define(stage, CONTAINER_INFO_PANEL_MESH_PATH)
            panel_prim = panel.GetPrim()
            half_w = CONTAINER_INFO_PANEL_WIDTH_M * 0.5
            half_h = CONTAINER_INFO_PANEL_HEIGHT_M * 0.5
            panel.CreatePointsAttr(
                [
                    Gf.Vec3f(half_w, 0.0, -half_h),
                    Gf.Vec3f(-half_w, 0.0, -half_h),
                    Gf.Vec3f(-half_w, 0.0, half_h),
                    Gf.Vec3f(half_w, 0.0, half_h),
                ]
            )
            panel.CreateFaceVertexCountsAttr([4])
            panel.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
            panel.CreateDoubleSidedAttr(True)
            panel.CreateExtentAttr(
                [Gf.Vec3f(-half_w, 0.0, -half_h), Gf.Vec3f(half_w, 0.0, half_h)]
            )
            st = UsdGeom.PrimvarsAPI(panel_prim).CreatePrimvar(
                "st",
                Sdf.ValueTypeNames.TexCoord2fArray,
                UsdGeom.Tokens.faceVarying,
            )
            st.Set([Gf.Vec2f(u, v) for u, v in TASK_PANEL_UV_COORDS])
            panel_prim.CreateAttribute(
                "rtg:containerInfoTexture",
                Sdf.ValueTypeNames.Asset,
            ).Set(Sdf.AssetPath(str(texture_path)))

            material = UsdShade.Material.Define(
                stage,
                CONTAINER_INFO_PANEL_MATERIAL_PATH,
            )
            shader = UsdShade.Shader.Define(
                stage,
                f"{CONTAINER_INFO_PANEL_MATERIAL_PATH}/PreviewSurface",
            )
            shader.CreateIdAttr("UsdPreviewSurface")
            shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.42)
            texture = UsdShade.Shader.Define(
                stage,
                f"{CONTAINER_INFO_PANEL_MATERIAL_PATH}/Texture",
            )
            texture.CreateIdAttr("UsdUVTexture")
            texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(
                Sdf.AssetPath(str(texture_path))
            )
            texture.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
            reader = UsdShade.Shader.Define(
                stage,
                f"{CONTAINER_INFO_PANEL_MATERIAL_PATH}/Primvar_st",
            )
            reader.CreateIdAttr("UsdPrimvarReader_float2")
            reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
            texture.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(
                reader.ConnectableAPI(), "result"
            )
            shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
                texture.ConnectableAPI(), "rgb"
            )
            shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
                texture.ConnectableAPI(), "rgb"
            )
            shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).ConnectToSource(
                texture.ConnectableAPI(), "a"
            )
            material.CreateSurfaceOutput().ConnectToSource(
                shader.ConnectableAPI(), "surface"
            )
            UsdShade.MaterialBindingAPI(panel_prim).Bind(material)

        self._container_info_panel_key = panel_key
        self._container_info_panel_visible = True
        self._log(
            "container info panel shown "
            f"root={root_path} path={CONTAINER_INFO_PANEL_ROOT_PATH}"
        )

    @staticmethod
    def _hide_truck_info_panel(stage: Any) -> None:
        try:
            from pxr import Usd, UsdGeom

            with Usd.EditContext(stage, stage.GetSessionLayer()):
                root = stage.GetPrimAtPath(TRUCK_INFO_PANEL_ROOT_PATH)
                if root:
                    UsdGeom.Imageable(root).MakeInvisible()
        except Exception:
            return

    @staticmethod
    def _truck_info_panel_world_translate(stage: Any, root_path: str) -> Any:
        from pxr import Gf, Usd, UsdGeom

        selected = stage.GetPrimAtPath(root_path)
        if not selected:
            return Gf.Vec3d(0.0, 0.0, TRUCK_INFO_PANEL_Z_OFFSET_M)
        try:
            cache = UsdGeom.BBoxCache(
                Usd.TimeCode.Default(),
                [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
            )
            bounds = cache.ComputeWorldBound(selected).ComputeAlignedBox()
            minimum = bounds.GetMin()
            maximum = bounds.GetMax()
            return Gf.Vec3d(
                (float(minimum[0]) + float(maximum[0])) * 0.5,
                (float(minimum[1]) + float(maximum[1])) * 0.5
                + TRUCK_INFO_PANEL_Y_OFFSET_M,
                float(maximum[2]) + TRUCK_INFO_PANEL_Z_OFFSET_M,
            )
        except Exception:
            return Gf.Vec3d(0.0, 0.0, TRUCK_INFO_PANEL_Z_OFFSET_M)

    def _sync_truck_info_panel_pose(self, stage: Any, root: Any, root_path: str) -> None:
        from pxr import Gf

        translate = self._sync_truck_info_panel_position(stage, root, root_path)
        rotate = camera_facing_rotate_xyz(
            stage,
            translate,
            TRUCK_INFO_PANEL_ROTATE_XYZ,
        )
        self._set_xform_rotate_xyz(root, Gf.Vec3f(*rotate))
        self._normalize_panel_xform_op_order(root)

    def _sync_truck_info_panel_position(
        self,
        stage: Any,
        root: Any,
        root_path: str,
    ) -> Any:
        translate = self._truck_info_panel_world_translate(stage, root_path)
        position_key = (
            root_path,
            round(float(translate[0]), 4),
            round(float(translate[1]), 4),
            round(float(translate[2]), 4),
        )
        if position_key != self._truck_info_panel_position_key:
            self._set_xform_translate(root, translate)
            self._truck_info_panel_position_key = position_key
        return translate

    def _show_truck_info_panel(self, stage: Any, root_path: str) -> None:
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

        info = self._current_truck_runinfo(stage)
        if info is None:
            self._hide_truck_info_panel(stage)
            self._truck_info_panel_visible = False
            self._log_truck_info_status(
                "hidden_no_valid_runinfo",
                "truck info panel hidden: selected truck but no validated truck runinfo",
            )
            return

        panel_key = (root_path, info.car_type, info.car_no)
        mesh = stage.GetPrimAtPath(TRUCK_INFO_PANEL_MESH_PATH)
        if (
            mesh
            and panel_key == self._truck_info_panel_key
            and stage.GetPrimAtPath(TRUCK_INFO_PANEL_ROOT_PATH)
        ):
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                root = stage.GetPrimAtPath(TRUCK_INFO_PANEL_ROOT_PATH)
                UsdGeom.Imageable(root).MakeVisible()
                self._sync_truck_info_panel_pose(stage, root, root_path)
            self._truck_info_panel_visible = True
            return

        self._truck_info_panel_bitmap_seq += 1
        texture_path = (
            Path(tempfile.gettempdir())
            / f"smart_port_rtg_truck_info_{os.getpid()}_{self._truck_info_panel_bitmap_seq}.png"
        )
        if not _render_truck_info_panel_bitmap(info, texture_path):
            self._log("truck info panel bitmap render failed")
            return

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            root = UsdGeom.Xform.Define(stage, TRUCK_INFO_PANEL_ROOT_PATH)
            root_prim = root.GetPrim()
            UsdGeom.Imageable(root_prim).MakeVisible()
            self._sync_truck_info_panel_pose(stage, root_prim, root_path)

            panel = UsdGeom.Mesh.Define(stage, TRUCK_INFO_PANEL_MESH_PATH)
            panel_prim = panel.GetPrim()
            half_w = TRUCK_INFO_PANEL_WIDTH_M * 0.5
            half_h = TRUCK_INFO_PANEL_HEIGHT_M * 0.5
            panel.CreatePointsAttr(
                [
                    Gf.Vec3f(half_w, 0.0, -half_h),
                    Gf.Vec3f(-half_w, 0.0, -half_h),
                    Gf.Vec3f(-half_w, 0.0, half_h),
                    Gf.Vec3f(half_w, 0.0, half_h),
                ]
            )
            panel.CreateFaceVertexCountsAttr([4])
            panel.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
            panel.CreateDoubleSidedAttr(True)
            panel.CreateExtentAttr(
                [Gf.Vec3f(-half_w, 0.0, -half_h), Gf.Vec3f(half_w, 0.0, half_h)]
            )
            st = UsdGeom.PrimvarsAPI(panel_prim).CreatePrimvar(
                "st",
                Sdf.ValueTypeNames.TexCoord2fArray,
                UsdGeom.Tokens.faceVarying,
            )
            st.Set([Gf.Vec2f(u, v) for u, v in TRUCK_INFO_PANEL_UV_COORDS])
            panel_prim.CreateAttribute(
                "rtg:truckInfoTexture",
                Sdf.ValueTypeNames.Asset,
            ).Set(Sdf.AssetPath(str(texture_path)))
            panel_prim.CreateAttribute(
                "rtg:truckType",
                Sdf.ValueTypeNames.String,
            ).Set(info.car_type)
            panel_prim.CreateAttribute(
                "rtg:truckNo",
                Sdf.ValueTypeNames.String,
            ).Set(info.car_no)

            material = UsdShade.Material.Define(stage, TRUCK_INFO_PANEL_MATERIAL_PATH)
            shader = UsdShade.Shader.Define(
                stage,
                f"{TRUCK_INFO_PANEL_MATERIAL_PATH}/PreviewSurface",
            )
            shader.CreateIdAttr("UsdPreviewSurface")
            shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.42)
            texture = UsdShade.Shader.Define(
                stage,
                f"{TRUCK_INFO_PANEL_MATERIAL_PATH}/Texture",
            )
            texture.CreateIdAttr("UsdUVTexture")
            texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(
                Sdf.AssetPath(str(texture_path))
            )
            texture.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
            reader = UsdShade.Shader.Define(
                stage,
                f"{TRUCK_INFO_PANEL_MATERIAL_PATH}/Primvar_st",
            )
            reader.CreateIdAttr("UsdPrimvarReader_float2")
            reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
            texture.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(
                reader.ConnectableAPI(), "result"
            )
            shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
                texture.ConnectableAPI(), "rgb"
            )
            shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
                texture.ConnectableAPI(), "rgb"
            )
            shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).ConnectToSource(
                texture.ConnectableAPI(), "a"
            )
            material.CreateSurfaceOutput().ConnectToSource(
                shader.ConnectableAPI(), "surface"
            )
            UsdShade.MaterialBindingAPI(panel_prim).Bind(material)

        self._truck_info_panel_key = panel_key
        self._truck_info_panel_visible = True
        self._log(
            "truck info panel shown "
            f"root={root_path} type={info.car_type} no={info.car_no}"
        )

    def _update_validated_truck_runinfo_cache(
        self,
        raw_runinfo: Any,
        task: Dict[str, Any],
    ) -> None:
        """Cache validated LOAD/UNLOAD truck data for the active task.

        RunInfo is a single latest-message stream, so a later guidance or
        arrival message must not replace the truck number that the operator
        expects to inspect.  The cache is scoped to taskId and is cleared at
        task boundaries or terminal task states.
        """

        task_id = str(task.get("taskId") or "").strip()
        cache_task_id = self._validated_truck_task_id
        task_ended = (
            not task_id
            or _task_is_complete(task)
            or _task_is_cancelled(task)
            or _runinfo_is_task_completion(raw_runinfo)
            or _runinfo_is_task_cancellation(raw_runinfo)
        )
        if task_ended or (
            cache_task_id is not None and cache_task_id != task_id
        ):
            self._validated_truck_runinfo = None
            self._validated_truck_task_id = None if task_ended else task_id

        if task_ended or _task_work_type(task) not in (
            WORK_TYPE_LOAD,
            WORK_TYPE_UNLOAD,
        ):
            return

        if self._validated_truck_task_id != task_id:
            self._validated_truck_runinfo = None
            self._validated_truck_task_id = task_id

        info = _truck_runinfo_from_payload(raw_runinfo)
        if info is not None:
            self._validated_truck_runinfo = info

    def _current_truck_runinfo(self, stage: Any) -> Optional[TruckRunInfo]:
        """Read validated truck info from cached, pending, or authored status runinfo."""

        if self._validated_truck_runinfo is not None:
            return self._validated_truck_runinfo

        candidates: list[Any] = [self._last_runinfo_payload]
        with self._latest_lock:
            latest = self._latest_state
            if isinstance(latest, dict):
                candidates.append(latest.get("runinfo"))
        status_runinfo = _read_status_value(stage, "rtg:runinfo", "")
        if status_runinfo:
            candidates.append({"message": status_runinfo})
        for candidate in candidates:
            info = _truck_runinfo_from_payload(candidate)
            if info is not None:
                return info
        return None

    def _log_truck_info_status(self, status: str, message: str) -> None:
        key = (status, message)
        if key == self._truck_info_panel_diag_key:
            return
        self._truck_info_panel_diag_key = key
        self._log(message)

    def _sync_device_location_values_overlay(
        self,
        stage: Any,
        values: dict[str, str],
    ) -> bool:
        """Update only the numeric layer while keeping the panel background stable."""

        from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

        texture_path = (
            Path(tempfile.gettempdir())
            / f"smart_port_rtg_device_location_values_{os.getpid()}.png"
        )
        if not _render_device_location_values_bitmap(values, texture_path):
            self._log("device location values overlay render failed")
            return False

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            panel = UsdGeom.Mesh.Define(
                stage,
                DEVICE_LOCATION_PANEL_VALUES_MESH_PATH,
            )
            panel_prim = panel.GetPrim()
            half_w = DEVICE_LOCATION_PANEL_WIDTH_M * 0.5
            half_h = DEVICE_LOCATION_PANEL_HEIGHT_M * 0.5
            offset = 0.02
            panel.CreatePointsAttr(
                [
                    Gf.Vec3f(half_w, offset, -half_h),
                    Gf.Vec3f(-half_w, offset, -half_h),
                    Gf.Vec3f(-half_w, offset, half_h),
                    Gf.Vec3f(half_w, offset, half_h),
                ]
            )
            panel.CreateFaceVertexCountsAttr([4])
            panel.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
            panel.CreateDoubleSidedAttr(True)
            panel.CreateExtentAttr(
                [Gf.Vec3f(-half_w, offset, -half_h), Gf.Vec3f(half_w, offset, half_h)]
            )
            st = UsdGeom.PrimvarsAPI(panel_prim).CreatePrimvar(
                "st",
                Sdf.ValueTypeNames.TexCoord2fArray,
                UsdGeom.Tokens.faceVarying,
            )
            st.Set([Gf.Vec2f(u, v) for u, v in DEVICE_LOCATION_PANEL_UV_COORDS])
            panel_prim.CreateAttribute(
                "rtg:deviceLocationValuesTexture",
                Sdf.ValueTypeNames.Asset,
            ).Set(Sdf.AssetPath(str(texture_path)))

            material = UsdShade.Material.Define(
                stage,
                DEVICE_LOCATION_PANEL_VALUES_MATERIAL_PATH,
            )
            shader = UsdShade.Shader.Define(
                stage,
                f"{DEVICE_LOCATION_PANEL_VALUES_MATERIAL_PATH}/PreviewSurface",
            )
            shader.CreateIdAttr("UsdPreviewSurface")
            shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.42)
            shader.CreateInput("opacityThreshold", Sdf.ValueTypeNames.Float).Set(0.5)
            texture = UsdShade.Shader.Define(
                stage,
                f"{DEVICE_LOCATION_PANEL_VALUES_MATERIAL_PATH}/Texture",
            )
            texture.CreateIdAttr("UsdUVTexture")
            texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(
                Sdf.AssetPath(str(texture_path))
            )
            texture.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
            reader = UsdShade.Shader.Define(
                stage,
                f"{DEVICE_LOCATION_PANEL_VALUES_MATERIAL_PATH}/Primvar_st",
            )
            reader.CreateIdAttr("UsdPrimvarReader_float2")
            reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
            texture.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(
                reader.ConnectableAPI(), "result"
            )
            shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
                texture.ConnectableAPI(), "rgb"
            )
            shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
                texture.ConnectableAPI(), "rgb"
            )
            shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).ConnectToSource(
                texture.ConnectableAPI(), "a"
            )
            material.CreateSurfaceOutput().ConnectToSource(
                shader.ConnectableAPI(), "surface"
            )
            UsdShade.MaterialBindingAPI(panel_prim).Bind(material)
            UsdGeom.Imageable(panel_prim).MakeVisible()
        self._floating_device_panel_value_key = _device_location_panel_value_key(values)
        return True

    def _sync_work_mode_indicator(
        self,
        stage: Any,
        mode: Any,
        task: Dict[str, Any],
    ) -> None:
        """Keep one independent, rotating current-work-mode badge visible."""

        from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

        mode_code = _work_mode_code(mode)
        self._work_mode_indicator_mode = mode_code
        bay_center_y = self._device_panel_bay_center_y(stage, task)
        stage_changed = self._work_mode_indicator_stage_id != id(stage)
        root_prim = stage.GetPrimAtPath(WORK_MODE_INDICATOR_ROOT_PATH)
        if not root_prim:
            root_prim = UsdGeom.Xform.Define(
                stage,
                WORK_MODE_INDICATOR_ROOT_PATH,
            ).GetPrim()
        if stage_changed:
            UsdGeom.Imageable(root_prim).MakeVisible()
        translate = Gf.Vec3d(
            DEVICE_LOCATION_PANEL_WORLD_X,
            bay_center_y + DEVICE_LOCATION_PANEL_Y_OFFSET_M,
            DEVICE_LOCATION_PANEL_WORLD_Z
            + DEVICE_LOCATION_PANEL_UP_OFFSET_M
            - DEVICE_LOCATION_PANEL_HEIGHT_M * 0.5
            - WORK_MODE_INDICATOR_GAP_M
            - WORK_MODE_INDICATOR_HEIGHT_M * 0.5,
        )
        rotate = camera_facing_rotate_xyz(
            stage,
            translate,
            DEVICE_LOCATION_PANEL_ROTATE_XYZ,
        )
        transform_key = (
            id(stage),
            round(float(translate[0]), 4),
            round(float(translate[1]), 4),
            round(float(translate[2]), 4),
            *(round(float(value), 3) for value in rotate),
        )
        if transform_key != self._work_mode_indicator_transform_key:
            self._set_xform_translate(root_prim, translate)
            self._set_xform_rotate_xyz(root_prim, Gf.Vec3f(*rotate))
            self._normalize_panel_xform_op_order(root_prim)
            self._work_mode_indicator_transform_key = transform_key

        spinner_prim = stage.GetPrimAtPath(WORK_MODE_INDICATOR_SPINNER_PATH)
        if not spinner_prim:
            spinner_prim = UsdGeom.Xform.Define(
                stage,
                WORK_MODE_INDICATOR_SPINNER_PATH,
            ).GetPrim()
            # Keep the current-mode badge static for now.  The spinner Xform
            # is retained as a stable parent for a future animation.
            self._set_xform_rotate_xyz(spinner_prim, Gf.Vec3f(0.0, 0.0, 0.0))

        now = time.monotonic()
        mode_changed = (
            stage_changed
            or self._work_mode_indicator_rendered_mode != mode_code
        )
        mesh_exists = stage.GetPrimAtPath(WORK_MODE_INDICATOR_MESH_PATH)
        if mesh_exists and not mode_changed:
            return

        self._work_mode_indicator_bitmap_seq += 1
        texture_path = (
            Path(tempfile.gettempdir())
            / f"smart_port_rtg_work_mode_{os.getpid()}_{self._work_mode_indicator_bitmap_seq}.png"
        )
        if not _render_work_mode_indicator_bitmap(mode_code, texture_path):
            self._log("work mode indicator render failed")
            return

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            panel = UsdGeom.Mesh.Define(stage, WORK_MODE_INDICATOR_MESH_PATH)
            panel_prim = panel.GetPrim()
            half_w = WORK_MODE_INDICATOR_WIDTH_M * 0.5
            half_h = WORK_MODE_INDICATOR_HEIGHT_M * 0.5
            panel.CreatePointsAttr(
                [
                    Gf.Vec3f(half_w, 0.0, -half_h),
                    Gf.Vec3f(-half_w, 0.0, -half_h),
                    Gf.Vec3f(-half_w, 0.0, half_h),
                    Gf.Vec3f(half_w, 0.0, half_h),
                ]
            )
            panel.CreateFaceVertexCountsAttr([4])
            panel.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
            panel.CreateDoubleSidedAttr(True)
            panel.CreateExtentAttr(
                [Gf.Vec3f(-half_w, 0.0, -half_h), Gf.Vec3f(half_w, 0.0, half_h)]
            )
            st = UsdGeom.PrimvarsAPI(panel_prim).CreatePrimvar(
                "st",
                Sdf.ValueTypeNames.TexCoord2fArray,
                UsdGeom.Tokens.faceVarying,
            )
            st.Set([Gf.Vec2f(0.0, 0.0), Gf.Vec2f(1.0, 0.0), Gf.Vec2f(1.0, 1.0), Gf.Vec2f(0.0, 1.0)])
            panel_prim.CreateAttribute(
                "rtg:workModeIndicatorTexture",
                Sdf.ValueTypeNames.Asset,
            ).Set(Sdf.AssetPath(str(texture_path)))

            material = UsdShade.Material.Define(stage, WORK_MODE_INDICATOR_MATERIAL_PATH)
            shader = UsdShade.Shader.Define(
                stage, f"{WORK_MODE_INDICATOR_MATERIAL_PATH}/PreviewSurface"
            )
            shader.CreateIdAttr("UsdPreviewSurface")
            shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.32)
            shader.CreateInput("opacityThreshold", Sdf.ValueTypeNames.Float).Set(0.02)
            texture = UsdShade.Shader.Define(
                stage, f"{WORK_MODE_INDICATOR_MATERIAL_PATH}/Texture"
            )
            texture.CreateIdAttr("UsdUVTexture")
            texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(str(texture_path)))
            texture.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
            reader = UsdShade.Shader.Define(
                stage, f"{WORK_MODE_INDICATOR_MATERIAL_PATH}/Primvar_st"
            )
            reader.CreateIdAttr("UsdPrimvarReader_float2")
            reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
            texture.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(
                reader.ConnectableAPI(), "result"
            )
            shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
                texture.ConnectableAPI(), "rgb"
            )
            shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
                texture.ConnectableAPI(), "rgb"
            )
            shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).ConnectToSource(
                texture.ConnectableAPI(), "a"
            )
            material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
            UsdShade.MaterialBindingAPI(panel_prim).Bind(material)
            UsdGeom.Imageable(panel_prim).MakeVisible()
        self._work_mode_indicator_stage_id = id(stage)
        self._work_mode_indicator_rendered_mode = mode_code
        self._work_mode_indicator_last_render_at = now

    @staticmethod
    def _hide_work_mode_indicator(stage: Any) -> None:
        try:
            from pxr import Usd, UsdGeom

            with Usd.EditContext(stage, stage.GetSessionLayer()):
                root = stage.GetPrimAtPath(WORK_MODE_INDICATOR_ROOT_PATH)
                if root:
                    UsdGeom.Imageable(root).MakeInvisible()
        except Exception:
            return

    def _sync_floating_device_panel(
        self,
        stage: Any,
        task: Dict[str, Any],
    ) -> None:
        """Show device location only while an RTG prim is selected."""

        from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

        if not self._device_panel_selected:
            if self._device_panel_visible:
                self._hide_floating_device_panel(stage)
                self._device_panel_visible = False
            return

        values = _device_location_panel_values(stage)
        bay_center_y = self._device_panel_bay_center_y(stage, task)
        value_key = _device_location_panel_value_key(values)
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            mesh_exists = stage.GetPrimAtPath(DEVICE_LOCATION_PANEL_MESH_PATH)
            if mesh_exists and self._floating_device_panel_key is not None:
                root = stage.GetPrimAtPath(DEVICE_LOCATION_PANEL_ROOT_PATH)
                if root:
                    UsdGeom.Imageable(root).MakeVisible()
                    self._device_panel_visible = True
                    self._sync_floating_device_panel_pose(
                        stage,
                        root,
                        bay_center_y,
                    )
                values_prim = stage.GetPrimAtPath(DEVICE_LOCATION_PANEL_VALUES_MESH_PATH)
                if values_prim:
                    UsdGeom.Imageable(values_prim).MakeVisible()
                now = time.monotonic()
                if (
                    value_key != self._floating_device_panel_value_key
                    and now - self._last_device_panel_render_at
                    >= DEVICE_LOCATION_PANEL_RENDER_INTERVAL_SECONDS
                ):
                    if self._sync_device_location_values_overlay(stage, values):
                        self._last_device_panel_render_at = now
                return

            self._floating_device_panel_bitmap_seq += 1
            texture_path = (
                Path(tempfile.gettempdir())
                / f"smart_port_rtg_device_location_{os.getpid()}_{self._floating_device_panel_bitmap_seq}.png"
            )
            if not _render_device_location_panel_bitmap(
                values,
                texture_path,
                draw_values=False,
            ):
                self._log("device location panel bitmap render failed")
                return

            self._floating_device_panel_key = ("static",)
            self._last_device_panel_render_at = time.monotonic()
            self._pending_device_panel_key = None
            self._pending_device_panel_since = 0.0
            root = UsdGeom.Xform.Define(stage, DEVICE_LOCATION_PANEL_ROOT_PATH)
            root_prim = root.GetPrim()
            UsdGeom.Imageable(root_prim).MakeVisible()
            self._device_panel_visible = True
            self._sync_floating_device_panel_pose(
                stage,
                root_prim,
                bay_center_y,
            )

            panel = UsdGeom.Mesh.Define(stage, DEVICE_LOCATION_PANEL_MESH_PATH)
            panel_prim = panel.GetPrim()
            half_w = DEVICE_LOCATION_PANEL_WIDTH_M * 0.5
            half_h = DEVICE_LOCATION_PANEL_HEIGHT_M * 0.5
            panel.CreatePointsAttr(
                [
                    Gf.Vec3f(half_w, 0.0, -half_h),
                    Gf.Vec3f(-half_w, 0.0, -half_h),
                    Gf.Vec3f(-half_w, 0.0, half_h),
                    Gf.Vec3f(half_w, 0.0, half_h),
                ]
            )
            panel.CreateFaceVertexCountsAttr([4])
            panel.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
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
            st.Set([Gf.Vec2f(u, v) for u, v in DEVICE_LOCATION_PANEL_UV_COORDS])
            panel_prim.CreateAttribute(
                "rtg:deviceLocationTexture",
                Sdf.ValueTypeNames.Asset,
            ).Set(Sdf.AssetPath(str(texture_path)))

            material = UsdShade.Material.Define(
                stage,
                DEVICE_LOCATION_PANEL_MATERIAL_PATH,
            )
            shader = UsdShade.Shader.Define(
                stage,
                f"{DEVICE_LOCATION_PANEL_MATERIAL_PATH}/PreviewSurface",
            )
            shader.CreateIdAttr("UsdPreviewSurface")
            shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.42)
            shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
            texture = UsdShade.Shader.Define(
                stage,
                f"{DEVICE_LOCATION_PANEL_MATERIAL_PATH}/Texture",
            )
            texture.CreateIdAttr("UsdUVTexture")
            texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(
                Sdf.AssetPath(str(texture_path))
            )
            texture.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
            reader = UsdShade.Shader.Define(
                stage,
                f"{DEVICE_LOCATION_PANEL_MATERIAL_PATH}/Primvar_st",
            )
            reader.CreateIdAttr("UsdPrimvarReader_float2")
            reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
            texture.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(
                reader.ConnectableAPI(),
                "result",
            )
            shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
                texture.ConnectableAPI(),
                "rgb",
            )
            shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
                texture.ConnectableAPI(),
                "rgb",
            )
            shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).ConnectToSource(
                texture.ConnectableAPI(),
                "a",
            )
            material.CreateSurfaceOutput().ConnectToSource(
                shader.ConnectableAPI(),
                "surface",
            )
            UsdShade.MaterialBindingAPI(panel_prim).Bind(material)
        self._sync_device_location_values_overlay(stage, values)

    def _sync_floating_device_panel_pose(
        self,
        stage: Any,
        root_prim: Any,
        bay_center_y: float,
    ) -> None:
        from pxr import Gf

        translate = Gf.Vec3d(
            DEVICE_LOCATION_PANEL_WORLD_X,
            bay_center_y + DEVICE_LOCATION_PANEL_Y_OFFSET_M,
            DEVICE_LOCATION_PANEL_WORLD_Z + DEVICE_LOCATION_PANEL_UP_OFFSET_M,
        )
        camera = active_viewport_camera_position(stage)
        self._set_xform_translate(root_prim, translate)
        rotate = camera_facing_rotate_xyz(
            stage,
            translate,
            DEVICE_LOCATION_PANEL_ROTATE_XYZ,
        )
        self._set_xform_rotate_xyz(root_prim, Gf.Vec3f(*rotate))
        self._normalize_panel_xform_op_order(root_prim)

    @staticmethod
    def _hide_floating_device_panel(stage: Any) -> None:
        try:
            from pxr import Usd, UsdGeom

            with Usd.EditContext(stage, stage.GetSessionLayer()):
                current_root = stage.GetPrimAtPath(DEVICE_LOCATION_PANEL_ROOT_PATH)
                if current_root:
                    UsdGeom.Imageable(current_root).MakeInvisible()

                # The old click-to-open service created a second panel on the
                # right.  Hiding it is not enough: its stale mesh/material can
                # be made visible again by a cached service after hot reload.
                legacy_root = stage.GetPrimAtPath(
                    DEVICE_LOCATION_PANEL_RIGHT_ROOT_PATH
                )
                if legacy_root:
                    stage.RemovePrim(legacy_root.GetPath())
        except Exception:
            return

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
            work_type = _task_work_type(task)
            if work_type not in CONTAINER_WORK_TYPES:
                self._log_task_panel_status(
                    "shown_non_container_task",
                    "task panel uses current RTG bay: non-container task "
                    f"work={work_type or '--'} task={task.get('taskId') or '--'}",
                )
            else:
                self._log_task_panel_status(
                    "shown_fallback_position",
                    "task panel uses current RTG bay: task has no valid bayMap/position "
                    f"task={task.get('taskId') or '--'} "
                    f"work={task.get('workType') or task.get('taskType') or '--'} "
                    f"start={task.get('startPos') or task.get('sourcePos') or '--'} "
                    f"end={task.get('endPos') or task.get('targetPos') or '--'} "
                    f"bayMap={task.get('bayMap') or '--'}",
                )

        panel_key = _task_panel_content_key(task)
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            if (
                panel_key == self._floating_task_panel_key
                and stage.GetPrimAtPath(TASK_PANEL_MESH_PATH)
            ):
                root = stage.GetPrimAtPath(TASK_PANEL_ROOT_PATH)
                if root:
                    UsdGeom.Imageable(root).MakeVisible()
                    self._sync_floating_task_panel_pose(root, stack_key, task)
                return

            self._floating_task_panel_key = panel_key
            self._floating_task_panel_bitmap_seq += 1
            texture_path = (
                Path(tempfile.gettempdir())
                / f"smart_port_rtg_task_panel_{os.getpid()}_{self._floating_task_panel_bitmap_seq}.png"
            )
            if not _render_task_info_panel_bitmap(
                task,
                texture_path,
                route_phase=0.0,
            ):
                self._log("task info panel bitmap render failed")
                return

            root = UsdGeom.Xform.Define(stage, TASK_PANEL_ROOT_PATH)
            root_prim = root.GetPrim()
            UsdGeom.Imageable(root_prim).MakeVisible()
            self._sync_floating_task_panel_pose(root_prim, stack_key, task)

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
            shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
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
        stack_key: Optional[tuple[str, int, str, int]],
        task: Optional[Dict[str, Any]] = None,
    ) -> None:
        from pxr import Gf

        if stack_key is not None:
            from build_live_containers import container_center_y_for_bay

            block, bay_number, _pattern, container_size_ft = stack_key
            bay_center_y = container_center_y_for_bay(
                block,
                bay_number,
                container_size_ft,
            )
        else:
            stage = self._current_stage()
            bay_center_y = (
                self._device_panel_bay_center_y(stage, task or {})
                if stage is not None
                else 0.0
            )
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
        self._normalize_panel_xform_op_order(root_prim)

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
        translate = self._get_xform_world_translate(stage, root)
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
            self._normalize_panel_xform_op_order(root)
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
            translate = self._get_xform_world_translate(stage, root)
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
                self._normalize_panel_xform_op_order(root)
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
            work_mode = pose.get("workMode")
            if work_mode is None:
                work_mode = pose.get("work_mode")
            if work_mode is None:
                work_mode = state.get("workMode")
            if work_mode is None:
                work_mode = state.get("work_mode")
            self._set_attr(
                prim, "rtg:workMode", Sdf.ValueTypeNames.Int, _as_int(work_mode, 0)
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
            raw_runinfo = state.get("runinfo")
            runinfo = self._runinfo_payload_for_task(raw_runinfo, task)
            self._set_attr(
                prim,
                "rtg:runinfo",
                Sdf.ValueTypeNames.String,
                runinfo.get("message") or "",
            )
            self._sync_floating_runinfo_billboard(
                stage,
                runinfo,
                visible=_runinfo_should_be_visible(task, raw_runinfo),
            )
            self._sync_floating_device_panel(stage, task)
            self._sync_floating_task_panel(stage, task)
            self._set_attr(
                prim, "rtg:lastAppliedUnix", Sdf.ValueTypeNames.Double, time.time()
            )

    def _effective_runinfo_payload(self, raw_runinfo: Any) -> Dict[str, Any]:
        """Keep the last message until the upstream explicitly clears it."""

        if isinstance(raw_runinfo, str):
            message = raw_runinfo.strip()
            if message:
                self._last_runinfo_payload = {"message": message}
            return dict(self._last_runinfo_payload)

        if isinstance(raw_runinfo, dict):
            message = str(raw_runinfo.get("message") or "").strip()
            if message:
                self._last_runinfo_payload = dict(raw_runinfo)
            elif "message" in raw_runinfo:
                self._last_runinfo_payload = {}
            elif raw_runinfo:
                merged = dict(self._last_runinfo_payload)
                merged.update(raw_runinfo)
                self._last_runinfo_payload = merged
        return dict(self._last_runinfo_payload)

    def _runinfo_payload_for_task(
        self,
        raw_runinfo: Any,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Clear stale RunInfo whenever there is no active unfinished task."""

        if not _runinfo_should_be_visible(task, raw_runinfo):
            self._last_runinfo_payload = {}
            return {}
        return self._effective_runinfo_payload(raw_runinfo)

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


def _stop_legacy_device_info_popup() -> None:
    """Disable all old click-to-open device panel services."""

    for module_name, stopper_names in (
        (
            "rtg_device_info_popup",
            ("stop_rtg_device_info_popup", "stop_rtg_device_location_panel"),
        ),
        (
            "rtg_device_location_panel",
            ("stop_rtg_device_location_panel", "stop_rtg_device_info_popup"),
        ),
    ):
        try:
            module = importlib.import_module(module_name)
            for stopper_name in stopper_names:
                stopper = getattr(module, stopper_name, None)
                if callable(stopper):
                    stopper()
                    break
        except Exception as exc:  # noqa: BLE001 - legacy cleanup is best-effort
            print(
                "[RTG Twin Driver] unable to disable legacy device popup "
                f"module={module_name}: {exc}"
            )

    # A previous Script Editor execution may have left a service object in
    # builtins even when its source module is no longer importable.
    for service_name in (
        "_rtg_device_location_panel_service",
        "_rtg_device_info_popup_service",
    ):
        previous = getattr(builtins, service_name, None)
        if previous is not None:
            try:
                previous.stop()
            except Exception:
                pass
        setattr(builtins, service_name, None)


def _start_live_info_popups() -> None:
    """Keep container inspection inside this receiver's update loop."""

    _stop_legacy_container_info_popup()
    print("[RTG Twin Driver] container info popup is owned by receiver")


def _stop_legacy_container_info_popup() -> None:
    """Stop the old standalone container popup service."""

    try:
        module = importlib.import_module("rtg_container_info_popup")
        stopper = getattr(module, "stop_rtg_container_info_popup", None)
        if callable(stopper):
            stopper()
    except Exception as exc:  # noqa: BLE001 - legacy cleanup is best-effort
        print(f"[RTG Twin Driver] unable to disable legacy container popup: {exc}")
    previous = getattr(builtins, "_rtg_container_info_popup_service", None)
    if previous is not None:
        try:
            previous.stop()
        except Exception:
            pass
    builtins._rtg_container_info_popup_service = None


def _stop_live_info_popups() -> None:
    """Stop standalone legacy UI services when live mode stops."""

    _stop_legacy_container_info_popup()
    _stop_legacy_device_info_popup()


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
        previous_driver = _driver
        try:
            previous_driver.stop()
        except Exception as exc:  # noqa: BLE001 - hot reload cleanup must continue
            print(f"[RTG Twin Driver] previous driver stop failed: {exc}")
        finally:
            _driver = None
    # The persistent device panel owns the old popup's USD path, so clear the
    # legacy service before the driver creates the replacement panel.
    _stop_legacy_container_info_popup()
    _stop_legacy_device_info_popup()
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
        driver = _driver
        try:
            driver.stop()
        except Exception as exc:  # noqa: BLE001 - shutdown must clear stale driver
            print(f"[RTG Twin Driver] driver stop failed: {exc}")
        finally:
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
