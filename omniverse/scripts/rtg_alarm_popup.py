"""RTG alarm prompt overlay for Omniverse Kit.

Run in Omniverse Script Editor::

    exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_alarm_popup.py", encoding="utf-8").read())
    start_rtg_alarm_popup()

Manual demo::

    show_rtg_alarm("大车防撞报警！")
    clear_rtg_alarm()

Future PLC/service integration can write these attributes to
``/World/LiveTwinState``:

    rtg:alarmActive  bool
    rtg:alarmStatus  int
    rtg:alarmMessage string
    rtg:alarmSeverity string

The popup renders Chinese text into a bitmap first, so it does not depend on
Omniverse Kit's Label font support.
"""

from __future__ import annotations

import builtins
import math
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


STATUS_PRIM_PATH = "/World/LiveTwinState"
DEFAULT_ALARM_MESSAGE = "大车防撞报警！"
DEFAULT_SEVERITY = "ALARM"
CJK_FONT_PATHS = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
)
ALARM_MANUAL_PAYLOAD_KEY = "_SMART_PORT_RTG_MANUAL_ALARM"

_service: Optional["RtgAlarmPopupService"] = None


@dataclass(frozen=True)
class AlarmPayload:
    active: bool
    message: str = DEFAULT_ALARM_MESSAGE
    severity: str = DEFAULT_SEVERITY
    timestamp: float = 0.0


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


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return int(value) != 0
    text = str(value).strip().upper()
    return text in {"1", "TRUE", "YES", "Y", "ON", "ALARM", "ACTIVE"}


def _status_prim(stage: Any) -> Any:
    try:
        return stage.GetPrimAtPath(STATUS_PRIM_PATH)
    except Exception:
        return None


def _manual_alarm_payload(now: Optional[float] = None) -> Optional[AlarmPayload]:
    payload = getattr(builtins, ALARM_MANUAL_PAYLOAD_KEY, None)
    if not isinstance(payload, dict):
        return None
    started_at = float(payload.get("startedAt") or 0.0)
    duration = float(payload.get("durationSeconds") or 0.0)
    current = time.time() if now is None else float(now)
    if duration > 0 and current - started_at > duration:
        try:
            delattr(builtins, ALARM_MANUAL_PAYLOAD_KEY)
        except Exception:
            builtins.__dict__.pop(ALARM_MANUAL_PAYLOAD_KEY, None)
        return None
    return AlarmPayload(
        active=True,
        message=str(payload.get("message") or DEFAULT_ALARM_MESSAGE),
        severity=str(payload.get("severity") or DEFAULT_SEVERITY),
        timestamp=started_at,
    )


def _collect_alarm_payload(stage: Any, now: Optional[float] = None) -> AlarmPayload:
    manual = _manual_alarm_payload(now)
    if manual is not None:
        return manual

    status = _status_prim(stage)
    alarm_status = _read_attr(status, "rtg:alarmStatus", 0)
    active = _boolish(_read_attr(status, "rtg:alarmActive", False)) or _boolish(alarm_status)
    message = str(_read_attr(status, "rtg:alarmMessage", "") or "").strip()
    severity = str(_read_attr(status, "rtg:alarmSeverity", "") or "").strip()
    return AlarmPayload(
        active=active,
        message=message or DEFAULT_ALARM_MESSAGE,
        severity=severity or DEFAULT_SEVERITY,
        timestamp=float(now if now is not None else time.time()),
    )


def _find_cjk_font_path() -> Optional[Path]:
    for path in CJK_FONT_PATHS:
        candidate = Path(path)
        try:
            if candidate.is_file():
                return candidate
        except Exception:
            continue
    return None


def _burst_points(
    center_x: float,
    center_y: float,
    inner_radius: float,
    outer_radius: float,
    count: int,
) -> list[tuple[float, float]]:
    points = []
    for index in range(count * 2):
        radius = outer_radius if index % 2 == 0 else inner_radius
        angle = -math.pi * 0.5 + index * math.pi / count
        points.append((center_x + math.cos(angle) * radius, center_y + math.sin(angle) * radius))
    return points


def _render_alarm_bitmap(
    message: str,
    output_path: Path,
    *,
    width: int = 460,
    height: int = 190,
) -> bool:
    """Render a comic-style alarm badge as a PNG."""

    font_path = _find_cjk_font_path()
    if font_path is None:
        return False
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore

        image = Image.new("RGBA", (int(width), int(height)), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        # White rounded backing like the user's visual reference.
        draw.rounded_rectangle(
            (6, 6, width - 6, height - 6),
            radius=18,
            fill=(250, 250, 248, 238),
            outline=(220, 224, 228, 255),
            width=2,
        )

        cx = width * 0.50
        cy = height * 0.52
        burst_outer = _burst_points(cx, cy, 76, 112, 22)
        burst_inner = _burst_points(cx, cy, 62, 96, 22)
        draw.polygon(burst_outer, fill=(20, 20, 20, 255))
        draw.polygon(burst_inner, fill=(238, 18, 31, 255))

        # Speed lines.
        for angle_deg in (-28, -16, 15, 29, 153, 166, 195, 208):
            angle = math.radians(angle_deg)
            start = (cx + math.cos(angle) * 96, cy + math.sin(angle) * 70)
            end = (cx + math.cos(angle) * 225, cy + math.sin(angle) * 155)
            draw.line((start, end), fill=(0, 0, 0, 255), width=3)

        # Yellow lightning shards.
        lightning = (255, 222, 0, 255)
        outline = (20, 20, 20, 255)
        shards = (
            [(82, 52), (124, 78), (108, 85), (132, 122), (76, 84)],
            [(350, 52), (325, 90), (341, 88), (311, 132), (373, 79)],
        )
        for shard in shards:
            draw.line(shard + [shard[0]], fill=outline, width=8, joint="curve")
            draw.polygon(shard, fill=lightning)

        font = ImageFont.truetype(str(font_path), 42)
        text = str(message or DEFAULT_ALARM_MESSAGE)
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=3)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (width - text_width) * 0.5 - bbox[0]
        y = cy - text_height * 0.5 - bbox[1] + 6
        draw.text(
            (x, y),
            text,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=5,
            stroke_fill=(0, 0, 0, 255),
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path)
        return True
    except Exception as exc:
        print(f"[RTG Alarm] alarm bitmap render failed: {exc}")
        return False


