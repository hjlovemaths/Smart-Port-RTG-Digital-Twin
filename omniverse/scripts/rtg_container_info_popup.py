"""Clickable RTG container information popup for Omniverse Kit.

Run in Omniverse Script Editor::

    exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_container_info_popup.py", encoding="utf-8").read())
    start_rtg_container_info_popup()

Stop with::

    stop_rtg_container_info_popup()

The service listens to USD selection. Selecting the live hoisted container,
the task truck's loaded container, or a live yard container opens an omni.ui
window with task and container metadata from the selected prim plus
``/World/LiveTwinState``.
"""

from __future__ import annotations

import builtins
import importlib
import importlib.util
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Tuple

try:
    from rtg_selection_highlight import hide_selection_highlight, show_selection_highlight
except ModuleNotFoundError:
    helper_path = Path(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_selection_highlight.py")
    if not helper_path.is_file():
        helper_path = Path(__file__).resolve().parent / "rtg_selection_highlight.py"
    spec = importlib.util.spec_from_file_location("rtg_selection_highlight", helper_path)
    if spec is None or spec.loader is None:
        raise
    helper_module = importlib.util.module_from_spec(spec)
    sys.modules["rtg_selection_highlight"] = helper_module
    spec.loader.exec_module(helper_module)
    hide_selection_highlight = helper_module.hide_selection_highlight
    show_selection_highlight = helper_module.show_selection_highlight

import rtg_billboard_utils as _billboard_utils

_billboard_utils = importlib.reload(_billboard_utils)
camera_facing_rotate_xyz = _billboard_utils.camera_facing_rotate_xyz
existing_xform_op = _billboard_utils.existing_xform_op
get_or_add_xform_op = _billboard_utils.get_or_add_xform_op


STATUS_PRIM_PATH = "/World/LiveTwinState"
LIVE_HOISTED_LOAD_PATH = "/World/LiveContainers/LiveTwinHoistedContainer"
LEGACY_HOISTED_LOAD_SUFFIX = "/LIVE_TwinHoistedContainer"
LIVE_TASK_TRUCK_PATH = "/World/LiveContainers/LiveTaskTruck"
LIVE_TASK_STACK_PATH = "/World/LiveContainers/LiveTaskStack"
YARD_INVENTORY_ROOT_PATH = "/World/LiveContainers/YardInventory"
HIGHLIGHT_OWNER = "container"
WINDOW_TITLE = "RTG Container Info"
LEGACY_WINDOW_TITLES = ("RTG Container Info",)
DIAGNOSTIC_LOG_ENABLED = False
DIAGNOSTIC_IDLE_POLL_INTERVAL_SECONDS = 3.0
DIAGNOSTIC_IDLE_POLL_LIMIT = 3
UPDATE_POLL_INTERVAL_SECONDS = 1.0 / 30.0
CJK_FONT_PATHS = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
)
CONTAINER_PANEL_WIDTH = 320
CONTAINER_PANEL_HEIGHT = 380
CONTAINER_PANEL_ROW_Y = {
    "Container": 124,
    "Size": 158,
    "Type": 192,
    "Height": 226,
    "Bay": 260,
    "Source": 294,
    "Task ID": 328,
    "Locked": 362,
}
CONTAINER_PANEL_LABELS = {
    "Container": "箱号",
    "Size": "箱尺寸",
    "Type": "箱型",
    "Height": "箱高",
    "Bay": "当前位置",
    "Source": "来源",
    "Task ID": "关联任务",
    "Locked": "锁闭",
}
CONTAINER_SCENE_PANEL_ROOT_PATH = "/World/LiveHUD/RTG_CONTAINER_INFO_PANEL"
CONTAINER_SCENE_PANEL_MESH_PATH = f"{CONTAINER_SCENE_PANEL_ROOT_PATH}/Panel"
CONTAINER_SCENE_PANEL_MATERIAL_PATH = "/World/Looks/RTG_Container_Info_Panel"
CONTAINER_SCENE_GUIDE_LINE_PATH = "/World/LiveHUD/RTG_CONTAINER_INFO_GUIDE_LINE"
CONTAINER_SCENE_PANEL_WIDTH_M = 4.4
CONTAINER_SCENE_PANEL_HEIGHT_M = 5.2
CONTAINER_SCENE_PANEL_Z_OFFSET_M = 4.4
CONTAINER_SCENE_PANEL_STACK_BASE_Z_M = 0.12
CONTAINER_SCENE_PANEL_VISUAL_TIER_HEIGHT_M = 0.904353
CONTAINER_SCENE_PANEL_TIER_GAP_Z_M = 0.035
CONTAINER_SCENE_PANEL_MIN_BOTTOM_WORLD_Z = (
    CONTAINER_SCENE_PANEL_STACK_BASE_Z_M
    + 6 * CONTAINER_SCENE_PANEL_VISUAL_TIER_HEIGHT_M
    + 5 * CONTAINER_SCENE_PANEL_TIER_GAP_Z_M
)
CONTAINER_SCENE_PANEL_Y_OFFSET_M = -1.8
CONTAINER_SCENE_PANEL_ROTATE_XYZ = (0.0, 0.0, 180.0)
CONTAINER_SCENE_GUIDE_LINE_WIDTH_M = 0.045
CONTAINER_SCENE_GUIDE_PANEL_BOTTOM_MARGIN_M = 0.18
CONTAINER_SCENE_GUIDE_SOURCE_SURFACE_GAP_M = 0.008
LIVE_HUD_ROOT_PATH = "/World/LiveHUD"
CONTAINER_SCENE_HUD_SELECTABLE_PATH_PREFIXES = (
    CONTAINER_SCENE_PANEL_ROOT_PATH,
    CONTAINER_SCENE_GUIDE_LINE_PATH,
)
CONTAINER_SCENE_PANEL_UV_COORDS = (
    (0.0, 0.0),
    (1.0, 0.0),
    (1.0, 1.0),
    (0.0, 1.0),
)

