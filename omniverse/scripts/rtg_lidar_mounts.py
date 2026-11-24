"""Calibrated LiDAR mounting poses for the main RTG."""

from math import atan2, cos, degrees, radians, sin
from typing import NamedTuple


GANTRY_PATH = "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL"
TROLLEY_PATH = f"{GANTRY_PATH}/ANIM_CTRL_RTG_TROLLEY_TRAVEL"
HOIST_PATH = f"{TROLLEY_PATH}/ANIM_CTRL_RTG_HOIST_VERTICAL"
FRONT_TELESCOPE_PATH = f"{HOIST_PATH}/ANIM_CTRL_SPREADER_TELESCOPE_FRONT"
REAR_TELESCOPE_PATH = f"{HOIST_PATH}/ANIM_CTRL_SPREADER_TELESCOPE_REAR"
# Trolley-local offsets. Place the optics under opposite edges of the trolley
# deck, clear of the cabin and outside the spreader's narrow footprint.
PERIMETER_TRANSLATION = (2.55, -6.25, 8.10)
YARD_TRANSLATION = (2.55, -3.75, 8.10)
PERIMETER_ANGLES = (90.0, 28.0)
YARD_ANGLES = (-90.0, 65.0)


class LidarMount(NamedTuple):
    id: str
    group: str
    parent_path: str
    offset: tuple[float, float, float]
    angles: tuple[float, float]


# Keep the original four views and add opposing views at different heights.
# Telescope-mounted scanners follow both the hoist and the changing box length.
SIM_MOUNTS = (
    LidarMount("yard", "trolley", TROLLEY_PATH, YARD_TRANSLATION, YARD_ANGLES),
    LidarMount("perimeter", "trolley", TROLLEY_PATH, PERIMETER_TRANSLATION, PERIMETER_ANGLES),
    LidarMount("yard_column", "column", GANTRY_PATH, (-5.36, -2.35, 3.0), (-25.0, 10.0)),
    LidarMount("lane_column", "column", GANTRY_PATH, (3.77, -7.65, 3.0), (150.0, 10.0)),
    LidarMount("yard_column_cross", "column", GANTRY_PATH, (-5.36, -7.65, 2.15), (25.0, 14.0)),
    LidarMount("lane_column_cross", "column", GANTRY_PATH, (3.77, -2.35, 2.15), (-150.0, 14.0)),
    LidarMount("yard_column_out", "column", GANTRY_PATH, (-5.80, -1.91, 2.1), (68.0, 12.0)),
    LidarMount("lane_column_out", "column", GANTRY_PATH, (4.21, -8.09, 2.1), (-112.0, 12.0)),
    LidarMount("front_beam", "beam", GANTRY_PATH, (-2.20, -7.65, 7.55), (-90.0, 42.0)),
    LidarMount("rear_beam", "beam", GANTRY_PATH, (0.60, -2.35, 7.55), (90.0, 42.0)),
    LidarMount("front_spreader", "spreader", FRONT_TELESCOPE_PATH, (2.55, -7.71, 3.04), (-90.0, 22.0)),
    LidarMount("rear_spreader", "spreader", REAR_TELESCOPE_PATH, (2.55, -2.29, 3.04), (90.0, 22.0)),
    LidarMount("front_yard_beam_tip", "beam", GANTRY_PATH, (-5.05, -7.84, 7.52), (-62.0, 42.0)),
    LidarMount("rear_yard_beam_tip", "beam", GANTRY_PATH, (-5.05, -2.16, 7.52), (62.0, 42.0)),
    LidarMount("yard_column_low", "column", GANTRY_PATH, (-5.37, -7.70, 1.24), (34.0, 3.0)),
    LidarMount("lane_column_low", "column", GANTRY_PATH, (3.72, -2.31, 1.24), (-146.0, 4.0)),
)


def trolley_transform(stage, time_code=None):
    return mount_transform(stage, TROLLEY_PATH, time_code)


def mount_transform(stage, path, time_code):
    from pxr import Usd, UsdGeom

    prim = stage.GetPrimAtPath(path)
    if not prim:
        raise ValueError("场景中找不到主 RTG 雷达的安装基准。")
    return UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
        Usd.TimeCode.Default() if time_code is None else time_code
    )


def world_pose(matrix, translation, angles):
    from pxr import Gf

    position = matrix.Transform(Gf.Vec3d(*translation))
    axis = matrix.TransformDir(Gf.Vec3d(1, 0, 0))
    yaw, pitch = angles
    yaw = (degrees(atan2(axis[1], axis[0])) + yaw) % 360
    return tuple(position), yaw, pitch


def orientation_wxyz(yaw, pitch):
    """Rotate sensor +X by a downward pitch, then a world-frame yaw."""
    yaw, pitch = radians(yaw) / 2, radians(pitch) / 2
    return (
        cos(yaw) * cos(pitch), -sin(yaw) * sin(pitch),
        cos(yaw) * sin(pitch), sin(yaw) * cos(pitch),
    )


def install_sim_housings(stage, mounts=SIM_MOUNTS, *, housing_scale=1.0):
    """Show the SIM scanners on their moving assemblies."""
    from pxr import Gf, Usd, UsdGeom

    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for sensor in mounts:
            if not stage.GetPrimAtPath(sensor.parent_path):
                continue
            path = f"{sensor.parent_path}/SIM_LIDAR_{sensor.id.upper()}"
            if stage.GetPrimAtPath(path):
                continue
            mount = UsdGeom.Xform.Define(stage, path)
            mount.AddTranslateOp().Set(Gf.Vec3d(*sensor.offset))
            yaw, pitch = sensor.angles
            mount.AddRotateZOp().Set(yaw)
            mount.AddRotateYOp().Set(pitch)
            for part, x, scale, color in (
                ("Bracket", -0.23, (0.12, 0.065, 0.045), (0.28, 0.34, 0.40)),
                ("Housing", -0.10, (0.10, 0.105, 0.085), (0.06, 0.09, 0.13)),
                ("Lens", 0.006, (0.012, 0.075, 0.057), (0.16, 0.75, 0.86)),
            ):
                cube = UsdGeom.Cube.Define(stage, f"{path}/{part}")
                cube.AddTranslateOp().Set(Gf.Vec3d(x * housing_scale, 0, 0))
                cube.AddScaleOp().Set(Gf.Vec3f(*scale) * housing_scale)
                cube.CreateDisplayColorAttr([Gf.Vec3f(*color)])
