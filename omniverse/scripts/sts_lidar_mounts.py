"""Downward SIM scanners on the two moving ends of the STS_01 spreader."""

from rtg_lidar_mounts import LidarMount, mount_transform

CRANE_ID = "STS_01"
HOIST_PATH = f"/World/STSControls/{CRANE_ID}/Gantry/Trolley/Hoist"
FRONT = f"{HOIST_PATH}/STS_SpreaderEndFront"
REAR = f"{HOIST_PATH}/STS_SpreaderEndRear"
STS_MOUNTS = (
    LidarMount("sts_front_port", "spreader", FRONT, (.19, -1.20, 2.85), (-27.0, 66.0)),
    LidarMount("sts_front_starboard", "spreader", FRONT, (.19, -.20, 2.85), (27.0, 66.0)),
    LidarMount("sts_front_center", "spreader", FRONT, (.24, -.70, 2.94), (0.0, 83.0)),
    LidarMount("sts_front_edge", "spreader", FRONT, (.06, -1.43, 2.91), (-90.0, 72.0)),
    LidarMount("sts_rear_port", "spreader", REAR, (-.19, -1.20, 2.85), (-153.0, 66.0)),
    LidarMount("sts_rear_starboard", "spreader", REAR, (-.19, -.20, 2.85), (153.0, 66.0)),
    LidarMount("sts_rear_center", "spreader", REAR, (-.24, -.70, 2.94), (180.0, 83.0)),
    LidarMount("sts_rear_edge", "spreader", REAR, (-.06, -.005, 2.91), (90.0, 72.0)),
)


def under_spreader_pose(stage):
    from pxr import Gf
    from large_vessel_layout import SHIP

    matrix = mount_transform(stage, HOIST_PATH, None)
    eye = matrix.Transform(Gf.Vec3d(0, -.70, 2.56))
    ship = stage.GetPrimAtPath(SHIP)
    deck_z = ship.GetAttribute("ship:deckZ").Get() if ship else None
    target_z = mount_transform(stage, SHIP, None).Transform(Gf.Vec3d(0, 0, deck_z))[2] if deck_z is not None else 0.0
    distance = max(.5, eye[2] - target_z)
    forward = matrix.TransformDir(Gf.Vec3d(0, 1, 0))
    up = Gf.Vec3d(forward[0], forward[1], 0).GetNormalized()
    return eye, eye - Gf.Vec3d(0, 0, distance), up, distance
