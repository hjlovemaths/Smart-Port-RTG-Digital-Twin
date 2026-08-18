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
import math
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
RTG_FOLLOW_TARGET_PATH = (
    f"{PRIMARY_RTG_PATH}/ANIM_CTRL_RTG_GANTRY_TRAVEL"
)
RTG_FOLLOW_CAMERA_PATH = "/World/RTGViewportFollowCamera"
RTG_FOLLOW_TARGET_LOCAL = (0.0, 5.0, 4.8)
DEFAULT_FOLLOW_DISTANCE = 52.0
DEFAULT_FOLLOW_AZIMUTH_DEGREES = 60.0
DEFAULT_FOLLOW_PITCH_DEGREES = 15.0


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
    """Prevent editing while retaining camera navigation and click inspection."""

    import carb.settings  # type: ignore

    settings = carb.settings.get_settings()
    read_only = bool(enabled)
    settings.set("/app/window/drawMouse", True)
    settings.set("/persistent/app/viewport/coiDoubleClick", True)
    settings.set("/persistent/app/viewport/coiDoubleClickObjectOnly", True)
    settings.set("/persistent/app/viewport/pickingMode", "type:ALL")
    settings.set("/persistent/app/viewport/pickingModeNoKinds", True)
    settings.set("/persistent/app/viewport/pickingModeIncludeRef", True)
    settings.set("/exts/omni.kit.viewport.window/startup/cameraManipulator/enabled", True)
    settings.set("/exts/omni.kit.viewport.window/startup/selectionManipulator/enabled", True)

    previous = getattr(builtins, "_rtg_viewport_read_only_sub", None)
    if previous is not None:
        try:
            previous.unsubscribe()
        except Exception:
            pass
        builtins._rtg_viewport_read_only_sub = None

    # Selection changes are consumed by rtg_twin_state_receiver to open the
    # RTG/container/truck information panels.  Never clear them here.
    _enable_existing_viewport_interaction()


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


def _rtg_follow_target_world(stage: Any) -> Any:
    """Return the moving RTG portal center in world coordinates."""

    from pxr import Gf, Usd, UsdGeom  # type: ignore

    target_prim = stage.GetPrimAtPath(RTG_FOLLOW_TARGET_PATH)
    if not target_prim:
        return None
    matrix = UsdGeom.XformCache(Usd.TimeCode.Default()).GetLocalToWorldTransform(
        target_prim
    )
    return matrix.Transform(Gf.Vec3d(*RTG_FOLLOW_TARGET_LOCAL))


def _default_follow_eye(target: Any) -> Any:
    """Return a 60-degree oblique side-view eye position around the RTG."""

    from pxr import Gf  # type: ignore

    azimuth = math.radians(DEFAULT_FOLLOW_AZIMUTH_DEGREES)
    pitch = math.radians(DEFAULT_FOLLOW_PITCH_DEGREES)
    horizontal = DEFAULT_FOLLOW_DISTANCE * math.cos(pitch)
    return Gf.Vec3d(
        float(target[0]) + horizontal * math.sin(azimuth),
        float(target[1]) - horizontal * math.cos(azimuth),
        float(target[2]) + DEFAULT_FOLLOW_DISTANCE * math.sin(pitch),
    )


def _look_at_camera_matrix(eye: Any, target: Any) -> Any:
    from pxr import Gf  # type: ignore

    matrix = Gf.Matrix4d(1.0)
    matrix.SetLookAt(eye, target, Gf.Vec3d(0.0, 0.0, 1.0))
    return matrix.GetInverse()


def _matrix_close(left: Any, right: Any, tolerance: float = 1e-7) -> bool:
    if left is None or right is None:
        return left is right
    try:
        return all(
            abs(float(left[row][column]) - float(right[row][column])) <= tolerance
            for row in range(4)
            for column in range(4)
        )
    except Exception:
        return False


def _vector_close(left: Any, right: Any, tolerance: float = 1e-7) -> bool:
    if left is None or right is None:
        return left is right
    try:
        return all(
            abs(float(left[index]) - float(right[index])) <= tolerance
            for index in range(3)
        )
    except Exception:
        return False


