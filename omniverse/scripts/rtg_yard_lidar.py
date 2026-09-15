"""Attach horizontal and downward RTX LiDARs to the primary RTG trolley.

Run this file from the Isaac Sim Script Editor after ``smart_port.usda`` is
open::

    exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_yard_lidar.py", encoding="utf-8").read())
    start_rtg_yard_lidar()

    import asyncio

    async def capture_once():
        print(await capture_rtg_yard_pointcloud())

    asyncio.ensure_future(capture_once())

The sensors are created at the validated trolley pose and the visual mounts
are placed at the trolley.  The live viewport uses the RTX point-cloud debug
writer: horizontal returns are green and downward returns are orange.
When enabled, the service also creates temporary blue L1-L24 guide spheres at
the RTG drawing's approximate LiDAR installation points.
Captures default to the primary horizontal LiDAR coordinate frame, where
that RTG radar is the origin; the downward sensor is transformed into the
same frame.  Pass ``output_frame="world"`` for a world-coordinate map.
Both are exported as PLY, PCD, and NPY.

This module intentionally uses LiDAR rather than millimetre-wave Radar:
Radar is useful for sparse detections and velocity, while a yard geometry
point cloud requires the dense range returns produced by LiDAR.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Any, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "rtg_pointcloud"

PRIMARY_PATH = "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC"
GANTRY_PATH = f"{PRIMARY_PATH}/ANIM_CTRL_RTG_GANTRY_TRAVEL"
TROLLEY_PATH = f"{GANTRY_PATH}/ANIM_CTRL_RTG_TROLLEY_TRAVEL"
# Keep the visual housing out of the animated/PhysicsRigidBody trolley
# hierarchy.  In Isaac Sim 6.0.1, even a static sensor housing authored below
# that hierarchy can invalidate the RTX LiDAR acceleration structure.  The
# housing is therefore a session-layer root prim placed at the trolley pose.
MOUNT_PATH = "/World/RTG_YARD_LIDAR_MOUNT_RUNTIME"
# Isaac Sim 6.0.1 can return a valid GMO header with all-zero ranges when an
# RTX sensor is parented below a dynamic/PhysicsRigidBody RTG controller. The
# visible housing remains on the trolley; the runtime OmniLidar is authored at
# the world root and initialized to the housing's world pose.
LIDAR_PATH = "/World/RTG_YARD_LIDAR_RUNTIME"
DOWN_MOUNT_PATH = "/World/RTG_YARD_LIDAR_DOWN_MOUNT_RUNTIME"
DOWN_LIDAR_PATH = "/World/RTG_YARD_LIDAR_DOWN_RUNTIME"

# Calibrated against the imported RTG trolley controller.  The earlier
# (2.55,-5.0) guess placed the scanner at [4.68,11.58,9.35], outside the live
# yard scan corridor.  This local pose maps to the verified RTG world pose
# [-4.6515,21.5775,9.35] and remains above the trolley roof.
DEFAULT_SENSOR_TRANSLATION = (-3.7811240757, 5.00, 9.35)
# The second unit is co-located just below the horizontal unit and points at
# nadir.  Its smaller range keeps the second RTX render product affordable on
# 8 GB GPUs while covering the containers directly below the RTG.
DOWN_SENSOR_VERTICAL_OFFSET_M = -0.20
DEFAULT_DOWN_SENSOR_TRANSLATION = (
    DEFAULT_SENSOR_TRANSLATION[0],
    DEFAULT_SENSOR_TRANSLATION[1],
    DEFAULT_SENSOR_TRANSLATION[2] + DOWN_SENSOR_VERTICAL_OFFSET_M,
)
DEFAULT_DOWN_FAR_RANGE_M = 150.0
# Lidar orientations use world-frame quaternions in wxyz order.  With the
# sensor's local +X ray direction, +90 degrees about world Y points +X down
# along world -Z.
DOWNWARD_ORIENTATION_WXYZ = (0.7071067812, 0.0, 0.7071067812, 0.0)
DEFAULT_CONFIG = "Example_Rotary"
DEFAULT_TICK_RATE_HZ = 10.0
DEFAULT_FAR_RANGE_M = 600.0
DEFAULT_SENSOR_WARMUP_FRAMES = 30
DEFAULT_CAPTURE_SCANS = 8
DEFAULT_VOXEL_SIZE_M = 0.05
DEFAULT_MAX_ACCUMULATED_POINTS = 750_000

_service: Optional["RtgYardLidarService"] = None


try:
    # Isaac Sim provides the real Replicator base class.  Keep a dependency-
    # light fallback so the file's pure Python helpers remain unit-testable
    # with the regular project Python interpreter.
    import omni.replicator.core as _rep

    _ReplicatorWriterBase = _rep.Writer
except Exception:  # pragma: no cover - only used outside Isaac Sim
    class _ReplicatorWriterBase:
        pass


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "scan"


def spherical_to_cartesian(points: Any):
    """Convert RTX GMO spherical ``(azimuth_deg, elevation_deg, range_m)``.

    The helper is dependency-light so it can also be unit-tested outside Kit.
    """

    import numpy as np

    values = np.asarray(points, dtype=np.float32).reshape(-1, 3)
    azimuth = np.deg2rad(values[:, 0])
    elevation = np.deg2rad(values[:, 1])
    distance = values[:, 2]
    xy = distance * np.cos(elevation)
    result = np.column_stack(
        (xy * np.cos(azimuth), xy * np.sin(azimuth), distance * np.sin(elevation))
    ).astype(np.float32, copy=False)
    result[distance < 1e-6] = 0.0
    return result


def _numpy_array(value: Any):
    import numpy as np

    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("data")
    if value is None:
        return None
    if hasattr(value, "numpy"):
        value = value.numpy()
    return np.asarray(value)


def _make_world_transform(stage: Any, prim_path: str, time_code: Any):
    from pxr import Usd, UsdGeom

    cache = UsdGeom.XformCache(Usd.TimeCode(time_code))
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        raise RuntimeError(f"LiDAR prim is not valid: {prim_path}")
    return cache.GetLocalToWorldTransform(prim)


def _transform_points(points: Any, matrix: Any):
    import numpy as np
    from pxr import Gf

    local = np.asarray(points, dtype=np.float32).reshape(-1, 3)
    if not len(local):
        return local
    # Gf.Matrix4d.TransformAffine handles the USD row/column convention and
    # keeps the conversion correct if the RTG later receives a rotation.
    result = np.empty((len(local), 3), dtype=np.float32)
    for index, point in enumerate(local):
        transformed = matrix.TransformAffine(Gf.Vec3d(*map(float, point)))
        result[index] = (float(transformed[0]), float(transformed[1]), float(transformed[2]))
    return result


def write_ply(path: Path, points: Any) -> Path:
    """Write an ASCII PLY point cloud without requiring Open3D or PCL."""

    import numpy as np

    cloud = np.asarray(points, dtype=np.float32).reshape(-1, 3)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "ply\nformat ascii 1.0\n"
        f"element vertex {len(cloud)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "end_header\n"
    )
    with path.open("w", encoding="ascii", newline="\n") as stream:
        stream.write(header)
        np.savetxt(stream, cloud, fmt="%.6f %.6f %.6f")
    return path


def write_pcd(path: Path, points: Any) -> Path:
    """Write an ASCII PCD 0.7 point cloud."""

    import numpy as np

    cloud = np.asarray(points, dtype=np.float32).reshape(-1, 3)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# .PCD v0.7 - Point Cloud Data file format\n"
        "VERSION 0.7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\n"
        "COUNT 1 1 1\n"
        f"WIDTH {len(cloud)}\nHEIGHT 1\nVIEWPOINT 0 0 0 1 0 0 0\n"
        f"POINTS {len(cloud)}\nDATA ascii\n"
    )
    with path.open("w", encoding="ascii", newline="\n") as stream:
        stream.write(header)
        np.savetxt(stream, cloud, fmt="%.6f %.6f %.6f")
    return path


def write_npy(path: Path, points: Any) -> Path:
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, np.asarray(points, dtype=np.float32).reshape(-1, 3))
    return path


def write_pointcloud_bundle(output_dir: Path, stem: str, points: Any) -> dict[str, str]:
    """Write a point cloud in formats useful to PCL/Open3D.

    The coordinates are not changed here.  The caller chooses whether the
    points are in the LiDAR sensor frame or the world frame.
    """

    safe_stem = _safe_filename(stem)
    return {
        "ply": str(write_ply(output_dir / f"{safe_stem}.ply", points)),
        "pcd": str(write_pcd(output_dir / f"{safe_stem}.pcd", points)),
        "npy": str(write_npy(output_dir / f"{safe_stem}.npy", points)),
    }


class _PointCloudWriter(_ReplicatorWriterBase):
    """Replicator writer that retains both sensor-frame and world points."""

    def __init__(self, service: "RtgYardLidarService", output_dir: Path) -> None:
        self.data_structure = "renderProduct"
        self.annotators = []
        self._service = service
        self.output_dir = output_dir
        self.frame_count = 0
        self.scan_count = 0
        self.last_sensor_points = None
        self.last_points = None
        self.last_bundle: Optional[dict[str, str]] = None
        self.callback_count = 0
        self.last_render_product_count = 0
        self.empty_frame_count = 0
        self.last_raw_element_count = 0
        self.last_valid_point_count = 0
        self.missing_gmo_count = 0
        self.zero_element_count = 0
        self.invalid_only_count = 0
        self.direct_poll_count = 0
        self.direct_data_count = 0
        self._registered_name = "RtgYardLidarPointCloudWriter"

    def initialize_annotators(self) -> None:
        import omni.replicator.core as rep

        self.annotators = [
            rep.annotators.get("GenericModelOutput"),
        ]

    def write(self, data: dict[str, Any]) -> None:
        self.callback_count += 1
        render_products = data.get("renderProducts")
        self.last_render_product_count = (
            len(render_products) if render_products is not None else 0
        )
        if not render_products:
            self.empty_frame_count += 1
            return
        # In Isaac Sim 6.x the GenericModelOutput payload passed through a
        # Replicator writer is not always the raw ``wp.array`` accepted by
        # ``parse_generic_model_output_data``; treating it as such produces
        # the misleading "invalid magic number" warning. The official
        # LidarSensor.get_data() path is read by capture_rtg_yard_pointcloud()
        # below and is the authoritative data path. Keep this writer as
        # callback/render-product telemetry and leave decoding to that path.
        self.empty_frame_count += 1

    def write_metadata(self) -> None:
        """Metadata is not needed; point clouds are written directly to disk."""

        return None


class RtgYardLidarPointCloudWriter(_PointCloudWriter):
    """Registry adapter that binds each writer instance to the active service."""

    def __init__(self) -> None:
        if _service is None:
            raise RuntimeError("RTG yard LiDAR service is not active")
        super().__init__(_service, _service.output_dir)
        self.initialize_annotators()


@dataclass
class RtgYardLidarService:
    stage: Any
    sensor: Optional[Any]
    lidar: Any
    lidar_path: str
    down_sensor: Optional[Any]
    down_lidar: Any
    down_lidar_path: str
    output_dir: Path
    writer: Optional[_PointCloudWriter]
    down_writer: Optional[_PointCloudWriter]
    sensor_local_translation: tuple[float, float, float]
    down_sensor_local_translation: tuple[float, float, float]
    accumulated_points: Any
    accumulated_sensor_points: Any
    voxel_size_m: float = DEFAULT_VOXEL_SIZE_M
    max_accumulated_points: int = DEFAULT_MAX_ACCUMULATED_POINTS
    draw_point_size: float = 0.05
    _last_timestamp_ns: int = 0
    _down_last_timestamp_ns: int = 0

    def _ingest_generic_model_output(
        self,
        gmo_raw: Any,
        *,
        cartesian_raw: Any = None,
        sensor_key: str = "horizontal",
        lidar_path: str | None = None,
        writer: Optional[_PointCloudWriter] = None,
    ) -> int:
        """Convert one GMO packet into RTG-sensor/world points and export it.

        This is shared by the Replicator writer and the direct
        ``LidarSensor.get_data`` fallback.  Isaac Sim versions differ in how
        writer payloads wrap the Warp array, while the parsed GMO structure is
        stable; keeping the conversion in one place prevents those two paths
        from producing different point clouds.
        """

        import numpy as np
        from isaacsim.sensors.experimental.rtx import parse_generic_model_output_data
        import omni.timeline

        if isinstance(gmo_raw, dict):
            gmo_raw = gmo_raw.get("data")
        if gmo_raw is None:
            return 0

        source_path = lidar_path or self.lidar_path
        source_writer = writer or self.writer
        gmo = parse_generic_model_output_data(gmo_raw)
        count = int(getattr(gmo, "numElements", 0) or 0)
        if source_writer is None:
            raise RuntimeError("LiDAR outputs are not attached yet")
        source_writer.last_raw_element_count = count
        if count <= 0:
            return 0
        timestamp_ns = int(getattr(gmo, "timestampNs", 0) or 0)
        last_timestamp_ns = (
            self._down_last_timestamp_ns
            if sensor_key == "down"
            else self._last_timestamp_ns
        )
        if timestamp_ns and timestamp_ns == last_timestamp_ns:
            return 0

        cartesian = _numpy_array(cartesian_raw)
        if cartesian is not None:
            sensor_points = np.asarray(cartesian, dtype=np.float32).reshape(-1, 3)[:count]
            valid = np.isfinite(sensor_points).all(axis=1) & (
                np.linalg.norm(sensor_points, axis=1) > 1e-6
            )
        else:
            # GenericModelOutput stores LiDAR returns as azimuth (x),
            # elevation (y), and range (z).  This is the portable path across
            # Isaac Sim 5.x/6.x; the extractor is optional.
            spherical = np.column_stack(
                (
                    _numpy_array(gmo.x)[:count],
                    _numpy_array(gmo.y)[:count],
                    _numpy_array(gmo.z)[:count],
                )
            )
            valid = np.isfinite(spherical).all(axis=1) & (spherical[:, 2] > 1e-6)
            sensor_points = spherical_to_cartesian(spherical)

        sensor_points = sensor_points[valid]
        source_writer.last_valid_point_count = int(len(sensor_points))
        if not len(sensor_points):
            return 0

        stage = self.stage
        if stage is None:
            return 0
        # Kit timeline time is seconds; USD xform samples are time codes.
        timeline = omni.timeline.get_timeline_interface()
        time_code = timeline.get_current_time() * float(
            stage.GetTimeCodesPerSecond() or 1.0
        )
        world_matrix = _make_world_transform(stage, source_path, time_code)
        world_points = _transform_points(sensor_points, world_matrix)
        world_points = world_points[np.isfinite(world_points).all(axis=1)]
        if not len(world_points):
            return 0

        # Normalize every source into the primary horizontal LiDAR frame.
        # This keeps the exported "sensor" cloud centered on the RTG radar
        # even though the downward unit has a different origin and rotation.
        primary_matrix = _make_world_transform(stage, self.lidar_path, time_code)
        rtg_sensor_points = _transform_points(
            world_points, primary_matrix.GetInverse()
        )
        rtg_sensor_points = rtg_sensor_points[
            np.isfinite(rtg_sensor_points).all(axis=1)
        ]
        if not len(rtg_sensor_points):
            return 0

        source_writer.last_sensor_points = rtg_sensor_points
        source_writer.last_points = world_points
        self._append_accumulated(rtg_sensor_points, sensor_frame=True)
        self._append_accumulated(world_points)
        source_writer.frame_count += 1
        # A rotary scan may deliver partial packets.  Saving every packet is
        # noisy, so export one cloud per completed render callback or direct
        # sensor sample.
        source_writer.scan_count += 1
        source_writer.last_bundle = write_pointcloud_bundle(
            self.output_dir,
            f"rtg_yard_scan_{sensor_key}_{source_writer.scan_count:06d}",
            world_points,
        )
        if sensor_key == "down":
            self._down_last_timestamp_ns = timestamp_ns
        else:
            self._last_timestamp_ns = timestamp_ns
        return int(len(world_points))

    def _append_accumulated(self, points: Any, *, sensor_frame: bool = False) -> None:
        import numpy as np

        cloud = np.asarray(points, dtype=np.float32).reshape(-1, 3)
        if self.voxel_size_m > 0 and len(cloud):
            # Lightweight deterministic voxel thinning prevents a moving RTG
            # from retaining millions of nearly identical returns.
            keys = np.floor(cloud / self.voxel_size_m).astype(np.int64)
            _, unique_indices = np.unique(keys, axis=0, return_index=True)
            cloud = cloud[np.sort(unique_indices)]
        if len(cloud):
            attribute = (
                "accumulated_sensor_points" if sensor_frame else "accumulated_points"
            )
            current = getattr(self, attribute)
            setattr(
                self,
                attribute,
                np.concatenate((current, cloud), axis=0)[-self.max_accumulated_points :],
            )

    def save_accumulated(self, stem: str = "rtg_yard_map") -> dict[str, str]:
        return write_pointcloud_bundle(
            self.output_dir, stem, self.accumulated_points
        )

    def save_sensor_accumulated(
        self, stem: str = "rtg_yard_sensor_scan"
    ) -> dict[str, str]:
        return write_pointcloud_bundle(
            self.output_dir, stem, self.accumulated_sensor_points
        )

    def status(self) -> dict[str, Any]:
        writer = self.writer
        return {
            "sensorPath": self.lidar_path,
            "downSensorPath": self.down_lidar_path,
            "outputDir": str(self.output_dir),
            "writersAttached": writer is not None,
            "frames": 0 if writer is None else writer.frame_count,
            "scans": 0 if writer is None else writer.scan_count,
            "writerCallbacks": 0 if writer is None else writer.callback_count,
            "writerRenderProducts": 0 if writer is None else writer.last_render_product_count,
            "writerEmptyFrames": 0 if writer is None else writer.empty_frame_count,
            "writerLastRawElementCount": 0 if writer is None else writer.last_raw_element_count,
            "writerLastValidPointCount": 0 if writer is None else writer.last_valid_point_count,
            "writerMissingGmo": 0 if writer is None else writer.missing_gmo_count,
            "writerZeroElementFrames": 0 if writer is None else writer.zero_element_count,
            "writerInvalidOnlyFrames": 0 if writer is None else writer.invalid_only_count,
            "directPolls": 0 if writer is None else writer.direct_poll_count,
            "directDataFrames": 0 if writer is None else writer.direct_data_count,
            "latestPointCount": int(
                0 if writer is None or writer.last_points is None else len(writer.last_points)
            ),
            "latestSensorPointCount": int(
                0
                if writer is None or writer.last_sensor_points is None
                else len(writer.last_sensor_points)
            ),
            "accumulatedPointCount": int(len(self.accumulated_points)),
            "accumulatedSensorPointCount": int(len(self.accumulated_sensor_points)),
            "latestBundle": None if writer is None else writer.last_bundle,
            "downWritersAttached": self.down_writer is not None,
            "downFrames": 0
            if self.down_writer is None
            else self.down_writer.frame_count,
            "downScans": 0
            if self.down_writer is None
            else self.down_writer.scan_count,
            "downWriterCallbacks": 0
            if self.down_writer is None
            else self.down_writer.callback_count,
            "downWriterRenderProducts": 0
            if self.down_writer is None
            else self.down_writer.last_render_product_count,
            "downWriterEmptyFrames": 0
            if self.down_writer is None
            else self.down_writer.empty_frame_count,
            "downWriterLastRawElementCount": 0
            if self.down_writer is None
            else self.down_writer.last_raw_element_count,
            "downWriterLastValidPointCount": 0
            if self.down_writer is None
            else self.down_writer.last_valid_point_count,
            "downWriterMissingGmo": 0
            if self.down_writer is None
            else self.down_writer.missing_gmo_count,
            "downWriterZeroElementFrames": 0
            if self.down_writer is None
            else self.down_writer.zero_element_count,
            "downWriterInvalidOnlyFrames": 0
            if self.down_writer is None
            else self.down_writer.invalid_only_count,
            "downDirectPolls": 0
            if self.down_writer is None
            else self.down_writer.direct_poll_count,
            "downDirectDataFrames": 0
            if self.down_writer is None
            else self.down_writer.direct_data_count,
            "downLatestPointCount": int(
                0
                if self.down_writer is None or self.down_writer.last_points is None
                else len(self.down_writer.last_points)
            ),
            "downLatestSensorPointCount": int(
                0
                if self.down_writer is None
                or self.down_writer.last_sensor_points is None
                else len(self.down_writer.last_sensor_points)
            ),
            "downLatestBundle": None
            if self.down_writer is None
            else self.down_writer.last_bundle,
        }


def _author_lidar_mount(
    stage: Any,
    translation: tuple[float, float, float],
    mount_path: str = MOUNT_PATH,
) -> None:
    """Create a root-level visible sensor housing in the active scene layer."""

    from contextlib import nullcontext
    from pxr import Gf, Usd, UsdGeom

    # A session-layer renderable prim can invalidate the RTX scene/GMO on
    # Isaac Sim 6.0.x.  Keep the runtime visual helper in the same normal edit
    # layer as the RTX sensor instead of composing it into the session layer.
    with nullcontext():
        mount = UsdGeom.Xform.Define(stage, mount_path)
        mount_xform = UsdGeom.Xformable(mount.GetPrim())
        mount_xform.ClearXformOpOrder()
        mount_xform.AddTranslateOp().Set(Gf.Vec3d(*translation))

        base = UsdGeom.Cube.Define(stage, f"{mount_path}/Base")
        base.AddScaleOp().Set(Gf.Vec3f(0.24, 0.24, 0.08))
        base.AddTranslateOp().Set(Gf.Vec3d(0, 0, -0.08))
        base.CreatePurposeAttr().Set(UsdGeom.Tokens.guide)
        base.CreateDisplayColorAttr().Set([Gf.Vec3f(0.08, 0.10, 0.12)])

        housing = UsdGeom.Cylinder.Define(stage, f"{mount_path}/Housing")
        housing.CreateRadiusAttr(0.16)
        housing.CreateHeightAttr(0.22)
        housing.AddTranslateOp().Set(Gf.Vec3d(0, 0, 0.08))
        housing.CreatePurposeAttr().Set(UsdGeom.Tokens.guide)
        housing.CreateDisplayColorAttr().Set([Gf.Vec3f(0.15, 0.65, 0.78)])

        cap = UsdGeom.Cylinder.Define(stage, f"{mount_path}/Top")
        cap.CreateRadiusAttr(0.18)
        cap.CreateHeightAttr(0.03)
        cap.AddTranslateOp().Set(Gf.Vec3d(0, 0, 0.21))
        cap.CreatePurposeAttr().Set(UsdGeom.Tokens.guide)
        cap.CreateDisplayColorAttr().Set([Gf.Vec3f(0.85, 0.90, 0.92)])


def _world_translation(stage: Any, prim_path: str) -> tuple[float, float, float]:
    """Return a prim's current world translation for a root-level sensor."""

    from pxr import Usd, UsdGeom

    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        raise RuntimeError(f"Prim is not valid: {prim_path}")
    matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
        Usd.TimeCode.Default()
    )
    position = matrix.ExtractTranslation()
    return (float(position[0]), float(position[1]), float(position[2]))