_service: Optional["RtgContainerInfoPopupService"] = None


@dataclass(frozen=True)
class InfoRow:
    label: str
    value: str


def _selected_paths() -> List[str]:
    try:
        import omni.usd  # type: ignore

        return list(omni.usd.get_context().get_selection().get_selected_prim_paths())
    except Exception:
        return []


def _path_parts(path: str) -> List[str]:
    return [part for part in str(path or "").strip().split("/") if part]


def _container_component_index(parts: Sequence[str]) -> Optional[int]:
    """Return the path component index that denotes one physical container."""

    for index, part in enumerate(parts):
        name = str(part)
        if name in {"LiveContainers", "YardInventory"}:
            continue
        if name == "LiveTwinHoistedContainer" or name == "LIVE_TwinHoistedContainer":
            return index
        if name == "LoadedContainer":
            return index
        if name.endswith("_Container") or "_Container_" in name:
            return index
        if name.lower().endswith("container") and name.lower() != "livecontainers":
            return index
    return None


def _container_root_path_from_selection(path: str) -> Optional[str]:
    """Normalize a clicked mesh/detail path to its logical container root."""

    text = str(path or "").strip()
    if not text.startswith("/"):
        return None
    if text == LIVE_HOISTED_LOAD_PATH or text.startswith(f"{LIVE_HOISTED_LOAD_PATH}/"):
        return LIVE_HOISTED_LOAD_PATH
    if LEGACY_HOISTED_LOAD_SUFFIX in text:
        before, _sep, _after = text.partition(LEGACY_HOISTED_LOAD_SUFFIX)
        return before + LEGACY_HOISTED_LOAD_SUFFIX

    parts = _path_parts(text)
    index = _container_component_index(parts)
    if index is None:
        return None
    return "/" + "/".join(parts[: index + 1])


def _read_attr(prim: Any, name: str, default: Any = None) -> Any:
    if not prim:
        return default
    try:
        attr = prim.GetAttribute(name)
        if not attr:
            return default
        value = attr.Get()
        return default if value is None else value
    except Exception:
        return default


def _first_attr(prims: Iterable[Any], names: Iterable[str], default: Any = None) -> Any:
    for prim in prims:
        for name in names:
            value = _read_attr(prim, name, None)
            if value not in (None, ""):
                return value
    return default


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "--"
    if isinstance(value, float):
        text = f"{value:.3f}".rstrip("0").rstrip(".")
    else:
        text = str(value).strip()
    if not text:
        return "--"
    return f"{text}{suffix}"


def _fmt_bool(value: Any) -> str:
    if value is None:
        return "--"
    return "YES" if bool(value) else "NO"


def _find_cjk_font_path() -> Optional[Path]:
    for path in CJK_FONT_PATHS:
        candidate = Path(path)
        try:
            if candidate.is_file():
                return candidate
        except Exception:
            continue
    return None


def _container_panel_world_z(origin_z: Any) -> float:
    try:
        z = float(origin_z) + CONTAINER_SCENE_PANEL_Z_OFFSET_M
    except Exception:
        z = 4.0
    min_center_z = (
        CONTAINER_SCENE_PANEL_MIN_BOTTOM_WORLD_Z
        + CONTAINER_SCENE_PANEL_HEIGHT_M * 0.5
    )
    return max(z, min_center_z)


def _is_container_hud_selection_path(path: str) -> bool:
    text = str(path or "").strip()
    return any(
        text == prefix or text.startswith(f"{prefix}/")
        for prefix in CONTAINER_SCENE_HUD_SELECTABLE_PATH_PREFIXES
    )


