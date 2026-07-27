"""RTG camera marker selection and RTSP preview popup for Omniverse Kit.

The RTG model contains small camera markers (usually named ``C1``, ``C2`` …).
This module watches the USD selection: selecting a marker opens a small
Omni.ui window and starts an RTSP reader for the matching camera in the RCMS
camera JSON. The default camera JSON is kept in the project at
``omniverse/config/cameras/Cam_RTG_220.json``.

Run in Omniverse Script Editor::

    exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_camera_popup.py", encoding="utf-8").read())
    start_rtg_camera_popup()

Stop with::

    stop_rtg_camera_popup()

OpenCV is used when it is available in the Kit Python environment.  If the
environment has no OpenCV RTSP backend, the popup remains useful (it shows the
resolved URL and the exact install/backend error) instead of breaking the RTG
driver.
"""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


OMNIVERSE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = OMNIVERSE_ROOT / "config" / "cameras" / "Cam_RTG_220.json"
LEGACY_CONFIG_PATH = Path(
    r"D:/dnt/project/RCMS/RCMS_RTG/RCMS_RTG/bin/Config/Cameras/Cam_RTG_220.json"
)
DEFAULT_RTSP_TEMPLATE = "rtsp://{user}:{password}@{ip}:{port}/axis-media/media.amp?camera={line}"
PRIMARY_RTG_PATH = "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC"
MARKER_ROOT = PRIMARY_RTG_PATH + "/RTG_CAMERA_MARKERS"
LEGACY_MARKER_ROOT = "/World/RTGCameraMarkers"
# Only a marker-like leaf is accepted.  Matching the complete USD path caused
# unrelated container/yard names containing "C<number>" to become red dots.
MARKER_NAME_RE = re.compile(
    r"^(?:(?:CAMERA|CAM)[_-]?C?|C)[_-]?(\d+)(?:[_-].*)?$", re.IGNORECASE
)
SELECTED_MARKER_RE = re.compile(
    r"(?:^|[_-])(?:CAMERA|CAM|MARKER)?[_-]?C?([0-9]{1,2})(?:$|[_-])",
    re.IGNORECASE,
)

_service: Optional["RtgCameraPopupService"] = None


@dataclass(frozen=True)
class CameraDefinition:
    seq: int
    ip: str
    port: int
    username: str
    password: str
    attached_line: str
    brand: str = ""
    location: str = ""
    rotate_angle: int = 0
    rtsp_port: int = 554
    rtsp_channel: Optional[str] = None

    @property
    def rtsp_url(self) -> str:
        # Axis accepts the media.amp endpoint for the RTSP stream.  Keep the
        # credentials URL-encoded so special characters do not corrupt the URL.
        from urllib.parse import quote

        user = quote(self.username, safe="")
        password = quote(self.password, safe="")
        brand = self.brand.strip().lower()
        if any(token in brand for token in ("hik", "haikang", "海康")):
            channel = self.rtsp_channel or str(max(int(self.attached_line or "1"), 1))
            # Hikvision/HaiKang main stream channel 1 is 101, channel 2 is 201.
            channel_id = channel if len(channel) >= 3 else f"{channel}01"
            return (
                f"rtsp://{user}:{password}@{self.ip}:{self.rtsp_port}"
                f"/Streaming/Channels/{channel_id}"
            )
        return DEFAULT_RTSP_TEMPLATE.format(
            user=user,
            password=password,
            ip=self.ip,
            port=self.rtsp_port,
            line=self.attached_line,
        )

    @property
    def rtsp_urls(self) -> List[str]:
        """Return the preferred stream URL plus a legacy-port fallback."""

        urls = [self.rtsp_url]
        if self.port != self.rtsp_port:
            urls.append(self.rtsp_url.replace(f":{self.rtsp_port}/", f":{self.port}/", 1))
        return urls


def _read_camera_config(path: Path) -> Dict[int, CameraDefinition]:
    if not path.exists():
        # Keep existing installations working while the project-local file is
        # being provisioned. The project path remains the normal/default path.
        if path == DEFAULT_CONFIG_PATH and LEGACY_CONFIG_PATH.exists():
            path = LEGACY_CONFIG_PATH
        else:
            print(f"[RTG Camera] config not found: {path}")
            return {}
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            payload = json.load(stream)
    except Exception as exc:
        print(f"[RTG Camera] config read failed: {path}: {exc}")
        return {}

    result: Dict[int, CameraDefinition] = {}
    for item in payload.get("CameraList", []) or []:
        try:
            seq = int(item.get("Seq"))
            ip = str(item.get("IP") or "").strip()
            if not seq or not ip:
                continue
            result[seq] = CameraDefinition(
                seq=seq,
                ip=ip,
                port=int(item.get("Port") or 554),
                username=str(item.get("UserName") or ""),
                password=str(item.get("Password") or ""),
                attached_line=str(item.get("AttachedLine") or seq),
                brand=str(item.get("Brand") or ""),
                location=str(item.get("Location") or ""),
                rotate_angle=int(item.get("RotateAngle") or 0),
                rtsp_port=int(
                    item.get("RtspPort")
                    or item.get("RTSPPort")
                    or item.get("rtspPort")
                    or 554
                ),
                rtsp_channel=(
                    str(item.get("RtspChannel") or item.get("RTSPChannel"))
                    if item.get("RtspChannel") or item.get("RTSPChannel")
                    else None
                ),
            )
        except (TypeError, ValueError):
            continue
    print(f"[RTG Camera] loaded {len(result)} camera definitions from {path}")
    return result


