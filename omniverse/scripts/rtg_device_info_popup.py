"""Clickable RTG device information popup for Omniverse Kit.

Run in Omniverse Script Editor::

    exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_device_info_popup.py", encoding="utf-8").read())
    start_rtg_device_info_popup()

Stop with::

    stop_rtg_device_info_popup()

The service mirrors the DEVICE section of the RTG Twin HUD. Selecting any
visible prim under the primary RTG opens the popup; selecting a non-RTG prim
or clearing selection hides it.
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
from typing import Any, List, Optional, Sequence, Tuple

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


STATUS_PRIM_PATH = "/World/LiveTwinState"
PRIMARY_RTG_PATH = "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC"
PORT_AND_RTG_ROOT_NAME = "PortAndRTG"
UI_ASSET_DIR = Path(
    r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/assets/ui/rtg_device_panel"
)
if not UI_ASSET_DIR.is_dir():
    UI_ASSET_DIR = Path(__file__).resolve().parents[1] / "assets" / "ui" / "rtg_device_panel"
DEVICE_PANEL_TEMPLATE_PATH = UI_ASSET_DIR / "device_location_panel_bg.png"
HIGHLIGHT_OWNER = "device"
WINDOW_TITLE = "RTG Device Location"
LEGACY_WINDOW_TITLES = ("RTG Digital Twin", "RTG Device Location")
DIAGNOSTIC_LOG_ENABLED = False
DIAGNOSTIC_IDLE_POLL_INTERVAL_SECONDS = 3.0
DIAGNOSTIC_IDLE_POLL_LIMIT = 3
UPDATE_POLL_INTERVAL_SECONDS = 1.0 / 30.0
CJK_FONT_PATHS = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
)
LABEL_IMAGE_NAMES = {
    "设备编号": "label_device_no.png",
    "当前堆场": "label_field.png",
    "当前贝位": "label_bay.png",
    "大车位置": "label_gantry.png",
    "小车位置": "label_trolley.png",
    "吊具高度": "label_hoist.png",
}
DEVICE_PANEL_WIDTH = 768
DEVICE_PANEL_HEIGHT = 512
DEVICE_PANEL_VALUE_X = 724
DEVICE_PANEL_VALUE_Y = {
    "Device": 132,
    "Field": 196,
    "Bay": 260,
    "Gantry": 324,
    "Trolley": 388,
    "Hoist": 452,
}
DEVICE_SCENE_PANEL_ROOT_PATH = "/World/LiveHUD/RTG_DEVICE_LOCATION_PANEL"
DEVICE_SCENE_PANEL_MESH_PATH = f"{DEVICE_SCENE_PANEL_ROOT_PATH}/Panel"
DEVICE_SCENE_PANEL_MATERIAL_PATH = "/World/Looks/RTG_Device_Location_Panel"
DEVICE_SCENE_PANEL_WIDTH_M = 6.24
DEVICE_SCENE_PANEL_HEIGHT_M = 4.16
DEVICE_SCENE_PANEL_WORLD_X = -9.0
DEVICE_SCENE_PANEL_WORLD_Z = 12.0
DEVICE_SCENE_PANEL_Y_OFFSET_M = -2.2
DEVICE_SCENE_PANEL_ROTATE_XYZ = (0.0, 0.0, 180.0)
LIVE_HUD_ROOT_PATH = "/World/LiveHUD"
DEVICE_SCENE_HUD_SELECTABLE_PATH_PREFIXES = (DEVICE_SCENE_PANEL_ROOT_PATH,)
DEVICE_SCENE_PANEL_UV_COORDS = (
    (0.0, 0.0),
    (1.0, 0.0),
    (1.0, 1.0),
    (0.0, 1.0),
)

_service: Optional["RtgDeviceInfoPopupService"] = None


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


def _rtg_root_path_from_selection(path: str) -> Optional[str]:
    """Return the primary RTG root when a selected prim belongs to the RTG."""

    text = str(path or "").strip()
    if text == PRIMARY_RTG_PATH or text.startswith(f"{PRIMARY_RTG_PATH}/"):
        return PRIMARY_RTG_PATH
    parts = [part for part in text.split("/") if part]
    if parts and parts[-1] == PORT_AND_RTG_ROOT_NAME:
        return "/" + "/".join((*parts, "RTG_PRIMARY_DYNAMIC"))
    if "RTG_PRIMARY_DYNAMIC" in parts:
        index = parts.index("RTG_PRIMARY_DYNAMIC")
        return "/" + "/".join(parts[: index + 1])
    return None


def _is_live_hud_selection_path(path: str) -> bool:
    text = str(path or "").strip()
    return text == LIVE_HUD_ROOT_PATH or text.startswith(f"{LIVE_HUD_ROOT_PATH}/")


def _device_hud_root_from_selection(path: str) -> Optional[str]:
    text = str(path or "").strip()
    if any(
        text == prefix or text.startswith(f"{prefix}/")
        for prefix in DEVICE_SCENE_HUD_SELECTABLE_PATH_PREFIXES
    ):
        return DEVICE_SCENE_PANEL_ROOT_PATH
    return None


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


def _fmt_number(value: Any, places: int, suffix: str) -> str:
    try:
        if value is None:
            return "--"
        return f"{float(value):.{places}f}{suffix}"
    except (TypeError, ValueError):
        return "--"


def _fmt_int(value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "--"
    return str(number) if number > 0 else "--"


def _fmt_bool(value: Any) -> str:
    if value is None:
        return "--"
    return "YES" if bool(value) else "NO"


def _fmt_task_status(status: Any) -> str:
    task_id = _read_attr(status, "rtg:taskId")
    state = str(
        _read_attr(status, "rtg:operationState")
        or _read_attr(status, "rtg:visualPhase")
        or ""
    ).strip().upper()
    if state in {"COMPLETE", "COMPLETED", "DONE", "FINISHED"}:
        return "已完成"
    if state in {"FAULT", "ERROR", "ALARM"}:
        return "异常"
    if task_id not in (None, "", "--"):
        return "任务中"
    return "--"


def _fmt_work_type(value: Any) -> str:
    text = str(value or "").strip()
    upper = text.upper()
    return {
        "LOAD": "装箱",
        "UNLOAD": "卸箱",
        "SWITCH": "翻箱",
    }.get(upper, text or "--")


def _fmt_container_type(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "--"
    upper = text.upper()
    for prefix in ("20", "40", "45"):
        if upper.startswith(prefix) and len(upper) > len(prefix):
            return upper[len(prefix):]
    return text


def _fmt_height(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "--"
    try:
        normalized = text.lower().replace("mm", "").replace("m", "").strip()
        number = float(normalized)
        if "mm" in text.lower() or number > 20:
            number /= 1000.0
        return f"{number:.1f}"
    except (TypeError, ValueError):
        return text


def _find_cjk_font_path() -> Optional[Path]:
    for path in CJK_FONT_PATHS:
        candidate = Path(path)
        try:
            if candidate.is_file():
                return candidate
        except Exception:
            continue
    return None


def _render_text_bitmap(
    text: str,
    width: int,
    height: int,
    output_path: Path,
    *,
    font_size: int,
    color: tuple[int, int, int, int],
) -> bool:
    """Render CJK text to PNG so Kit's default label font is not involved."""

    font_path = _find_cjk_font_path()
    if font_path is None:
        return False
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore

        image = Image.new("RGBA", (int(width), int(height)), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(font_path), int(font_size))
        bbox = draw.textbbox((0, 0), text, font=font)
        text_height = bbox[3] - bbox[1]
        y = max(0, (height - text_height) // 2 - bbox[1])
        draw.text((0, y), text, fill=color, font=font)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path)
        return True
    except Exception:
        return False


