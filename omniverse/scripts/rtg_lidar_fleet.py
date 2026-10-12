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
top girder and top-frame units point down; the dedicated L6-L13 spreader mode
places four corners plus four side-center units around the spreader and aims
each one inward/downward; left electrical/end units point toward -X; right
end units point toward +X.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import importlib
import math
from pathlib import Path
import sys
from typing import Any, Optional, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "rtg_pointcloud"
FLEET_ROOT_PATH = "/World/RTG_LIDAR_FLEET"
SCAN_VOLUME_ROOT_PATH = "/World/RTG_LIDAR_SCAN_VOLUMES"
PRIMARY_PATH = "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC"
DEFAULT_FLEET_CONFIG = "Example_Rotary"
# Isaac Sim 6.x can fail while resolving the downloaded Ouster profile through
# ``Lidar.create(usd_path=...)``: the file contains an OmniLidar definition,
# but the reference is composed as an unloaded/indirect root and the API then
# reports "Unable to find OmniLidar prim".  Use the shipped schema-valid 3D
# rotary profile as the fleet default.  A local Ouster profile can still be
# passed explicitly after it has been composed into the current Stage.
DEFAULT_REPRESENTATIVE_CONFIG = "Example_Rotary"
DEFAULT_REPRESENTATIVE_VARIANT = None
DEFAULT_FLEET_TICK_RATE_HZ = 1.0
DEFAULT_REPRESENTATIVE_TICK_RATE_HZ = 10.0
DEFAULT_FLEET_FAR_RANGE_M = 150.0
DEFAULT_FLEET_WARMUP_FRAMES = 10
DEFAULT_FLEET_CAPTURE_SCANS = 1
DEFAULT_FLEET_VOXEL_SIZE_M = 0.05
DEFAULT_FLEET_MAX_POINTS = 500_000
DEFAULT_OS1_VERTICAL_FOV_DEG = (-21.48, 20.93)
DEFAULT_SCAN_VOLUME_DISPLAY_RANGE_M = 25.0
# The current controlled scan is placed at 3C/002. Keep only the world-space
# corridor containing that RTG, its spreader, and nearby container stacks.
# This removes distant yard/ship/sky returns that otherwise appear as long
# radial-looking lines when several trolley positions are merged.
DEFAULT_RTG_WORLD_ROI = (
    (-12.0, 15.0),    # world X
    (250.0, 278.0),   # world Y around 3C/002
    (0.25, 15.0),     # world Z: remove road/ground and high background
)
DEFAULT_RTG_CAPTURE_RANGE_M = 35.0

# One representative sensor from each row/installation zone in the supplied
# table: trolley frame, spreader, electrical-room side, left/right gantry end,
# outer electrical-room side, trolley roof, and main girder. L2 is used for
# the trolley-bottom row because the L1 GMO stream is invalid in this stage.
REPRESENTATIVE_LIDAR_NAMES = (
    "L2",
    "L6",
    "L14",
    "L18",
    "L20",
    "L22",
    "L23",
    "L24",
)

# The four sensors on the trolley-frame bottom are the focused test set for
# scanning the container bay directly below the current RTG.
TROLLEY_BOTTOM_LIDAR_NAMES = ("L2", "L3", "L4", "L5")

# The eight sensors installed around the four sides of the spreader: four
# corners plus four side-center units.
SPREADER_LIDAR_NAMES = (
    "L6",
    "L7",
    "L8",
    "L9",
    "L10",
    "L11",
    "L12",
    "L13",
)

# Complete runtime fleet.  Keep this explicit instead of relying on
# ``sensor_names=None`` so launch scripts and capture snippets cannot
# accidentally omit the right-side units.
ALL_LIDAR_NAMES = tuple(f"L{index}" for index in range(1, 25))

# Right-hand installation points in the current RTG drawing: the right side
# of the spreader plus the positive-X gantry end.  These are useful for a
# focused visibility/point-cloud check before running the full 24-sensor scan.
RIGHT_SIDE_LIDAR_NAMES = ("L7", "L9", "L13", "L20", "L21")
# The perimeter sensors look inward and downward from the four spreader sides.
# A 35-degree down tilt keeps the container tops and upper side faces inside
# the OS1 vertical field of view while avoiding a near-vertical view of the
# spreader structure itself.
SPREADER_PERIMETER_TILT_DEG = 35.0

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
    orientation_mode: str = "group_default"
    target_point: Optional[tuple[float, float, float]] = None
    installation_side: Optional[str] = None