def _marker_number(path: str) -> Optional[int]:
    """Extract a marker number from a USD prim path."""

    # A viewport click often selects the mesh below the marker (for example
    # /.../C1/geometry), not the C1 transform itself.  Check every path
    # component, while keeping the strict marker-name regex so yard names such
    # as Container_C12 are not misclassified.
    for component in reversed([part for part in path.rstrip("/").split("/") if part]):
        match = MARKER_NAME_RE.match(component)
        if match is None:
            continue
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            return None
    return None


def _selected_marker_number(path: str) -> Optional[int]:
    """Resolve a clicked marker, allowing descriptive mesh child names."""

    strict = _marker_number(path)
    if strict is not None:
        return strict
    for component in reversed([part for part in path.rstrip("/").split("/") if part]):
        match = SELECTED_MARKER_RE.search(component)
        if match is None:
            continue
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            return None
    return None


def _safe_rtsp_url(url: str) -> str:
    """Hide credentials before a URL is written to the Kit UI or console."""

    return re.sub(r"(rtsp://[^:]+:)[^@]+(@)", r"\1***\2", url)


def _selected_paths() -> List[str]:
    try:
        import omni.usd  # type: ignore

        return list(omni.usd.get_context().get_selection().get_selected_prim_paths())
    except Exception:
        return []


class RtgRtspReader:
    """Decode one RTSP stream on a worker thread and expose JPEG frames."""

    def __init__(self, camera: CameraDefinition, on_frame: Any, on_status: Any) -> None:
        self.camera = camera
        self.on_frame = on_frame
        self.on_status = on_status
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"rtg-rtsp-{self.camera.seq}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        try:
            import cv2  # type: ignore
        except Exception as exc:
            self.on_status(
                "RTSP decoder unavailable in Kit Python. Install OpenCV with an RTSP backend. "
                f"({exc})"
            )
            return

        capture = None
        try:
            # FFMPEG is the most reliable OpenCV backend for Axis/HaiKang RTSP.
            for candidate in self.camera.rtsp_urls:
                capture = cv2.VideoCapture(candidate, cv2.CAP_FFMPEG)
                if capture and capture.isOpened():
                    break
                if capture is not None:
                    capture.release()
                capture = None
            if capture is None:
                attempted = ", ".join(_safe_rtsp_url(url) for url in self.camera.rtsp_urls)
                self.on_status(f"Cannot open RTSP stream; attempted: {attempted}")
                return
            self.on_status(f"Streaming {_safe_rtsp_url(candidate)}")
            while not self._stop.is_set():
                ok, frame = capture.read()
                if not ok or frame is None:
                    self.on_status("RTSP frame read failed; reconnecting…")
                    time.sleep(0.5)
                    continue
                # Keep the popup lightweight: JPEG encode happens off the Kit
                # thread and only bytes cross into the UI update callback.
                ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                if ok:
                    self.on_frame(bytes(encoded))
        except Exception as exc:
            self.on_status(f"RTSP error: {exc}")
        finally:
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    pass


