"""Four live USD viewport cameras for the remote-manual spreader view.

The reference operator layout is a 2x2 view of the *digital-twin model*.  This
module deliberately does not open RTSP streams.  It creates four session-layer
USD cameras below the animated hoist and opens four Kit legacy viewport
instances, so the views follow the modeled spreader as it moves.
"""

from __future__ import annotations

import math
from typing import Any, Optional


HOIST_PATH = (
    "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL/"
    "ANIM_CTRL_RTG_TROLLEY_TRAVEL/ANIM_CTRL_RTG_HOIST_VERTICAL"
)
CAMERA_ROOT_PATH = f"{HOIST_PATH}/REMOTE_MANUAL_SPREAD_MODEL_CAMERAS"
LOCK_FOOT_TOKEN = "RTG_SPREAD_REDRAW_red_corner_lock_foot"
CAMERA_LAYOUT = ((1, 2), (4, 3))
CAMERA_SIZE = (640, 360)
CAMERA_POSITIONS = {
    1: (40, 80),
    2: (680, 80),
    4: (40, 450),
    3: (680, 450),
}

_service: Optional["RtgModelCameraGridService"] = None


def _camera_matrix(eye: Any, target: Any, up: Any) -> Any:
    from pxr import Gf

    matrix = Gf.Matrix4d(1.0)
    matrix.SetLookAt(eye, target, up)
    return matrix.GetInverse()


def _set_camera_transform(camera_prim: Any, matrix: Any) -> None:
    from pxr import UsdGeom

    xform = UsdGeom.Xformable(camera_prim)
    transform_attr = camera_prim.GetAttribute("xformOp:transform")
    if not transform_attr:
        transform_attr = xform.AddTransformOp(opSuffix="model_camera").GetAttr()
    transform_attr.Set(matrix)


