"""Create and capture the 24 drawing LiDARs L1-L24.

This is the multi-sensor test path.  Each L position becomes a separate
``OmniLidar`` at a root-level path under ``/World/RTG_LIDAR_FLEET``.  The
initial coordinates are resolved from the existing green C camera markers;
the blue marker script and this module therefore use the same calibration
anchors.  The resulting point clouds are written per sensor and as a merged
world-coordinate cloud.

The fleet is intentionally opt-in because 24 RTX render products are much
heavier than the normal two-sensor yard scan.  Use one scan first::

    exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_yard_lidar.py", encoding="utf-8").read())
    print(start_rtg_lidar_fleet())

    import asyncio

    asyncio.ensure_future(
        capture_rtg_lidar_fleet_pointcloud(timeout_frames=600, min_scans=1)
    )

The fleet is a first-pass geometric sensor layout.  The drawing does not
contain calibrated sensor orientations, so the group defaults are: trolley,
spreader, top girder and top-frame units point down; left electrical/end units
point toward -X; right end units point toward +X.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import importlib
from pathlib import Path
import sys
from typing import Any, Optional, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "rtg_pointcloud"
FLEET_ROOT_PATH = "/World/RTG_LIDAR_FLEET"
PRIMARY_PATH = "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC"
DEFAULT_FLEET_CONFIG = "Example_Rotary"
DEFAULT_REPRESENTATIVE_CONFIG = "Single_Emitter_Rotary"
DEFAULT_FLEET_TICK_RATE_HZ = 1.0
DEFAULT_FLEET_FAR_RANGE_M = 150.0
DEFAULT_FLEET_WARMUP_FRAMES = 10
DEFAULT_FLEET_CAPTURE_SCANS = 1
DEFAULT_FLEET_VOXEL_SIZE_M = 0.05
DEFAULT_FLEET_MAX_POINTS = 500_000

# One representative sensor from each row/installation zone in the supplied
# table: trolley frame, spreader, electrical-room side, left/right gantry end,
# outer electrical-room side, trolley roof, and main girder.
REPRESENTATIVE_LIDAR_NAMES = (
    "L1",
    "L6",
    "L14",
    "L18",
    "L20",
    "L22",
    "L23",
    "L24",
)

# The experimental RTX Lidar API uses a quaternion in wxyz order.  The ray
# direction of the sensor is its local +X axis, matching the existing
# downward unit in rtg_yard_lidar.py.
DOWNWARD_WXYZ = (0.7071067812, 0.0, 0.7071067812, 0.0)
POSITIVE_X_WXYZ = (1.0, 0.0, 0.0, 0.0)
NEGATIVE_X_WXYZ = (0.0, 0.0, 1.0, 0.0)

_fleet_service: Optional["RtgLidarFleetService"] = None


@dataclass
class FleetSensorState:
    name: str
    requested_path: str
    actual_path: str
    lidar: Any
    position: tuple[float, float, float]
    anchor: str
    orientation_wxyz: tuple[float, float, float, float]
    sensor: Optional[Any] = None
    scan_count: int = 0
    last_timestamp_ns: int = 0
    direct_poll_count: int = 0
    direct_data_count: int = 0
    latest_point_count: int = 0
    latest_sensor_points: Any = None
    latest_world_points: Any = None
    latest_bundle: Optional[dict[str, str]] = None
    last_raw_element_count: int = 0
    last_valid_point_count: int = 0


@dataclass
class RtgLidarFleetService:
    stage: Any
    output_dir: Path
    sensors: list[FleetSensorState]
    config: str
    tick_rate_hz: float
    far_range_m: float
    voxel_size_m: float
    max_points: int
    draw_point_size: float
    draw_point_cloud: bool
    accumulated_world_points: Any = None
    marker_purposes: dict[str, Any] = field(default_factory=dict)

    def status(self) -> dict[str, Any]:
        attached = sum(1 for item in self.sensors if item.sensor is not None)
        scans = {item.name: item.scan_count for item in self.sensors}
        valid = {item.name: item.last_valid_point_count for item in self.sensors}
        positions = {
            item.name: [float(value) for value in item.position]
            for item in self.sensors
        }
        return {
            "running": True,
            "sensorRoot": FLEET_ROOT_PATH,
            "sensorCount": len(self.sensors),
            "sensorNames": [item.name for item in self.sensors],
            "sensorsAttached": attached,
            "allSensorsAttached": attached == len(self.sensors),
            "config": self.config,
            "tickRateHz": self.tick_rate_hz,
            "farRangeM": self.far_range_m,
            "outputDir": str(self.output_dir),
            "scans": scans,
            "totalScans": int(sum(scans.values())),
            "lastValidPointCounts": valid,
            "accumulatedWorldPointCount": int(
                0
                if self.accumulated_world_points is None
                else len(self.accumulated_world_points)
            ),
            "positions": positions,
            "paths": {item.name: item.actual_path for item in self.sensors},
        }


def _resolve_stage(stage: Any = None) -> Any:
    if stage is not None:
        return stage
    import omni.usd  # type: ignore

    resolved = omni.usd.get_context().get_stage()
    if resolved is None:
        raise RuntimeError("Open smart_port.usda before starting the RTG LiDAR fleet")
    return resolved


def _base_module() -> Any:
    """Return the already loaded single-LiDAR module when possible."""

    for module_name in ("rtg_yard_lidar_live", "rtg_yard_lidar", "__main__"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "_create_lidar"):
            return module
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.append(str(SCRIPT_DIR))
    return importlib.import_module("rtg_yard_lidar")


def _marker_module() -> Any:
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.append(str(SCRIPT_DIR))
    return importlib.import_module("rtg_lidar_position_markers")


def _normalize_sensor_names(
    sensor_names: Optional[Sequence[str]],
) -> tuple[str, ...]:
    available = tuple(
        spec.name for spec in _marker_module().LIDAR_MARKER_SPECS
    )
    if sensor_names is None:
        return available
    requested = tuple(
        str(name).strip().upper() for name in sensor_names if str(name).strip()
    )
    unknown = tuple(name for name in requested if name not in available)
    if unknown:
        raise ValueError(
            f"Unknown LiDAR names: {unknown}; expected names are L1-L24"
        )
    if not requested:
        raise ValueError("sensor_names must contain at least one L1-L24 name")
    if len(set(requested)) != len(requested):
        raise ValueError("sensor_names must not contain duplicate LiDAR names")
    return requested


def _fleet_orientation(name: str) -> tuple[float, float, float, float]:
    number = int(str(name).lstrip("L"))
    if number in (18, 19, 22):
        return NEGATIVE_X_WXYZ
    if number in (20, 21):
        return POSITIVE_X_WXYZ
    return DOWNWARD_WXYZ


def _resolve_marker_positions(
    stage: Any,
    sensor_names: Optional[Sequence[str]] = None,
) -> list[dict[str, Any]]:
    """Resolve all L positions using the same C-anchor code as blue markers."""

    from pxr import Gf, Usd, UsdGeom

    marker_module = _marker_module()
    camera_paths = marker_module._camera_marker_paths(stage)
    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    # A temporary use of the marker module's pure position resolver keeps the
    # visual and sensor positions identical without requiring guide spheres to
    # be visible during a point-cloud capture.
    selected = set(_normalize_sensor_names(sensor_names))
    result: list[dict[str, Any]] = []
    for spec in marker_module.LIDAR_MARKER_SPECS:
        if spec.name not in selected:
            continue
        position, source = marker_module._position_for_spec(
            stage,
            spec,
            camera_paths,
            cache,
            Gf,
            UsdGeom,
        )
        result.append(
            {
                "name": spec.name,
                "position": (
                    float(position[0]),
                    float(position[1]),
                    float(position[2]),
                ),
                "anchor": str(source),
                "orientation": _fleet_orientation(spec.name),
            }
        )
    if not result:
        raise RuntimeError("No selected LiDAR marker positions were resolved")
    return result


def _set_root_purpose(stage: Any, purpose: Any) -> dict[str, Any]:
    """Temporarily switch L marker spheres between Rendered and Guide."""

    from pxr import Usd, UsdGeom

    marker_root = stage.GetPrimAtPath("/World/RTG_LIDAR_POSITION_MARKERS")
    if not marker_root or not marker_root.IsValid():
        return {}
    saved: dict[str, Any] = {}
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for prim in marker_root.GetChildren():
            name = str(prim.GetName())
            if not name.startswith("L"):
                continue
            purpose_attr = UsdGeom.Imageable(prim).GetPurposeAttr()
            saved[name] = purpose_attr.Get()
            purpose_attr.Set(purpose)
    return saved


def _restore_marker_purposes(stage: Any, saved: dict[str, Any]) -> None:
    if not saved:
        return
    from pxr import Usd, UsdGeom

    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for name, purpose in saved.items():
            prim = stage.GetPrimAtPath(
                f"/World/RTG_LIDAR_POSITION_MARKERS/{name}"
            )
            if prim and prim.IsValid():
                UsdGeom.Imageable(prim).GetPurposeAttr().Set(purpose)


def _ensure_visible_markers(stage: Any) -> None:
    marker_root = stage.GetPrimAtPath("/World/RTG_LIDAR_POSITION_MARKERS")
    marker_count = 0
    if marker_root and marker_root.IsValid():
        marker_count = sum(
            1
            for prim in marker_root.GetChildren()
            if str(prim.GetName()).startswith("L")
        )
    if marker_count == 24:
        return
    _marker_module().show_rtg_lidar_position_markers(
        stage,
        renderable=True,
    )


def _actual_sensor_path(stage: Any, lidar: Any, requested_path: str) -> str:
    base = _base_module()
    for path in base._sensor_prim_paths(lidar, requested_path):
        prim = stage.GetPrimAtPath(path)
        if prim and prim.IsValid():
            return path
    return requested_path


def _author_fleet_lidars(
    stage: Any,
    *,
    config: str,
    tick_rate_hz: float,
    far_range_m: float,
    sensor_names: Optional[Sequence[str]] = None,
) -> list[FleetSensorState]:
    import numpy as np
    from isaacsim.sensors.experimental.rtx import Lidar

    base = _base_module()
    positions = _resolve_marker_positions(stage, sensor_names)
    result: list[FleetSensorState] = []
    try:
        for item in positions:
            name = str(item["name"])
            requested_path = f"{FLEET_ROOT_PATH}/{name}"
            lidar = base._create_lidar(
                Lidar,
                requested_path,
                config,
                tick_rate_hz,
                far_range_m,
                np,
                translations=item["position"],
                orientations=item["orientation"],
            )
            actual_path = _actual_sensor_path(stage, lidar, requested_path)
            for sensor_path in base._sensor_prim_paths(lidar, requested_path):
                base._set_prim_orientation(
                    stage,
                    sensor_path,
                    item["orientation"],
                )
                prim = stage.GetPrimAtPath(sensor_path)
                if prim and prim.IsValid():
                    skip_invalid = prim.GetAttribute(
                        "omni:sensor:Core:skipDroppingInvalidPoints"
                    )
                    if skip_invalid:
                        skip_invalid.Set(True)
            result.append(
                FleetSensorState(
                    name=name,
                    requested_path=requested_path,
                    actual_path=actual_path,
                    lidar=lidar,
                    position=item["position"],
                    anchor=item["anchor"],
                    orientation_wxyz=item["orientation"],
                )
            )
    except Exception:
        from pxr import Sdf

        for item in result:
            if stage.GetPrimAtPath(item.requested_path):
                stage.RemovePrim(Sdf.Path(item.requested_path))
        raise
    return result


def _attach_fleet_sensors(
    service: RtgLidarFleetService,
) -> None:
    from isaacsim.sensors.experimental.rtx import LidarSensor

    colors = (
        [0.05, 0.35, 1.0, 1.0],
        [0.15, 0.85, 1.0, 1.0],
        [0.35, 0.10, 1.0, 1.0],
    )
    for index, item in enumerate(service.sensors):
        if item.sensor is not None:
            continue
        item.sensor = LidarSensor(
            item.lidar,
            annotators=["generic-model-output"],
        )
        if not service.draw_point_cloud:
            continue
        try:
            item.sensor.attach_writer(
                "draw-point-cloud",
                size=float(service.draw_point_size),
                color=colors[index % len(colors)],
            )
        except Exception as error:
            print(
                f"[RTG LiDAR fleet] draw-point-cloud unavailable for "
                f"{item.name}: {error}"
            )


def _extract_sensor_points(raw: Any) -> tuple[int, Any] | None:
    import numpy as np

    base = _base_module()
    from isaacsim.sensors.experimental.rtx import parse_generic_model_output_data

    if isinstance(raw, tuple):
        raw = raw[0]
    if isinstance(raw, dict):
        raw = raw.get("data")
    if raw is None:
        return None
    gmo = parse_generic_model_output_data(raw)
    count = int(getattr(gmo, "numElements", 0) or 0)
    if count <= 0:
        return None
    x = base._numpy_array(getattr(gmo, "x", None))
    y = base._numpy_array(getattr(gmo, "y", None))
    z = base._numpy_array(getattr(gmo, "z", None))
    if x is None or y is None or z is None:
        return None
    count = min(count, len(x), len(y), len(z))
    spherical = np.column_stack((x[:count], y[:count], z[:count]))
    valid = np.isfinite(spherical).all(axis=1) & (spherical[:, 2] > 1e-6)
    return count, base.spherical_to_cartesian(spherical[valid])


def _append_world_points(service: RtgLidarFleetService, points: Any) -> None:
    import numpy as np

    cloud = np.asarray(points, dtype=np.float32).reshape(-1, 3)
    if not len(cloud):
        return
    if service.voxel_size_m > 0:
        keys = np.floor(cloud / service.voxel_size_m).astype(np.int64)
        _, indices = np.unique(keys, axis=0, return_index=True)
        cloud = cloud[np.sort(indices)]
    current = service.accumulated_world_points
    if current is None or not len(current):
        service.accumulated_world_points = cloud[-service.max_points :]
    else:
        service.accumulated_world_points = np.concatenate(
            (current, cloud), axis=0
        )[-service.max_points :]


def _ingest_fleet_packet(
    service: RtgLidarFleetService,
    item: FleetSensorState,
    raw: Any,
    *,
    time_code: Any,
    output_frame: str,
) -> int:
    import numpy as np

    parsed = _extract_sensor_points(raw)
    if parsed is None:
        return 0
    raw_count, sensor_points = parsed
    item.last_raw_element_count = int(raw_count)
    item.last_valid_point_count = int(len(sensor_points))
    if not len(sensor_points):
        return 0
    base = _base_module()
    from pxr import Usd, UsdGeom

    gmo = raw[0] if isinstance(raw, tuple) else raw
    if isinstance(gmo, dict):
        gmo = gmo.get("data")
    timestamp_ns = 0
    try:
        timestamp_ns = int(
            getattr(
                __import__(
                    "isaacsim.sensors.experimental.rtx",
                    fromlist=["parse_generic_model_output_data"],
                ),
                "parse_generic_model_output_data",
            )(gmo).timestampNs
            or 0
        )
    except Exception:
        pass
    if timestamp_ns and timestamp_ns == item.last_timestamp_ns:
        return 0

    stage = service.stage
    world_matrix = base._make_world_transform(stage, item.actual_path, time_code)
    world_points = base._transform_points(sensor_points, world_matrix)
    world_points = world_points[np.isfinite(world_points).all(axis=1)]
    if not len(world_points):
        return 0
    item.latest_sensor_points = sensor_points
    item.latest_world_points = world_points
    item.latest_point_count = int(len(world_points))
    item.scan_count += 1
    item.last_timestamp_ns = timestamp_ns
    cloud = sensor_points if output_frame == "sensor" else world_points
    from rtg_yard_lidar import write_pointcloud_bundle

    item.latest_bundle = write_pointcloud_bundle(
        service.output_dir,
        f"rtg_lidar_fleet_{item.name}_{output_frame}_{item.scan_count:06d}",
        cloud,
    )
    _append_world_points(service, world_points)
    return int(len(world_points))


def start_rtg_lidar_fleet(
    stage: Any = None,
    *,
    config: str = DEFAULT_FLEET_CONFIG,
    output_dir: Path | str = OUTPUT_DIR,
    tick_rate_hz: float = DEFAULT_FLEET_TICK_RATE_HZ,
    far_range_m: float = DEFAULT_FLEET_FAR_RANGE_M,
    draw_point_size: float = 0.035,
    draw_point_cloud: bool = True,
    voxel_size_m: float = DEFAULT_FLEET_VOXEL_SIZE_M,
    max_points: int = DEFAULT_FLEET_MAX_POINTS,
    show_position_markers: bool = True,
    sensor_names: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """Create the selected independent RTX LiDAR sensors at L1-L24."""

    global _fleet_service
    if _fleet_service is not None:
        return _fleet_service.status()
    base = _base_module()
    if getattr(base, "_service", None) is not None:
        print(
            "[RTG LiDAR fleet] stopping the existing 2-sensor service "
            "before creating the 24-sensor fleet"
        )
        base.stop_rtg_yard_lidar()
    resolved = _resolve_stage(stage)
    if not resolved.GetPrimAtPath(PRIMARY_PATH):
        raise RuntimeError(f"Primary RTG was not found: {PRIMARY_PATH}")
    if tick_rate_hz <= 0 or far_range_m <= 0:
        raise ValueError("tick_rate_hz and far_range_m must be greater than zero")
    if max_points <= 0:
        raise ValueError("max_points must be greater than zero")
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.append(str(SCRIPT_DIR))
    import omni.usd  # type: ignore  # noqa: F401
    from pxr import UsdGeom

    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    UsdGeom.Xform.Define(resolved, FLEET_ROOT_PATH)
    sensors = _author_fleet_lidars(
        resolved,
        config=config,
        tick_rate_hz=float(tick_rate_hz),
        far_range_m=float(far_range_m),
        sensor_names=sensor_names,
    )
    _fleet_service = RtgLidarFleetService(
        stage=resolved,
        output_dir=output_path,
        sensors=sensors,
        config=str(config),
        tick_rate_hz=float(tick_rate_hz),
        far_range_m=float(far_range_m),
        voxel_size_m=float(voxel_size_m),
        max_points=int(max_points),
        draw_point_size=float(draw_point_size),
        draw_point_cloud=bool(draw_point_cloud),
    )
    if show_position_markers:
        try:
            _ensure_visible_markers(resolved)
        except Exception as error:
            print(f"[RTG LiDAR fleet] blue marker display skipped: {error}")
    result = _fleet_service.status()
    print(
        f"[RTG LiDAR fleet] created {len(sensors)} independent RTX LiDARs "
        f"({', '.join(item.name for item in sensors)}): "
        f"root={FLEET_ROOT_PATH}, output={output_path}, "
        f"config={config}, draw={draw_point_cloud}"
    )
    return result


def start_rtg_lidar_representative_fleet(
    stage: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Create one low-load representative RTX LiDAR for each table zone."""

    kwargs.setdefault("config", DEFAULT_REPRESENTATIVE_CONFIG)
    # The point cloud is still polled from generic-model-output and written to
    # PLY/PCD/NPY.  Avoiding eight live draw-point-cloud writers is important on
    # an 8 GiB GPU and keeps this mode focused on capture rather than viewport
    # visualization.
    kwargs.setdefault("draw_point_cloud", False)
    kwargs.setdefault("sensor_names", REPRESENTATIVE_LIDAR_NAMES)
    return start_rtg_lidar_fleet(stage, **kwargs)