def _container_panel_guide_points(
    container_bounds: Sequence[Sequence[Any]],
    panel_translate: Sequence[Any],
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    bounds_min, bounds_max = container_bounds
    target = tuple(float(value) for value in panel_translate[:3])
    low = tuple(float(value) for value in bounds_min[:3])
    high = tuple(float(value) for value in bounds_max[:3])
    start_values = [max(low[index], min(high[index], target[index])) for index in range(3)]

    # If the panel is inside the box bounds, project to the nearest face.
    if all(low[index] <= target[index] <= high[index] for index in range(3)):
        nearest_axis = min(
            range(3),
            key=lambda index: min(target[index] - low[index], high[index] - target[index]),
        )
        if target[nearest_axis] - low[nearest_axis] <= high[nearest_axis] - target[nearest_axis]:
            start_values[nearest_axis] = low[nearest_axis]
        else:
            start_values[nearest_axis] = high[nearest_axis]

    # Keep the line just above the surface so it does not z-fight with the box.
    for index in range(3):
        direction = target[index] - start_values[index]
        if abs(direction) > 1e-6:
            start_values[index] += (
                CONTAINER_SCENE_GUIDE_SOURCE_SURFACE_GAP_M
                if direction > 0.0
                else -CONTAINER_SCENE_GUIDE_SOURCE_SURFACE_GAP_M
            )
            break
    start = tuple(start_values)
    end = (
        float(panel_translate[0]),
        float(panel_translate[1]),
        float(panel_translate[2])
        - CONTAINER_SCENE_PANEL_HEIGHT_M * 0.5
        + CONTAINER_SCENE_GUIDE_PANEL_BOTTOM_MARGIN_M,
    )
    return start, end


def _is_live_hud_selection_path(path: str) -> bool:
    text = str(path or "").strip()
    return text == LIVE_HUD_ROOT_PATH or text.startswith(f"{LIVE_HUD_ROOT_PATH}/")


def _container_hud_root_from_selection(path: str) -> Optional[str]:
    text = str(path or "").strip()
    if _is_container_hud_selection_path(text):
        return CONTAINER_SCENE_PANEL_ROOT_PATH
    return None


def _render_container_panel_bitmap(rows: Sequence[InfoRow], output_path: Path) -> bool:
    font_path = _find_cjk_font_path()
    if font_path is None:
        return False
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont  # type: ignore

        values = {row.label: row.value for row in rows}
        width = CONTAINER_PANEL_WIDTH
        height = CONTAINER_PANEL_HEIGHT
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        panel_box = (1, 1, width - 2, height - 2)
        glow_draw.rounded_rectangle(
            panel_box,
            radius=5,
            fill=(54, 151, 190, 44),
            outline=(150, 228, 248, 222),
            width=3,
        )
        image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(4)))

        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            panel_box,
            radius=4,
            fill=(9, 31, 46, 250),
            outline=(154, 230, 250, 238),
            width=2,
        )
        draw.line((8, 8, width - 9, 8), fill=(255, 172, 44, 255), width=3)
        draw.rectangle((4, 4, width - 5, height - 5), outline=(142, 219, 238, 120))
        draw.polygon(
            [(width - 26, height - 1), (width - 1, height - 26), (width - 1, height - 1)],
            fill=(109, 194, 220, 150),
        )
        draw.rectangle((0, 0, width, 58), fill=(16, 55, 76, 252))

        title_font = ImageFont.truetype(str(font_path), 16)
        label_font = ImageFont.truetype(str(font_path), 13)
        value_font = ImageFont.truetype(str(font_path), 15)
        value_font_big = ImageFont.truetype(str(font_path), 20)
        badge_font = ImageFont.truetype(str(font_path), 12)

        draw.text(
            (12, 15),
            "箱信息",
            fill=(255, 255, 255, 255),
            font=title_font,
            stroke_width=1,
            stroke_fill=(0, 16, 24, 220),
        )
        badge = "选中箱"
        badge_bbox = draw.textbbox((0, 0), badge, font=badge_font)
        badge_w = badge_bbox[2] - badge_bbox[0] + 18
        draw.rounded_rectangle(
            (width - badge_w - 12, 15, width - 12, 38),
            radius=11,
            fill=(58, 74, 50, 218),
            outline=(255, 197, 72, 230),
        )
        draw.text(
            (width - badge_w - 3, 18),
            badge,
            fill=(255, 211, 92, 255),
            font=badge_font,
            stroke_width=1,
            stroke_fill=(42, 30, 0, 180),
        )

        visual = (12, 66, width - 12, 106)
        draw.rectangle(visual, fill=(43, 92, 115, 242), outline=(156, 232, 250, 220))
        for x in range(visual[0] + 20, visual[2], 24):
            draw.line((x, visual[1], x, visual[3]), fill=(190, 242, 252, 82), width=1)
        draw.line((visual[0], visual[1] + 9, visual[2], visual[1] + 9), fill=(255, 255, 255, 74))

        values["Bay"] = " / ".join(
            part for part in (values.get("Bay", "--"), values.get("Row / Tier", "--")) if part and part != "--"
        ) or "--"
        for key, y in CONTAINER_PANEL_ROW_Y.items():
            label = CONTAINER_PANEL_LABELS[key]
            value = str(values.get(key) or "--")
            if key == "Locked":
                value = {"YES": "已锁闭", "NO": "未锁闭"}.get(value, value)
            font = value_font_big if key == "Container" else value_font
            max_value_width = 184
            while len(value) > 2 and draw.textbbox((0, 0), value, font=font)[2] > max_value_width:
                value = value[:-2] + "..."
            draw.text(
                (12, y - 11),
                label,
                fill=(218, 238, 247, 255),
                font=label_font,
                stroke_width=1,
                stroke_fill=(0, 15, 22, 190),
            )
            bbox = draw.textbbox((0, 0), value, font=font)
            color = (116, 234, 255, 255) if key == "Container" else (255, 255, 255, 255)
            if key == "Bay":
                color = (221, 246, 255, 255)
            if key == "Locked" and value == "未锁闭":
                color = (255, 86, 86, 255)
            draw.text(
                (width - 14 - (bbox[2] - bbox[0]), y - 14),
                value,
                fill=color,
                font=font,
                stroke_width=1,
                stroke_fill=(0, 14, 22, 210),
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path)
        return True
    except Exception:
        return False


