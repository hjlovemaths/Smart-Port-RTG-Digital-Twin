"""Shared helpers for camera-facing live HUD panels."""

from __future__ import annotations

import math
from typing import Any, Optional, Sequence, Tuple


def billboard_z_rotation_degrees(
    panel_position: Sequence[float],
    camera_position: Sequence[float],
    fallback_degrees: float = 180.0,
) -> float:
    """Return the Z rotation that turns a +Y-facing panel toward the camera."""

    try:
        dx = float(camera_position[0]) - float(panel_position[0])
        dy = float(camera_position[1]) - float(panel_position[1])
    except Exception:
        return float(fallback_degrees)
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return float(fallback_degrees)
    return math.degrees(math.atan2(-dx, dy)) % 360.0


def active_viewport_camera_position(stage: Any) -> Optional[Any]:
    """Best-effort active viewport camera world position."""

    position = _active_viewport_transform_camera_position()
    if position is not None:
        return position

    camera_path = _active_viewport_camera_path()
    if not camera_path:
        return None
    try:
        from pxr import Gf, Usd, UsdGeom

        prim = stage.GetPrimAtPath(str(camera_path))
        if not prim:
            return None
        cache = UsdGeom.XformCache(Usd.TimeCode.Default())
        matrix = cache.GetLocalToWorldTransform(prim)
        return matrix.Transform(Gf.Vec3d(0.0, 0.0, 0.0))
    except Exception:
        return None


def camera_facing_rotate_xyz(
    stage: Any,
    panel_position: Sequence[float],
    fallback_rotate_xyz: Sequence[float],
) -> Tuple[float, float, float]:
    """Return rotateXYZ that faces the active viewport camera when available."""

    return camera_facing_rotate_xyz_from_camera(
        panel_position,
        active_viewport_camera_position(stage),
        fallback_rotate_xyz,
    )


def camera_facing_rotate_xyz_from_camera(
    panel_position: Sequence[float],
    camera_position: Optional[Sequence[float]],
    fallback_rotate_xyz: Sequence[float],
) -> Tuple[float, float, float]:
    """Return rotateXYZ using an already-resolved camera position."""

    fallback = (
        float(fallback_rotate_xyz[0]),
        float(fallback_rotate_xyz[1]),
        float(fallback_rotate_xyz[2]),
    )
    if camera_position is None:
        return fallback
    return (
        fallback[0],
        fallback[1],
        billboard_z_rotation_degrees(panel_position, camera_position, fallback[2]),
    )


def _active_viewport_camera_path() -> Optional[str]:
    for viewport in _active_viewports():
        camera_path = _viewport_camera_path(viewport)
        if camera_path:
            return camera_path
    return None


def _active_viewport_transform_camera_position() -> Optional[Any]:
    for viewport in _active_viewports():
        position = _viewport_transform_camera_position(viewport)
        if position is not None:
            return position
    return None


def _active_viewports() -> Tuple[Any, ...]:
    viewports = []
    for viewport in (_active_viewport(), _active_viewport_window_api()):
        if viewport is not None and all(id(viewport) != id(existing) for existing in viewports):
            viewports.append(viewport)
    return tuple(viewports)


def _active_viewport() -> Any:
    try:
        from omni.kit.viewport.utility import get_active_viewport  # type: ignore

        return get_active_viewport()
    except Exception:
        return None


def _active_viewport_window_api() -> Any:
    try:
        from omni.kit.viewport.utility import get_active_viewport_window  # type: ignore

        window = get_active_viewport_window()
        return getattr(window, "viewport_api", None) or window
    except Exception:
        return None


def _viewport_transform_camera_position(viewport: Any) -> Optional[Any]:
    transform = _viewport_matrix(viewport, "transform")
    position = _matrix_translation(transform)
    if position is not None:
        return position

    view = _viewport_matrix(viewport, "view")
    inverse = _matrix_inverse(view)
    return _matrix_translation(inverse)


def _viewport_matrix(viewport: Any, name: str) -> Any:
    if viewport is None:
        return None
    try:
        value = getattr(viewport, name, None)
    except Exception:
        return None
    if callable(value):
        try:
            value = value()
        except Exception:
            return None
    return value


def _matrix_translation(matrix: Any) -> Optional[Any]:
    if matrix is None:
        return None
    for name in ("ExtractTranslation", "GetTranslation"):
        method = getattr(matrix, name, None)
        if callable(method):
            try:
                return method()
            except Exception:
                pass
    try:
        return (float(matrix[3][0]), float(matrix[3][1]), float(matrix[3][2]))
    except Exception:
        return None


def _matrix_inverse(matrix: Any) -> Any:
    if matrix is None:
        return None
    for name in ("GetInverse", "GetInverseTransform"):
        method = getattr(matrix, name, None)
        if callable(method):
            try:
                return method()
            except Exception:
                pass
    try:
        return matrix.GetOrthonormalized().GetInverse()
    except Exception:
        return None


def _viewport_camera_path(viewport: Any) -> Optional[str]:
    if viewport is None:
        return None
    for name in ("camera_path", "cameraPath", "active_camera", "activeCamera"):
        try:
            value = getattr(viewport, name, None)
        except Exception:
            value = None
        text = _stringify_path(value)
        if text:
            return text
    return None


def _stringify_path(value: Any) -> Optional[str]:
    if value is None:
        return None
    for name in ("pathString", "path_string"):
        try:
            text = getattr(value, name, None)
        except Exception:
            text = None
        if text:
            return str(text)
    text = str(value).strip()
    return text or None
