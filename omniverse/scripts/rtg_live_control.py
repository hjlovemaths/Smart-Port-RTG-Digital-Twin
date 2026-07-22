"""Runtime command helper for the primary RTG.

Run this module inside Omniverse Kit and pass the current USD stage to
``RTGController``. Commands are written to the anonymous session layer, keeping
the project USD files unchanged while the application is running.
"""

from __future__ import annotations

from pxr import Gf, Usd, UsdGeom, UsdPhysics


GANTRY_JOINT_PATH = "/World/RTGPhysics/GantryTravelJoint"
TROLLEY_JOINT_PATH = "/World/RTGPhysics/TrolleyTravelJoint"
HOIST_JOINT_PATH = "/World/RTGPhysics/HoistVerticalJoint"
GANTRY_PATH = (
    "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL"
)
TROLLEY_PATH = f"{GANTRY_PATH}/ANIM_CTRL_RTG_TROLLEY_TRAVEL"
HOIST_PATH = f"{TROLLEY_PATH}/ANIM_CTRL_RTG_HOIST_VERTICAL"
# Live commands are offsets from the Blender-authored frame-1 pose.  Keeping
# all three controller origins at zero prevents the first ROS2/WPF command from
# jumping to a value captured from an earlier validation frame.
GANTRY_BASE_TRANSLATE = Gf.Vec3d(0.0, 0.0, 0.0)
TROLLEY_BASE_TRANSLATE = Gf.Vec3d(0.0, 0.0, 0.0)
HOIST_BASE_TRANSLATE = Gf.Vec3d(0.0, 0.0, 0.0)
ROPE_SYSTEM_PATH = (
    "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL/"
    "ANIM_CTRL_RTG_TROLLEY_TRAVEL/RTG_DYNAMIC_HOIST_ROPES"
)

ROPE_ENDPOINTS = (
    ((1.0620, -5.55, 8.81), (2.2421, -5.56, 4.30)),
    ((1.0980, -5.55, 8.81), (2.2781, -5.56, 4.30)),
    ((1.0620, -4.45, 8.81), (2.2421, -4.44, 4.30)),
    ((1.0980, -4.45, 8.81), (2.2781, -4.44, 4.30)),
    ((1.9120, -5.55, 8.81), (2.2421, -5.56, 4.30)),
    ((1.9480, -5.55, 8.81), (2.2781, -5.56, 4.30)),
    ((1.9120, -4.45, 8.81), (2.2421, -4.44, 4.30)),
    ((1.9480, -4.45, 8.81), (2.2781, -4.44, 4.30)),
    ((3.0120, -5.55, 8.81), (3.0821, -5.56, 4.30)),
    ((3.0480, -5.55, 8.81), (3.1181, -5.56, 4.30)),
    ((3.0120, -4.45, 8.81), (3.0821, -4.44, 4.30)),
    ((3.0480, -4.45, 8.81), (3.1181, -4.44, 4.30)),
    ((3.8620, -5.55, 8.81), (3.0821, -5.56, 4.30)),
    ((3.8980, -5.55, 8.81), (3.1181, -5.56, 4.30)),
    ((3.8620, -4.45, 8.81), (3.0821, -4.44, 4.30)),
    ((3.8980, -4.45, 8.81), (3.1181, -4.44, 4.30)),
)
# Must match build_rtg_simready.py so live WPF/ROS2 commands keep the same
# lower attachment point as the authored validation animation.
LOWER_ROPE_VISIBLE_OFFSET_Z = -0.51
GANTRY_LIMITS = (0.0, 4.20)
TROLLEY_ENGINEERING_LIMITS = (0.0, 18.0)
TROLLEY_USD_LIMITS = (0.0, -2.25)
HOIST_ENGINEERING_LIMITS = (0.0, 15.0)
HOIST_USD_LIMITS = (-0.45, 0.85)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


def _linear_map(
    value: float,
    source: tuple[float, float],
    target: tuple[float, float],
) -> float:
    ratio = (value - source[0]) / (source[1] - source[0])
    return target[0] + ratio * (target[1] - target[0])


def trolley_actual_to_usd(position_m: float) -> float:
    value = _clamp(position_m, *TROLLEY_ENGINEERING_LIMITS)
    return _linear_map(value, TROLLEY_ENGINEERING_LIMITS, TROLLEY_USD_LIMITS)


def trolley_usd_to_actual(position_usd: float) -> float:
    value = _clamp(position_usd, min(TROLLEY_USD_LIMITS), max(TROLLEY_USD_LIMITS))
    return _linear_map(value, TROLLEY_USD_LIMITS, TROLLEY_ENGINEERING_LIMITS)


