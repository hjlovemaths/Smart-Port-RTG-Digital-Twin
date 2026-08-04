"""Start Omniverse WebRTC streaming with only the RTX viewport visible.

Run in Omniverse Kit Script Editor after opening smart_port.usda:

    exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_viewport_only_livestream.py", encoding="utf-8").read())
    start_viewport_only_livestream()

Open the stream in a browser or WPF WebView2:

    http://localhost:8011

Restore the normal Kit UI:

    restore_kit_ui()
"""

from __future__ import annotations

import builtins
from typing import Any


DEFAULT_HTTP_PORT = 8011
DEFAULT_SIGNAL_PORT = 49100
DEFAULT_STREAM_PORT = 47998
DEFAULT_TARGET_FPS = 30
DEFAULT_STREAM_WIDTH = 1920
DEFAULT_STREAM_HEIGHT = 1080
QHD_2K_WIDTH = 2560
QHD_2K_HEIGHT = 1440
PRIMARY_RTG_PATH = "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC"


def _clear_viewport_selection() -> None:
    try:
        import omni.usd  # type: ignore

        selection = omni.usd.get_context().get_selection()
        try:
            selection.clear_selected_prim_paths()
        except Exception:
            selection.set_selected_prim_paths([], False)
    except Exception:
        pass


def _set_viewport_read_only(enabled: bool = True) -> None:
    """Best-effort Kit-side read-only mode for WebRTC display clients."""

    import carb.settings  # type: ignore

    settings = carb.settings.get_settings()
    read_only = bool(enabled)
    settings.set("/app/window/drawMouse", not read_only)
    settings.set("/persistent/app/viewport/coiDoubleClick", not read_only)
    settings.set("/persistent/app/viewport/coiDoubleClickObjectOnly", True)
    settings.set("/persistent/app/viewport/pickingMode", "type:None" if read_only else "type:ALL")
    settings.set("/persistent/app/viewport/pickingModeNoKinds", True)
    settings.set("/persistent/app/viewport/pickingModeIncludeRef", True)
    settings.set("/exts/omni.kit.viewport.window/startup/cameraManipulator/enabled", not read_only)
    settings.set("/exts/omni.kit.viewport.window/startup/selectionManipulator/enabled", not read_only)

    previous = getattr(builtins, "_rtg_viewport_read_only_sub", None)
    if previous is not None:
        try:
            previous.unsubscribe()
        except Exception:
            pass
        builtins._rtg_viewport_read_only_sub = None

    if not read_only:
        _enable_existing_viewport_interaction()
        return

    try:
        import omni.kit.app  # type: ignore

        stream = omni.kit.app.get_app().get_update_event_stream()
        builtins._rtg_viewport_read_only_sub = stream.create_subscription_to_pop(
            lambda *_args: _clear_viewport_selection()
        )
    except Exception as exc:
        print(f"[RTG Viewport Stream] read-only selection guard unavailable: {exc}")


def _enable_existing_viewport_interaction() -> None:
    """Re-enable interaction on already-created viewport windows where possible."""

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


def _viewport_window_titles() -> tuple[str, ...]:
    try:
        import omni.ui as ui  # type: ignore

        titles = []
        for window in ui.Workspace.get_windows():
            title = str(getattr(window, "title", "") or window)
            if "viewport" in title.lower():
                titles.append(title)
        return tuple(titles) or ("Viewport",)
    except Exception:
        return ("Viewport",)


def _fit_viewport_to_main_window(width: int | None = None, height: int | None = None) -> None:
    """Move the Viewport window to fill the Kit framebuffer."""

    try:
        import omni.ui as ui  # type: ignore
    except Exception as exc:
        print(f"[RTG Viewport Stream] viewport resize unavailable: {exc}")
        return

    target_width = int(width or ui.Workspace.get_main_window_width() or DEFAULT_STREAM_WIDTH)
    target_height = int(height or ui.Workspace.get_main_window_height() or DEFAULT_STREAM_HEIGHT)

    for title in _viewport_window_titles():
        try:
            ui.Workspace.show_window(title, True)
        except Exception:
            pass
        window = None
        try:
            window = ui.Workspace.get_window(title)
        except Exception:
            pass
        if window is None:
            continue
        for attr, value in (
            ("visible", True),
            ("position_x", 0),
            ("position_y", 0),
            ("width", target_width),
            ("height", target_height),
            ("noTabBar", True),
            ("padding_x", 0),
            ("padding_y", 0),
        ):
            try:
                setattr(window, attr, value)
            except Exception:
                pass