def _set_root_translation(stage: Any, prim_path: str, translation: tuple[float, float, float]) -> None:
    """Update an existing root prim's translation without rebuilding it."""

    from pxr import Gf, UsdGeom

    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return
    xformable = UsdGeom.Xformable(prim)
    for op in xformable.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
            op.Set(Gf.Vec3d(*translation))
            return
    xformable.AddTranslateOp().Set(Gf.Vec3d(*translation))


def _set_prim_orientation(
    stage: Any,
    prim_path: str,
    orientation: tuple[float, float, float, float],
) -> None:
    """Write a stable USD orientation to a runtime sensor prim.

    ``Lidar.create(orientations=...)`` normally performs this write through
    ``XformPrim``.  Reasserting the value on the USD prim is intentional here:
    the sensor is created from a referenced profile, and on some Isaac Sim
    6.0.x builds the reference opinion can win again during the first render
    graph update.  The downward unit must keep its 90-degree pitch while the
    runtime translation is synchronized with the trolley.
    """

    from pxr import Gf, UsdGeom

    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return
    xformable = UsdGeom.Xformable(prim)
    orient_attr = prim.GetAttribute("xformOp:orient")
    if orient_attr and orient_attr.IsValid():
        orient_attr.Set(Gf.Quatd(*orientation))
        return
    # This fallback is only needed for a locally authored OmniLidar schema
    # whose profile did not provide the standard transform stack.
    xformable.AddOrientOp(UsdGeom.XformOp.PrecisionDouble).Set(
        Gf.Quatd(*orientation)
    )