def hoist_actual_to_usd(height_m: float) -> float:
    value = _clamp(height_m, *HOIST_ENGINEERING_LIMITS)
    return _linear_map(value, HOIST_ENGINEERING_LIMITS, HOIST_USD_LIMITS)


def hoist_usd_to_actual(position_usd: float) -> float:
    value = _clamp(position_usd, min(HOIST_USD_LIMITS), max(HOIST_USD_LIMITS))
    return _linear_map(value, HOIST_USD_LIMITS, HOIST_ENGINEERING_LIMITS)


def _rope_points(hoist_position: float) -> list[Gf.Vec3f]:
    points: list[Gf.Vec3f] = []
    for upper, lower in ROPE_ENDPOINTS:
        points.append(Gf.Vec3f(*upper))
        points.append(
            Gf.Vec3f(
                lower[0],
                lower[1],
                lower[2] + LOWER_ROPE_VISIBLE_OFFSET_Z + hoist_position,
            )
        )
    return points


class RTGController:
    """Write synchronized gantry, trolley, hoist, and rope commands."""

    def __init__(self, stage: Usd.Stage):
        self.stage = stage
        self._session = stage.GetSessionLayer()
        self._gantry = self._drive_target(GANTRY_JOINT_PATH)
        self._gantry_prim = stage.GetPrimAtPath(GANTRY_PATH)
        if not self._gantry_prim:
            raise RuntimeError(f"Missing gantry controller: {GANTRY_PATH}")
        self._gantry_translate = self._gantry_prim.GetAttribute("xformOp:translate")
        if not self._gantry_translate:
            raise RuntimeError(f"Missing gantry translate op: {GANTRY_PATH}")
        self._trolley = self._drive_target(TROLLEY_JOINT_PATH)
        self._hoist = self._drive_target(HOIST_JOINT_PATH)
        self._trolley_translate = stage.GetPrimAtPath(TROLLEY_PATH).GetAttribute(
            "xformOp:translate"
        )
        self._hoist_translate = stage.GetPrimAtPath(HOIST_PATH).GetAttribute(
            "xformOp:translate"
        )
        self._ropes = UsdGeom.BasisCurves.Get(stage, ROPE_SYSTEM_PATH)
        if not self._ropes:
            raise RuntimeError(f"Missing rope system: {ROPE_SYSTEM_PATH}")

    def _drive_target(self, joint_path: str):
        joint = UsdPhysics.PrismaticJoint.Get(self.stage, joint_path)
        if not joint:
            raise RuntimeError(f"Missing prismatic joint: {joint_path}")
        return UsdPhysics.DriveAPI.Get(
            joint.GetPrim(), UsdPhysics.Tokens.linear
        ).GetTargetPositionAttr()

    def set_gantry(self, position_m: float) -> float:
        value = _clamp(position_m, *GANTRY_LIMITS)
        with Usd.EditContext(self.stage, self._session):
            self._gantry.Set(value)
            # A session-layer default overrides the authored demo samples and
            # moves the complete gantry hierarchy deterministically.
            self._gantry_translate.Set(
                Gf.Vec3d(
                    GANTRY_BASE_TRANSLATE[0],
                    GANTRY_BASE_TRANSLATE[1] + value,
                    GANTRY_BASE_TRANSLATE[2],
                )
            )
        return value

    def set_trolley(self, position_m: float) -> float:
        """Set actual trolley travel: 0 m at the tower, 18 m at the far end."""
        actual = _clamp(position_m, *TROLLEY_ENGINEERING_LIMITS)
        value = trolley_actual_to_usd(actual)
        with Usd.EditContext(self.stage, self._session):
            self._trolley.Set(value)
            self._trolley_translate.Set(
                Gf.Vec3d(
                    TROLLEY_BASE_TRANSLATE[0] + value,
                    TROLLEY_BASE_TRANSLATE[1],
                    TROLLEY_BASE_TRANSLATE[2],
                )
            )
        return actual

    def set_hoist(self, height_m: float) -> float:
        """Set actual spreader height and update lower rope endpoints atomically."""
        actual = _clamp(height_m, *HOIST_ENGINEERING_LIMITS)
        value = hoist_actual_to_usd(actual)
        with Usd.EditContext(self.stage, self._session):
            self._hoist.Set(value)
            self._hoist_translate.Set(
                Gf.Vec3d(
                    HOIST_BASE_TRANSLATE[0],
                    HOIST_BASE_TRANSLATE[1],
                    HOIST_BASE_TRANSLATE[2] + value,
                )
            )
            self._ropes.GetPointsAttr().Set(_rope_points(value))
        return actual

    def set_positions(
        self, *, gantry_m: float, trolley_m: float, hoist_m: float
    ) -> tuple[float, float, float]:
        return (
            self.set_gantry(gantry_m),
            self.set_trolley(trolley_m),
            self.set_hoist(hoist_m),
        )