class RtgModelCameraGridService:
    """Own four animated model cameras and their viewport instances."""

    def __init__(self, stage: Any) -> None:
        self.stage = stage
        self._viewport_interface = None
        self._viewport_instances: list[tuple[Any, Any]] = []
        self._camera_paths: dict[int, str] = {}
        self._last_signature: Optional[tuple[tuple[float, float, float], ...]] = None

    def start(self) -> None:
        self.refresh()
        if len(self._camera_paths) < 4:
            return
        if self._viewport_instances:
            return
        try:
            from omni.kit import viewport_legacy  # type: ignore

            self._viewport_interface = viewport_legacy.get_viewport_interface()
            for seq in (1, 2, 4, 3):
                handle = self._viewport_interface.create_instance()
                window = self._viewport_interface.get_viewport_window(handle)
                if window is None:
                    continue
                window.set_window_size(*CAMERA_SIZE)
                window.set_window_pos(*CAMERA_POSITIONS[seq])
                window.set_active_camera(self._camera_paths[seq])
                self._viewport_instances.append((handle, window))
            print(
                "[RTG Model Camera Grid] opened four USD model views: "
                "C1, C2, C4, C3"
            )
        except Exception as exc:
            self._close_viewports()
            print(f"[RTG Model Camera Grid] viewport creation failed: {exc}")

    def stop(self) -> None:
        self._close_viewports()

    def refresh(self) -> None:
        points = self._lock_foot_points()
        if len(points) < 4:
            print(
                "[RTG Model Camera Grid] expected four spreader lock-foot anchors, "
                f"found {len(points)}"
            )
            return
        signature = tuple(
            (round(float(point[0]), 4), round(float(point[1]), 4), round(float(point[2]), 4))
            for point in points
        )
        if signature == self._last_signature:
            return
        self._last_signature = signature
        self._author_cameras(points)
        for seq, window in self._viewport_instances:
            camera_path = self._camera_paths.get(seq)
            if camera_path:
                try:
                    window.set_active_camera(camera_path)
                except Exception:
                    pass

    def _lock_foot_points(self) -> list[Any]:
        from pxr import Gf, Usd, UsdGeom

        bbox_cache = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            useExtentsHint=False,
        )
        points: list[Any] = []
        for prim in self.stage.Traverse():
            path = str(prim.GetPath())
            if not path.startswith(f"{HOIST_PATH}/"):
                continue
            if LOCK_FOOT_TOKEN not in str(prim.GetName()):
                continue
            bounds = bbox_cache.ComputeWorldBound(prim).ComputeAlignedBox()
            minimum = bounds.GetMin()
            maximum = bounds.GetMax()
            if float(minimum[0]) > float(maximum[0]) or float(minimum[1]) > float(maximum[1]):
                continue
            center = (minimum + maximum) * 0.5
            points.append(Gf.Vec3d(float(center[0]), float(center[1]), float(minimum[2])))
        return points

    def _author_cameras(self, points: list[Any]) -> None:
        from pxr import Gf, Usd, UsdGeom

        min_x = min(float(point[0]) for point in points)
        max_x = max(float(point[0]) for point in points)
        min_y = min(float(point[1]) for point in points)
        max_y = max(float(point[1]) for point in points)
        center = Gf.Vec3d(
            sum(float(point[0]) for point in points) / len(points),
            sum(float(point[1]) for point in points) / len(points),
            sum(float(point[2]) for point in points) / len(points),
        )
        corners = {
            1: Gf.Vec3d(min_x, max_y, center[2]),
            2: Gf.Vec3d(max_x, max_y, center[2]),
            4: Gf.Vec3d(min_x, min_y, center[2]),
            3: Gf.Vec3d(max_x, min_y, center[2]),
        }
        hoist = self.stage.GetPrimAtPath(HOIST_PATH)
        if not hoist:
            return
        hoist_world = UsdGeom.XformCache().GetLocalToWorldTransform(hoist)
        world_to_hoist = hoist_world.GetInverse()
        with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
            for seq, corner in corners.items():
                outward = Gf.Vec3d(corner[0] - center[0], corner[1] - center[1], 0.0)
                length = max((outward[0] ** 2 + outward[1] ** 2) ** 0.5, 0.001)
                outward = outward / length
                eye_world = corner + outward * 0.45 + Gf.Vec3d(0.0, 0.0, 0.55)
                target_world = center + Gf.Vec3d(0.0, 0.0, -0.55)
                eye = world_to_hoist.Transform(eye_world)
                target = world_to_hoist.Transform(target_world)
                path = f"{CAMERA_ROOT_PATH}/C{seq:02d}"
                camera = UsdGeom.Camera.Define(self.stage, path)
                camera.CreateProjectionAttr(UsdGeom.Tokens.perspective)
                camera.CreateFocalLengthAttr(14.0)
                camera.CreateHorizontalApertureAttr(36.0)
                camera.CreateVerticalApertureAttr(20.25)
                camera.CreateClippingRangeAttr(Gf.Vec2f(0.03, 2500.0))
                camera.CreateFocusDistanceAttr(
                    max(
                        math.sqrt(sum(float(component) ** 2 for component in (eye - target))),
                        0.1,
                    )
                )
                _set_camera_transform(
                    camera.GetPrim(),
                    _camera_matrix(eye, target, Gf.Vec3d(0.0, 0.0, 1.0)),
                )
                UsdGeom.Imageable(camera.GetPrim()).MakeInvisible()
                self._camera_paths[seq] = path

    def _close_viewports(self) -> None:
        interface = self._viewport_interface
        instances = list(self._viewport_instances)
        self._viewport_instances.clear()
        for handle, window in instances:
            closed = False
            if interface is not None:
                for method_name in ("destroy_instance", "release_instance"):
                    method = getattr(interface, method_name, None)
                    if callable(method):
                        try:
                            method(handle)
                            closed = True
                            break
                        except Exception:
                            pass
            if not closed:
                for method_name in ("set_visible", "set_visibility"):
                    method = getattr(window, method_name, None)
                    if callable(method):
                        try:
                            method(False)
                            break
                        except Exception:
                            pass


def start_rtg_model_camera_grid(stage: Any) -> RtgModelCameraGridService:
    global _service
    if _service is not None:
        _service.stop()
    _service = RtgModelCameraGridService(stage)
    _service.start()
    return _service


def refresh_rtg_model_camera_grid(stage: Any) -> None:
    if _service is not None and _service.stage is stage:
        _service.refresh()


def stop_rtg_model_camera_grid() -> None:
    global _service
    if _service is not None:
        _service.stop()
        _service = None