def get_rtg_lidar_fleet_status() -> dict[str, Any]:
    if _fleet_service is None:
        return {"running": False}
    return _fleet_service.status()


async def capture_rtg_lidar_fleet_pointcloud(
    *,
    timeout_frames: int = 600,
    output_stem: str = "rtg_lidar_fleet_capture",
    output_frame: str = "world",
    warmup_frames: int = DEFAULT_FLEET_WARMUP_FRAMES,
    min_scans: int = DEFAULT_FLEET_CAPTURE_SCANS,
    reset_accumulation: bool = True,
    hold_pose: bool = False,
) -> dict[str, Any]:
    """Capture at least ``min_scans`` from every selected LiDAR."""

    global _fleet_service
    if _fleet_service is None:
        start_rtg_lidar_fleet()
    assert _fleet_service is not None
    service = _fleet_service
    output_frame = str(output_frame).strip().lower()
    if output_frame not in {"sensor", "world"}:
        raise ValueError("output_frame must be either 'sensor' or 'world'")
    timeout_frames = int(timeout_frames)
    min_scans = int(min_scans)
    if timeout_frames < 1 or min_scans < 1:
        raise ValueError("timeout_frames and min_scans must be at least 1")
    import numpy as np
    import omni.kit.app
    import omni.timeline
    from pxr import Usd, UsdGeom

    if reset_accumulation:
        service.accumulated_world_points = np.empty((0, 3), dtype=np.float32)
        for item in service.sensors:
            item.scan_count = 0
            item.last_timestamp_ns = 0
            item.latest_bundle = None

    timeline = omni.timeline.get_timeline_interface()
    static_time_s: float | None = None
    if hold_pose:
        static_time_s = max(1.0, float(timeline.get_current_time()))

    def tick_static_timeline() -> None:
        if static_time_s is not None:
            timeline.set_current_time(static_time_s)

    was_playing = bool(timeline.is_playing())
    saved_purposes: dict[str, Any] = {}
    if not was_playing:
        current_time = float(timeline.get_current_time())
        required_end_time = current_time + max(
            10.0,
            float(timeout_frames) / 30.0 + 2.0,
        )
        try:
            if float(timeline.get_end_time()) < required_end_time:
                timeline.set_end_time(required_end_time)
        except AttributeError:
            timeline.set_end_time(required_end_time)
        timeline.play()

    try:
        # Guide-purpose spheres are excluded from RTX returns while the fleet
        # test runs, then their original visible purpose is restored.
        saved_purposes = _set_root_purpose(service.stage, UsdGeom.Tokens.guide)
        if not all(item.sensor is not None for item in service.sensors):
            for _ in range(max(1, int(warmup_frames))):
                tick_static_timeline()
                await omni.kit.app.get_app().next_update_async()
            _attach_fleet_sensors(service)

        targets = {item.name: item.scan_count + min_scans for item in service.sensors}
        for _ in range(timeout_frames):
            tick_static_timeline()
            await omni.kit.app.get_app().next_update_async()
            time_code = timeline.get_current_time() * float(
                service.stage.GetTimeCodesPerSecond() or 1.0
            )
            for item in service.sensors:
                if item.sensor is None or item.scan_count >= targets[item.name]:
                    continue
                item.direct_poll_count += 1
                try:
                    raw = item.sensor.get_data("generic-model-output")
                    point_count = _ingest_fleet_packet(
                        service,
                        item,
                        raw,
                        time_code=Usd.TimeCode(time_code),
                        output_frame=output_frame,
                    )
                    if point_count > 0:
                        item.direct_data_count += 1
                except Exception as error:
                    if item.direct_poll_count == 1:
                        print(
                            f"[RTG LiDAR fleet] {item.name} read unavailable: {error}"
                        )
            if all(
                item.scan_count >= targets[item.name] for item in service.sensors
            ):
                break
    finally:
        _restore_marker_purposes(service.stage, saved_purposes)
        if not was_playing:
            timeline.stop()

    missing = {
        item.name: item.scan_count
        for item in service.sensors
        if item.scan_count < min_scans
    }
    if missing or service.accumulated_world_points is None or not len(
        service.accumulated_world_points
    ):
        raise TimeoutError(
            "RTX LiDAR fleet did not produce the requested scans for every sensor. "
            f"Missing={missing}; status={service.status()}"
        )

    from rtg_yard_lidar import write_pointcloud_bundle

    world_bundle = write_pointcloud_bundle(
        service.output_dir,
        f"{output_stem}_world",
        service.accumulated_world_points,
    )
    per_sensor = {
        item.name: {
            "scans": item.scan_count,
            "pointCount": item.latest_point_count,
            "bundle": item.latest_bundle,
            "position": [float(value) for value in item.position],
            "orientationWXYZ": [float(value) for value in item.orientation_wxyz],
        }
        for item in service.sensors
    }
    result = {
        "sensorCount": len(service.sensors),
        "scanCountPerSensor": min(item.scan_count for item in service.sensors),
        "totalPointCount": int(len(service.accumulated_world_points)),
        "outputFrame": output_frame,
        "worldBundle": world_bundle,
        "perSensor": per_sensor,
    }
    print(
        f"[RTG LiDAR fleet] captured {len(service.sensors)} sensors, "
        f"{result['totalPointCount']} merged world points: {world_bundle}"
    )
    return result