def _status_prim(stage: Any) -> Any:
    try:
        return stage.GetPrimAtPath(STATUS_PRIM_PATH)
    except Exception:
        return None


def _current_stage() -> Any:
    try:
        import omni.usd  # type: ignore

        return omni.usd.get_context().get_stage()
    except Exception:
        return None


def _hide_known_windows() -> None:
    try:
        import omni.ui as ui  # type: ignore

        workspace = getattr(ui, "Workspace", None)
        show_window = getattr(workspace, "show_window", None) if workspace else None
        if callable(show_window):
            for title in LEGACY_WINDOW_TITLES:
                try:
                    show_window(title, False)
                except Exception:
                    pass
    except Exception:
        pass


def _stop_previous_builtin_service() -> None:
    previous = getattr(builtins, "_rtg_container_info_popup_service", None)
    if previous is not None:
        try:
            previous.stop()
        except Exception:
            pass
        try:
            builtins._rtg_container_info_popup_service = None
        except Exception:
            pass
    _hide_known_windows()


_stop_previous_builtin_service()


def _prim_at(stage: Any, path: str) -> Any:
    try:
        return stage.GetPrimAtPath(path)
    except Exception:
        return None


def _container_context_prims(stage: Any, root_path: str) -> List[Any]:
    """Return selected container prims plus task/inventory context roots."""

    paths = [
        root_path,
        f"{root_path}/Body",
    ]
    if root_path.startswith(f"{LIVE_TASK_TRUCK_PATH}/"):
        paths.append(LIVE_TASK_TRUCK_PATH)
    if root_path.startswith(f"{LIVE_TASK_STACK_PATH}/"):
        paths.append(LIVE_TASK_STACK_PATH)
    if root_path.startswith(f"{YARD_INVENTORY_ROOT_PATH}/"):
        parts = _path_parts(root_path)
        if len(parts) >= 4:
            paths.append("/" + "/".join(parts[:4]))
    prims = []
    for path in paths:
        prim = _prim_at(stage, path)
        if prim:
            prims.append(prim)
    return prims


