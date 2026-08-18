"""Shared helpers for camera-facing live HUD panels."""

from __future__ import annotations

import math
from typing import Any, Optional, Sequence, Tuple


def existing_xform_op(xform: Any, op_name: str) -> Any:
    """Return an existing xform op by name, including unordered attributes."""

    try:
        for op in xform.GetOrderedXformOps():
            if op.GetOpName() == op_name:
                return op
    except Exception:
        pass
    try:
        from pxr import UsdGeom

        attr = xform.GetPrim().GetAttribute(op_name)
        return UsdGeom.XformOp(attr) if attr else None
    except Exception:
        return None


def get_or_add_xform_op(xform: Any, op_name: str, add_op: Any) -> Any:
    """Reuse an authored xform op and only add it when it truly is absent."""

    try:
        ordered_ops = list(xform.GetOrderedXformOps())
    except Exception:
        ordered_ops = []
    for op in ordered_ops:
        try:
            if op.GetOpName() == op_name:
                return op
        except Exception:
            pass
    op = existing_xform_op(xform, op_name)
    if op is None:
        try:
            return add_op()
        except Exception:
            # USD can report an empty ordered-op list while the backing
            # xformOp attribute already exists in another layer.  AddXformOp
            # then raises "already exists"; recover by reading that attribute.
            op = existing_xform_op(xform, op_name)
            if op is None:
                raise
    try:
        ordered_names = {
            ordered_op.GetOpName()
            for ordered_op in ordered_ops
            if hasattr(ordered_op, "GetOpName")
        }
        if op_name not in ordered_names:
            xform.SetXformOpOrder(ordered_ops + [op])
    except Exception:
        pass
    return op


def set_xform_op_attr_value(
    prim: Any,
    op_name: str,
    type_name: Any,
    value: Any,
) -> Any:
    """Set a standard xform op through USD attributes, without AddXformOp."""

    attr = prim.GetAttribute(op_name)
    try:
        attr_valid = bool(attr and attr.IsValid())
    except Exception:
        attr_valid = bool(attr)
    if not attr_valid:
        attr = prim.CreateAttribute(op_name, type_name)
    attr.Set(value)

    order_attr = prim.GetAttribute("xformOpOrder")
    try:
        order_valid = bool(order_attr and order_attr.IsValid())
    except Exception:
        order_valid = bool(order_attr)
    if not order_valid:
        from pxr import Sdf

        order_attr = prim.CreateAttribute(
            "xformOpOrder",
            Sdf.ValueTypeNames.TokenArray,
        )
    order = list(order_attr.Get() or [])
    if op_name not in order:
        order_attr.Set([*order, op_name])
    return attr


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


def billboard_x_rotation_degrees(
    panel_position: Sequence[float],
    camera_position: Sequence[float],
    fallback_degrees: float = 0.0,
) -> float:
    """Return the X rotation that pitches a +Y-facing panel toward the camera."""

    try:
        dx = float(camera_position[0]) - float(panel_position[0])
        dy = float(camera_position[1]) - float(panel_position[1])
        dz = float(camera_position[2]) - float(panel_position[2])
    except Exception:
        return float(fallback_degrees)
    horizontal_distance = math.hypot(dx, dy)
    if horizontal_distance < 1e-6 and abs(dz) < 1e-6:
        return float(fallback_degrees)
    return math.degrees(math.atan2(dz, horizontal_distance))


def active_viewport_camera_position(stage: Any) -> Optional[Any]:
    """Best-effort active viewport camera world position."""

    # In current Kit builds ``viewport.transform`` can expose the view matrix
    # rather than a camera-to-world transform.  Its translation then points
    # billboards at a phantom position and makes them appear edge-on.  The
    # authored Camera prim is the authoritative world-space source.
    camera_path = _active_viewport_camera_path()
    if camera_path:
        try:
            from pxr import Gf, Usd, UsdGeom

            prim = stage.GetPrimAtPath(str(camera_path))
            if prim:
                cache = UsdGeom.XformCache(Usd.TimeCode.Default())
                matrix = cache.GetLocalToWorldTransform(prim)
                return matrix.Transform(Gf.Vec3d(0.0, 0.0, 0.0))
        except Exception:
            pass

    return _active_viewport_transform_camera_position()


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
        fallback[0]
        + billboard_x_rotation_degrees(
            panel_position,
            camera_position,
        ),
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