def _sensor_prim_paths(lidar: Any, requested_path: str) -> tuple[str, ...]:
    """Return the requested path and any referenced OmniLidar child path."""

    paths = [requested_path]
    for path in getattr(lidar, "paths", ()) or ():
        value = str(path)
        if value not in paths:
            paths.append(value)
    return tuple(paths)


def _sync_runtime_lidar_pose(service: RtgYardLidarService) -> tuple[float, float, float]:
    """Place the runtime sensor at the trolley's current world pose.

    The runtime RTX prim intentionally lives at ``/World`` for Isaac Sim 6.x
    stability, so it cannot inherit later trolley animation automatically.
    Syncing immediately before capture keeps sensor and visible mount aligned
    when ``move_gantry``/``move_trolley`` were called after startup.
    """

    from pxr import Gf, Usd, UsdGeom

    trolley = service.stage.GetPrimAtPath(TROLLEY_PATH)
    if not trolley or not trolley.IsValid():
        raise RuntimeError(f"Primary RTG trolley was not found: {TROLLEY_PATH}")
    trolley_matrix = UsdGeom.Xformable(trolley).ComputeLocalToWorldTransform(
        Usd.TimeCode.Default()
    )
    sensor_position = trolley_matrix.Transform(
        Gf.Vec3d(*service.sensor_local_translation)
    )
    world_translation = (
        float(sensor_position[0]),
        float(sensor_position[1]),
        float(sensor_position[2]),
    )
    _set_root_translation(service.stage, MOUNT_PATH, world_translation)
    _set_root_translation(service.stage, service.lidar_path, world_translation)
    down_sensor_position = trolley_matrix.Transform(
        Gf.Vec3d(*service.down_sensor_local_translation)
    )
    down_world_translation = (
        float(down_sensor_position[0]),
        float(down_sensor_position[1]),
        float(down_sensor_position[2]),
    )
    _set_root_translation(service.stage, DOWN_MOUNT_PATH, down_world_translation)
    _set_root_translation(
        service.stage, service.down_lidar_path, down_world_translation
    )
    return world_translation