def _render_device_panel_bitmap(values: dict[str, str], output_path: Path) -> bool:
    font_path = _find_cjk_font_path()
    if font_path is None:
        return False
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont  # type: ignore

        image = Image.new("RGBA", (DEVICE_PANEL_WIDTH, DEVICE_PANEL_HEIGHT), (0, 0, 0, 0))
        glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        panel_box = (1, 1, DEVICE_PANEL_WIDTH - 2, DEVICE_PANEL_HEIGHT - 2)
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
        draw.line((14, 14, DEVICE_PANEL_WIDTH - 15, 14), fill=(255, 156, 22, 232), width=4)
        draw.rectangle(
            (12, 12, DEVICE_PANEL_WIDTH - 13, DEVICE_PANEL_HEIGHT - 13),
            outline=(142, 219, 238, 50),
        )
        draw.polygon(
            [
                (DEVICE_PANEL_WIDTH - 68, DEVICE_PANEL_HEIGHT - 1),
                (DEVICE_PANEL_WIDTH - 1, DEVICE_PANEL_HEIGHT - 68),
                (DEVICE_PANEL_WIDTH - 1, DEVICE_PANEL_HEIGHT - 1),
            ],
            fill=(153, 206, 218, 112),
        )
        draw.rectangle((0, 0, DEVICE_PANEL_WIDTH, 86), fill=(22, 68, 91, 250))

        title_font = ImageFont.truetype(str(font_path), 38)
        label_font = ImageFont.truetype(str(font_path), 28)
        value_font = ImageFont.truetype(str(font_path), 31)
        device_font = ImageFont.truetype(str(font_path), 42)
        draw.text((36, 25), "设备定位", fill=(248, 252, 255, 255), font=title_font)

        label_by_key = {
            "Device": "设备编号",
            "Field": "当前堆场",
            "Bay": "当前贝位",
            "Gantry": "大车位置",
            "Trolley": "小车位置",
            "Hoist": "吊具高度",
        }
        for key, y in DEVICE_PANEL_VALUE_Y.items():
            text = values.get(key, "--")
            font = device_font if key == "Device" else value_font
            color = (88, 222, 248, 255) if key == "Device" else (246, 252, 255, 255)
            label = label_by_key.get(key, key)
            draw.text((40, y - 18), label, font=label_font, fill=(198, 221, 232, 255))
            max_width = 260 if key == "Device" else 220
            while len(text) > 2 and draw.textbbox((0, 0), text, font=font)[2] > max_width:
                text = text[:-2] + "..."
            bbox = draw.textbbox((0, 0), text, font=font)
            x = DEVICE_PANEL_VALUE_X - (bbox[2] - bbox[0])
            draw.text((x, y - (bbox[3] - bbox[1]) // 2), text, font=font, fill=color)
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
    previous = getattr(builtins, "_rtg_device_info_popup_service", None)
    if previous is not None:
        try:
            previous.stop()
        except Exception:
            pass
        try:
            builtins._rtg_device_info_popup_service = None
        except Exception:
            pass
    _hide_known_windows()


_stop_previous_builtin_service()


def _device_bay_id(status: Any) -> str:
    explicit = _read_attr(status, "rtg:currentBayId")
    if explicit:
        return _fmt(explicit)
    field = str(_read_attr(status, "rtg:currentField") or "").strip()
    bay = _read_attr(status, "rtg:currentBay")
    try:
        bay_int = int(bay)
    except (TypeError, ValueError):
        bay_int = 0
    return f"{field}/{bay_int:03d}" if field and bay_int > 0 else "--"


def _collect_device_info_rows(stage: Any, selected_path: str) -> Tuple[InfoRow, ...]:
    """Collect the DEVICE HUD values for a selected RTG prim."""

    status = _status_prim(stage)
    payload = (
        _read_attr(status, "rtg:manualPayload")
        or _read_attr(status, "rtg:carriedContainer")
        or ""
    )
    start_pos = _read_attr(status, "rtg:startPos") or _read_attr(status, "rtg:sourcePos")
    target_pos = _read_attr(status, "rtg:endPos") or _read_attr(status, "rtg:targetPos")
    rows = [
        InfoRow("Device", _fmt(_read_attr(status, "rtg:rtgName"))),
        InfoRow("Payload", _fmt(payload)),
        InfoRow("Field", _fmt(_read_attr(status, "rtg:currentField"))),
        InfoRow("Bay", _fmt_int(_read_attr(status, "rtg:currentBay"))),
        InfoRow("Bay ID", _device_bay_id(status)),
        InfoRow("Mode", _fmt(_read_attr(status, "rtg:controlMode") or "LIVE")),
        InfoRow("Gantry", _fmt_number(_read_attr(status, "rtg:gantryMeter"), 2, " m")),
        InfoRow("Trolley", _fmt_number(_read_attr(status, "rtg:trolleyMeter"), 2, " m")),
        InfoRow("Hoist", _fmt_number(_read_attr(status, "rtg:hoistMeter"), 2, " m")),
        InfoRow("Tool Size", _fmt_number(_read_attr(status, "rtg:spreaderSizeFt"), 0, " ft")),
        InfoRow("Locked", _fmt_bool(_read_attr(status, "rtg:spreaderLocked"))),
        InfoRow("Task Status", _fmt_task_status(status)),
        InfoRow("Task Type", _fmt(_read_attr(status, "rtg:taskType"))),
        InfoRow("Task ID", _fmt(_read_attr(status, "rtg:taskId"))),
        InfoRow("Start Pos", _fmt(start_pos)),
        InfoRow("Target Pos", _fmt(target_pos)),
        InfoRow("Work Type", _fmt_work_type(_read_attr(status, "rtg:workType"))),
        InfoRow("Container No", _fmt(_read_attr(status, "rtg:containerNo"))),
        InfoRow("Container Type", _fmt_container_type(_read_attr(status, "rtg:containerType"))),
        InfoRow("Container Height", _fmt_height(_read_attr(status, "rtg:containerHeight"))),
        InfoRow("Spreader Pos", _fmt(_read_attr(status, "rtg:spreaderPos"))),
        InfoRow("Car Type", _fmt(_read_attr(status, "rtg:carType"))),
        InfoRow("Car No", _fmt(_read_attr(status, "rtg:carNo"))),
        InfoRow("Container Size", _fmt(_read_attr(status, "rtg:containerSize"))),
        InfoRow("Bay Map", _fmt(_read_attr(status, "rtg:bayMap"))),
        InfoRow("Selected", selected_path),
    ]
    return tuple(rows)


class RtgDeviceInfoPopupService:
    def __init__(self) -> None:
        self._selection_sub = None
        self._update_sub = None
        self._window = None
        self._panel_image = None
        self._bitmap_seq = 0
        self._clearing_selection = False
        self._normalizing_hud_selection = False
        self._last_selection_signature: tuple[str, ...] = ()
        self._last_idle_diagnostic_at = 0.0
        self._last_update_poll_at = 0.0
        self._idle_diagnostics_remaining = DIAGNOSTIC_IDLE_POLL_LIMIT
        self._log("starting device popup service")
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
        self._hide()
        self._window = None
        _hide_known_windows()
        stage = _current_stage()
        if stage is not None:
            hide_selection_highlight(stage, owner=HIGHLIGHT_OWNER)
            self._hide_scene_panel(stage)

    def _build_window(self) -> None:
        _hide_known_windows()
        self._window = None
        self._panel_image = None

    @staticmethod
    def _resolve_cjk_font(ui: Any) -> str:
        try:
            registry = getattr(ui, "FontRegistry", None)
        except Exception:
            registry = None
        if registry is not None:
            for path in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"):
                try:
                    family = registry.add_font(path)
                    if family:
                        return str(family)
                except Exception:
                    continue
        return ""

    def _image_text(
        self,
        ui: Any,
        text: str,
        *,
        width: int,
        height: int,
        font_size: int,
        color: tuple[int, int, int, int],
        fallback_style: dict[str, Any],
    ) -> Any:
        self._bitmap_seq += 1
        path = (
            Path(tempfile.gettempdir())
            / f"smart_port_rtg_device_popup_{os.getpid()}_{self._bitmap_seq}.png"
        )
        if _render_text_bitmap(
            text,
            width,
            height,
            path,
            font_size=font_size,
            color=color,
        ):
            try:
                return ui.Image(
                    str(path),
                    width=width,
                    height=height,
                    fill_policy=ui.FillPolicy.STRETCH,
                )
            except Exception:
                pass
        return ui.Label(text, width=width, height=height, style=fallback_style)

    def _asset_image(
        self,
        ui: Any,
        filename: str,
        *,
        width: int,
        height: int,
        fallback_text: str,
        fallback_style: dict[str, Any],
    ) -> Any:
        path = UI_ASSET_DIR / filename
        try:
            if path.is_file():
                return ui.Image(
                    str(path),
                    width=width,
                    height=height,
                    fill_policy=ui.FillPolicy.STRETCH,
                )
        except Exception:
            pass
        return ui.Label(fallback_text, width=width, height=height, style=fallback_style)

    def _metric_card(
        self,
        ui: Any,
        title: str,
        key: str,
        title_style: dict[str, Any],
        value_style: dict[str, Any],
        frame_style: dict[str, Any],
    ) -> None:
        with ui.Frame(style=frame_style):
            with ui.VStack(spacing=5):
                self._image_text(
                    ui,
                    title,
                    width=42,
                    height=20,
                    font_size=12,
                    color=(145, 168, 184, 255),
                    fallback_style=title_style,
                )
                self._labels[key] = ui.Label("--", height=34, style=value_style)

    def _field(
        self,
        ui: Any,
        label: str,
        key: str,
        label_style: dict[str, Any],
        value_style: dict[str, Any],
    ) -> None:
        with ui.VStack(spacing=2):
            self._image_text(
                ui,
                label,
                width=60,
                height=18,
                font_size=12,
                color=(128, 148, 168, 255),
                fallback_style=label_style,
            )
            self._labels[key] = ui.Label("--", height=24, style=value_style)

    def _row(
        self,
        ui: Any,
        label: str,
        key: str,
        label_style: dict[str, Any],
        value_style: dict[str, Any],
    ) -> None:
        with ui.HStack(spacing=8, height=28):
            self._image_text(
                ui,
                label,
                width=58,
                height=22,
                font_size=12,
                color=(128, 148, 168, 255),
                fallback_style=label_style,
            )
            self._labels[key] = ui.Label("--", word_wrap=True, style=value_style)

    def _transparent_row(
        self,
        ui: Any,
        label: str,
        key: str,
        label_style: dict[str, Any],
        value_style: dict[str, Any],
    ) -> None:
        alignment = getattr(ui, "Alignment", None)
        right = getattr(alignment, "RIGHT_CENTER", None) if alignment else None
        value_kwargs = {"style": value_style}
        if right is not None:
            value_kwargs["alignment"] = right
        with ui.HStack(spacing=8, height=26):
            filename = LABEL_IMAGE_NAMES.get(label)
            if filename:
                self._asset_image(
                    ui,
                    filename,
                    width=108,
                    height=22,
                    fallback_text=label,
                    fallback_style=label_style,
                )
            else:
                self._image_text(
                    ui,
                    label,
                    width=108,
                    height=22,
                    font_size=13,
                    color=(150, 170, 188, 255),
                    fallback_style=label_style,
                )
            self._labels[key] = ui.Label("--", width=146, **value_kwargs)

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
            print(f"[RTG Device Info] selection polling unavailable: {exc}")

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
                    for root in (_device_hud_root_from_selection(path) for path in paths)
                    if root is not None
                ),
                None,
            )
            if hud_root is not None:
                self._select_hud_root_for_dragging(paths, hud_root)
            self._log("selection ignored: live HUD selected")
            return

        for path in actionable_paths:
            rtg_root = _rtg_root_path_from_selection(path)
            if rtg_root is None:
                continue
            self._log(f"selected RTG prim ({source}): {path}")
            self.open_device(path, rtg_root)
            self._clear_selection_gizmo()
            return
        if paths:
            self._log("selection ignored: no RTG_PRIMARY_DYNAMIC path found")
        stage = _current_stage()
        if stage is not None:
            hide_selection_highlight(stage, owner=HIGHLIGHT_OWNER)
        self._hide()

    def open_device(self, selected_path: str, rtg_root_path: Optional[str] = None) -> None:
        stage = _current_stage()
        if stage is None:
            self._show_message("Stage unavailable")
            return
        hide_selection_highlight(stage)
        self._log(
            "opening device popup "
            f"selected={selected_path} root={rtg_root_path or PRIMARY_RTG_PATH} "
            f"title={WINDOW_TITLE} window={self._window is not None}"
        )
        self._show_rows(_collect_device_info_rows(stage, selected_path))

    def _show_rows(self, rows: Sequence[InfoRow]) -> None:
        values = {row.label: row.value for row in rows}
        self._bitmap_seq += 1
        panel_path = (
            Path(tempfile.gettempdir())
            / f"smart_port_rtg_device_panel_{os.getpid()}_{self._bitmap_seq}.png"
        )
        stage = _current_stage()
        if stage is None:
            return
        if _render_device_panel_bitmap(values, panel_path):
            self._show_scene_panel(stage, panel_path)

    def _show_message(self, message: str) -> None:
        rows = [InfoRow("Device", message)] + [InfoRow("", "--") for _ in range(11)]
        self._show_rows(rows)

    def _hide(self) -> None:
        _hide_known_windows()
        if self._window is not None:
            try:
                self._window.visible = False
            except Exception:
                pass
        stage = _current_stage()
        if stage is not None:
            self._hide_scene_panel(stage)

    def _show_scene_panel(self, stage: Any, texture_path: Path) -> None:
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

        _hide_known_windows()
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            root = UsdGeom.Xform.Define(stage, DEVICE_SCENE_PANEL_ROOT_PATH)
            root_prim = root.GetPrim()
            UsdGeom.Imageable(root_prim).MakeVisible()
            self._sync_scene_panel_pose(stage, root_prim)

            panel = UsdGeom.Mesh.Define(stage, DEVICE_SCENE_PANEL_MESH_PATH)
            panel_prim = panel.GetPrim()
            half_w = DEVICE_SCENE_PANEL_WIDTH_M * 0.5
            half_h = DEVICE_SCENE_PANEL_HEIGHT_M * 0.5
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
            st.Set([Gf.Vec2f(u, v) for u, v in DEVICE_SCENE_PANEL_UV_COORDS])
            panel_prim.CreateAttribute(
                "rtg:devicePanelTexture",
                Sdf.ValueTypeNames.Asset,
            ).Set(Sdf.AssetPath(str(texture_path)))

            material = UsdShade.Material.Define(stage, DEVICE_SCENE_PANEL_MATERIAL_PATH)
            shader = UsdShade.Shader.Define(
                stage, f"{DEVICE_SCENE_PANEL_MATERIAL_PATH}/PreviewSurface"
            )
            shader.CreateIdAttr("UsdPreviewSurface")
            shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.42)
            shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
            texture = UsdShade.Shader.Define(
                stage, f"{DEVICE_SCENE_PANEL_MATERIAL_PATH}/Texture"
            )
            texture.CreateIdAttr("UsdUVTexture")
            texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(
                Sdf.AssetPath(str(texture_path))
            )
            texture.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
            reader = UsdShade.Shader.Define(
                stage, f"{DEVICE_SCENE_PANEL_MATERIAL_PATH}/Primvar_st"
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
        try:
            from pxr import Usd, UsdGeom

            with Usd.EditContext(stage, stage.GetSessionLayer()):
                root = stage.GetPrimAtPath(DEVICE_SCENE_PANEL_ROOT_PATH)
                if root:
                    UsdGeom.Imageable(root).MakeInvisible()
        except Exception:
            pass

    def _sync_scene_panel_pose(self, stage: Any, root_prim: Any) -> None:
        from pxr import Gf, UsdGeom

        y = 0.0
        status = _status_prim(stage)
        field = _read_attr(status, "rtg:currentField")
        bay = _read_attr(status, "rtg:currentBay")
        try:
            from build_live_containers import container_center_y_for_bay

            bay_number = int(bay)
            size_ft = 40 if bay_number % 2 == 0 else 20
            y = container_center_y_for_bay(str(field or "4C"), bay_number, size_ft)
        except Exception:
            pass

        xform = UsdGeom.Xformable(root_prim)
        translate = Gf.Vec3d(
            DEVICE_SCENE_PANEL_WORLD_X,
            y + DEVICE_SCENE_PANEL_Y_OFFSET_M,
            DEVICE_SCENE_PANEL_WORLD_Z,
        )
        for op in xform.GetOrderedXformOps():
            if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                op.Set(translate)
                break
        else:
            xform.AddTranslateOp().Set(translate)

        rotate = Gf.Vec3f(
            *camera_facing_rotate_xyz(stage, translate, DEVICE_SCENE_PANEL_ROTATE_XYZ)
        )
        for op in xform.GetOrderedXformOps():
            if op.GetOpName() == "xformOp:rotateXYZ":
                op.Set(rotate)
                break
        else:
            xform.AddRotateXYZOp().Set(rotate)

    def _sync_visible_scene_panel_facing_camera(self) -> None:
        stage = _current_stage()
        if stage is None:
            return
        root = stage.GetPrimAtPath(DEVICE_SCENE_PANEL_ROOT_PATH)
        if not root:
            return
        try:
            from pxr import Gf, UsdGeom

            xform = UsdGeom.Xformable(root)
            translate = None
            for op in xform.GetOrderedXformOps():
                if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                    translate = op.Get()
                    break
            if translate is None:
                return
            rotate = Gf.Vec3f(
                *camera_facing_rotate_xyz(
                    stage,
                    translate,
                    DEVICE_SCENE_PANEL_ROTATE_XYZ,
                )
            )
            for op in xform.GetOrderedXformOps():
                if op.GetOpName() == "xformOp:rotateXYZ":
                    op.Set(rotate)
                    return
            xform.AddRotateXYZOp().Set(rotate)
        except Exception:
            return

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

    def _select_rtg_root_for_viewport_frame(self, rtg_root_path: str) -> None:
        """Keep viewport focus on the full RTG so pressing F frames the machine."""

        try:
            import omni.usd  # type: ignore

            self._clearing_selection = True
            selection = omni.usd.get_context().get_selection()
            try:
                selection.set_selected_prim_paths([rtg_root_path], False)
            except Exception:
                selection.clear_selected_prim_paths()
                selection.set_prim_path_selected(
                    rtg_root_path,
                    True,
                    True,
                    True,
                    True,
                )
            self._last_selection_signature = (rtg_root_path,)
        except Exception:
            self._last_selection_signature = ()
        finally:
            self._clearing_selection = False

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
            print(f"[RTG Device Info] {message}")


def start_rtg_device_info_popup() -> RtgDeviceInfoPopupService:
    global _service
    _hide_known_windows()
    if _service is not None:
        _service.stop()
    previous = getattr(builtins, "_rtg_device_info_popup_service", None)
    if previous is not None and previous is not _service:
        try:
            previous.stop()
        except Exception:
            pass
    _service = RtgDeviceInfoPopupService()
    builtins._rtg_device_info_popup_service = _service
    return _service


def stop_rtg_device_info_popup() -> None:
    global _service
    _hide_known_windows()
    if _service is not None:
        _service.stop()
        _service = None
    previous = getattr(builtins, "_rtg_device_info_popup_service", None)
    if previous is not None:
        try:
            previous.stop()
        except Exception:
            pass
        builtins._rtg_device_info_popup_service = None


if __name__ == "__main__":
    start_rtg_device_info_popup()