@dataclass
class RtgLidarFleetService:
    stage: Any
    output_dir: Path
    sensors: list[FleetSensorState]
    config: str
    variant: str | dict[str, str] | None
    tick_rate_hz: float
    far_range_m: float
    voxel_size_m: float
    max_points: int
    draw_point_size: float
    draw_point_cloud: bool
    accumulated_world_points: Any = None
    marker_purposes: dict[str, Any] = field(default_factory=dict)
    hydra_engine_uid: Optional[int] = None

    def status(self) -> dict[str, Any]:
        attached = sum(1 for item in self.sensors if item.sensor is not None)
        active_annotators: list[str] = []
        for item in self.sensors:
            if item.sensor is None:
                continue
            try:
                if "generic-model-output" in item.sensor.annotators:
                    active_annotators.append(item.name)
            except Exception:
                pass
        scans = {item.name: item.scan_count for item in self.sensors}
        raw_counts = {
            item.name: item.last_raw_element_count for item in self.sensors
        }
        valid = {item.name: item.last_valid_point_count for item in self.sensors}
        positions = {
            item.name: [float(value) for value in item.position]
            for item in self.sensors
        }
        anchors = {item.name: item.anchor for item in self.sensors}
        orientations = {
            item.name: [float(value) for value in item.orientation_wxyz]
            for item in self.sensors
        }
        orientation_modes = {
            item.name: item.orientation_mode for item in self.sensors
        }
        installation_sides = {
            item.name: item.installation_side
            for item in self.sensors
            if item.installation_side is not None
        }
        target_points = {
            item.name: [float(value) for value in item.target_point]
            for item in self.sensors
            if item.target_point is not None
        }
        actual_positions: dict[str, list[float]] = {}
        try:
            base = _base_module()
            for item in self.sensors:
                actual_positions[item.name] = [
                    float(value)
                    for value in base._world_translation(
                        self.stage,
                        item.actual_path,
                    )
                ]
        except Exception:
            actual_positions = {}
        return {
            "running": True,
            "sensorRoot": FLEET_ROOT_PATH,
            "sensorCount": len(self.sensors),
            "sensorNames": [item.name for item in self.sensors],
            "sensorsAttached": attached,
            "allSensorsAttached": attached == len(self.sensors),
            "activeGmoSensors": active_annotators,
            "config": self.config,
            "variant": self.variant,
            "tickRateHz": self.tick_rate_hz,
            "farRangeM": self.far_range_m,
            "outputDir": str(self.output_dir),
            "scans": scans,
            "totalScans": int(sum(scans.values())),
            "lastRawElementCounts": raw_counts,
            "lastValidPointCounts": valid,
            "accumulatedWorldPointCount": int(
                0
                if self.accumulated_world_points is None
                else len(self.accumulated_world_points)
            ),
            "anchors": anchors,
            "positions": positions,
            "actualPositions": actual_positions,
            "orientationsWXYZ": orientations,
            "orientationModes": orientation_modes,
            "installationSides": installation_sides,
            "targetPoints": target_points,
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


def _orientation_for_forward_direction(
    direction: tuple[float, float, float],
) -> tuple[float, float, float, float]:
    """Return the shortest-arc wxyz quaternion from local +X to ``direction``."""

    length = math.sqrt(sum(float(value) ** 2 for value in direction))
    if length <= 1e-9:
        return DOWNWARD_WXYZ
    target = tuple(float(value) / length for value in direction)
    dot = max(-1.0, min(1.0, target[0]))
    if dot < -0.999999:
        return NEGATIVE_X_WXYZ
    # (1, 0, 0) cross target.
    cross = (0.0, -target[2], target[1])
    scale = math.sqrt(2.0 * (1.0 + dot))
    return (
        0.5 * scale,
        cross[0] / scale,
        cross[1] / scale,
        cross[2] / scale,
    )


def _spreader_oblique_orientation(
    position: tuple[float, float, float],
    target: tuple[float, float, float],
) -> tuple[float, float, float, float]:
    """Aim a LiDAR at a container/ground target in the current bay.

    The first implementation used the centre of the eight sensor positions as
    the horizontal target.  That centre is on the spreader itself, not over
    the boxes: in this scene the container rows are about ten metres toward
    world +X.  A direct target point keeps the full OS1 vertical FOV centred on
    the actual stack instead of on empty space below the spreader.  The target
    may be above a low side/leg sensor, so upward directions are valid too.
    """

    direction = (
        float(target[0]) - float(position[0]),
        float(target[1]) - float(position[1]),
        float(target[2]) - float(position[2]),
    )
    return _orientation_for_forward_direction(direction)


def _spreader_perimeter_orientation(
    position: tuple[float, float, float],
    center: tuple[float, float, float],
    side: str,
) -> tuple[float, float, float, float]:
    """Aim one world-space perimeter sensor toward the spreader centre/down."""

    if str(side).strip().lower() not in {
        "front_left",
        "front_right",
        "rear_left",
        "rear_right",
        "front",
        "rear",
        "left",
        "right",
    }:
        raise ValueError(f"Unknown spreader perimeter side: {side!r}")

    inward = (
        float(center[0]) - float(position[0]),
        float(center[1]) - float(position[1]),
    )
    horizontal_length = math.sqrt(inward[0] ** 2 + inward[1] ** 2)
    if horizontal_length <= 1e-9:
        return DOWNWARD_WXYZ
    tilt = math.radians(float(SPREADER_PERIMETER_TILT_DEG))
    world_direction = (
        inward[0] / horizontal_length * math.sin(tilt),
        inward[1] / horizontal_length * math.sin(tilt),
        -math.cos(tilt),
    )
    return _orientation_for_forward_direction(
        world_direction
    )


def _find_nearby_container_scan_target(
    stage: Any,
    reference_position: tuple[float, float, float],
    *,
    max_distance_m: float = 30.0,
) -> tuple[float, float, float] | None:
    """Find a useful box/ground target near one LiDAR installation.

    Live containers are authored as ``Cube`` prims with ``live:bayId`` and
    world-space translations.  Selecting the nearest local group avoids
    aiming across the yard.  High LiDARs target the highest tier; low side
    LiDARs target the upper side of that group so their lower FOV includes the
    ground.
    """

    from pxr import Usd, UsdGeom

    root = stage.GetPrimAtPath("/World/LiveContainers")
    if not root or not root.IsValid():
        return None
    sensor_center = (
        float(reference_position[0]),
        float(reference_position[1]),
        float(reference_position[2]),
    )
    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    candidates: list[tuple[float, tuple[float, float, float]]] = []
    stack = [root]
    while stack:
        prim = stack.pop()
        if not prim or not prim.IsValid():
            continue
        try:
            stack.extend(reversed(list(prim.GetChildren())))
        except Exception:
            pass
        bay_attr = prim.GetAttribute("live:bayId")
        if not bay_attr or not bay_attr.IsValid():
            continue
        try:
            bay_id = bay_attr.Get()
        except Exception:
            continue
        if not bay_id:
            continue
        try:
            position = cache.GetLocalToWorldTransform(prim).ExtractTranslation()
            point = (float(position[0]), float(position[1]), float(position[2]))
        except Exception:
            continue
        distance_sq = (point[0] - sensor_center[0]) ** 2 + (
            point[1] - sensor_center[1]
        ) ** 2
        if distance_sq <= float(max_distance_m) ** 2:
            candidates.append((distance_sq, point))
    if not candidates:
        return None

    # Restrict the target to the nearest local group, then use its highest
    # tier. This avoids aiming at a neighbouring bay when several bays are
    # visible in the same 60 m OS1 scan.
    candidates.sort(key=lambda item: item[0])
    nearest_limit = candidates[min(len(candidates), 40) - 1][0] + 1.5**2
    nearby = [point for distance, point in candidates if distance <= nearest_limit]
    highest_z = max(point[2] for point in nearby)
    top = [point for point in nearby if point[2] >= highest_z - 0.95]
    target_x = sum(point[0] for point in top) / len(top)
    target_y = sum(point[1] for point in top) / len(top)
    # A low side/leg LiDAR should see the box side and ground, not aim at the
    # very top edge from below.  Sensors above the stack keep the top-tier
    # target so their downward FOV reaches both the top and lower ground.
    if sensor_center[2] < highest_z - 1.0:
        target_z = max(0.8, highest_z - 1.5)
    else:
        target_z = highest_z
    return (
        target_x,
        target_y,
        target_z,
    )


def _find_spreader_scan_target(
    stage: Any,
    spreader_items: Sequence[dict[str, Any]],
) -> tuple[float, float, float] | None:
    """Find the nearest/highest target for the spreader sensor group."""

    if not spreader_items:
        return None
    center = (
        sum(float(item["position"][0]) for item in spreader_items)
        / len(spreader_items),
        sum(float(item["position"][1]) for item in spreader_items)
        / len(spreader_items),
        sum(float(item["position"][2]) for item in spreader_items)
        / len(spreader_items),
    )
    return _find_nearby_container_scan_target(stage, center)
    direction = (
        math.sin(tilt) * horizontal[0] / horizontal_length,
        math.sin(tilt) * horizontal[1] / horizontal_length,
        -math.cos(tilt),
    )
    return _orientation_for_forward_direction(direction)


def _resolve_marker_positions(
    stage: Any,
    sensor_names: Optional[Sequence[str]] = None,
) -> list[dict[str, Any]]:
    """Resolve all L positions using the same C-anchor code as blue markers."""

    from pxr import Gf, Usd, UsdGeom

    marker_module = _marker_module()
    camera_paths = marker_module._camera_marker_paths(stage)
    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    spreader_positions = marker_module._spreader_perimeter_positions(
        stage, Gf, UsdGeom
    )
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
            spreader_positions,
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
                "installation_side": spec.installation_side,
            }
        )
    if not result:
        raise RuntimeError("No selected LiDAR marker positions were resolved")

    # L6-L13 are mounted around the four spreader sides.  Aim all of them at
    # the highest nearby container surface instead of at the spreader centre.
    # The old perimeter direction was approximately 55 degrees below
    # horizontal and mostly illuminated the ground/spreader, leaving the
    # container top outside the OS1 vertical field of view.  A target-based
    # direction keeps the box top near the centre of the field while the
    # lower vertical rays still cover the box sides and ground.
    spreader_items = [
        item for item in result if item["name"] in SPREADER_LIDAR_NAMES
    ]
    if spreader_items:
        center = (
            sum(item["position"][0] for item in spreader_items)
            / len(spreader_items),
            sum(item["position"][1] for item in spreader_items)
            / len(spreader_items),
            sum(item["position"][2] for item in spreader_items)
            / len(spreader_items),
        )
        target = _find_spreader_scan_target(stage, spreader_items)
        if target is None:
            # Fallback for stages without live:bayId container metadata.  The
            # target remains below the sensor plane so the vertical FOV points
            # toward boxes/ground instead of the sky.
            target = (center[0] + 8.0, center[1], center[2] - 1.0)
        for item in spreader_items:
            item["target"] = tuple(float(value) for value in target)
            item["orientation"] = _spreader_oblique_orientation(
                item["position"],
                item["target"],
            )
            item["orientation_mode"] = "targeted_downward_container_surface"

    # Re-aim the remaining installation zones as well.  This keeps the
    # opposite gantry side, electrical-room side, legs and top-frame units
    # contributing box/ground returns to the merged cloud instead of only
    # returning their original fixed axis direction.
    spreader_names = {item["name"] for item in spreader_items}
    for item in result:
        if item["name"] in spreader_names:
            continue
        target = _find_nearby_container_scan_target(
            stage,
            tuple(float(value) for value in item["position"]),
        )
        if target is None:
            continue
        item["target"] = tuple(float(value) for value in target)
        item["orientation"] = _spreader_oblique_orientation(
            item["position"],
            item["target"],
        )
        item["orientation_mode"] = "targeted_container_surface_and_ground"
    return result


def _sync_fleet_sensor_poses(service: RtgLidarFleetService) -> None:
    """Refresh root-level sensor transforms from the current RTG pose.

    RTX stability is better when the runtime LiDAR prims stay at the fleet
    root, so the sensors are not parented directly under the animated hoist.
    Refreshing their transforms immediately before capture keeps the physical
    installation aligned with the current spreader pose without rebuilding
    render products.
    """

    base = _base_module()
    resolved = {
        item["name"]: item
        for item in _resolve_marker_positions(
            service.stage,
            sensor_names=tuple(item.name for item in service.sensors),
        )
    }
    for item in service.sensors:
        pose = resolved.get(item.name)
        if pose is None:
            continue
        item.position = tuple(float(value) for value in pose["position"])
        item.anchor = str(pose["anchor"])
        item.orientation_wxyz = tuple(
            float(value) for value in pose["orientation"]
        )
        item.orientation_mode = str(
            pose.get("orientation_mode", item.orientation_mode)
        )
        item.installation_side = (
            str(pose["installation_side"])
            if pose.get("installation_side")
            else None
        )
        item.target_point = (
            tuple(float(value) for value in pose["target"])
            if pose.get("target") is not None
            else None
        )
        base._set_root_translation(
            service.stage,
            item.requested_path,
            item.position,
        )
        for sensor_path in base._sensor_prim_paths(
            item.lidar,
            item.requested_path,
        ):
            base._set_prim_orientation(
                service.stage,
                sensor_path,
                item.orientation_wxyz,
            )


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
            if not purpose_attr or not purpose_attr.IsValid():
                continue
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