def _schedule_viewport_fit(width: int, height: int, frames: int = 30) -> None:
    """Repeat viewport fitting while Kit finishes applying hideUi/layout changes."""

    previous = getattr(builtins, "_rtg_viewport_fit_sub", None)
    if previous is not None:
        try:
            previous.unsubscribe()
        except Exception:
            pass
        builtins._rtg_viewport_fit_sub = None

    try:
        import omni.kit.app  # type: ignore

        state = {"remaining": int(frames)}

        def _on_update(*_args: Any) -> None:
            _fit_viewport_to_main_window(width, height)
            state["remaining"] -= 1
            if state["remaining"] <= 0:
                sub = getattr(builtins, "_rtg_viewport_fit_sub", None)
                if sub is not None:
                    try:
                        sub.unsubscribe()
                    except Exception:
                        pass
                    builtins._rtg_viewport_fit_sub = None

        stream = omni.kit.app.get_app().get_update_event_stream()
        builtins._rtg_viewport_fit_sub = stream.create_subscription_to_pop(_on_update)
    except Exception as exc:
        print(f"[RTG Viewport Stream] viewport fit scheduler unavailable: {exc}")
        _fit_viewport_to_main_window(width, height)


def _enable_extension(ext_id: str) -> None:
    import omni.kit.app  # type: ignore

    manager = omni.kit.app.get_app().get_extension_manager()
    manager.set_extension_enabled_immediate(ext_id, True)


def _configure_livestream(
    http_port: int = DEFAULT_HTTP_PORT,
    signal_port: int = DEFAULT_SIGNAL_PORT,
    stream_port: int = DEFAULT_STREAM_PORT,
    target_fps: int = DEFAULT_TARGET_FPS,
    width: int = DEFAULT_STREAM_WIDTH,
    height: int = DEFAULT_STREAM_HEIGHT,
) -> None:
    import carb.settings  # type: ignore

    settings = carb.settings.get_settings()
    settings.set("/exts/omni.kit.livestream.app/primaryStream/streamType", "webrtc")
    settings.set("/exts/omni.kit.livestream.app/primaryStream/signalPort", int(signal_port))
    settings.set("/exts/omni.kit.livestream.app/primaryStream/streamPort", int(stream_port))
    settings.set("/exts/omni.kit.livestream.app/primaryStream/targetFps", int(target_fps))
    settings.set("/exts/omni.kit.livestream.app/primaryStream/allowDynamicResize", True)
    settings.set("/app/window/width", int(width))
    settings.set("/app/window/height", int(height))
    settings.set("/exts/omni.services.transport.server.http/port", int(http_port))


def _hide_non_viewport_windows() -> None:
    """Best-effort fallback for Kit builds where /app/window/hideUi is partial."""

    try:
        import omni.ui as ui  # type: ignore
    except Exception:
        return

    keep_tokens = ("viewport",)
    for window in ui.Workspace.get_windows():
        title = str(getattr(window, "title", "") or window)
        should_keep = any(token in title.lower() for token in keep_tokens)
        try:
            if should_keep:
                window.visible = True
            else:
                window.visible = False
        except Exception:
            try:
                ui.Workspace.show_window(title, should_keep)
            except Exception:
                pass


def _set_viewport_camera(camera_path: str | None = None) -> None:
    if not camera_path:
        return
    try:
        from omni.kit.viewport.utility import get_active_viewport  # type: ignore

        viewport = get_active_viewport()
        if viewport is not None:
            viewport.camera_path = camera_path
    except Exception as exc:
        print(f"[RTG Viewport Stream] camera bind skipped: {exc}")


def start_viewport_only_livestream(
    http_port: int = DEFAULT_HTTP_PORT,
    signal_port: int = DEFAULT_SIGNAL_PORT,
    stream_port: int = DEFAULT_STREAM_PORT,
    target_fps: int = DEFAULT_TARGET_FPS,
    width: int = DEFAULT_STREAM_WIDTH,
    height: int = DEFAULT_STREAM_HEIGHT,
    camera_path: str | None = None,
    hide_kit_ui: bool = True,
    read_only: bool = True,
) -> str:
    """Enable WebRTC streaming and show only the RTX viewport in the stream."""

    import carb.settings  # type: ignore

    _configure_livestream(http_port, signal_port, stream_port, target_fps, width, height)

    for ext_id in (
        "omni.kit.livestream.webrtc",
        "omni.kit.livestream.app",
        "omni.services.transport.server.http",
        "omni.services.livestream.webrtc",
    ):
        _enable_extension(ext_id)

    _set_viewport_camera(camera_path)

    settings = carb.settings.get_settings()
    settings.set("/app/window/hideUi", bool(hide_kit_ui))
    settings.set("/app/docks/disabled", bool(hide_kit_ui))
    settings.set("/app/viewport/defaults/noTitleBar", bool(hide_kit_ui))
    _set_viewport_read_only(read_only)
    if hide_kit_ui:
        _hide_non_viewport_windows()
        _schedule_viewport_fit(int(width), int(height))

    url = f"http://localhost:{int(http_port)}"
    print(f"[RTG Viewport Stream] viewport-only WebRTC page: {url}")
    if read_only:
        print("[RTG Viewport Stream] Kit-side read-only guard enabled")
    print("[RTG Viewport Stream] restore Kit UI with restore_kit_ui()")
    return url