def _ensure_rtg_follow_camera(stage: Any) -> tuple[str, Any, Any] | None:
    """Create the session camera once without resetting a saved user view."""

    from pxr import Gf, Sdf, Usd, UsdGeom  # type: ignore

    target = _rtg_follow_target_world(stage)
    if target is None:
        print(
            f"[RTG Viewport Stream] RTG follow target missing: {RTG_FOLLOW_TARGET_PATH}"
        )
        return None

    existing = stage.GetPrimAtPath(RTG_FOLLOW_CAMERA_PATH)
    had_camera = bool(existing and existing.IsValid())
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        camera = UsdGeom.Camera.Define(stage, RTG_FOLLOW_CAMERA_PATH)
        camera.CreateProjectionAttr(UsdGeom.Tokens.perspective)
        camera.CreateFocalLengthAttr(18.0)
        camera.CreateHorizontalApertureAttr(36.0)
        camera.CreateVerticalApertureAttr(20.25)
        camera.CreateClippingRangeAttr(Gf.Vec2f(0.03, 2500.0))
        prim = camera.GetPrim()
        prim.CreateAttribute("omni:kit:cameraLock", Sdf.ValueTypeNames.Bool).Set(
            False
        )
        center_attr = prim.CreateAttribute(
            "omni:kit:centerOfInterest",
            Sdf.ValueTypeNames.Vector3d,
            custom=True,
        )
        transform_attr = prim.GetAttribute("xformOp:transform")
        if not transform_attr:
            transform_attr = UsdGeom.Xformable(prim).AddTransformOp().GetAttr()
        if not had_camera or transform_attr.Get() is None:
            center_attr.Set(target)
            transform_attr.Set(_look_at_camera_matrix(_default_follow_eye(target), target))
        prim.CreateAttribute("xformOpOrder", Sdf.ValueTypeNames.TokenArray).Set(
            ["xformOp:transform"]
        )
    return RTG_FOLLOW_CAMERA_PATH, transform_attr, center_attr


def _stop_rtg_camera_follow() -> None:
    previous = getattr(builtins, "_rtg_viewport_follow_sub", None)
    if previous is not None:
        try:
            previous.unsubscribe()
        except Exception:
            pass
    builtins._rtg_viewport_follow_sub = None
    builtins._rtg_viewport_follow_state = None


def _start_rtg_camera_follow() -> str | None:
    """Follow RTG translation while retaining mouse-adjusted relative view."""

    try:
        import omni.kit.app  # type: ignore
        import omni.usd  # type: ignore
        from pxr import Gf, Usd  # type: ignore

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return None
        camera_info = _ensure_rtg_follow_camera(stage)
        if camera_info is None:
            return None
        camera_path, transform_attr, center_attr = camera_info
        target = _rtg_follow_target_world(stage)
        current_matrix = transform_attr.Get()
        current_center = center_attr.Get()
        state = {
            "stage": stage,
            "target": target,
            "matrix": current_matrix,
            "center": current_center,
        }
        _stop_rtg_camera_follow()
        builtins._rtg_viewport_follow_state = state

        def _on_update(*_args: Any) -> None:
            follow_state = getattr(builtins, "_rtg_viewport_follow_state", None)
            if not follow_state or follow_state.get("stage") is not stage:
                return
            new_target = _rtg_follow_target_world(stage)
            if new_target is None:
                return
            matrix_now = transform_attr.Get()
            center_now = center_attr.Get()

            # A gesture changed the camera since our previous update.  Save
            # that new orbit/zoom as the relative follow view and do not snap
            # it back to the default camera.
            if not _matrix_close(matrix_now, follow_state["matrix"]) or not _vector_close(
                center_now,
                follow_state["center"],
            ):
                follow_state["matrix"] = matrix_now
                follow_state["center"] = center_now
                follow_state["target"] = new_target
                return

            old_target = follow_state["target"]
            delta = Gf.Vec3d(
                float(new_target[0]) - float(old_target[0]),
                float(new_target[1]) - float(old_target[1]),
                float(new_target[2]) - float(old_target[2]),
            )
            if _vector_close(delta, Gf.Vec3d(0.0, 0.0, 0.0)):
                return

            moved_matrix = Gf.Matrix4d(matrix_now)
            moved_matrix.SetTranslateOnly(matrix_now.ExtractTranslation() + delta)
            moved_center = (
                Gf.Vec3d(center_now) + delta if center_now is not None else new_target
            )
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                transform_attr.Set(moved_matrix)
                center_attr.Set(moved_center)
            follow_state["target"] = new_target
            follow_state["matrix"] = moved_matrix
            follow_state["center"] = moved_center

        stream = omni.kit.app.get_app().get_update_event_stream()
        builtins._rtg_viewport_follow_sub = stream.create_subscription_to_pop(
            _on_update,
            name="RTGViewportCameraFollow",
        )
        print(
            "[RTG Viewport Stream] camera follows RTG; mouse-adjusted view is retained"
        )
        return camera_path
    except Exception as exc:
        print(f"[RTG Viewport Stream] RTG camera follow unavailable: {exc}")
        _stop_rtg_camera_follow()
        return None


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

    if camera_path is None:
        camera_path = _start_rtg_camera_follow()
    else:
        _stop_rtg_camera_follow()
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
        print(
            "[RTG Viewport Stream] click inspection enabled; native camera controls preserved"
        )
    print("[RTG Viewport Stream] restore Kit UI with restore_kit_ui()")
    return url


def restore_kit_ui() -> None:
    """Restore normal Kit docking/UI after viewport-only streaming setup."""

    import carb.settings  # type: ignore

    settings = carb.settings.get_settings()
    settings.set("/app/window/hideUi", False)
    settings.set("/app/docks/disabled", False)
    settings.set("/app/viewport/defaults/noTitleBar", False)
    _stop_rtg_camera_follow()
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