def _collect_info_rows(stage: Any, root_path: str) -> Tuple[InfoRow, ...]:
    """Collect display rows for a selected RTG container."""

    status = _status_prim(stage)
    prims = _container_context_prims(stage, root_path)

    bay_id = _first_attr(prims, ("live:bayId", "inventory:bayId"))
    row = _first_attr(prims, ("live:row",))
    tier = _first_attr(prims, ("live:tier",))
    tiers = _first_attr(prims, ("live:tiers",))
    container_size = _first_attr(
        prims,
        ("live:containerSizeFt", "inventory:containerSizeFt"),
        _read_attr(status, "rtg:containerSize"),
    )
    container_height = _first_attr(
        prims,
        ("live:containerHeightMeters", "live:taskContainerHeightMeters"),
        _read_attr(status, "rtg:containerHeight"),
    )
    source = _first_attr(prims, ("live:source",))
    task_id = _first_attr(prims, ("live:taskId",), _read_attr(status, "rtg:taskId"))
    work_type = _first_attr(prims, ("live:workType",), _read_attr(status, "rtg:workType"))
    container_no = _first_attr(
        prims,
        ("live:containerNo", "inventory:containerNo", "containerNo"),
        _read_attr(status, "rtg:containerNo"),
    )
    container_type = _first_attr(
        prims,
        ("live:containerType", "inventory:containerType", "containerType"),
        _read_attr(status, "rtg:containerType"),
    )

    rows = [
        InfoRow("Container", _fmt(container_no)),
        InfoRow("Type", _fmt(container_type)),
        InfoRow(
            "Size",
            _fmt(container_size, " ft")
            if container_size not in (None, "", 0, "0")
            else "--",
        ),
        InfoRow("Height", _fmt(container_height, " m") if container_height not in (None, "") else "--"),
        InfoRow("Task ID", _fmt(task_id)),
        InfoRow("Work Type", _fmt(work_type)),
        InfoRow("State", _fmt(_read_attr(status, "rtg:operationState") or _read_attr(status, "rtg:visualPhase"))),
        InfoRow("Source Pos", _fmt(_read_attr(status, "rtg:startPos") or _read_attr(status, "rtg:sourcePos"))),
        InfoRow("Target Pos", _fmt(_read_attr(status, "rtg:endPos") or _read_attr(status, "rtg:targetPos"))),
        InfoRow("Bay", _fmt(bay_id)),
        InfoRow("Row / Tier", _format_row_tier(row, tier, tiers)),
        InfoRow("Bay Map", _fmt(_read_attr(status, "rtg:bayMap"))),
        InfoRow("Locked", _fmt_bool(_read_attr(status, "rtg:spreaderLocked"))),
        InfoRow("Path", root_path),
        InfoRow("Source", _fmt(source)),
    ]
    return tuple(rows)


def _format_row_tier(row: Any, tier: Any, tiers: Any) -> str:
    if row in (None, "") and tier in (None, ""):
        return "--"
    row_int = int(row or 0)
    tier_int = int(tier or 0)
    if row_int <= 0 or tier_int <= 0:
        return "--"
    if tiers not in (None, ""):
        return f"R{row_int:02d} / T{tier_int:02d} of {int(tiers)}"
    return f"R{row_int:02d} / T{tier_int:02d}"