def _create_lidar(
    Lidar: Any,
    lidar_path: str,
    config: str,
    tick_rate_hz: float,
    far_range_m: float,
    np: Any,
    translations: tuple[float, float, float] | None = None,
    orientations: tuple[float, float, float, float] | None = None,
):
    """Create a configured LiDAR, with a local schema-default fallback."""

    sensor_translation = np.array(
        [0.0, 0.0, 0.0] if translations is None else translations
    )

    def offline_rotary_attributes() -> dict[str, Any]:
        """Build the bundled Example_Rotary pattern when Nucleus is offline."""

        # Example_Rotary uses four azimuth groups and 32 evenly spaced
        # elevation channels, followed by eight 3.5 us firing-time groups.
        elevations = np.tile(np.linspace(-15.0, 10.0, 32, dtype=np.float32), 4)
        azimuths = np.repeat(np.array([-3.0, -1.0, 1.0, 3.0], dtype=np.float32), 32)
        fire_times = np.repeat(np.arange(8, dtype=np.uint32) * 3500, 16)
        channels = np.arange(1, 129, dtype=np.uint32)
        return {
            "omni:sensor:Core:rayType": "IDEALIZED",
            "omni:sensor:Core:rangeResolutionM": 0.004,
            "omni:sensor:Core:rangeAccuracyM": 0.02,
            "omni:sensor:Core:maxReturns": 2,
            # The experimental Lidar API expects native Python sequences here.
            # Passing pxr.Vt.FloatArray/UIntArray objects through its
            # attributes dictionary makes Fabric report an invalid array data
            # source during startup on Isaac Sim 6.x.
            "omni:sensor:Core:emitterState:s001:azimuthDeg": azimuths.tolist(),
            "omni:sensor:Core:emitterState:s001:elevationDeg": elevations.tolist(),
            "omni:sensor:Core:emitterState:s001:fireTimeNs": fire_times.tolist(),
            "omni:sensor:Core:emitterState:s001:channelId": channels.tolist(),
            "omni:sensor:Core:emitterState:s001:distanceCorrectionM": [0.0] * 128,
            "omni:sensor:Core:emitterState:s001:focalDistM": [0.0] * 128,
            "omni:sensor:Core:emitterState:s001:focalSlope": [0.0] * 128,
            "omni:sensor:Core:emitterState:s001:horOffsetM": [0.0] * 128,
            "omni:sensor:Core:emitterState:s001:vertOffsetM": [0.0] * 128,
        }

    attributes = {
        "omni:sensor:Core:outputFrameOfReference": "SENSOR",
        "omni:sensor:Core:nearRangeM": 1.0,
        "omni:sensor:Core:farRangeM": float(far_range_m),
        # Isaac Sim 5.x/6.x can otherwise return a valid GMO buffer with
        # numElements == 0 for rotary sensors.  NVIDIA documents this as a
        # workaround for the invalid-point dropping path.
        "omni:sensor:Core:skipDroppingInvalidPoints": True,
        "omni:sensor:Core:scanType": "ROTARY",
        "omni:sensor:Core:rotationDirection": "CW",
        "omni:sensor:Core:scanRateBaseHz": int(round(tick_rate_hz)),
        "omni:sensor:Core:patternFiringRateHz": 36000,
        "omni:sensor:Core:numberOfEmitters": 128,
        "omni:sensor:Core:validStartAzimuthDeg": 0.0,
        "omni:sensor:Core:validEndAzimuthDeg": 360.0,
        "omni:sensor:Core:startAzimuthOffsetDeg": 0.0,
    }
    if config.strip().lower() in {
        "single_emitter_rotary",
        "single-emitter-rotary",
        "debug_single_emitter",
    }:
        # Do not hand-author emitterState arrays through the experimental
        # Lidar constructor.  Isaac Sim 6.0.1 can close Kit while Fabric is
        # converting those arrays.  Example_Rotary is the shipped,
        # schema-valid 3D rotary profile and is the safe compatibility
        # profile for this project.  The former 2D profile produced only a
        # horizontal scan plane at the sensor height, so box side surfaces
        # could not appear in the point cloud.
        print(
            "[RTG LiDAR] using built-in Example_Rotary 3D compatibility "
            "profile"
        )
        config = "Example_Rotary"
        # Keep all intrinsic firing/scan attributes from the shipped USD
        # asset.  Overriding scanType, patternFiringRateHz, emitter count, or
        # azimuth bounds on a named asset can produce a valid GMO header whose
        # every range is zero.  Only the project-level range/workaround values
        # are safe to override.
        attributes = {
            "omni:sensor:Core:nearRangeM": 1.0,
            "omni:sensor:Core:farRangeM": float(far_range_m),
            "omni:sensor:Core:skipDroppingInvalidPoints": True,
        }
    try:
        return Lidar.create(
            lidar_path,
            config=config,
            tick_rate=tick_rate_hz,
            aux_output_level="FULL",
            accumulate_outputs=True,
            attributes=attributes,
            translations=sensor_translation,
            orientations=orientations,
        )
    except RuntimeError as exc:
        # Isaac Sim's named lidar assets are hosted in the Isaac Nucleus asset
        # root.  The schema defaults contain the same 128-channel rotary
        # pattern as Example_Rotary, so allow an offline local run to proceed.
        if "assets root folder" not in str(exc).lower():
            raise
        print(
            "[RTG LiDAR] named asset unavailable; using local "
            f"OmniLidar schema defaults for {config!r}"
        )
        attributes.update(offline_rotary_attributes())
        return Lidar(
            lidar_path,
            tick_rate=tick_rate_hz,
            aux_output_level="FULL",
            accumulate_outputs=True,
            attributes=attributes,
            translations=sensor_translation,
            orientations=orientations,
        )