def stop_rtg_lidar_fleet(*, remove_markers: bool = False) -> dict[str, Any]:
    """Detach the selected sensors and remove their runtime USD prims."""

    global _fleet_service
    if _fleet_service is None:
        return {"stopped": False}
    service = _fleet_service
    sensor_count = len(service.sensors)
    for item in service.sensors:
        if item.sensor is None:
            continue
        try:
            item.sensor.detach_writer("draw-point-cloud")
        except Exception:
            pass
    from pxr import Sdf

    if service.stage is not None:
        for item in service.sensors:
            if service.stage.GetPrimAtPath(item.requested_path):
                service.stage.RemovePrim(Sdf.Path(item.requested_path))
        if service.stage.GetPrimAtPath(FLEET_ROOT_PATH):
            service.stage.RemovePrim(Sdf.Path(FLEET_ROOT_PATH))
        if remove_markers:
            try:
                _marker_module().remove_rtg_lidar_position_markers(service.stage)
            except Exception as error:
                print(f"[RTG LiDAR fleet] marker cleanup skipped: {error}")
    _fleet_service = None
    print(f"[RTG LiDAR fleet] stopped and removed {sensor_count} runtime sensors")
    return {"stopped": True, "sensorCount": sensor_count}


__all__ = [
    "FLEET_ROOT_PATH",
    "DEFAULT_REPRESENTATIVE_CONFIG",
    "REPRESENTATIVE_LIDAR_NAMES",
    "capture_rtg_lidar_fleet_pointcloud",
    "get_rtg_lidar_fleet_status",
    "start_rtg_lidar_fleet",
    "start_rtg_lidar_representative_fleet",
    "stop_rtg_lidar_fleet",
]