class RtgContainerInfoPopupService:
    def __init__(self) -> None:
        self._selection_sub = None
        self._update_sub = None
        self._window = None
        self._panel_image = None
        self._bitmap_seq = 0
        self._clearing_selection = False
        self._normalizing_hud_selection = False
        self._active_container_root_path: Optional[str] = None
        self._last_selection_signature: tuple[str, ...] = ()
        self._last_idle_diagnostic_at = 0.0
        self._last_update_poll_at = 0.0
        self._idle_diagnostics_remaining = DIAGNOSTIC_IDLE_POLL_LIMIT
        self._log("starting container popup service")
        self._build_window()
        self._subscribe_selection()
        self._subscribe_update_poll()
        self._log(f"service ready window={self._window is not None}")

    def stop(self) -> None:
        if self._selection_sub is not None:
            try:
                self._selection_sub.unsubscribe()
            except Exception:
                pass
            self._selection_sub = None
        if self._update_sub is not None:
            try:
                self._update_sub.unsubscribe()
            except Exception:
                pass
            self._update_sub = None
        stage = _current_stage()
        if stage is not None:
            hide_selection_highlight(stage, owner=HIGHLIGHT_OWNER)
            self._hide_scene_panel(stage)
        if self._window is not None:
            try:
                self._window.visible = False
            except Exception:
                pass
            self._window = None

    def _build_window(self) -> None:
        _hide_known_windows()
        self._window = None
        self._panel_image = None

    def _subscribe_selection(self) -> None:
        try:
            import omni.usd  # type: ignore

            stream = omni.usd.get_context().get_selection().get_changed_event_stream()
            self._selection_sub = stream.create_subscription_to_pop(self._on_selection_changed)
            self._log("selection event subscription active")
        except Exception as exc:
            self._log(f"selection subscription unavailable: {exc}")

    def _subscribe_update_poll(self) -> None:
        try:
            import omni.kit.app  # type: ignore

            stream = omni.kit.app.get_app().get_update_event_stream()
            self._update_sub = stream.create_subscription_to_pop(self._on_update)
            self._log("selection polling active")
        except Exception as exc:
            print(f"[RTG Container Info] selection polling unavailable: {exc}")

    def _on_selection_changed(self, *_args: Any) -> None:
        self._handle_selected_paths(_selected_paths(), source="event")

    def _on_update(self, *_args: Any) -> None:
        now = time.monotonic()
        last_update = float(getattr(self, "_last_update_poll_at", 0.0) or 0.0)
        if now - last_update < UPDATE_POLL_INTERVAL_SECONDS:
            return
        self._last_update_poll_at = now
        self._handle_selected_paths(_selected_paths(), source="poll")
        self._sync_visible_scene_panel_facing_camera()
        self._sync_visible_guide_line()

    def _handle_selected_paths(self, paths: List[str], source: str = "selection") -> None:
        if self._clearing_selection or self._normalizing_hud_selection:
            return
        if not paths and any(
            _is_live_hud_selection_path(path) for path in self._last_selection_signature
        ):
            # The live driver dismisses the non-draggable runinfo mesh to avoid
            # leaving an orange USD selection outline around the billboard.
            self._last_selection_signature = ()
            return
        self._diagnose_idle_poll(paths, source)
        signature = tuple(paths)
        if signature == self._last_selection_signature:
            return
        self._last_selection_signature = signature
        self._log(f"selection changed source={source} paths={list(paths)}")

        actionable_paths = [
            path for path in paths if not _is_live_hud_selection_path(path)
        ]
        if paths and not actionable_paths:
            hud_root = next(
                (
                    root
                    for root in (_container_hud_root_from_selection(path) for path in paths)
                    if root is not None
                ),
                None,
            )
            if hud_root is not None:
                self._select_hud_root_for_dragging(paths, hud_root)
            self._log("selection ignored: live HUD selected")
            return

        for path in actionable_paths:
            root_path = _container_root_path_from_selection(path)
            if root_path is None:
                continue
            self._log(f"selected container ({source}): {root_path}")
            self.open_container(root_path)
            self._clear_selection_gizmo()
            return
        if paths:
            self._log("selection ignored: no live container root found")
        stage = _current_stage()
        if stage is not None:
            hide_selection_highlight(stage, owner=HIGHLIGHT_OWNER)
            self._hide_scene_panel(stage)

    def open_container(self, root_path: str) -> None:
        stage = _current_stage()
        if stage is None:
            self._show_message("Stage unavailable")
            return
        hide_selection_highlight(stage)
        self._log(
            "opening container popup "
            f"root={root_path} window={self._window is not None}"
        )
        rows = _collect_info_rows(stage, root_path)
        self._active_container_root_path = root_path
        self._show_rows(rows, root_path=root_path)

    def _show_rows(self, rows: Sequence[InfoRow], root_path: str = "") -> None:
        self._bitmap_seq += 1
        panel_path = (
            Path(tempfile.gettempdir())
            / f"smart_port_rtg_container_panel_{os.getpid()}_{self._bitmap_seq}.png"
        )
        stage = _current_stage()
        if stage is None:
            return
        if _render_container_panel_bitmap(rows, panel_path):
            self._show_scene_panel(stage, root_path, panel_path)

    def _show_message(self, message: str) -> None:
        self._active_container_root_path = None
        rows = [InfoRow("Container", message)] + [InfoRow("", "--") for _ in range(14)]
        self._show_rows(rows)

    def _show_scene_panel(self, stage: Any, root_path: str, texture_path: Path) -> None:
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

        _hide_known_windows()
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            root = UsdGeom.Xform.Define(stage, CONTAINER_SCENE_PANEL_ROOT_PATH)
            root_prim = root.GetPrim()
            UsdGeom.Imageable(root_prim).MakeVisible()
            self._sync_scene_panel_pose(stage, root_prim, root_path)

            panel = UsdGeom.Mesh.Define(stage, CONTAINER_SCENE_PANEL_MESH_PATH)
            panel_prim = panel.GetPrim()
            half_w = CONTAINER_SCENE_PANEL_WIDTH_M * 0.5
            half_h = CONTAINER_SCENE_PANEL_HEIGHT_M * 0.5
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
            st.Set([Gf.Vec2f(u, v) for u, v in CONTAINER_SCENE_PANEL_UV_COORDS])
            panel_prim.CreateAttribute(
                "rtg:containerPanelTexture",
                Sdf.ValueTypeNames.Asset,
            ).Set(Sdf.AssetPath(str(texture_path)))

            material = UsdShade.Material.Define(stage, CONTAINER_SCENE_PANEL_MATERIAL_PATH)
            shader = UsdShade.Shader.Define(
                stage, f"{CONTAINER_SCENE_PANEL_MATERIAL_PATH}/PreviewSurface"
            )
            shader.CreateIdAttr("UsdPreviewSurface")
            shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.42)
            shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
            texture = UsdShade.Shader.Define(
                stage, f"{CONTAINER_SCENE_PANEL_MATERIAL_PATH}/Texture"
            )
            texture.CreateIdAttr("UsdUVTexture")
            texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(
                Sdf.AssetPath(str(texture_path))
            )
            texture.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
            reader = UsdShade.Shader.Define(
                stage, f"{CONTAINER_SCENE_PANEL_MATERIAL_PATH}/Primvar_st"
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

    def _hide_scene_panel(self, stage: Any) -> None:
        self._active_container_root_path = None
        try:
            from pxr import Usd, UsdGeom

            with Usd.EditContext(stage, stage.GetSessionLayer()):
                root = stage.GetPrimAtPath(CONTAINER_SCENE_PANEL_ROOT_PATH)
                if root:
                    UsdGeom.Imageable(root).MakeInvisible()
                guide = stage.GetPrimAtPath(CONTAINER_SCENE_GUIDE_LINE_PATH)
                if guide:
                    UsdGeom.Imageable(guide).MakeInvisible()
        except Exception:
            pass

    def _sync_scene_panel_pose(self, stage: Any, root_prim: Any, root_path: str) -> None:
        from pxr import Gf, Usd, UsdGeom

        translate = Gf.Vec3d(8.0, 0.0, 4.0)
        source_origin = None
        selected = stage.GetPrimAtPath(root_path) if root_path else None
        if selected:
            try:
                cache = UsdGeom.XformCache(Usd.TimeCode.Default())
                matrix = cache.GetLocalToWorldTransform(selected)
                origin = matrix.Transform(Gf.Vec3d(0.0, 0.0, 0.0))
                source_origin = origin
                translate = Gf.Vec3d(
                    float(origin[0]) + 1.2,
                    float(origin[1]) + CONTAINER_SCENE_PANEL_Y_OFFSET_M,
                    _container_panel_world_z(origin[2]),
                )
            except Exception:
                pass

        xform = UsdGeom.Xformable(root_prim)
        get_or_add_xform_op(
            xform,
            "xformOp:translate",
            xform.AddTranslateOp,
        ).Set(translate)

        rotate = Gf.Vec3f(
            *camera_facing_rotate_xyz(stage, translate, CONTAINER_SCENE_PANEL_ROTATE_XYZ)
        )
        get_or_add_xform_op(
            xform,
            "xformOp:rotateXYZ",
            xform.AddRotateXYZOp,
        ).Set(rotate)

        if source_origin is not None:
            source_bounds = self._container_world_bounds(stage, selected)
            if source_bounds is not None:
                self._sync_scene_panel_guide_line(stage, source_bounds, translate)
            else:
                self._hide_scene_panel_guide_line(stage)
        else:
            self._hide_scene_panel_guide_line(stage)

    def _sync_visible_guide_line(self) -> None:
        root_path = self._active_container_root_path
        if not root_path:
            return
        stage = _current_stage()
        if stage is None:
            return
        panel_root = stage.GetPrimAtPath(CONTAINER_SCENE_PANEL_ROOT_PATH)
        selected = stage.GetPrimAtPath(root_path)
        if not panel_root or not selected:
            return
        try:
            from pxr import UsdGeom

            panel_translate = None
            panel_xform = UsdGeom.Xformable(panel_root)
            for op in panel_xform.GetOrderedXformOps():
                if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                    panel_translate = op.Get()
                    break
            if panel_translate is None:
                return

            source_bounds = self._container_world_bounds(stage, selected)
            if source_bounds is not None:
                self._sync_scene_panel_guide_line(stage, source_bounds, panel_translate)
        except Exception:
            return

    @staticmethod
    def _container_world_bounds(stage: Any, selected: Any) -> Optional[Tuple[Any, Any]]:
        try:
            from pxr import Usd, UsdGeom

            cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
            aligned = cache.ComputeWorldBound(selected).ComputeAlignedBox()
            return aligned.GetMin(), aligned.GetMax()
        except Exception:
            return None

    def _sync_scene_panel_guide_line(
        self,
        stage: Any,
        container_bounds: Sequence[Sequence[Any]],
        panel_translate: Sequence[Any],
    ) -> None:
        try:
            from pxr import Gf, Usd, UsdGeom

            start, end = _container_panel_guide_points(container_bounds, panel_translate)
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                curve = UsdGeom.BasisCurves.Define(stage, CONTAINER_SCENE_GUIDE_LINE_PATH)
                curve_prim = curve.GetPrim()
                UsdGeom.Imageable(curve_prim).MakeVisible()
                curve.CreateTypeAttr("linear")
                curve.CreateWrapAttr("nonperiodic")
                curve.CreateCurveVertexCountsAttr([2])
                curve.CreatePointsAttr(
                    [
                        Gf.Vec3f(*start),
                        Gf.Vec3f(*end),
                    ]
                )
                curve.CreateWidthsAttr(
                    [
                        CONTAINER_SCENE_GUIDE_LINE_WIDTH_M,
                        CONTAINER_SCENE_GUIDE_LINE_WIDTH_M,
                    ]
                )
                curve.CreateDisplayColorAttr([Gf.Vec3f(0.46, 0.88, 1.0)])
                curve.CreateDisplayOpacityAttr([0.86])
        except Exception:
            return

    def _hide_scene_panel_guide_line(self, stage: Any) -> None:
        try:
            from pxr import Usd, UsdGeom

            with Usd.EditContext(stage, stage.GetSessionLayer()):
                guide = stage.GetPrimAtPath(CONTAINER_SCENE_GUIDE_LINE_PATH)
                if guide:
                    UsdGeom.Imageable(guide).MakeInvisible()
        except Exception:
            pass

    def _sync_visible_scene_panel_facing_camera(self) -> None:
        stage = _current_stage()
        if stage is None:
            return
        root = stage.GetPrimAtPath(CONTAINER_SCENE_PANEL_ROOT_PATH)
        if not root:
            return
        try:
            from pxr import Gf, Usd, UsdGeom

            xform = UsdGeom.Xformable(root)
            translate = self._scene_panel_world_translate(stage, root)
            if translate is None:
                return
            rotate = Gf.Vec3f(
                *camera_facing_rotate_xyz(
                    stage,
                    translate,
                    CONTAINER_SCENE_PANEL_ROTATE_XYZ,
                )
            )
            rotate_op = get_or_add_xform_op(
                xform,
                "xformOp:rotateXYZ",
                xform.AddRotateXYZOp,
            )
            rotate_op.Set(rotate)
            translate_op = existing_xform_op(xform, "xformOp:translate")
            if translate_op is not None:
                xform.SetXformOpOrder([translate_op, rotate_op])
        except Exception:
            return

    @staticmethod
    def _scene_panel_world_translate(stage: Any, root: Any) -> Any:
        """Return the panel origin in world coordinates for billboard facing."""

        try:
            from pxr import Usd, UsdGeom

            matrix = UsdGeom.XformCache(
                Usd.TimeCode.Default()
            ).GetLocalToWorldTransform(root)
            for name in ("ExtractTranslation", "GetTranslation"):
                method = getattr(matrix, name, None)
                if callable(method):
                    return method()
        except Exception:
            pass
        try:
            from pxr import UsdGeom

            xform = UsdGeom.Xformable(root)
            translate_op = existing_xform_op(xform, "xformOp:translate")
            return translate_op.Get() if translate_op is not None else None
        except Exception:
            return None

    def _clear_selection_gizmo(self) -> None:
        try:
            import omni.usd  # type: ignore

            self._clearing_selection = True
            selection = omni.usd.get_context().get_selection()
            try:
                selection.clear_selected_prim_paths()
            except Exception:
                selection.set_selected_prim_paths([], False)
        except Exception:
            pass
        finally:
            self._clearing_selection = False
            self._last_selection_signature = ()

    def _select_hud_root_for_dragging(self, paths: Sequence[str], root_path: str) -> None:
        """Make the scene panel root the active transform target without hiding it."""

        if tuple(paths) == (root_path,):
            return
        try:
            import omni.usd  # type: ignore

            self._normalizing_hud_selection = True
            selection = omni.usd.get_context().get_selection()
            try:
                selection.set_selected_prim_paths([root_path], False)
            except Exception:
                selection.clear_selected_prim_paths()
                selection.set_prim_path_selected(root_path, True, True, True, True)
            self._last_selection_signature = (root_path,)
        except Exception:
            pass
        finally:
            self._normalizing_hud_selection = False

    def _diagnose_idle_poll(self, paths: List[str], source: str) -> None:
        if paths or source != "poll" or self._idle_diagnostics_remaining <= 0:
            return
        now = time.time()
        if now - self._last_idle_diagnostic_at < DIAGNOSTIC_IDLE_POLL_INTERVAL_SECONDS:
            return
        self._last_idle_diagnostic_at = now
        self._idle_diagnostics_remaining -= 1
        self._log("poll alive; selected=[]")

    @staticmethod
    def _log(message: str) -> None:
        if DIAGNOSTIC_LOG_ENABLED:
            print(f"[RTG Container Info] {message}")


def start_rtg_container_info_popup() -> RtgContainerInfoPopupService:
    global _service
    _hide_known_windows()
    if _service is not None:
        _service.stop()
    previous = getattr(builtins, "_rtg_container_info_popup_service", None)
    if previous is not None and previous is not _service:
        try:
            previous.stop()
        except Exception:
            pass
    _service = RtgContainerInfoPopupService()
    builtins._rtg_container_info_popup_service = _service
    return _service


def stop_rtg_container_info_popup() -> None:
    global _service
    _hide_known_windows()
    if _service is not None:
        _service.stop()
        _service = None
    previous = getattr(builtins, "_rtg_container_info_popup_service", None)
    if previous is not None:
        try:
            previous.stop()
        except Exception:
            pass
        builtins._rtg_container_info_popup_service = None


if __name__ == "__main__":
    start_rtg_container_info_popup()