def restore_kit_ui() -> None:
    """Restore normal Kit docking/UI after viewport-only streaming setup."""

    import carb.settings  # type: ignore

    settings = carb.settings.get_settings()
    settings.set("/app/window/hideUi", False)
    settings.set("/app/docks/disabled", False)
    settings.set("/app/viewport/defaults/noTitleBar", False)
    _set_viewport_read_only(False)
    previous = getattr(builtins, "_rtg_viewport_fit_sub", None)
    if previous is not None:
        try:
            previous.unsubscribe()
        except Exception:
            pass
        builtins._rtg_viewport_fit_sub = None
    try:
        import omni.ui as ui  # type: ignore

        for title in (
            "Stage",
            "Layer",
            "Render Settings",
            "Property",
            "Content",
            "Console",
            "Script Editor",
        ):
            try:
                ui.Workspace.show_window(title, True)
            except Exception:
                pass
    except Exception:
        pass
    print("[RTG Viewport Stream] Kit UI restore requested")


def enable_viewport_interaction() -> None:
    """Re-enable local viewport selection/camera interaction after read-only streaming."""

    import carb.settings  # type: ignore

    settings = carb.settings.get_settings()
    settings.set("/app/window/drawMouse", True)
    settings.set("/persistent/app/viewport/coiDoubleClick", True)
    settings.set("/persistent/app/viewport/coiDoubleClickObjectOnly", True)
    settings.set("/persistent/app/viewport/pickingMode", "type:ALL")
    settings.set("/persistent/app/viewport/pickingModeNoKinds", True)
    settings.set("/persistent/app/viewport/pickingModeIncludeRef", True)
    settings.set("/exts/omni.kit.viewport.window/startup/cameraManipulator/enabled", True)
    settings.set("/exts/omni.kit.viewport.window/startup/selectionManipulator/enabled", True)
    _set_viewport_read_only(False)
    _enable_existing_viewport_interaction()
    try:
        import omni.ui as ui  # type: ignore

        ui.Workspace.show_window("Viewport", True)
    except Exception:
        pass
    print("[RTG Viewport Stream] viewport interaction restored")


def print_viewport_interaction_status() -> None:
    """Print key viewport interaction settings and the current USD selection."""

    import carb.settings  # type: ignore

    settings = carb.settings.get_settings()
    paths = (
        "/app/window/hideUi",
        "/app/docks/disabled",
        "/app/window/drawMouse",
        "/persistent/app/viewport/coiDoubleClick",
        "/persistent/app/viewport/coiDoubleClickObjectOnly",
        "/persistent/app/viewport/pickingMode",
        "/persistent/app/viewport/pickingModeNoKinds",
        "/persistent/app/viewport/pickingModeIncludeRef",
        "/exts/omni.kit.viewport.window/startup/cameraManipulator/enabled",
        "/exts/omni.kit.viewport.window/startup/selectionManipulator/enabled",
    )
    for path in paths:
        try:
            print(f"[RTG Viewport Stream] {path} = {settings.get(path)}")
        except Exception as exc:
            print(f"[RTG Viewport Stream] {path} unavailable: {exc}")
    try:
        import omni.usd  # type: ignore

        selected = list(omni.usd.get_context().get_selection().get_selected_prim_paths())
        print(f"[RTG Viewport Stream] selected prims = {selected}")
    except Exception as exc:
        print(f"[RTG Viewport Stream] selection query unavailable: {exc}")


def select_primary_rtg(path: str = PRIMARY_RTG_PATH) -> str:
    """Select the primary RTG root in the current USD stage."""

    import omni.usd  # type: ignore

    selection = omni.usd.get_context().get_selection()
    try:
        selection.set_selected_prim_paths([path], False)
    except Exception:
        selection.clear_selected_prim_paths()
        selection.set_prim_path_selected(path, True, True, True, True)
    print(f"[RTG Viewport Stream] selected: {path}")
    return path


def select_primary_rtg_and_frame(path: str = PRIMARY_RTG_PATH) -> str:
    """Select the primary RTG and ask the active viewport to frame/focus it."""

    selected = select_primary_rtg(path)
    try:
        from omni.kit.viewport.utility import frame_viewport_selection  # type: ignore

        frame_viewport_selection()
        print("[RTG Viewport Stream] frame_viewport_selection requested")
        return selected
    except Exception:
        pass

    try:
        from omni.kit.viewport.utility import get_active_viewport_window  # type: ignore

        viewport_window = get_active_viewport_window()
        viewport_api = getattr(viewport_window, "viewport_api", None)
        for method_name in (
            "frame_selection",
            "focus_selection",
            "frame_selected",
            "focus_selected",
        ):
            method = getattr(viewport_api, method_name, None)
            if callable(method):
                method()
                print(f"[RTG Viewport Stream] {method_name} requested")
                return selected
    except Exception as exc:
        print(f"[RTG Viewport Stream] frame selected fallback failed: {exc}")

    print("[RTG Viewport Stream] selected RTG; press F in the viewport to frame it")
    return selected


def start_stream_for_wpf(**kwargs: Any) -> str:
    """Alias with a WPF-oriented name for Script Editor use."""

    return start_viewport_only_livestream(**kwargs)


def start_viewport_only_livestream_2k(**kwargs: Any) -> str:
    """Start viewport-only streaming at 2560x1440."""

    kwargs.setdefault("width", QHD_2K_WIDTH)
    kwargs.setdefault("height", QHD_2K_HEIGHT)
    return start_viewport_only_livestream(**kwargs)
