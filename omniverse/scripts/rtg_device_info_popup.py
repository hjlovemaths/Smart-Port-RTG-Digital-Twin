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
        self._value_labels: List[Any] = []
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
            "RTG Device Info",
            width=380,
            height=360,
            visible=False,
            flags=flags,
        )
        try:
            self._window.position_x = 320
            self._window.position_y = 120
        except Exception:
            pass

        name_style = {"color": 0xFF93A4B4, "font_size": 13}
        value_style = {"color": 0xFFFFFFFF, "font_size": 13}
        title_style = {"color": 0xFFFFFFFF, "font_size": 17}

        with self._window.frame:
            with ui.VStack(spacing=6):
                self._title_label = ui.Label("RTG Device", height=28, style=title_style)
                self._value_labels.clear()
                for label in (
                    "Device",
                    "Payload",
                    "Field",
                    "Bay",
                    "Bay ID",
                    "Mode",
                    "Gantry",
                    "Trolley",
                    "Hoist",
                    "Tool Size",
                    "Locked",
                    "Selected",
                ):
                    with ui.HStack(spacing=8, height=24):
                        ui.Label(label, width=92, style=name_style)
                        value = ui.Label("--", word_wrap=True, style=value_style)
                        self._value_labels.append(value)

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
            self._title_label.text = f"RTG Device · {device if device != '--' else 'Selected'}"
        for label, row in zip(self._value_labels, rows):
            try:
                label.text = row.value
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
