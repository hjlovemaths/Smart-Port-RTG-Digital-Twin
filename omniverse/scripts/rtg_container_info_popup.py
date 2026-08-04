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
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence, Tuple


STATUS_PRIM_PATH = "/World/LiveTwinState"
LIVE_HOISTED_LOAD_PATH = "/World/LiveContainers/LiveTwinHoistedContainer"
LEGACY_HOISTED_LOAD_SUFFIX = "/LIVE_TwinHoistedContainer"
LIVE_TASK_TRUCK_PATH = "/World/LiveContainers/LiveTaskTruck"
LIVE_TASK_STACK_PATH = "/World/LiveContainers/LiveTaskStack"
YARD_INVENTORY_ROOT_PATH = "/World/LiveContainers/YardInventory"

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


def _status_prim(stage: Any) -> Any:
    try:
        return stage.GetPrimAtPath(STATUS_PRIM_PATH)
    except Exception:
        return None


def _prim_at(stage: Any, path: str) -> Any:
    try:
        return stage.GetPrimAtPath(path)
    except Exception:
        return None


def _collect_info_rows(stage: Any, root_path: str) -> Tuple[InfoRow, ...]:
    """Collect display rows for a selected RTG container."""

    root = _prim_at(stage, root_path)
    body = _prim_at(stage, f"{root_path}/Body")
    status = _status_prim(stage)
    prims = [root, body]

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

    rows = [
        InfoRow("Container", _fmt(_read_attr(status, "rtg:containerNo"))),
        InfoRow("Type", _fmt(_read_attr(status, "rtg:containerType"))),
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
        self._title_label = None
        self._value_labels: List[Any] = []
        self._clearing_selection = False
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
        if self._window is not None:
            try:
                self._window.visible = False
            except Exception:
                pass
            self._window = None

    def _build_window(self) -> None:
        try:
            import omni.ui as ui  # type: ignore
        except Exception as exc:
            print(f"[RTG Container Info] omni.ui unavailable: {exc}")
            return

        flags = 0
        for name in (
            "WINDOW_FLAGS_NO_SCROLLBAR",
            "WINDOW_FLAGS_NO_COLLAPSE",
        ):
            flags |= getattr(ui, name, 0)

        self._window = ui.Window(
            "RTG Container Info",
            width=420,
            height=520,
            visible=False,
            flags=flags,
        )
        try:
            self._window.position_x = 360
            self._window.position_y = 120
        except Exception:
            pass

        name_style = {"color": 0xFF93A4B4, "font_size": 13}
        value_style = {"color": 0xFFFFFFFF, "font_size": 13}
        title_style = {"color": 0xFFFFFFFF, "font_size": 17}

        with self._window.frame:
            with ui.VStack(spacing=6):
                self._title_label = ui.Label("Container", height=28, style=title_style)
                self._value_labels.clear()
                for label in (
                    "Container",
                    "Type",
                    "Size",
                    "Height",
                    "Task ID",
                    "Work Type",
                    "State",
                    "Source Pos",
                    "Target Pos",
                    "Bay",
                    "Row / Tier",
                    "Bay Map",
                    "Locked",
                    "Path",
                    "Source",
                ):
                    with ui.HStack(spacing=8, height=24):
                        ui.Label(label, width=104, style=name_style)
                        value = ui.Label("--", word_wrap=True, style=value_style)
                        self._value_labels.append(value)

    def _subscribe_selection(self) -> None:
        try:
            import omni.usd  # type: ignore

            stream = omni.usd.get_context().get_selection().get_changed_event_stream()
            self._selection_sub = stream.create_subscription_to_pop(self._on_selection_changed)
        except Exception as exc:
            print(f"[RTG Container Info] selection subscription unavailable: {exc}")

    def _subscribe_update_poll(self) -> None:
        try:
            import omni.kit.app  # type: ignore

            stream = omni.kit.app.get_app().get_update_event_stream()
            self._update_sub = stream.create_subscription_to_pop(self._on_update)
        except Exception as exc:
            print(f"[RTG Container Info] selection polling unavailable: {exc}")

    def _on_selection_changed(self, *_args: Any) -> None:
        self._handle_selected_paths(_selected_paths(), source="event")

    def _on_update(self, *_args: Any) -> None:
        self._handle_selected_paths(_selected_paths(), source="poll")

    def _handle_selected_paths(self, paths: List[str], source: str = "selection") -> None:
        if self._clearing_selection:
            return
        signature = tuple(paths)
        if signature == self._last_selection_signature:
            return
        self._last_selection_signature = signature

        for path in paths:
            root_path = _container_root_path_from_selection(path)
            if root_path is None:
                continue
            print(f"[RTG Container Info] selected container ({source}): {root_path}")
            self.open_container(root_path)
            self._clear_selection_gizmo()
            return

    def open_container(self, root_path: str) -> None:
        try:
            import omni.usd  # type: ignore

            stage = omni.usd.get_context().get_stage()
        except Exception as exc:
            self._show_message(f"Stage unavailable: {exc}")
            return
        if stage is None:
            self._show_message("Stage unavailable")
            return
        rows = _collect_info_rows(stage, root_path)
        self._show_rows(rows)

    def _show_rows(self, rows: Sequence[InfoRow]) -> None:
        if self._title_label is not None and rows:
            first = rows[0].value
            self._title_label.text = f"Container · {first if first != '--' else 'Selected'}"
        for label, row in zip(self._value_labels, rows):
            try:
                label.text = row.value
            except Exception:
                pass
        if self._window is not None:
            self._window.visible = True

    def _show_message(self, message: str) -> None:
        rows = [InfoRow("Container", message)] + [InfoRow("", "--") for _ in range(14)]
        self._show_rows(rows)

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


def start_rtg_container_info_popup() -> RtgContainerInfoPopupService:
    global _service
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