def _attach_lidar_outputs(service: RtgYardLidarService, draw_point_size: float) -> None:
    """Attach writers to both RTX sensors after playback warmup."""

    global _service
    if service.writer is not None and service.down_writer is not None:
        return
    import omni.replicator.core as rep
    from isaacsim.sensors.experimental.rtx import LidarSensor

    _service = service
    bound_name = "RtgYardLidarPointCloudWriter"
    try:
        rep.WriterRegistry.register(RtgYardLidarPointCloudWriter)
    except Exception:
        pass
    def attach_one(lidar: Any, label: str) -> tuple[Any, _PointCloudWriter]:
        sensor = LidarSensor(lidar, annotators=["generic-model-output"])
        writer = sensor.attach_writer(bound_name)
        try:
            sensor.attach_writer(
                "draw-point-cloud",
                size=float(draw_point_size),
                color=[0.0, 1.0, 0.5, 1.0]
                if label == "horizontal"
                else [1.0, 0.35, 0.05, 1.0],
            )
        except Exception as exc:
            print(f"[RTG LiDAR] {label} viewport debug draw unavailable: {exc}")
        return sensor, writer

    service.sensor, service.writer = attach_one(service.lidar, "horizontal")
    service.down_sensor, service.down_writer = attach_one(
        service.down_lidar, "downward"
    )
    print("[RTG LiDAR] writers attached after playback warmup")