class RtgCameraPopupService:
    def __init__(self, config_path: Optional[Path] = None) -> None:
        self.config_path = Path(config_path or os.environ.get("RTG_CAMERA_CONFIG", DEFAULT_CONFIG_PATH))
        self.cameras = _read_camera_config(self.config_path)
        self._reader: Optional[RtgRtspReader] = None
        self._selected_seq: Optional[int] = None
        self._selection_sub = None
        self._window = None
        self._image_provider = None
        self._status_label = None
        self._title_label = None
        self._last_frame: Optional[bytes] = None
        self._frame_lock = threading.Lock()
        self._frame_pending = False
        self._clearing_selection = False
        self._build_window()
        self._subscribe_selection()
        self._color_camera_markers()

    def stop(self) -> None:
        if self._reader:
            self._reader.stop()
            self._reader = None
        if self._selection_sub is not None:
            try:
                self._selection_sub.unsubscribe()
            except Exception:
                pass
            self._selection_sub = None
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
            print(f"[RTG Camera] omni.ui unavailable: {exc}")
            return
        flags = 0
        for name in (
            "WINDOW_FLAGS_NO_SCROLLBAR",
            "WINDOW_FLAGS_NO_COLLAPSE",
        ):
            flags |= getattr(ui, name, 0)
        self._window = ui.Window("RTG Camera", width=660, height=470, visible=False, flags=flags)
        try:
            self._window.position_x = 300
            self._window.position_y = 100
        except Exception:
            pass
        with self._window.frame:
            with ui.VStack(spacing=6):
                self._title_label = ui.Label("RTG Camera", height=24, style={"font_size": 16})
                self._image_provider = ui.ByteImageProvider()
                try:
                    ui.ImageWithProvider(self._image_provider, width=640, height=360)
                except Exception:
                    ui.Label("RTSP frame preview requires omni.ui.ByteImageProvider")
                self._status_label = ui.Label("Select a green camera marker", word_wrap=True, height=42)

    def _subscribe_selection(self) -> None:
        try:
            import omni.usd  # type: ignore

            stream = omni.usd.get_context().get_selection().get_changed_event_stream()
            self._selection_sub = stream.create_subscription_to_pop(self._on_selection_changed)
        except Exception as exc:
            print(f"[RTG Camera] selection subscription unavailable: {exc}")

    def _on_selection_changed(self, *_args: Any) -> None:
        if self._clearing_selection:
            return
        paths = _selected_paths()
        if paths:
            print(f"[RTG Camera] selected prim: {paths[0]}")
        for path in paths:
            seq = _selected_marker_number(path)
            if seq is None:
                continue
            if seq in self.cameras:
                self.open_camera(seq)
            else:
                self._show_unconfigured(seq, path)
            self._clear_selection_gizmo()
            return

    def _clear_selection_gizmo(self) -> None:
        """Clear the viewport selection after opening the camera popup."""

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

    def open_camera(self, seq: int) -> None:
        camera = self.cameras.get(seq)
        if camera is None:
            self._show_unconfigured(seq, f"C{seq}")
            return
        if self._reader:
            self._reader.stop()
        self._selected_seq = seq
        self._set_window_text(
            f"Camera C{seq} · {camera.brand or 'RTSP'}",
            f"Connecting…\n{_safe_rtsp_url(camera.rtsp_url)}",
        )
        if self._window is not None:
            self._window.visible = True
        self._reader = RtgRtspReader(camera, self._queue_frame, self._set_status)
        self._reader.start()

    def _show_unconfigured(self, seq: int, path: str) -> None:
        if self._reader:
            self._reader.stop()
            self._reader = None
        self._set_window_text(f"Camera C{seq} · UNCONFIGURED", f"No entry in Cam_RTG_220.json\n{path}")
        if self._window is not None:
            self._window.visible = True

    def _set_window_text(self, title: str, status: str) -> None:
        if self._title_label is not None:
            self._title_label.text = title
        self._set_status(status)

    def _set_status(self, status: str) -> None:
        if self._status_label is None:
            return
        try:
            self._status_label.text = str(status)
        except Exception:
            pass

    def _queue_frame(self, frame: bytes) -> None:
        with self._frame_lock:
            self._last_frame = frame
            if self._frame_pending:
                return
            self._frame_pending = True
        try:
            import omni.kit.app  # type: ignore

            omni.kit.app.get_app().next_update_async().add_done_callback(lambda _f: self._apply_frame())
        except Exception:
            self._apply_frame()

    def _apply_frame(self) -> None:
        with self._frame_lock:
            frame = self._last_frame
            self._frame_pending = False
        if not frame or self._image_provider is None:
            return
        try:
            import omni.ui as ui  # type: ignore

            # JPEG bytes are accepted by ByteImageProvider in current Kit builds.
            self._image_provider.set_bytes(frame, format=ui.ByteImageProvider.Format.JPEG)
        except Exception as exc:
            self._set_status(f"Frame received, preview update failed: {exc}")

    def _color_camera_markers(self) -> None:
        """Remove generated marker overlays and keep the authored RTG markers.

        The imported RTG already contains the authoritative C1-C33 marker
        geometry.  Earlier versions created extra spheres by guessing from USD
        object names; some of those objects had detached origins, which caused
        red dots to appear across the yard.  We now remove both old overlay
        roots and never create substitute geometry.  Selection/RTSP mapping
        still works against the authored markers.
        """
        try:
            import omni.usd  # type: ignore

            stage = omni.usd.get_context().get_stage()
            if stage is None:
                return
            for old_path in (MARKER_ROOT, LEGACY_MARKER_ROOT):
                old_root = stage.GetPrimAtPath(old_path)
                if old_root:
                    stage.RemovePrim(old_root.GetPath())
        except Exception as exc:
            print(f"[RTG Camera] marker overlay cleanup skipped: {exc}")


def start_rtg_camera_popup(config_path: Optional[str] = None) -> RtgCameraPopupService:
    global _service
    if _service is not None:
        _service.stop()
    _service = RtgCameraPopupService(Path(config_path) if config_path else None)
    return _service


def stop_rtg_camera_popup() -> None:
    global _service
    if _service is not None:
        _service.stop()
        _service = None


if __name__ == "__main__":
    start_rtg_camera_popup()
