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
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple


STATUS_PRIM_PATH = "/World/LiveTwinState"
PRIMARY_RTG_PATH = "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC"

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


def _status_prim(stage: Any) -> Any:
    try:
        return stage.GetPrimAtPath(STATUS_PRIM_PATH)
    except Exception:
        return None


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
        InfoRow("Selected", selected_path),
    ]
    return tuple(rows)


class RtgDeviceInfoPopupService:
    def __init__(self) -> None:
        self._selection_sub = None
        self._update_sub = None
        self._window = None
        self._title_label = None
        self._subtitle_label = None
        self._status_label = None
        self._labels: dict[str, Any] = {}
        self._last_selection_signature: tuple[str, ...] = ()
        self._build_window()
        self._subscribe_selection()
        self._subscribe_update_poll()

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

    def _build_window(self) -> None:
        try:
            import omni.ui as ui  # type: ignore
        except Exception as exc:
            print(f"[RTG Device Info] omni.ui unavailable: {exc}")
            return

        flags = 0
        for name in (
            "WINDOW_FLAGS_NO_SCROLLBAR",
            "WINDOW_FLAGS_NO_COLLAPSE",
        ):
            flags |= getattr(ui, name, 0)

        self._window = ui.Window(
            "RTG Digital Twin",
            width=560,
            height=430,
            visible=False,
            flags=flags,
        )
        try:
            self._window.position_x = 300
            self._window.position_y = 110
        except Exception:
            pass

        cjk_font = self._resolve_cjk_font(ui)

        def cjk_style(base: dict[str, Any]) -> dict[str, Any]:
            style = dict(base)
            if cjk_font:
                style["font"] = cjk_font
            return style

        shell_style = {
            "background_color": 0xEE071524,
            "border_color": 0xFF22DFFF,
            "border_width": 1,
            "border_radius": 4,
        }
        header_style = {
            "background_color": 0xF00B2135,
            "border_color": 0x8032E6FF,
            "border_width": 1,
            "border_radius": 3,
        }
        section_style = {
            "background_color": 0xC20E1C2A,
            "border_color": 0x4022DFFF,
            "border_width": 1,
            "border_radius": 3,
        }
        title_style = cjk_style({"color": 0xFFFFFFFF, "font_size": 22})
        subtitle_style = cjk_style({"color": 0xFF8FA8BC, "font_size": 13})
        badge_style = cjk_style({"color": 0xFF071524, "font_size": 13})
        section_title_style = cjk_style({"color": 0xFF22DFFF, "font_size": 13})
        metric_name_style = cjk_style({"color": 0xFF91A8B8, "font_size": 12})
        metric_value_style = cjk_style({"color": 0xFFFFFFFF, "font_size": 19})
        label_style = cjk_style({"color": 0xFF8094A8, "font_size": 12})
        value_style = cjk_style({"color": 0xFFEAF6FF, "font_size": 13})
        muted_style = cjk_style({"color": 0xFF70889A, "font_size": 11})
        alignment = getattr(ui, "Alignment", None)
        center_alignment = getattr(alignment, "CENTER", None) if alignment else None

        with self._window.frame:
            with ui.Frame(style=shell_style):
                with ui.VStack(spacing=8):
                    with ui.Frame(style=header_style, height=86):
                        with ui.HStack(spacing=10):
                            with ui.VStack(spacing=4):
                                self._title_label = ui.Label(
                                    "RTG",
                                    height=34,
                                    style=title_style,
                                )
                                self._subtitle_label = ui.Label(
                                    "数字孪生设备状态",
                                    height=22,
                                    style=subtitle_style,
                                )
                            with ui.VStack(width=128, spacing=3):
                                ui.Spacer(height=12)
                                with ui.Frame(
                                    style={
                                        "background_color": 0xFF22DFFF,
                                        "border_radius": 3,
                                    },
                                    height=28,
                                ):
                                    label_kwargs = {"style": badge_style}
                                    if center_alignment is not None:
                                        label_kwargs["alignment"] = center_alignment
                                    self._status_label = ui.Label("LIVE", **label_kwargs)
                                self._labels["Locked"] = ui.Label(
                                    "--",
                                    height=18,
                                    style=subtitle_style,
                                )

                    with ui.HStack(spacing=8, height=88):
                        self._metric_card(ui, "大车", "Gantry", metric_name_style, metric_value_style, section_style)
                        self._metric_card(ui, "小车", "Trolley", metric_name_style, metric_value_style, section_style)
                        self._metric_card(ui, "起升", "Hoist", metric_name_style, metric_value_style, section_style)

                    with ui.Frame(style=section_style, height=108):
                        with ui.VStack(spacing=5):
                            ui.Label("作业位置", height=20, style=section_title_style)
                            with ui.HStack(spacing=10):
                                self._field(ui, "堆场", "Field", label_style, value_style)
                                self._field(ui, "贝位", "Bay", label_style, value_style)
                                self._field(ui, "贝位号", "Bay ID", label_style, value_style)
                            with ui.HStack(spacing=10):
                                self._field(ui, "吊具尺寸", "Tool Size", label_style, value_style)
                                self._field(ui, "控制模式", "Mode", label_style, value_style)

                    with ui.Frame(style=section_style, height=112):
                        with ui.VStack(spacing=5):
                            ui.Label("载荷与选中部件", height=20, style=section_title_style)
                            self._row(ui, "载荷", "Payload", label_style, value_style)
                            self._row(ui, "选中", "Selected", label_style, muted_style)

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
                ui.Label(title, height=20, style=title_style)
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
            ui.Label(label, height=18, style=label_style)
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
            ui.Label(label, width=58, style=label_style)
            self._labels[key] = ui.Label("--", word_wrap=True, style=value_style)

    def _subscribe_selection(self) -> None:
        try:
            import omni.usd  # type: ignore

            stream = omni.usd.get_context().get_selection().get_changed_event_stream()
            self._selection_sub = stream.create_subscription_to_pop(self._on_selection_changed)
        except Exception as exc:
            print(f"[RTG Device Info] selection subscription unavailable: {exc}")

    def _subscribe_update_poll(self) -> None:
        try:
            import omni.kit.app  # type: ignore

            stream = omni.kit.app.get_app().get_update_event_stream()
            self._update_sub = stream.create_subscription_to_pop(self._on_update)
        except Exception as exc:
            print(f"[RTG Device Info] selection polling unavailable: {exc}")

    def _on_selection_changed(self, *_args: Any) -> None:
        self._handle_selected_paths(_selected_paths(), source="event")

    def _on_update(self, *_args: Any) -> None:
        self._handle_selected_paths(_selected_paths(), source="poll")

    def _handle_selected_paths(self, paths: List[str], source: str = "selection") -> None:
        signature = tuple(paths)
        if signature == self._last_selection_signature:
            return
        self._last_selection_signature = signature

        for path in paths:
            rtg_root = _rtg_root_path_from_selection(path)
            if rtg_root is None:
                continue
            print(f"[RTG Device Info] selected RTG prim ({source}): {path}")
            self.open_device(path)
            return
        self._hide()

    def open_device(self, selected_path: str) -> None:
        try:
            import omni.usd  # type: ignore

            stage = omni.usd.get_context().get_stage()
        except Exception as exc:
            self._show_message(f"Stage unavailable: {exc}")
            return
        if stage is None:
            self._show_message("Stage unavailable")
            return
        self._show_rows(_collect_device_info_rows(stage, selected_path))

    def _show_rows(self, rows: Sequence[InfoRow]) -> None:
        values = {row.label: row.value for row in rows}
        if self._title_label is not None:
            device = values.get("Device") or "RTG"
            self._title_label.text = device if device != "--" else "RTG"
        if self._subtitle_label is not None:
            bay_id = values.get("Bay ID") or "--"
            self._subtitle_label.text = f"数字孪生设备状态 · {bay_id}"
        if self._status_label is not None:
            try:
                self._status_label.text = values.get("Mode") or "LIVE"
            except Exception:
                pass
        for key, label in self._labels.items():
            if key == "Locked":
                text = f"锁具：{values.get(key, '--')}"
            else:
                text = values.get(key, "--")
            try:
                label.text = text
            except Exception:
                pass
        if self._window is not None:
            self._window.visible = True

    def _show_message(self, message: str) -> None:
        rows = [InfoRow("Device", message)] + [InfoRow("", "--") for _ in range(11)]
        self._show_rows(rows)

    def _hide(self) -> None:
        if self._window is not None:
            try:
                self._window.visible = False
            except Exception:
                pass


def start_rtg_device_info_popup() -> RtgDeviceInfoPopupService:
    global _service
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