class RtgAlarmPopupService:
    def __init__(self) -> None:
        self._window = None
        self._image = None
        self._fallback_label = None
        self._update_sub = None
        self._last_signature: tuple[str, str] = ("", "")
        self._bitmap_seq = 0
        self._build_window()
        self._subscribe_update_poll()

    def stop(self) -> None:
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
            print(f"[RTG Alarm] omni.ui unavailable: {exc}")
            return

        flags = 0
        for name in (
            "WINDOW_FLAGS_NO_TITLE_BAR",
            "WINDOW_FLAGS_NO_RESIZE",
            "WINDOW_FLAGS_NO_SCROLLBAR",
            "WINDOW_FLAGS_NO_COLLAPSE",
        ):
            flags |= getattr(ui, name, 0)

        self._window = ui.Window(
            "RTG Alarm",
            width=480,
            height=210,
            visible=False,
            flags=flags,
        )
        try:
            self._window.position_x = 780
            self._window.position_y = 260
        except Exception:
            pass

        with self._window.frame:
            with ui.VStack(spacing=2):
                self._image = ui.Image(
                    "",
                    width=460,
                    height=190,
                    fill_policy=ui.FillPolicy.STRETCH,
                )
                self._fallback_label = ui.Label(
                    DEFAULT_ALARM_MESSAGE,
                    height=1,
                    visible=False,
                    style={"color": 0xFFFF3333, "font_size": 28},
                )

    def _subscribe_update_poll(self) -> None:
        try:
            import omni.kit.app  # type: ignore

            stream = omni.kit.app.get_app().get_update_event_stream()
            self._update_sub = stream.create_subscription_to_pop(self._on_update)
        except Exception as exc:
            print(f"[RTG Alarm] update polling unavailable: {exc}")

    def _on_update(self, *_args: Any) -> None:
        try:
            import omni.usd  # type: ignore

            stage = omni.usd.get_context().get_stage()
        except Exception:
            stage = None
        if stage is None:
            self._hide()
            return
        payload = _collect_alarm_payload(stage)
        if payload.active:
            self.show(payload.message, payload.severity)
        else:
            self._hide()

    def show(self, message: str = DEFAULT_ALARM_MESSAGE, severity: str = DEFAULT_SEVERITY) -> None:
        signature = (str(message or DEFAULT_ALARM_MESSAGE), str(severity or DEFAULT_SEVERITY))
        if signature != self._last_signature:
            self._last_signature = signature
            self._bitmap_seq += 1
            path = (
                Path(tempfile.gettempdir())
                / f"smart_port_rtg_alarm_{os.getpid()}_{self._bitmap_seq}.png"
            )
            if _render_alarm_bitmap(signature[0], path) and self._image is not None:
                try:
                    self._image.source_url = str(path)
                except Exception:
                    pass
            elif self._fallback_label is not None:
                try:
                    self._fallback_label.text = signature[0]
                    self._fallback_label.visible = True
                except Exception:
                    pass
        if self._window is not None:
            self._window.visible = True

    def _hide(self) -> None:
        if self._window is not None:
            try:
                self._window.visible = False
            except Exception:
                pass


def start_rtg_alarm_popup() -> RtgAlarmPopupService:
    global _service
    if _service is not None:
        _service.stop()
    previous = getattr(builtins, "_rtg_alarm_popup_service", None)
    if previous is not None and previous is not _service:
        try:
            previous.stop()
        except Exception:
            pass
    _service = RtgAlarmPopupService()
    builtins._rtg_alarm_popup_service = _service
    return _service


def stop_rtg_alarm_popup() -> None:
    global _service
    if _service is not None:
        _service.stop()
        _service = None
    previous = getattr(builtins, "_rtg_alarm_popup_service", None)
    if previous is not None:
        try:
            previous.stop()
        except Exception:
            pass
        builtins._rtg_alarm_popup_service = None


def show_rtg_alarm(
    message: str = DEFAULT_ALARM_MESSAGE,
    *,
    severity: str = DEFAULT_SEVERITY,
    duration_seconds: float = 0.0,
) -> RtgAlarmPopupService:
    builtins.__dict__[ALARM_MANUAL_PAYLOAD_KEY] = {
        "message": str(message or DEFAULT_ALARM_MESSAGE),
        "severity": str(severity or DEFAULT_SEVERITY),
        "startedAt": time.time(),
        "durationSeconds": float(duration_seconds or 0.0),
    }
    service = _service or start_rtg_alarm_popup()
    service.show(message, severity)
    return service


def clear_rtg_alarm() -> None:
    builtins.__dict__.pop(ALARM_MANUAL_PAYLOAD_KEY, None)
    if _service is not None:
        _service._hide()


if __name__ == "__main__":
    start_rtg_alarm_popup()