def show_rtg_lidar_position_markers(
    stage: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Create/update the temporary blue L1-L24 installation-point spheres."""

    from rtg_lidar_position_markers import (
        show_rtg_lidar_position_markers as _show_markers,
    )

    return _show_markers(stage, **kwargs)


def remove_rtg_lidar_position_markers(stage: Any = None) -> dict[str, Any]:
    """Remove the temporary blue L1-L24 installation-point spheres."""

    from rtg_lidar_position_markers import (
        remove_rtg_lidar_position_markers as _remove_markers,
    )

    return _remove_markers(stage)


def start_rtg_yard_lidar(
    stage: Any = None,
    *,
    config: str = DEFAULT_CONFIG,
    output_dir: Path | str = OUTPUT_DIR,
    translation: tuple[float, float, float] = DEFAULT_SENSOR_TRANSLATION,
    tick_rate_hz: float = DEFAULT_TICK_RATE_HZ,
    far_range_m: float = DEFAULT_FAR_RANGE_M,
    down_far_range_m: float = DEFAULT_DOWN_FAR_RANGE_M,
    draw_point_size: float = 0.05,
    voxel_size_m: float = DEFAULT_VOXEL_SIZE_M,
    max_accumulated_points: int = DEFAULT_MAX_ACCUMULATED_POINTS,
    show_position_markers: bool = True,
) -> dict[str, Any]:
    """Create the horizontal/downward trolley-mounted LiDAR pair."""

    global _service
    if _service is not None:
        return _service.status()
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.append(str(SCRIPT_DIR))

    import numpy as np
    import omni
    from isaacsim.core.experimental.utils.app import enable_extension
    from isaacsim.sensors.experimental.rtx import Lidar

    enable_extension("isaacsim.sensors.rtx.nodes")
    stage = stage or omni.usd.get_context().get_stage()
    if stage is None:
        raise RuntimeError("No USD stage is open. Open smart_port.usda first.")
    trolley = stage.GetPrimAtPath(TROLLEY_PATH)
    if not trolley or not trolley.IsValid():
        raise RuntimeError(f"Primary RTG trolley was not found: {TROLLEY_PATH}")
    if tick_rate_hz <= 0:
        raise ValueError("tick_rate_hz must be greater than zero")
    if far_range_m <= 0:
        raise ValueError("far_range_m must be greater than zero")
    if down_far_range_m <= 0:
        raise ValueError("down_far_range_m must be greater than zero")

    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    from pxr import Gf, Usd, UsdGeom

    trolley_matrix = UsdGeom.Xformable(trolley).ComputeLocalToWorldTransform(
        Usd.TimeCode.Default()
    )
    sensor_position = trolley_matrix.Transform(Gf.Vec3d(*translation))
    sensor_world_translation = (
        float(sensor_position[0]),
        float(sensor_position[1]),
        float(sensor_position[2]),
    )
    down_sensor_local_translation = (
        float(translation[0]),
        float(translation[1]),
        float(translation[2]) + DOWN_SENSOR_VERTICAL_OFFSET_M,
    )
    down_sensor_position = trolley_matrix.Transform(
        Gf.Vec3d(*down_sensor_local_translation)
    )
    down_sensor_world_translation = (
        float(down_sensor_position[0]),
        float(down_sensor_position[1]),
        float(down_sensor_position[2]),
    )
    _author_lidar_mount(stage, sensor_world_translation)
    _author_lidar_mount(
        stage, down_sensor_world_translation, mount_path=DOWN_MOUNT_PATH
    )

    # RTX sensor prims must not be authored into the USD session layer on
    # Isaac Sim 6.0.x.  A session-layer OmniLidar can produce an invalid GMO
    # buffer (bad magic / zero elements) even when the same sensor is valid in
    # the stage's normal edit layer.  Keep only the visual helper in the
    # session layer; create the actual sensor in the current scene layer.
    lidar = _create_lidar(
        Lidar,
        LIDAR_PATH,
        config,
        tick_rate_hz,
        float(far_range_m),
        np,
        translations=sensor_world_translation,
    )
    down_lidar = _create_lidar(
        Lidar,
        DOWN_LIDAR_PATH,
        config,
        tick_rate_hz,
        float(down_far_range_m),
        np,
        translations=down_sensor_world_translation,
        orientations=DOWNWARD_ORIENTATION_WXYZ,
    )
    # Reassert the downward pose after the referenced profile has been
    # composed.  Also apply the setting to a referenced child path when the
    # asset loader returns one, because the wrapper may not be the requested
    # root path on all Isaac Sim builds.
    for sensor_path in _sensor_prim_paths(
        down_lidar, DOWN_LIDAR_PATH
    ):
        _set_prim_orientation(
            stage, sensor_path, DOWNWARD_ORIENTATION_WXYZ
        )
    for lidar_object, requested_path in (
        (lidar, LIDAR_PATH),
        (down_lidar, DOWN_LIDAR_PATH),
    ):
        for sensor_path in _sensor_prim_paths(lidar_object, requested_path):
            lidar_prim = stage.GetPrimAtPath(sensor_path)
            if not lidar_prim or not lidar_prim.IsValid():
                continue
            # Re-assert the workaround after applying a named profile.  Some
            # profile versions author this field with a stronger/default
            # opinion than the attributes passed to Lidar.create().
            skip_invalid = lidar_prim.GetAttribute(
                "omni:sensor:Core:skipDroppingInvalidPoints"
            )
            if skip_invalid:
                skip_invalid.Set(True)
    service = RtgYardLidarService(
        stage=stage,
        sensor=None,
        lidar=lidar,
        lidar_path=LIDAR_PATH,
        down_sensor=None,
        down_lidar=down_lidar,
        down_lidar_path=DOWN_LIDAR_PATH,
        output_dir=output_path,
        writer=None,  # replaced immediately below
        down_writer=None,
        sensor_local_translation=tuple(float(value) for value in translation),
        down_sensor_local_translation=down_sensor_local_translation,
        accumulated_points=np.empty((0, 3), dtype=np.float32),
        accumulated_sensor_points=np.empty((0, 3), dtype=np.float32),
        voxel_size_m=float(voxel_size_m),
        max_accumulated_points=int(max_accumulated_points),
        draw_point_size=float(draw_point_size),
    )
    # Keep the sensor object alive, but deliberately do not attach a writer
    # while the stage is still paused.  Isaac Sim 6.0.1 has a multi-tick/FIF
    # race where an OmniLidar attached during startup can permanently produce
    # valid GMO headers with zero elements.  capture_rtg_yard_pointcloud()
    # attaches writers after playback warmup.
    _service = service

    position_marker_result = None
    if show_position_markers:
        try:
            from rtg_lidar_position_markers import (
                show_rtg_lidar_position_markers,
            )

            position_marker_result = show_rtg_lidar_position_markers(stage)
        except Exception as error:
            # Marker geometry is a convenience layer and must not prevent the
            # RTX sensors from starting on an asset revision without C markers.
            print(f"[RTG LiDAR] position markers skipped: {error}")

    print(
        "[RTG LiDAR] horizontal + downward sensors created at the trolley "
        "world pose; writers will attach after playback warmup at "
        f"{LIDAR_PATH} and {DOWN_LIDAR_PATH}; "
        f"downOrientationWXYZ={DOWNWARD_ORIENTATION_WXYZ}; "
        f"horizontalPosition={sensor_world_translation}; "
        f"downPosition={down_sensor_world_translation}; output={output_path}"
    )
    print(
        "[RTG LiDAR] commands: capture_rtg_yard_pointcloud(), "
        "save_rtg_yard_map(), save_rtg_yard_sensor_scan(), "
        "show_rtg_lidar_position_markers(), stop_rtg_yard_lidar()"
    )
    result = service.status()
    if position_marker_result is not None:
        result["positionMarkers"] = {
            "rootPath": position_marker_result.get("rootPath"),
            "count": position_marker_result.get("count", 0),
            "anchorCount": position_marker_result.get("anchorCount", 0),
            "fallbackCount": position_marker_result.get("fallbackCount", 0),
            "followPrimaryRtg": position_marker_result.get("followPrimaryRtg", False),
        }
    return result


async def capture_rtg_yard_pointcloud(
    *,
    timeout_frames: int = 240,
    output_stem: str = "rtg_yard_capture",
    output_frame: str = "sensor",
    warmup_frames: int = DEFAULT_SENSOR_WARMUP_FRAMES,
    min_scans: int = DEFAULT_CAPTURE_SCANS,
    reset_accumulation: bool = True,
    hold_pose: bool = False,
) -> dict[str, Any]:
    """Accumulate several fresh LiDAR packets, then export a dense point cloud.

    ``output_frame="sensor"`` (the default) exports coordinates relative to
    the RTG-mounted LiDAR, so the radar is at ``(0, 0, 0)``.  Use
    ``output_frame="world"`` when a global yard map is required.  The service
    retains both frames during capture.

    ``min_scans`` defaults to eight so the result contains multiple rotary
    passes instead of the sparse line pattern from a single packet.  Set it
    to ``1`` when a single-frame cloud is explicitly required.  By default a
    capture starts a new accumulation; pass ``reset_accumulation=False`` to
    continue an existing map.  Use ``hold_pose=True`` only when the RTG is not
    already frozen by ``freeze_rtg_animations()``; for a dense static scan,
    prefer the authored session-layer freeze so LiDAR time can advance.

    This function is intentionally async because Script Editor itself runs an
    asyncio task.  Calling ``app.update()`` synchronously from that task causes
    Kit UI callbacks to re-enter the event loop and produces ``Cannot enter
    into task`` errors.
    """

    global _service
    if _service is None:
        start_rtg_yard_lidar()
    assert _service is not None
    import numpy as np
    import omni.kit.app
    import omni.timeline

    sensor_world_translation = _sync_runtime_lidar_pose(_service)
    output_frame = str(output_frame).strip().lower()
    if output_frame not in {"sensor", "world"}:
        raise ValueError("output_frame must be either 'sensor' or 'world'")
    min_scans = int(min_scans)
    if min_scans < 1:
        raise ValueError("min_scans must be at least 1")
    if reset_accumulation:
        _service.accumulated_points = np.empty((0, 3), dtype=np.float32)
        _service.accumulated_sensor_points = np.empty((0, 3), dtype=np.float32)

    timeline = omni.timeline.get_timeline_interface()
    static_time_s: float | None = None
    try:
        # Keep the pose at the instant capture starts.  RTX 6.0.x still needs
        # an advancing/playable timeline pulse.  Only explicitly requested
        # pose holding resets the time; dense captures leave it advancing so
        # the rotary pattern can produce multiple passes.
        if hold_pose:
            static_time_s = max(1.0, float(timeline.get_current_time()))
    except AttributeError:
        pass

    def tick_static_timeline() -> None:
        if static_time_s is not None:
            timeline.set_current_time(static_time_s)

    was_playing = bool(timeline.is_playing())
    needs_output_attach = (
        _service.writer is None or _service.down_writer is None
    )
    if not was_playing:
        # A freshly opened headless stage may have an end time of zero/one,
        # which makes ``play()`` stop before RTX can render a scan.  Reserve
        # enough simulated time for at least one rotary sweep.
        current_time = float(timeline.get_current_time())
        required_end_time = current_time + max(5.0, float(timeout_frames) / 60.0 + 1.0)
        try:
            if float(timeline.get_end_time()) < required_end_time:
                timeline.set_end_time(required_end_time)
        except AttributeError:
            timeline.set_end_time(required_end_time)
        timeline.play()
    try:
        if needs_output_attach:
            # Let the timeline/render loop initialize the OmniLidar before
            # creating its RenderProduct and writers.  This avoids the
            # Isaac Sim 6.x multi-tick/FIF startup race that can permanently
            # produce zero-element GMO frames.
            for _ in range(max(1, int(warmup_frames))):
                tick_static_timeline()
                await omni.kit.app.get_app().next_update_async()
            _attach_lidar_outputs(
                _service, draw_point_size=_service.draw_point_size
            )
        assert _service.sensor is not None
        assert _service.writer is not None
        assert _service.down_sensor is not None
        assert _service.down_writer is not None
        streams = (
            (
                "horizontal",
                _service.sensor,
                _service.lidar_path,
                _service.writer,
            ),
            (
                "down",
                _service.down_sensor,
                _service.down_lidar_path,
                _service.down_writer,
            ),
        )
        start_frames = {key: writer.frame_count for key, _, _, writer in streams}
        target_frames = {
            key: start_frames[key] + min_scans for key, _, _, _ in streams
        }
        for _ in range(max(1, int(timeout_frames))):
            tick_static_timeline()
            await omni.kit.app.get_app().next_update_async()

            # The official LidarSensor API exposes the raw annotator data
            # directly.  Some Isaac Sim/Replicator versions invoke a custom
            # writer with an empty GenericModelOutput payload even though the
            # sensor itself has data.  Keep polling until all requested scans
            # arrive; the GMO timestamp prevents duplicate packets from being
            # added while the sensor waits for its next rotary sample.
            for sensor_key, sensor, lidar_path, writer in streams:
                if writer.frame_count >= target_frames[sensor_key]:
                    continue
                writer.direct_poll_count += 1
                try:
                    direct_result = sensor.get_data("generic-model-output")
                    if isinstance(direct_result, tuple):
                        direct_raw = direct_result[0]
                    else:
                        direct_raw = direct_result
                    if direct_raw is not None:
                        point_count = _service._ingest_generic_model_output(
                            direct_raw,
                            sensor_key=sensor_key,
                            lidar_path=lidar_path,
                            writer=writer,
                        )
                        if point_count > 0:
                            writer.direct_data_count += 1
                except Exception as error:
                    # Keep the other sensor path alive.  The final timeout
                    # status identifies which sensor did not produce data.
                    if writer.direct_poll_count == 1:
                        print(
                            f"[RTG LiDAR] {sensor_key} direct sensor read "
                            f"unavailable: {error}"
                        )
            if all(
                writer.frame_count >= target_frames[key]
                for key, _, _, writer in streams
            ):
                break
    finally:
        if not was_playing:
            timeline.stop()
    if (
        _service.writer.last_sensor_points is None
        or _service.down_writer.last_sensor_points is None
        or _service.writer.frame_count < target_frames["horizontal"]
        or _service.down_writer.frame_count < target_frames["down"]
        or not len(
            _service.accumulated_sensor_points
            if output_frame == "sensor"
            else _service.accumulated_points
        )
    ):
        status = _service.status()
        raise TimeoutError(
            "RTX LiDAR did not produce the requested number of scans. "
            f"Horizontal={status['scans']}/{target_frames['horizontal'] - start_frames['horizontal']}; "
            f"downward={status['downScans']}/{target_frames['down'] - start_frames['down']}. "
            "The horizontal stream is usable; inspect the downward raw/valid "
            "counters below rather than waiting longer. "
            f"Writer status: {status}"
        )
    points = (
        _service.accumulated_sensor_points
        if output_frame == "sensor"
        else _service.accumulated_points
    )
    bundle = write_pointcloud_bundle(
        _service.output_dir, output_stem, points
    )
    print(
        f"[RTG LiDAR] captured {len(points)} {output_frame}-frame points across "
        f"{_service.writer.frame_count - start_frames['horizontal']} horizontal + "
        f"{_service.down_writer.frame_count - start_frames['down']} downward "
        f"scans: {bundle}"
    )
    return {
        "pointCount": int(len(points)),
        "scanCount": int(_service.writer.frame_count - start_frames["horizontal"]),
        "downScanCount": int(
            _service.down_writer.frame_count - start_frames["down"]
        ),
        "frame": output_frame,
        "sensorWorldPosition": [float(value) for value in sensor_world_translation],
        "bundle": bundle,
    }


def save_rtg_yard_map(output_stem: str = "rtg_yard_map") -> dict[str, str]:
    """Export the voxel-thinned accumulated map collected so far."""

    if _service is None:
        raise RuntimeError("start_rtg_yard_lidar() has not been called")
    bundle = _service.save_accumulated(output_stem)
    print(f"[RTG LiDAR] accumulated map: {bundle}")
    return bundle


def save_rtg_yard_sensor_scan(
    output_stem: str = "rtg_yard_sensor_scan"
) -> dict[str, str]:
    """Export the currently accumulated cloud in the RTG LiDAR frame."""

    if _service is None:
        raise RuntimeError("start_rtg_yard_lidar() has not been called")
    bundle = _service.save_sensor_accumulated(output_stem)
    print(f"[RTG LiDAR] sensor-frame scan: {bundle}")
    return bundle


def get_rtg_yard_lidar_status() -> dict[str, Any]:
    if _service is None:
        return {"running": False}
    result = _service.status()
    result["running"] = True
    return result


def diagnose_rtg_yard_lidar() -> dict[str, Any]:
    """Return runtime sensor, pose, and render-product diagnostics."""

    import omni.usd
    from pxr import Usd, UsdGeom

    result: dict[str, Any] = {
        "running": _service is not None,
        "sensorPath": LIDAR_PATH,
        "downSensorPath": DOWN_LIDAR_PATH,
        "trolleyPath": TROLLEY_PATH,
    }
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        result["stageOpen"] = False
        return result
    result["stageOpen"] = True

    lidar = stage.GetPrimAtPath(LIDAR_PATH)
    result.update(
        {
            "sensorValid": bool(lidar and lidar.IsValid()),
            "sensorType": lidar.GetTypeName() if lidar and lidar.IsValid() else None,
            "sensorSchemas": lidar.GetAppliedSchemas() if lidar and lidar.IsValid() else [],
        }
    )
    if lidar and lidar.IsValid():
        transform = UsdGeom.Xformable(lidar).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()
        )
        position = transform.ExtractTranslation()
        result["sensorWorldPosition"] = [
            float(position[0]),
            float(position[1]),
            float(position[2]),
        ]
        for name in (
            "omni:sensor:Core:nearRangeM",
            "omni:sensor:Core:farRangeM",
            "omni:sensor:Core:scanType",
            "omni:sensor:Core:scanRateBaseHz",
            "omni:sensor:Core:numberOfEmitters",
            "omni:sensor:Core:numberOfChannels",
            "omni:sensor:Core:validStartAzimuthDeg",
            "omni:sensor:Core:validEndAzimuthDeg",
            "omni:sensor:Core:skipDroppingInvalidPoints",
            "omni:sensor:tickRate",
            "omni:sensor:Core:accumulateOutputs",
        ):
            attribute = lidar.GetAttribute(name)
            result[name] = attribute.Get() if attribute else None

    down_lidar = stage.GetPrimAtPath(DOWN_LIDAR_PATH)
    result["downSensorValid"] = bool(down_lidar and down_lidar.IsValid())
    result["downSensorType"] = (
        down_lidar.GetTypeName() if down_lidar and down_lidar.IsValid() else None
    )
    if down_lidar and down_lidar.IsValid():
        transform = UsdGeom.Xformable(down_lidar).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()
        )
        position = transform.ExtractTranslation()
        result["downSensorWorldPosition"] = [
            float(position[0]),
            float(position[1]),
            float(position[2]),
        ]
        try:
            from pxr import Gf

            ray_direction = transform.TransformDir(Gf.Vec3d(1.0, 0.0, 0.0))
            result["downSensorLocalPlusXWorldDirection"] = [
                float(ray_direction[0]),
                float(ray_direction[1]),
                float(ray_direction[2]),
            ]
        except Exception as error:
            result["downSensorDirectionError"] = str(error)
        for name in (
            "omni:sensor:Core:nearRangeM",
            "omni:sensor:Core:farRangeM",
            "omni:sensor:Core:skipDroppingInvalidPoints",
        ):
            attribute = down_lidar.GetAttribute(name)
            result[f"down.{name}"] = attribute.Get() if attribute else None

    if _service is not None and _service.sensor is not None:
        try:
            result["annotators"] = list(_service.sensor.annotators)
        except Exception as error:
            result["annotatorsError"] = str(error)
        try:
            product = _service.sensor.render_product
            result["renderProduct"] = str(product.GetPath() if product else product)
        except Exception as error:
            result["renderProductError"] = str(error)
    if _service is not None and _service.down_sensor is not None:
        try:
            result["downAnnotators"] = list(_service.down_sensor.annotators)
        except Exception as error:
            result["downAnnotatorsError"] = str(error)
        try:
            product = _service.down_sensor.render_product
            result["downRenderProduct"] = str(
                product.GetPath() if product else product
            )
        except Exception as error:
            result["downRenderProductError"] = str(error)

    sample_container = stage.GetPrimAtPath(
        "/World/LiveContainers/YardInventory/Bay_3C_002/R01_T01_Container"
    )
    result["sampleContainerValid"] = bool(
        sample_container and sample_container.IsValid()
    )
    if sample_container and sample_container.IsValid():
        transform = UsdGeom.Xformable(sample_container).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()
        )
        position = transform.ExtractTranslation()
        result["sampleContainerWorldPosition"] = [
            float(position[0]),
            float(position[1]),
            float(position[2]),
        ]
    return result


def create_rtg_lidar_probe() -> dict[str, Any]:
    """Create temporary session-layer walls around the sensor for diagnosis.

    The probe is deliberately separate from the live yard.  If the LiDAR
    returns points from these plain USD cubes, the RTX sensor is healthy and
    the remaining issue is live-yard geometry visibility/material composition.
    """

    if _service is None:
        raise RuntimeError("start_rtg_yard_lidar() has not been called")
    import omni.usd
    from pxr import Gf, Sdf, Usd, UsdGeom

    stage = omni.usd.get_context().get_stage()
    if stage is None:
        raise RuntimeError("No USD stage is open")
    probe_root_path = "/World/RTG_LIDAR_PROBE"
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        if stage.GetPrimAtPath(probe_root_path):
            stage.RemovePrim(Sdf.Path(probe_root_path))
        lidar = stage.GetPrimAtPath(LIDAR_PATH)
        if not lidar or not lidar.IsValid():
            raise RuntimeError(f"LiDAR prim is not valid: {LIDAR_PATH}")
        sensor_matrix = UsdGeom.Xformable(lidar).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()
        )
        sensor_position = sensor_matrix.ExtractTranslation()
        root = UsdGeom.Xform.Define(stage, probe_root_path)
        root.AddTranslateOp().Set(sensor_position)
        panels = (
            ("PosX", (15.0, 0.0, 0.0), (0.25, 12.0, 12.0)),
            ("NegX", (-15.0, 0.0, 0.0), (0.25, 12.0, 12.0)),
            ("PosY", (0.0, 15.0, 0.0), (12.0, 0.25, 12.0)),
            ("NegY", (0.0, -15.0, 0.0), (12.0, 0.25, 12.0)),
        )
        for name, translation, scale in panels:
            panel = UsdGeom.Cube.Define(stage, f"{probe_root_path}/{name}")
            panel.AddTranslateOp().Set(Gf.Vec3d(*translation))
            panel.AddScaleOp().Set(Gf.Vec3f(*scale))
            panel.CreateDisplayColorAttr().Set([Gf.Vec3f(0.9, 0.2, 0.05)])
    result = {
        "probePath": probe_root_path,
        "sensorPosition": [
            float(sensor_position[0]),
            float(sensor_position[1]),
            float(sensor_position[2]),
        ],
        "message": "Temporary session-layer LiDAR probe created",
    }
    print(f"[RTG LiDAR] probe created: {result}")
    return result


def remove_rtg_lidar_probe() -> None:
    """Remove the temporary session-layer LiDAR diagnostic probe."""

    import omni.usd
    from pxr import Sdf, Usd

    stage = omni.usd.get_context().get_stage()
    if stage is None:
        return
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        if stage.GetPrimAtPath("/World/RTG_LIDAR_PROBE"):
            stage.RemovePrim(Sdf.Path("/World/RTG_LIDAR_PROBE"))
    print("[RTG LiDAR] temporary probe removed")


def stop_rtg_yard_lidar() -> None:
    """Detach writers and remove the generated sensor/mount from the stage."""

    global _service
    if _service is None:
        return
    import omni.usd
    from pxr import Sdf

    for sensor in (_service.sensor, _service.down_sensor):
        if sensor is None:
            continue
        for writer_name in ("RtgYardLidarPointCloudWriter", "draw-point-cloud"):
            try:
                sensor.detach_writer(writer_name)
            except Exception:
                pass
    stage = omni.usd.get_context().get_stage()
    if stage is not None:
        try:
            from rtg_lidar_position_markers import (
                remove_rtg_lidar_position_markers,
            )

            remove_rtg_lidar_position_markers(stage)
        except Exception as error:
            print(f"[RTG LiDAR] position marker cleanup skipped: {error}")
        # Runtime sensor and guide housing are authored in the normal active
        # layer; removing them from Session Layer would leave stale prims.
        for prim_path in (
            LIDAR_PATH,
            DOWN_LIDAR_PATH,
            MOUNT_PATH,
            DOWN_MOUNT_PATH,
        ):
            if stage.GetPrimAtPath(prim_path):
                stage.RemovePrim(Sdf.Path(prim_path))
    _service = None
    print("[RTG LiDAR] stopped and removed horizontal/downward runtime sensors")


__all__ = [
    "capture_rtg_yard_pointcloud",
    "diagnose_rtg_yard_lidar",
    "get_rtg_yard_lidar_status",
    "create_rtg_lidar_probe",
    "remove_rtg_lidar_probe",
    "show_rtg_lidar_position_markers",
    "save_rtg_yard_map",
    "save_rtg_yard_sensor_scan",
    "spherical_to_cartesian",
    "start_rtg_yard_lidar",
    "stop_rtg_yard_lidar",
]