def show_rtg_lidar_scan_volumes(
    sensor_names: Optional[Sequence[str]] = None,
    *,
    display_range_m: float = DEFAULT_SCAN_VOLUME_DISPLAY_RANGE_M,
    horizontal_fov_deg: float = 360.0,
    vertical_min_deg: float = DEFAULT_OS1_VERTICAL_FOV_DEG[0],
    vertical_max_deg: float = DEFAULT_OS1_VERTICAL_FOV_DEG[1],
    samples: int = 48,
) -> dict[str, Any]:
    """Draw lightweight USD wireframes for the selected LiDAR scan volumes.

    The wireframes are only a viewport aid; they do not create extra RTX
    render products or change the sensor configuration.  ``display_range_m``
    controls the size of the drawn wireframe, while ``sensorRangeM`` in the
    result reports the actual configured sensor range.
    """

    if _fleet_service is None:
        raise RuntimeError("Start the RTG LiDAR fleet before showing scan volumes")
    if display_range_m <= 0:
        raise ValueError("display_range_m must be greater than zero")
    if horizontal_fov_deg <= 0 or horizontal_fov_deg > 360:
        raise ValueError("horizontal_fov_deg must be in the range (0, 360]")
    if vertical_min_deg >= vertical_max_deg:
        raise ValueError("vertical_min_deg must be less than vertical_max_deg")
    if int(samples) < 8:
        raise ValueError("samples must be at least 8")

    from pxr import Gf, Sdf, Usd, UsdGeom

    service = _fleet_service
    stage = service.stage
    selected = set(_normalize_sensor_names(sensor_names))
    display_range = min(float(display_range_m), float(service.far_range_m))
    sample_count = int(samples)
    half_horizontal = float(horizontal_fov_deg) / 2.0
    azimuths = [
        -half_horizontal + float(horizontal_fov_deg) * index / sample_count
        for index in range(sample_count + 1)
    ]
    colors = (
        Gf.Vec3f(0.10, 0.45, 1.0),
        Gf.Vec3f(0.10, 0.90, 0.95),
        Gf.Vec3f(0.45, 0.20, 1.0),
    )

    existing_root = stage.GetPrimAtPath(SCAN_VOLUME_ROOT_PATH)
    if existing_root and existing_root.IsValid():
        stage.RemovePrim(Sdf.Path(SCAN_VOLUME_ROOT_PATH))

    result: dict[str, Any] = {
        "root": SCAN_VOLUME_ROOT_PATH,
        "sensorCount": 0,
        "displayRangeM": display_range,
        "sensorRangeM": float(service.far_range_m),
        "horizontalFovDeg": float(horizontal_fov_deg),
        "verticalFovDeg": [float(vertical_min_deg), float(vertical_max_deg)],
        "sensors": {},
    }

    base = _base_module()
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        UsdGeom.Xform.Define(stage, SCAN_VOLUME_ROOT_PATH)
        for index, item in enumerate(service.sensors):
            if item.name not in selected:
                continue
            matrix = base._make_world_transform(
                stage,
                item.actual_path,
                Usd.TimeCode.Default(),
            )

            def world_point(point: tuple[float, float, float]) -> Gf.Vec3f:
                transformed = matrix.Transform(Gf.Vec3d(*point))
                return Gf.Vec3f(
                    float(transformed[0]),
                    float(transformed[1]),
                    float(transformed[2]),
                )

            def ray_point(azimuth_deg: float, elevation_deg: float):
                azimuth = math.radians(azimuth_deg)
                elevation = math.radians(elevation_deg)
                cos_elevation = math.cos(elevation)
                return (
                    display_range * cos_elevation * math.cos(azimuth),
                    display_range * cos_elevation * math.sin(azimuth),
                    display_range * math.sin(elevation),
                )

            points: list[Gf.Vec3f] = []
            vertex_counts: list[int] = []

            def add_curve(local_points: list[tuple[float, float, float]]):
                points.extend(world_point(point) for point in local_points)
                vertex_counts.append(len(local_points))

            # Center ray: the sensor's local +X axis is its forward ray.
            add_curve([(0.0, 0.0, 0.0), (display_range, 0.0, 0.0)])
            # Upper and lower vertical-FOV rings.
            for elevation in (float(vertical_min_deg), float(vertical_max_deg)):
                ring = [ray_point(azimuth, elevation) for azimuth in azimuths]
                add_curve(ring)
            # Boundary spokes make the scan direction readable even when the
            # full 360-degree rings are partly hidden by scene geometry.
            boundary_azimuths = (
                azimuths[0],
                azimuths[len(azimuths) // 4],
                azimuths[len(azimuths) // 2],
                azimuths[(3 * len(azimuths)) // 4],
            )
            for azimuth in boundary_azimuths:
                add_curve(
                    [
                        ray_point(azimuth, float(vertical_min_deg)),
                        ray_point(azimuth, float(vertical_max_deg)),
                    ]
                )

            curves = UsdGeom.BasisCurves.Define(
                stage,
                f"{SCAN_VOLUME_ROOT_PATH}/{item.name}",
            )
            curves.CreateTypeAttr().Set(UsdGeom.Tokens.linear)
            curves.CreateCurveVertexCountsAttr().Set(vertex_counts)
            curves.CreatePointsAttr().Set(points)
            curves.CreateWidthsAttr().Set([0.025])
            curves.CreateDisplayColorAttr().Set([colors[index % len(colors)]])

            # Some Isaac Sim viewport modes do not draw unshaded
            # BasisCurves reliably. Add a lightweight translucent Mesh shell
            # so the same range/FOV is visible in the rendered viewport.
            upper_local = [
                ray_point(azimuth, float(vertical_max_deg))
                for azimuth in azimuths[:-1]
            ]
            lower_local = [
                ray_point(azimuth, float(vertical_min_deg))
                for azimuth in azimuths[:-1]
            ]
            shell_points = [world_point((0.0, 0.0, 0.0))]
            shell_points.extend(world_point(point) for point in upper_local)
            shell_points.extend(world_point(point) for point in lower_local)
            ring_size = len(upper_local)
            upper_start = 1
            lower_start = 1 + ring_size
            closed_horizontal = float(horizontal_fov_deg) >= 359.999
            face_counts: list[int] = []
            face_indices: list[int] = []

            def add_face(indices: list[int]) -> None:
                face_counts.append(len(indices))
                face_indices.extend(indices)

            segment_count = ring_size if closed_horizontal else ring_size - 1
            for segment in range(segment_count):
                next_index = (
                    (segment + 1) % ring_size
                    if closed_horizontal
                    else segment + 1
                )
                upper_a = upper_start + segment
                upper_b = upper_start + next_index
                lower_a = lower_start + segment
                lower_b = lower_start + next_index
                add_face([upper_a, upper_b, lower_b, lower_a])
                add_face([0, upper_a, upper_b])
                add_face([0, lower_b, lower_a])

            if not closed_horizontal:
                add_face([0, upper_start, lower_start])
                last_upper = upper_start + ring_size - 1
                last_lower = lower_start + ring_size - 1
                add_face([0, last_lower, last_upper])

            mesh = UsdGeom.Mesh.Define(
                stage,
                f"{SCAN_VOLUME_ROOT_PATH}/{item.name}_surface",
            )
            mesh.CreatePointsAttr().Set(shell_points)
            mesh.CreateFaceVertexCountsAttr().Set(face_counts)
            mesh.CreateFaceVertexIndicesAttr().Set(face_indices)
            mesh.CreateDisplayColorAttr().Set([colors[index % len(colors)]])
            mesh.CreateDisplayOpacityAttr().Set([0.18])
            mesh.CreateDoubleSidedAttr().Set(True)

            result["sensors"][item.name] = {
                "path": item.actual_path,
                "position": [float(value) for value in item.position],
                "sensorRangeM": float(service.far_range_m),
                "displayRangeM": display_range,
                "horizontalFovDeg": float(horizontal_fov_deg),
                "verticalFovDeg": [float(vertical_min_deg), float(vertical_max_deg)],
                "orientationWXYZ": [float(value) for value in item.orientation_wxyz],
            }

    result["sensorCount"] = len(result["sensors"])
    print(f"[RTG LiDAR] scan volumes displayed: {result}", flush=True)
    return result


def show_rtg_lidar_scan_debug_draw(
    sensor_names: Optional[Sequence[str]] = None,
    *,
    display_range_m: float = DEFAULT_SCAN_VOLUME_DISPLAY_RANGE_M,
    horizontal_fov_deg: float = 360.0,
    vertical_min_deg: float = DEFAULT_OS1_VERTICAL_FOV_DEG[0],
    vertical_max_deg: float = DEFAULT_OS1_VERTICAL_FOV_DEG[1],
    samples: int = 36,
    line_size: float = 4.0,
) -> dict[str, Any]:
    """Draw scan axes and FOV rings directly in the Isaac Sim viewport."""

    if _fleet_service is None:
        raise RuntimeError("Start the RTG LiDAR fleet before drawing scan rays")
    if display_range_m <= 0:
        raise ValueError("display_range_m must be greater than zero")
    if horizontal_fov_deg <= 0 or horizontal_fov_deg > 360:
        raise ValueError("horizontal_fov_deg must be in the range (0, 360]")
    if vertical_min_deg >= vertical_max_deg:
        raise ValueError("vertical_min_deg must be less than vertical_max_deg")
    if int(samples) < 8:
        raise ValueError("samples must be at least 8")

    from isaacsim.util.debug_draw import _debug_draw
    from pxr import Gf, Usd

    service = _fleet_service
    selected = set(_normalize_sensor_names(sensor_names))
    display_range = min(float(display_range_m), float(service.far_range_m))
    sample_count = int(samples)
    half_horizontal = float(horizontal_fov_deg) / 2.0
    azimuths = [
        -half_horizontal + float(horizontal_fov_deg) * index / sample_count
        for index in range(sample_count + 1)
    ]
    starts: list[tuple[float, float, float]] = []
    ends: list[tuple[float, float, float]] = []
    colors: list[tuple[float, float, float, float]] = []
    sizes: list[float] = []

    base = _base_module()
    green = (0.05, 1.0, 0.10, 1.0)
    yellow_green = (0.55, 1.0, 0.05, 1.0)
    for item in service.sensors:
        if item.name not in selected:
            continue
        matrix = base._make_world_transform(
            service.stage,
            item.actual_path,
            Usd.TimeCode.Default(),
        )

        def world_point(point: tuple[float, float, float]) -> tuple[float, float, float]:
            transformed = matrix.Transform(Gf.Vec3d(*point))
            return (
                float(transformed[0]),
                float(transformed[1]),
                float(transformed[2]),
            )

        def ray_point(azimuth_deg: float, elevation_deg: float):
            azimuth = math.radians(azimuth_deg)
            elevation = math.radians(elevation_deg)
            cos_elevation = math.cos(elevation)
            return (
                display_range * cos_elevation * math.cos(azimuth),
                display_range * cos_elevation * math.sin(azimuth),
                display_range * math.sin(elevation),
            )

        def add_line(
            start: tuple[float, float, float],
            end: tuple[float, float, float],
            color: tuple[float, float, float, float],
        ) -> None:
            starts.append(world_point(start))
            ends.append(world_point(end))
            colors.append(color)
            sizes.append(float(line_size))

        origin = (0.0, 0.0, 0.0)
        # Green center ray is the current scan direction (+X in sensor frame).
        add_line(origin, (display_range, 0.0, 0.0), green)
        for elevation in (float(vertical_min_deg), float(vertical_max_deg)):
            ring = [ray_point(azimuth, elevation) for azimuth in azimuths]
            for start, end in zip(ring, ring[1:]):
                add_line(start, end, yellow_green)
        # Sparse green spokes make the cone readable without drawing every
        # physical OS1 beam, which would make the viewport unnecessarily busy.
        for azimuth in azimuths[::max(1, sample_count // 12)]:
            add_line(
                origin,
                ray_point(azimuth, float(vertical_max_deg)),
                green,
            )
            add_line(
                origin,
                ray_point(azimuth, float(vertical_min_deg)),
                green,
            )

    draw = _debug_draw.acquire_debug_draw_interface()
    draw.clear_lines()
    if starts:
        draw.draw_lines(starts, ends, colors, sizes)

    result = {
        "sensorCount": len(selected),
        "displayRangeM": display_range,
        "sensorRangeM": float(service.far_range_m),
        "horizontalFovDeg": float(horizontal_fov_deg),
        "verticalFovDeg": [float(vertical_min_deg), float(vertical_max_deg)],
        "lineCount": len(starts),
        "color": "green",
    }
    print(f"[RTG LiDAR] debug scan rays displayed: {result}", flush=True)
    return result


def show_rtg_lidar_position_scan_debug_draw(
    stage: Any = None,
    sensor_names: Optional[Sequence[str]] = None,
    *,
    display_range_m: float = DEFAULT_SCAN_VOLUME_DISPLAY_RANGE_M,
    horizontal_fov_deg: float = 360.0,
    vertical_min_deg: float = DEFAULT_OS1_VERTICAL_FOV_DEG[0],
    vertical_max_deg: float = DEFAULT_OS1_VERTICAL_FOV_DEG[1],
    samples: int = 36,
    line_size: float = 4.0,
) -> dict[str, Any]:
    """Draw planned scan envelopes without authoring any RTX LiDAR prim.

    This is deliberately separate from :func:`show_rtg_lidar_scan_debug_draw`.
    The latter reads transforms from active ``OmniLidar`` prims.  A full
    24-sensor installation preview must remain safe on a small RTX/Hydra
    budget, so this helper works only from the calibrated marker poses and
    creates neither LiDAR prims nor Replicator render products.
    """

    if display_range_m <= 0:
        raise ValueError("display_range_m must be greater than zero")
    if horizontal_fov_deg <= 0 or horizontal_fov_deg > 360:
        raise ValueError("horizontal_fov_deg must be in the range (0, 360]")
    if vertical_min_deg >= vertical_max_deg:
        raise ValueError("vertical_min_deg must be less than vertical_max_deg")
    if int(samples) < 8:
        raise ValueError("samples must be at least 8")

    from isaacsim.util.debug_draw import _debug_draw
    from pxr import Gf

    resolved = _resolve_stage(stage)
    poses = _resolve_marker_positions(resolved, sensor_names)
    display_range = float(display_range_m)
    sample_count = int(samples)
    half_horizontal = float(horizontal_fov_deg) / 2.0
    azimuths = [
        -half_horizontal + float(horizontal_fov_deg) * index / sample_count
        for index in range(sample_count + 1)
    ]
    starts: list[tuple[float, float, float]] = []
    ends: list[tuple[float, float, float]] = []
    colors: list[tuple[float, float, float, float]] = []
    sizes: list[float] = []
    green = (0.05, 1.0, 0.10, 1.0)
    yellow_green = (0.55, 1.0, 0.05, 1.0)

    def ray_point(azimuth_deg: float, elevation_deg: float) -> tuple[float, float, float]:
        azimuth = math.radians(azimuth_deg)
        elevation = math.radians(elevation_deg)
        cos_elevation = math.cos(elevation)
        return (
            display_range * cos_elevation * math.cos(azimuth),
            display_range * cos_elevation * math.sin(azimuth),
            display_range * math.sin(elevation),
        )

    for pose in poses:
        w, x, y, z = (float(value) for value in pose["orientation"])
        matrix = Gf.Matrix4d(1.0)
        matrix.SetRotate(Gf.Quatd(w, Gf.Vec3d(x, y, z)))
        matrix.SetTranslateOnly(Gf.Vec3d(*pose["position"]))

        def world_point(point: tuple[float, float, float]) -> tuple[float, float, float]:
            transformed = matrix.Transform(Gf.Vec3d(*point))
            return (
                float(transformed[0]),
                float(transformed[1]),
                float(transformed[2]),
            )

        def add_line(
            start: tuple[float, float, float],
            end: tuple[float, float, float],
            color: tuple[float, float, float, float],
        ) -> None:
            starts.append(world_point(start))
            ends.append(world_point(end))
            colors.append(color)
            sizes.append(float(line_size))

        origin = (0.0, 0.0, 0.0)
        add_line(origin, (display_range, 0.0, 0.0), green)
        for elevation in (float(vertical_min_deg), float(vertical_max_deg)):
            ring = [ray_point(azimuth, elevation) for azimuth in azimuths]
            for start, end in zip(ring, ring[1:]):
                add_line(start, end, yellow_green)
        for azimuth in azimuths[::max(1, sample_count // 12)]:
            add_line(origin, ray_point(azimuth, float(vertical_max_deg)), green)
            add_line(origin, ray_point(azimuth, float(vertical_min_deg)), green)

    draw = _debug_draw.acquire_debug_draw_interface()
    draw.clear_lines()
    if starts:
        draw.draw_lines(starts, ends, colors, sizes)
    result = {
        "sensorCount": len(poses),
        "displayRangeM": display_range,
        "horizontalFovDeg": float(horizontal_fov_deg),
        "verticalFovDeg": [float(vertical_min_deg), float(vertical_max_deg)],
        "lineCount": len(starts),
        "color": "green",
        "source": "calibrated_installation_positions",
    }
    print(f"[RTG LiDAR] planned scan rays displayed: {result}", flush=True)
    return result


def show_rtg_lidar_position_scan_wireframes(
    stage: Any = None,
    sensor_names: Optional[Sequence[str]] = None,
    *,
    display_range_m: float = DEFAULT_SCAN_VOLUME_DISPLAY_RANGE_M,
    horizontal_fov_deg: float = 360.0,
    vertical_min_deg: float = DEFAULT_OS1_VERTICAL_FOV_DEG[0],
    vertical_max_deg: float = DEFAULT_OS1_VERTICAL_FOV_DEG[1],
    samples: int = 36,
    line_width_m: float = 0.035,
) -> dict[str, Any]:
    """Author green USD scan-range curves without a Hydra debug-draw call.

    Unlike ``isaacsim.util.debug_draw``, USD curves do not modify the global
    viewport tick-rate setting.  They are the safe visual aid for this RTG
    scene, whose existing display engine runs at 0 Hz.
    """

    if display_range_m <= 0 or line_width_m <= 0:
        raise ValueError("display_range_m and line_width_m must be greater than zero")
    if horizontal_fov_deg <= 0 or horizontal_fov_deg > 360:
        raise ValueError("horizontal_fov_deg must be in the range (0, 360]")
    if vertical_min_deg >= vertical_max_deg:
        raise ValueError("vertical_min_deg must be less than vertical_max_deg")
    if int(samples) < 8:
        raise ValueError("samples must be at least 8")

    from pxr import Gf, Sdf, Usd, UsdGeom

    resolved = _resolve_stage(stage)
    poses = _resolve_marker_positions(resolved, sensor_names)
    display_range = float(display_range_m)
    sample_count = int(samples)
    half_horizontal = float(horizontal_fov_deg) / 2.0
    azimuths = [
        -half_horizontal + float(horizontal_fov_deg) * index / sample_count
        for index in range(sample_count + 1)
    ]
    green = Gf.Vec3f(0.05, 1.0, 0.10)
    yellow_green = Gf.Vec3f(0.55, 1.0, 0.05)
    total_line_count = 0

    def ray_point(azimuth_deg: float, elevation_deg: float) -> tuple[float, float, float]:
        azimuth = math.radians(azimuth_deg)
        elevation = math.radians(elevation_deg)
        cos_elevation = math.cos(elevation)
        return (
            display_range * cos_elevation * math.cos(azimuth),
            display_range * cos_elevation * math.sin(azimuth),
            display_range * math.sin(elevation),
        )

    with Usd.EditContext(resolved, resolved.GetSessionLayer()):
        root = resolved.GetPrimAtPath(SCAN_VOLUME_ROOT_PATH)
        if root and root.IsValid():
            resolved.RemovePrim(Sdf.Path(SCAN_VOLUME_ROOT_PATH))
        UsdGeom.Xform.Define(resolved, SCAN_VOLUME_ROOT_PATH)
        for pose in poses:
            w, x, y, z = (float(value) for value in pose["orientation"])
            matrix = Gf.Matrix4d(1.0)
            matrix.SetRotate(Gf.Quatd(w, Gf.Vec3d(x, y, z)))
            matrix.SetTranslateOnly(Gf.Vec3d(*pose["position"]))
            curve_points: list[Any] = []
            curve_counts: list[int] = []

            def add_line(
                start: tuple[float, float, float],
                end: tuple[float, float, float],
                _color: Any,
            ) -> None:
                curve_points.extend(
                    (matrix.Transform(Gf.Vec3d(*start)), matrix.Transform(Gf.Vec3d(*end)))
                )
                curve_counts.append(2)

            origin = (0.0, 0.0, 0.0)
            add_line(origin, (display_range, 0.0, 0.0), green)
            for elevation in (float(vertical_min_deg), float(vertical_max_deg)):
                ring = [ray_point(azimuth, elevation) for azimuth in azimuths]
                for start, end in zip(ring, ring[1:]):
                    add_line(start, end, yellow_green)
            for azimuth in azimuths[::max(1, sample_count // 12)]:
                add_line(origin, ray_point(azimuth, float(vertical_max_deg)), green)
                add_line(origin, ray_point(azimuth, float(vertical_min_deg)), green)

            curves = UsdGeom.BasisCurves.Define(
                resolved,
                f"{SCAN_VOLUME_ROOT_PATH}/{pose['name']}_wireframe",
            )
            curves.CreateTypeAttr().Set(UsdGeom.Tokens.linear)
            curves.CreateBasisAttr().Set(UsdGeom.Tokens.bezier)
            curves.CreateWrapAttr().Set(UsdGeom.Tokens.nonperiodic)
            curves.CreatePointsAttr().Set(curve_points)
            curves.CreateCurveVertexCountsAttr().Set(curve_counts)
            curves.CreateWidthsAttr().Set([float(line_width_m)])
            # Width interpolation is schema metadata, not a separate USD
            # attribute. Isaac Sim's USD binding exposes it as this method.
            # A single value with constant interpolation is valid for every
            # two-point line in the wireframe.
            curves.SetWidthsInterpolation(UsdGeom.Tokens.constant)
            curves.CreateDisplayColorAttr().Set([green])
            total_line_count += len(curve_counts)

    result = {
        "sensorCount": len(poses),
        "displayRangeM": display_range,
        "horizontalFovDeg": float(horizontal_fov_deg),
        "verticalFovDeg": [float(vertical_min_deg), float(vertical_max_deg)],
        "lineCount": total_line_count,
        "color": "green",
        "source": "USD_wireframe",
    }
    print(f"[RTG LiDAR] planned USD scan wireframes displayed: {result}", flush=True)
    return result


def _actual_sensor_path(stage: Any, lidar: Any, requested_path: str) -> str:
    """Resolve the actual OmniLidar prim below a fleet root path.

    ``Lidar.create(config=...)`` can return an authoring object whose cached
    path is an asset child such as ``.../sensor``.  On Isaac Sim 6.x that
    child may not be a composed prim even though the requested root is valid.
    Checking only ``prim.IsValid()`` therefore lets an Xform/invalid child
    path reach ``LidarSensor`` and produces ``No valid sensor paths`` during
    capture.
    """

    base = _base_module()
    candidates = list(base._sensor_prim_paths(lidar, requested_path))
    for path in candidates:
        prim = stage.GetPrimAtPath(path)
        if prim and prim.IsValid() and prim.GetTypeName() == "OmniLidar":
            return path

    root = stage.GetPrimAtPath(requested_path)
    if root and root.IsValid():
        from pxr import Usd

        for prim in Usd.PrimRange(root):
            if prim.GetTypeName() == "OmniLidar":
                return str(prim.GetPath())

    raise RuntimeError(
        "No composed OmniLidar prim found below "
        f"{requested_path}; candidates={candidates}"
    )


def _author_fleet_lidars(
    stage: Any,
    *,
    config: str,
    tick_rate_hz: float,
    far_range_m: float,
    sensor_names: Optional[Sequence[str]] = None,
    variant: str | dict[str, str] | None = None,
) -> list[FleetSensorState]:
    import numpy as np
    from isaacsim.sensors.experimental.rtx import Lidar

    base = _base_module()
    positions = _resolve_marker_positions(stage, sensor_names)
    target = next(
        (item.get("target") for item in positions if item.get("target") is not None),
        None,
    )
    if target is not None:
        print(
            f"[RTG LiDAR] spreader scan target (world m): {tuple(target)}",
            flush=True,
        )
    result: list[FleetSensorState] = []
    config_text = str(config).strip().lower()
    variant_text = str(variant).strip().lower()
    # OS1's native asset is correct when invalid samples are dropped.  The
    # generic rotary compatibility profile needs the historical workaround,
    # so keep that behavior isolated from the Ouster fleet.
    is_ouster_profile = (
        config_text in {"os0", "os1", "os2"}
        or "os0" in config_text
        or "os1" in config_text
        or "os2" in config_text
        or "os0" in variant_text
        or "os1" in variant_text
        or "os2" in variant_text
    )
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
                variant=variant,
            )
            actual_path = _actual_sensor_path(stage, lidar, requested_path)
            # The Ouster profile is a referenced USD asset. On some Isaac Sim
            # 6.x builds its reference/default transform can win over the
            # XformPrim translation passed to Lidar.create(), leaving the
            # scan origin at the stage origin. Reassert the root translation
            # explicitly so exported world points line up with the blue
            # installation marker and the reported position.
            base._set_root_translation(
                stage,
                requested_path,
                item["position"],
            )
            # Only the composed OmniLidar path should receive sensor
            # attributes.  The authoring object's cached child path can be
            # stale (for example ``.../L6/sensor``) on Isaac Sim 6.x.
            for sensor_path in (actual_path,):
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
                        skip_invalid.Set(not is_ouster_profile)
                    if is_ouster_profile:
                        min_reflectance = prim.GetAttribute(
                            "omni:sensor:Core:minReflectance"
                        )
                        if min_reflectance:
                            min_reflectance.Set(0.0)
            result.append(
                FleetSensorState(
                    name=name,
                    requested_path=requested_path,
                    actual_path=actual_path,
                    lidar=lidar,
                    position=item["position"],
                    anchor=item["anchor"],
                    orientation_wxyz=item["orientation"],
                    target_point=(
                        tuple(float(value) for value in item["target"])
                        if item.get("target") is not None
                        else None
                    ),
                    orientation_mode=str(
                        item.get("orientation_mode", "group_default")
                    ),
                    installation_side=(
                        str(item["installation_side"])
                        if item.get("installation_side")
                        else None
                    ),
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

    # Replicator's HydraTexture wrapper only requests an engine; on this
    # Isaac Sim 6.x setup it does not create the non-zero-tick RTX engine.
    # Create the exact engine configuration before constructing LidarSensor.
    _ensure_rtg_hydra_engine(service)
    for index, item in enumerate(service.sensors):
        if item.sensor is not None:
            continue
        item.sensor = LidarSensor(
            item.actual_path,
            annotators=["generic-model-output"],
        )
    if service.draw_point_cloud:
        # Isaac Sim 6.x does not ship the old ``draw-point-cloud`` Replicator
        # writer.  Keep GMO attached for real point-cloud capture and use the
        # version-independent debug-draw path for the visible scan envelopes.
        try:
            show_rtg_lidar_scan_debug_draw(
                sensor_names=tuple(item.name for item in service.sensors),
                display_range_m=min(25.0, float(service.far_range_m)),
                line_size=max(2.0, float(service.draw_point_size) * 100.0),
            )
            print(
                "[RTG LiDAR fleet] draw-point-cloud writer is unavailable; "
                "scan ranges are displayed with debug draw and GMO remains "
                "enabled for point-cloud capture",
                flush=True,
            )
        except Exception as error:
            print(
                f"[RTG LiDAR fleet] scan-range debug draw unavailable: {error}",
                flush=True,
            )


def _destroy_fleet_sensor_wrappers(
    service: RtgLidarFleetService,
) -> None:
    """Destroy fleet render products while keeping the USD LiDAR prims."""

    for item in service.sensors:
        sensor = item.sensor
        if sensor is None:
            continue
        item.sensor = None
        try:
            # LidarSensor owns the Hydra texture and annotators. Explicitly
            # invalidating it before releasing the Python reference avoids
            # leaving a stale render product in the RTX graph.
            sensor._invalidate_sensor()
        except Exception:
            pass
        del sensor


def _ensure_rtg_hydra_device_mask(tick_rate_hz: float = 10.0) -> int:
    """Make Replicator LiDAR products use the viewport's RTX GPU mask.

    Isaac Sim 6.0 exposes ``device_mask`` on the HydraTexture factory, but
    the bundled Replicator wrapper calls it without that keyword and leaves
    the value at zero. The interactive RTX viewport on this workstation is
    created with mask ``1``; a new LiDAR product with mask ``0`` cannot find a
    suitable engine and emits ``unable to find suitable engine``. Install a
    small proxy only on Replicator's existing factory so project code remains
    compatible with the installed Isaac Sim package.
    """

    import carb
    import omni.hydratexture
    from omni.replicator.core.scripts.utils import viewport_manager

    settings = carb.settings.get_settings()
    active_gpu = settings.get("/renderer/activeGpu")
    try:
        active_gpu = int(active_gpu)
    except (TypeError, ValueError):
        active_gpu = -1
    device_mask = 1 << active_gpu if active_gpu >= 0 else 1

    manager = viewport_manager.ViewportManager()
    textures = manager._hydra_textures
    factory_attr = "_MultiTexture__hydra_texture_factory"
    current_factory = getattr(textures, factory_attr, None)
    if current_factory is None:
        current_factory = omni.hydratexture.acquire_hydra_texture_factory_interface()

    if getattr(current_factory, "_rtg_device_mask_proxy_version", 0) >= 3:
        return device_mask

    class _RtgHydraFactoryProxy:
        _rtg_device_mask_proxy = True
        _rtg_device_mask_proxy_version = 3

        def __init__(self, factory: Any, mask: int, tick_rate: int) -> None:
            self._factory = factory
            self._mask = int(mask)
            self._tick_rate = int(tick_rate)

        def create_hydra_texture(self, *args: Any, **kwargs: Any) -> Any:
            kwargs.setdefault("device_mask", self._mask)
            kwargs.setdefault("hydra_tick_rate", self._tick_rate)
            # The interactive RTX viewport is created with the motion-aware
            # Hydra flag (flags=1). Matching it lets the sensor render product
            # reuse the existing engine instead of requesting a new one with
            # creationIndex=UINT_MAX.
            kwargs.setdefault("engine_creation_flags", 1)
            return self._factory.create_hydra_texture(*args, **kwargs)

    hydra_tick_rate = max(1, int(round(float(tick_rate_hz))))
    setattr(
        textures,
        factory_attr,
        _RtgHydraFactoryProxy(current_factory, device_mask, hydra_tick_rate),
    )
    print(
        "[RTG LiDAR] Replicator HydraTexture device mask forced to "
        f"{device_mask} (GPU {active_gpu if active_gpu >= 0 else 0}); "
        f"Hydra tick rate={hydra_tick_rate} Hz",
        flush=True,
    )
    return device_mask


def _ensure_rtg_hydra_engine(service: RtgLidarFleetService) -> int:
    """Create the RTX Hydra engine required by the LiDAR render product.

    The Replicator factory can pass the desired device mask/tick rate, but
    Isaac Sim 6.0 still leaves the engine creation index invalid when no
    matching non-zero-tick engine has been registered.  Register it explicitly
    through the public ``omni.usd`` API before ``LidarSensor`` is constructed.
    """

    if service.hydra_engine_uid is not None:
        return int(service.hydra_engine_uid)

    import omni.usd

    device_mask = _ensure_rtg_hydra_device_mask(service.tick_rate_hz)
    context = omni.usd.get_context()
    config = omni.usd.HydraEngineCreationConfig()
    config.flags = omni.usd.EngineCreationFlags.MOTION_RAYTRACING_ENABLED
    config.tickrate_in_hz = max(1, int(round(float(service.tick_rate_hz))))
    config.device_mask = int(device_mask)
    engine_uid = int(
        omni.usd.create_hydra_engine_with_config("rtx", context, config)
    )
    invalid_uid = int(
        getattr(omni.usd, "HydraEngineInvalidUniqueId", 0xFFFFFFFF)
    )
    if engine_uid == invalid_uid:
        raise RuntimeError(
            "Unable to create the RTX Hydra engine: "
            f"flags=1 tickRateInHz={config.tickrate_in_hz} "
            f"deviceMask={config.device_mask}"
        )

    service.hydra_engine_uid = engine_uid
    print(
        "[RTG LiDAR] explicit RTX Hydra engine created: "
        f"uid={engine_uid}, flags=1, "
        f"tickRateInHz={config.tickrate_in_hz}, "
        f"deviceMask={config.device_mask}",
        flush=True,
    )
    return engine_uid


def _set_serial_active_sensor(
    service: RtgLidarFleetService,
    active_name: str,
) -> None:
    """Enable GMO on one sensor and detach it from other fleet members."""

    from isaacsim.sensors.experimental.rtx import LidarSensor

    for item in service.sensors:
        if item.sensor is None:
            if item.name != active_name:
                continue
            _ensure_rtg_hydra_engine(service)
            item.sensor = LidarSensor(
                item.actual_path,
                annotators=["generic-model-output"],
            )
        try:
            has_gmo = "generic-model-output" in item.sensor.annotators
        except Exception:
            has_gmo = False
        if item.name == active_name:
            if not has_gmo:
                item.sensor.attach_annotators("generic-model-output")
            continue
        if has_gmo:
            item.sensor.detach_annotators("generic-model-output")


def _extract_sensor_points(raw: Any) -> tuple[int, Any] | None:
    import numpy as np

    base = _base_module()
    from isaacsim.sensors.experimental.rtx import parse_generic_model_output_data

    raw = base._coerce_gmo_buffer(raw)
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
    coordinates = np.column_stack((x[:count], y[:count], z[:count]))
    coords_type = getattr(gmo, "elementsCoordsType", None)
    coords_name = str(getattr(coords_type, "name", "")).upper()
    coords_value = getattr(coords_type, "value", None)
    if coords_name == "CARTESIAN" or coords_value == 0:
        sensor_points = coordinates.astype(np.float32, copy=False)
        valid = np.isfinite(sensor_points).all(axis=1) & (
            np.linalg.norm(sensor_points, axis=1) > 1e-6
        )
    else:
        valid = np.isfinite(coordinates).all(axis=1) & (
            coordinates[:, 2] > 1e-6
        )
        sensor_points = base.spherical_to_cartesian(coordinates)
    gmo_valid = base._gmo_valid_mask(gmo, count, np)
    if gmo_valid is not None:
        valid &= gmo_valid
    return count, sensor_points[valid]


def filter_rtg_world_points(
    points: Any,
    *,
    world_roi: Optional[
        tuple[tuple[float, float], tuple[float, float], tuple[float, float]]
    ] = DEFAULT_RTG_WORLD_ROI,
) -> Any:
    """Keep only the RTG/container corridor from a world-coordinate cloud.

    RTX LiDAR returns are geometric only in this capture path; there are no
    semantic labels saying "container" or "RTG". The practical way to
    remove long background/ray-looking returns is therefore a world-space
    ROI. Passing ``world_roi=None`` disables this filter.
    """

    import numpy as np

    cloud = np.asarray(points, dtype=np.float32).reshape(-1, 3)
    if not len(cloud) or world_roi is None:
        return cloud
    if len(world_roi) != 3:
        raise ValueError("world_roi must contain X, Y, and Z bounds")

    valid = np.isfinite(cloud).all(axis=1)
    for axis, bounds in enumerate(world_roi):
        if len(bounds) != 2 or float(bounds[0]) >= float(bounds[1]):
            raise ValueError(
                f"world_roi axis {axis} must be an increasing (min, max) pair"
            )
        valid &= (cloud[:, axis] >= float(bounds[0])) & (
            cloud[:, axis] <= float(bounds[1])
        )
    return cloud[valid]


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

    gmo = base._coerce_gmo_buffer(raw)
    timestamp_ns = 0
    if gmo is not None:
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
    draw_point_cloud: bool = False,
    voxel_size_m: float = DEFAULT_FLEET_VOXEL_SIZE_M,
    max_points: int = DEFAULT_FLEET_MAX_POINTS,
    show_position_markers: bool = True,
    sensor_names: Optional[Sequence[str]] = None,
    variant: str | dict[str, str] | None = None,
    pre_attach: bool = True,
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
        variant=variant,
    )
    _fleet_service = RtgLidarFleetService(
        stage=resolved,
        output_dir=output_path,
        sensors=sensors,
        config=str(config),
        variant=variant,
        tick_rate_hz=float(tick_rate_hz),
        far_range_m=float(far_range_m),
        voxel_size_m=float(voxel_size_m),
        max_points=int(max_points),
        draw_point_size=float(draw_point_size),
        draw_point_cloud=bool(draw_point_cloud),
    )
    # Construct all LidarSensor wrappers before the user presses Play. The
    # RTX multi-sensor/FIF path is sensitive to creating several render
    # products during playback; pre-play attachment is the documented stable
    # path and prevents only one of the eight OS1 sensors from warming up.
    if pre_attach:
        try:
            _attach_fleet_sensors(_fleet_service)
            print(
                f"[RTG LiDAR fleet] sensors attached before playback: "
                f"{len(_fleet_service.sensors)}",
                flush=True,
            )
        except Exception as error:
            print(
                f"[RTG LiDAR fleet] pre-play sensor attachment deferred: {error}",
                flush=True,
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
    kwargs.setdefault("variant", DEFAULT_REPRESENTATIVE_VARIANT)
    kwargs.setdefault("tick_rate_hz", DEFAULT_REPRESENTATIVE_TICK_RATE_HZ)
    # The point cloud is still polled from generic-model-output and written to
    # PLY/PCD/NPY.  Avoiding eight live draw-point-cloud writers is important on
    # an 8 GiB GPU and keeps this mode focused on capture rather than viewport
    # visualization.
    kwargs.setdefault("draw_point_cloud", False)
    kwargs.setdefault("sensor_names", REPRESENTATIVE_LIDAR_NAMES)
    return start_rtg_lidar_fleet(stage, **kwargs)


def start_rtg_lidar_trolley_bottom_fleet(
    stage: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Create only the four downward-facing trolley-bottom LiDARs."""

    kwargs.setdefault("config", DEFAULT_REPRESENTATIVE_CONFIG)
    kwargs.setdefault("variant", DEFAULT_REPRESENTATIVE_VARIANT)
    kwargs.setdefault("tick_rate_hz", DEFAULT_REPRESENTATIVE_TICK_RATE_HZ)
    kwargs.setdefault("draw_point_cloud", False)
    kwargs.setdefault("sensor_names", TROLLEY_BOTTOM_LIDAR_NAMES)
    return start_rtg_lidar_fleet(stage, **kwargs)


def start_rtg_lidar_spreader_fleet(
    stage: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Create L6-L13 on the spreader with overlapping oblique-down views."""

    kwargs.setdefault("config", DEFAULT_REPRESENTATIVE_CONFIG)
    kwargs.setdefault("variant", DEFAULT_REPRESENTATIVE_VARIANT)
    kwargs.setdefault("tick_rate_hz", DEFAULT_REPRESENTATIVE_TICK_RATE_HZ)
    kwargs.setdefault("draw_point_cloud", False)
    kwargs.setdefault("sensor_names", SPREADER_LIDAR_NAMES)
    kwargs.setdefault("pre_attach", False)
    return start_rtg_lidar_fleet(stage, **kwargs)


def start_rtg_lidar_all_fleet(
    stage: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Show every L1-L24 installation point and planned scan envelope.

    No RTX sensor is created in this interactive layout mode.  Use
    :func:`capture_rtg_lidar_fleet_in_batches` after the scene opens to scan
    every position one at a time and write one merged world point cloud.
    """

    # Do not even author 24 ``OmniLidar`` prims during interactive startup.
    # In Isaac Sim 6.x a ticking OmniLidar can request a Hydra engine before a
    # LidarSensor/GMO wrapper is created.  That still produces the repeated
    # ``failed to create Hydra Engine thread`` loop on the current RTX setup.
    # The full-fleet launcher is therefore a visual layout mode.  It shows all
    # blue installation markers and scan ranges without any RTX resources.
    # ``capture_rtg_lidar_fleet_pointcloud`` remains available for a selected
    # small batch after the scene has opened.
    if _fleet_service is not None:
        raise RuntimeError(
            "Stop the active RTX LiDAR fleet before opening the visual-only "
            "full-fleet layout."
        )
    resolved = _resolve_stage(stage)
    config = str(kwargs.get("config", DEFAULT_REPRESENTATIVE_CONFIG))
    far_range = float(kwargs.get("far_range_m", DEFAULT_FLEET_FAR_RANGE_M))
    if far_range <= 0:
        raise ValueError("far_range_m must be greater than zero")
    _ensure_visible_markers(resolved)
    result = {
        "running": False,
        "visualOnly": True,
        "sensorCount": len(ALL_LIDAR_NAMES),
        "sensorNames": list(ALL_LIDAR_NAMES),
        "config": config,
        "farRangeM": far_range,
        "outputDir": str(Path(kwargs.get("output_dir", OUTPUT_DIR)).resolve()),
    }
    try:
        result["scanRangeDisplay"] = show_rtg_lidar_position_scan_wireframes(
            resolved,
            sensor_names=ALL_LIDAR_NAMES,
            display_range_m=min(25.0, far_range),
        )
        print(
            "[RTG LiDAR fleet] visual-only L1-L24 layout is ready; no RTX "
            "LiDAR or Hydra render product was created.",
            flush=True,
        )
    except Exception as error:
        print(
            f"[RTG LiDAR fleet] visual-only scan-range draw unavailable: {error}",
            flush=True,
        )
    return result


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
    serial: bool = True,
) -> dict[str, Any]:
    """Capture at least ``min_scans`` from every selected LiDAR.

    ``serial=True`` enables one GenericModelOutput stream at a time. This
    lowers peak RTX/FIF load for a multi-OS1 fleet while still merging every
    sensor into the final world-frame cloud.
    """

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

    # The LiDAR prims remain root-level for RTX stability, so refresh their
    # transforms from the current hoist/spreader pose before making a scan.
    _sync_fleet_sensor_poses(service)

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
        # Do not reset the Kit timeline while it is playing. RTX rotary
        # sensors need advancing time to complete their firing pattern. The
        # RTG pose is held by --freeze-rtgs (session-layer transform masks),
        # while this function intentionally leaves sensor time advancing.
        return None

    was_playing = bool(timeline.is_playing())
    saved_purposes: dict[str, Any] = {}
    if serial and was_playing:
        # Serial mode rebuilds one render product at a time. Stop an already
        # playing session before releasing the old multi-product wrappers.
        timeline.stop()
    if not serial and not was_playing:
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
        targets = {item.name: item.scan_count + min_scans for item in service.sensors}

        async def poll_one(item: FleetSensorState) -> None:
            if item.sensor is None:
                return
            if item.scan_count >= targets[item.name]:
                return
            time_code = timeline.get_current_time() * float(
                service.stage.GetTimeCodesPerSecond() or 1.0
            )
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

        if serial:
            # Release any launch-time wrappers first. A true serial capture
            # must not leave eight HydraTexture products alive and merely
            # toggle their annotators; that pattern can permanently starve all
            # but the last OS1 sensor on affected RTX/FIF configurations.
            _destroy_fleet_sensor_wrappers(service)
            for index, item in enumerate(service.sensors):
                if item.scan_count >= targets[item.name]:
                    continue
                _set_serial_active_sensor(service, item.name)
                if not timeline.is_playing():
                    timeline.play()
                # Let the selected annotator bind before polling the sensor.
                for _ in range(max(1, int(warmup_frames))):
                    tick_static_timeline()
                    await omni.kit.app.get_app().next_update_async()
                for _ in range(timeout_frames):
                    tick_static_timeline()
                    await omni.kit.app.get_app().next_update_async()
                    await poll_one(item)
                    if item.scan_count >= targets[item.name]:
                        break
                if index < len(service.sensors) - 1:
                    timeline.stop()
                    _destroy_fleet_sensor_wrappers(service)
                    await omni.kit.app.get_app().next_update_async()
        else:
            # Always give every render product a short post-Play warmup. This
            # is needed even when the LidarSensor wrappers were attached
            # pre-play; otherwise the first poll can observe one sensor's
            # ready frame while the remaining products are initializing.
            for _ in range(max(1, int(warmup_frames))):
                tick_static_timeline()
                await omni.kit.app.get_app().next_update_async()
            if not all(item.sensor is not None for item in service.sensors):
                _attach_fleet_sensors(service)
                # Let newly attached products receive at least one complete Kit
                # update before the first direct GMO poll.
                await omni.kit.app.get_app().next_update_async()
            for _ in range(timeout_frames):
                tick_static_timeline()
                await omni.kit.app.get_app().next_update_async()
                for item in service.sensors:
                    if item.sensor is None or item.scan_count >= targets[item.name]:
                        continue
                    await poll_one(item)
                if all(
                    item.scan_count >= targets[item.name] for item in service.sensors
                ):
                    break
    finally:
        _restore_marker_purposes(service.stage, saved_purposes)
        if serial:
            if was_playing:
                timeline.play()
            else:
                timeline.stop()
        elif not was_playing:
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
            "orientationMode": item.orientation_mode,
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


async def capture_rtg_lidar_fleet_in_batches(
    stage: Any = None,
    *,
    sensor_names: Optional[Sequence[str]] = None,
    config: str = DEFAULT_REPRESENTATIVE_CONFIG,
    variant: str | dict[str, str] | None = DEFAULT_REPRESENTATIVE_VARIANT,
    output_dir: Path | str = OUTPUT_DIR,
    output_stem: str = "rtg_lidar_full_capture",
    tick_rate_hz: float = DEFAULT_REPRESENTATIVE_TICK_RATE_HZ,
    far_range_m: float = DEFAULT_FLEET_FAR_RANGE_M,
    timeout_frames: int = 480,
    warmup_frames: int = DEFAULT_FLEET_WARMUP_FRAMES,
    min_scans: int = DEFAULT_FLEET_CAPTURE_SCANS,
    hold_pose: bool = False,
    world_roi: Optional[
        tuple[tuple[float, float], tuple[float, float], tuple[float, float]]
    ] = None,
) -> dict[str, Any]:
    """Capture selected LiDARs one-at-a-time and merge their world points.

    ``LidarSensor`` creates a Hydra render product.  Creating all products at
    launch is not viable on the project's current RTX setup, so this routine
    authors one temporary ``OmniLidar``, captures it, destroys its Hydra
    product and USD prim, then advances to the next installation position.
    It is the safe full-L1-to-L24 capture path, including L7/L9/L13/L20/L21
    on the right-hand side.
    """

    global _fleet_service
    if _fleet_service is not None:
        raise RuntimeError(
            "An RTX LiDAR fleet is already active. Run stop_rtg_lidar_fleet() "
            "before starting a batched capture."
        )
    if tick_rate_hz <= 0 or far_range_m <= 0:
        raise ValueError("tick_rate_hz and far_range_m must be greater than zero")

    import numpy as np
    import omni.kit.app
    from rtg_yard_lidar import write_pointcloud_bundle

    resolved = _resolve_stage(stage)
    selected = _normalize_sensor_names(sensor_names or ALL_LIDAR_NAMES)
    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    merged_batches: list[Any] = []
    captured: dict[str, Any] = {}
    failures: dict[str, str] = {}

    for index, name in enumerate(selected, start=1):
        print(
            f"[RTG LiDAR fleet] batch {index}/{len(selected)} starting: {name}",
            flush=True,
        )
        try:
            start_rtg_lidar_fleet(
                resolved,
                config=config,
                variant=variant,
                output_dir=output_path,
                tick_rate_hz=float(tick_rate_hz),
                far_range_m=float(far_range_m),
                sensor_names=(name,),
                draw_point_cloud=False,
                show_position_markers=False,
                pre_attach=False,
            )
            batch_result = await capture_rtg_lidar_fleet_pointcloud(
                timeout_frames=int(timeout_frames),
                output_stem=f"{output_stem}_{name}",
                output_frame="world",
                warmup_frames=int(warmup_frames),
                min_scans=int(min_scans),
                reset_accumulation=True,
                hold_pose=bool(hold_pose),
                serial=True,
            )
            service = _fleet_service
            if service is not None and service.accumulated_world_points is not None:
                points = np.asarray(
                    service.accumulated_world_points,
                    dtype=np.float32,
                ).reshape(-1, 3)
                if len(points):
                    merged_batches.append(points)
            captured[name] = batch_result.get("perSensor", {}).get(name, batch_result)
        except Exception as error:
            failures[name] = str(error)
            print(
                f"[RTG LiDAR fleet] batch {index}/{len(selected)} failed for "
                f"{name}: {error}",
                flush=True,
            )
        finally:
            # The final serial sensor otherwise keeps a 300x300 Hydra product
            # alive.  Release it before creating the next sensor.
            stop_rtg_lidar_fleet(remove_markers=False)
            await omni.kit.app.get_app().next_update_async()

    if not merged_batches:
        raise TimeoutError(
            "No selected RTX LiDAR produced a point cloud. "
            f"failures={failures}"
        )
    merged = np.concatenate(merged_batches, axis=0)
    raw_merged_count = int(len(merged))
    merged = filter_rtg_world_points(merged, world_roi=world_roi)
    if len(merged) > DEFAULT_FLEET_MAX_POINTS:
        merged = merged[-DEFAULT_FLEET_MAX_POINTS :]
    bundle = write_pointcloud_bundle(output_path, f"{output_stem}_world", merged)
    result = {
        "requestedSensorCount": len(selected),
        "capturedSensorCount": len(captured),
        "failedSensorCount": len(failures),
        "captured": captured,
        "failures": failures,
        "rawPointCountBeforeWorldFilter": raw_merged_count,
        "worldFilter": world_roi,
        "totalPointCount": int(len(merged)),
        "worldBundle": bundle,
    }
    print(
        "[RTG LiDAR fleet] batched capture complete: "
        f"{result['capturedSensorCount']}/{len(selected)} sensors, "
        f"{result['totalPointCount']} merged world points: {bundle}",
        flush=True,
    )
    return result


async def capture_rtg_lidar_fleet_at_trolley_positions(
    stage: Any = None,
    *,
    primary_bay_id: Optional[str] = "3C/002",
    trolley_positions: Sequence[tuple[str, Optional[float]]] = (
        ("current", None),
        ("middle", 9.0),
        ("right", 16.5),
    ),
    sensor_names: Optional[Sequence[str]] = None,
    config: str = DEFAULT_REPRESENTATIVE_CONFIG,
    variant: str | dict[str, str] | None = DEFAULT_REPRESENTATIVE_VARIANT,
    output_dir: Path | str = OUTPUT_DIR,
    output_stem: str = "rtg_lidar_three_trolley_positions",
    tick_rate_hz: float = DEFAULT_REPRESENTATIVE_TICK_RATE_HZ,
    far_range_m: float = DEFAULT_RTG_CAPTURE_RANGE_M,
    timeout_frames: int = 600,
    warmup_frames: int = 60,
    min_scans: int = DEFAULT_FLEET_CAPTURE_SCANS,
    move_wait_frames: int = 30,
    world_roi: Optional[
        tuple[tuple[float, float], tuple[float, float], tuple[float, float]]
    ] = DEFAULT_RTG_WORLD_ROI,
) -> dict[str, Any]:
    """Scan the complete LiDAR fleet at several trolley positions.

    Each position is captured in world coordinates after moving the RTG
    trolley.  The per-position world bundles are then loaded and merged into
    one PLY/PCD/NPY bundle.  ``None`` means use the trolley's current command,
    allowing the first scan to preserve the user's present location.
    """

    if not trolley_positions:
        raise ValueError("trolley_positions must not be empty")

    import numpy as np
    import omni.kit.app

    resolved = _resolve_stage(stage)
    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    selected = tuple(sensor_names or ALL_LIDAR_NAMES)

    import rtg_isaac_physics

    if primary_bay_id:
        print(
            f"[RTG LiDAR fleet] placing primary RTG at {primary_bay_id} "
            "once before trolley-only scans",
            flush=True,
        )
        rtg_isaac_physics.move_primary_rtg_to_bay(
            str(primary_bay_id),
            resolved,
        )

    def move_trolley_only(trolley_m: float) -> dict[str, float]:
        """Move the trolley and preserve the evaluated gantry world position."""

        from pxr import Usd, UsdGeom

        gantry_path = getattr(
            rtg_isaac_physics,
            "GANTRY_PATH",
            "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL",
        )
        gantry = resolved.GetPrimAtPath(gantry_path)
        if not gantry or not gantry.IsValid():
            raise RuntimeError(f"Gantry prim is invalid: {gantry_path}")
        matrix = UsdGeom.Xformable(gantry).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()
        )
        gantry_world_y = float(matrix.ExtractTranslation()[1])
        try:
            cached_targets = rtg_isaac_physics.rtg_status(resolved).get(
                "engineering_m", {}
            )
        except Exception:
            cached_targets = {}
        result = rtg_isaac_physics.set_rtg_visual_targets(
            gantry_m=gantry_world_y,
            trolley_m=float(trolley_m),
            hoist_m=float(cached_targets.get("hoist_m", 14.0)),
            stage=resolved,
        )
        print(
            "[RTG LiDAR fleet] trolley-only move: "
            f"trolley={float(trolley_m):.3f} m, "
            f"preserved gantryWorldY={gantry_world_y:.4f} m",
            flush=True,
        )
        return result

    try:
        current_status = rtg_isaac_physics.rtg_status(resolved)
        current_trolley = float(
            current_status.get("engineering_m", {}).get("trolley_m", 1.6)
        )
    except Exception:
        current_trolley = 1.6

    app = omni.kit.app.get_app()
    per_position: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    point_batches: list[Any] = []

    for label, requested_trolley in trolley_positions:
        name = str(label).strip() or f"position_{len(per_position) + 1}"
        trolley_m = (
            current_trolley
            if requested_trolley is None
            else float(requested_trolley)
        )
        if not 0.0 <= trolley_m <= 18.0:
            raise ValueError(
                f"trolley position {name!r} is outside 0..18 m: {trolley_m}"
            )

        print(
            f"[RTG LiDAR fleet] moving trolley to {name}: "
            f"{trolley_m:.3f} m",
            flush=True,
        )
        move_trolley_only(trolley_m)
        for _ in range(max(1, int(move_wait_frames))):
            await app.next_update_async()

        try:
            result = await capture_rtg_lidar_fleet_in_batches(
                stage=resolved,
                sensor_names=selected,
                config=config,
                variant=variant,
                output_dir=output_path,
                output_stem=f"{output_stem}_{name}",
                tick_rate_hz=tick_rate_hz,
                far_range_m=far_range_m,
                timeout_frames=timeout_frames,
                warmup_frames=warmup_frames,
                min_scans=min_scans,
                hold_pose=True,
                world_roi=world_roi,
            )
            per_position[name] = {
                "trolleyM": trolley_m,
                "result": result,
            }
            position_npy = result["worldBundle"]["npy"]
            points = np.asarray(np.load(position_npy), dtype=np.float32).reshape(
                -1, 3
            )
            if len(points):
                point_batches.append(points)
            print(
                f"[RTG LiDAR fleet] {name} complete: "
                f"{len(points)} world points",
                flush=True,
            )
        except Exception as error:
            failures[name] = str(error)
            print(
                f"[RTG LiDAR fleet] {name} scan failed: {error}",
                flush=True,
            )

    if not point_batches:
        raise TimeoutError(
            "No trolley position produced a point cloud; "
            f"failures={failures}"
        )

    from rtg_yard_lidar import write_pointcloud_bundle

    merged = np.concatenate(point_batches, axis=0)
    raw_merged_count = int(len(merged))
    # Keep this second application so callers that supply their own batch
    # wrapper still receive a clean final merged file.
    merged = filter_rtg_world_points(merged, world_roi=world_roi)
    merged_bundle = write_pointcloud_bundle(
        output_path,
        f"{output_stem}_world_merged",
        merged,
    )
    result = {
        "requestedPositionCount": len(trolley_positions),
        "capturedPositionCount": len(per_position),
        "failedPositionCount": len(failures),
        "positions": per_position,
        "failures": failures,
        "rawPointCountBeforeWorldFilter": raw_merged_count,
        "worldFilter": world_roi,
        "totalPointCount": int(len(merged)),
        "worldBundle": merged_bundle,
    }
    print(
        "[RTG LiDAR fleet] multi-position capture complete: "
        f"{len(per_position)}/{len(trolley_positions)} positions, "
        f"{len(merged)} merged world points: {merged_bundle}",
        flush=True,
    )
    return result


def stop_rtg_lidar_fleet(*, remove_markers: bool = False) -> dict[str, Any]:
    """Detach the selected sensors and remove their runtime USD prims."""

    global _fleet_service
    if _fleet_service is None:
        return {"stopped": False}
    service = _fleet_service
    sensor_count = len(service.sensors)
    _destroy_fleet_sensor_wrappers(service)
    hydra_engine_uid = service.hydra_engine_uid
    service.hydra_engine_uid = None
    if hydra_engine_uid is not None:
        try:
            import omni.usd

            omni.usd.destroy_hydra_engine(int(hydra_engine_uid))
            print(
                f"[RTG LiDAR] explicit RTX Hydra engine destroyed: "
                f"uid={hydra_engine_uid}",
                flush=True,
            )
        except Exception as error:
            print(
                f"[RTG LiDAR] Hydra engine cleanup skipped: {error}",
                flush=True,
            )
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
    "DEFAULT_REPRESENTATIVE_VARIANT",
    "REPRESENTATIVE_LIDAR_NAMES",
    "TROLLEY_BOTTOM_LIDAR_NAMES",
    "SPREADER_LIDAR_NAMES",
    "ALL_LIDAR_NAMES",
    "RIGHT_SIDE_LIDAR_NAMES",
    "capture_rtg_lidar_fleet_pointcloud",
    "capture_rtg_lidar_fleet_in_batches",
    "capture_rtg_lidar_fleet_at_trolley_positions",
    "filter_rtg_world_points",
    "get_rtg_lidar_fleet_status",
    "show_rtg_lidar_scan_debug_draw",
    "show_rtg_lidar_position_scan_debug_draw",
    "show_rtg_lidar_position_scan_wireframes",
    "show_rtg_lidar_scan_volumes",
    "start_rtg_lidar_fleet",
    "start_rtg_lidar_all_fleet",
    "start_rtg_lidar_representative_fleet",
    "start_rtg_lidar_trolley_bottom_fleet",
    "start_rtg_lidar_spreader_fleet",
    "stop_rtg_lidar_fleet",
]
