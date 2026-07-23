"""Runtime command helper for the primary RTG.

Run this module inside Omniverse Kit and pass the current USD stage to
``RTGController``. Commands are written to the anonymous session layer, keeping
the project USD files unchanged while the application is running.
"""

from __future__ import annotations

from pathlib import Path
import sys

from pxr import Gf, Usd, UsdGeom, UsdPhysics

try:
    from yard_coordinate_mapping import (
        GANTRY_HARD_LIMITS_M,
        GANTRY_SOFT_LIMITS_M,
        YARD_TOTAL_LENGTH_M,
        YARD_LAYOUT_START_Y_M,
        bay_id_scene_y_m,
        bay_id_position_m,
        bay_scene_y_m,
        bay_position_m,
    )
except ModuleNotFoundError:
    sys.path.append(str(Path(__file__).resolve().parent))
    from yard_coordinate_mapping import (
        GANTRY_HARD_LIMITS_M,
        GANTRY_SOFT_LIMITS_M,
        YARD_TOTAL_LENGTH_M,
        YARD_LAYOUT_START_Y_M,
        bay_id_scene_y_m,
        bay_id_position_m,
        bay_scene_y_m,
        bay_position_m,
    )


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

LOWER_HEADER_LEFT_X = 2.3456
LOWER_HEADER_RIGHT_X = 2.7524
LOWER_HEADER_FRONT_Y = -5.56
LOWER_HEADER_REAR_Y = -4.44
LOWER_HEADER_SOURCE_Z = 4.3575

ROPE_ENDPOINTS = (
    ((1.0620, -5.55, 8.81), (LOWER_HEADER_LEFT_X, LOWER_HEADER_FRONT_Y, LOWER_HEADER_SOURCE_Z)),
    ((1.0980, -5.55, 8.81), (LOWER_HEADER_LEFT_X, LOWER_HEADER_FRONT_Y, LOWER_HEADER_SOURCE_Z)),
    ((1.0620, -4.45, 8.81), (LOWER_HEADER_LEFT_X, LOWER_HEADER_REAR_Y, LOWER_HEADER_SOURCE_Z)),
    ((1.0980, -4.45, 8.81), (LOWER_HEADER_LEFT_X, LOWER_HEADER_REAR_Y, LOWER_HEADER_SOURCE_Z)),
    ((1.9120, -5.55, 8.81), (LOWER_HEADER_LEFT_X, LOWER_HEADER_FRONT_Y, LOWER_HEADER_SOURCE_Z)),
    ((1.9480, -5.55, 8.81), (LOWER_HEADER_LEFT_X, LOWER_HEADER_FRONT_Y, LOWER_HEADER_SOURCE_Z)),
    ((1.9120, -4.45, 8.81), (LOWER_HEADER_LEFT_X, LOWER_HEADER_REAR_Y, LOWER_HEADER_SOURCE_Z)),
    ((1.9480, -4.45, 8.81), (LOWER_HEADER_LEFT_X, LOWER_HEADER_REAR_Y, LOWER_HEADER_SOURCE_Z)),
    ((3.0120, -5.55, 8.81), (LOWER_HEADER_RIGHT_X, LOWER_HEADER_FRONT_Y, LOWER_HEADER_SOURCE_Z)),
    ((3.0480, -5.55, 8.81), (LOWER_HEADER_RIGHT_X, LOWER_HEADER_FRONT_Y, LOWER_HEADER_SOURCE_Z)),
    ((3.0120, -4.45, 8.81), (LOWER_HEADER_RIGHT_X, LOWER_HEADER_REAR_Y, LOWER_HEADER_SOURCE_Z)),
    ((3.0480, -4.45, 8.81), (LOWER_HEADER_RIGHT_X, LOWER_HEADER_REAR_Y, LOWER_HEADER_SOURCE_Z)),
    ((3.8620, -5.55, 8.81), (LOWER_HEADER_RIGHT_X, LOWER_HEADER_FRONT_Y, LOWER_HEADER_SOURCE_Z)),
    ((3.8980, -5.55, 8.81), (LOWER_HEADER_RIGHT_X, LOWER_HEADER_FRONT_Y, LOWER_HEADER_SOURCE_Z)),
    ((3.8620, -4.45, 8.81), (LOWER_HEADER_RIGHT_X, LOWER_HEADER_REAR_Y, LOWER_HEADER_SOURCE_Z)),
    ((3.8980, -4.45, 8.81), (LOWER_HEADER_RIGHT_X, LOWER_HEADER_REAR_Y, LOWER_HEADER_SOURCE_Z)),
)
# Must match build_rtg_simready.py so live WPF/ROS2 commands keep the same
# lower attachment point as the authored validation animation.
LOWER_ROPE_VISIBLE_OFFSET_Z = -0.58
GANTRY_LIMITS = GANTRY_HARD_LIMITS_M
GANTRY_SOFT_LIMITS = GANTRY_SOFT_LIMITS_M
TROLLEY_ENGINEERING_LIMITS = (0.0, 18.0)
TROLLEY_SOFT_LIMITS = (0.3, 17.7)
TROLLEY_USD_LIMITS = (0.0, -2.25)
HOIST_ENGINEERING_LIMITS = (0.0, 15.0)
HOIST_SOFT_LIMITS = (0.5, 14.7)
HOIST_USD_LIMITS = (-0.45, 0.85)
# The current Omniverse visual alignment expects the gantry controller Y to
# match the yard bay scene Y directly.  Keep this explicit so bay commands such
# as 1C/005 park the RTG on the visible bay-map footprint rather than behind it.
RTG_WORK_CENTER_LOCAL_Y_M = 0.0
# Small visual tuning offset for the authored RTG wheel/leg footprint.  Negative
# moves the RTG toward the 1C seaside/start side so the tyres sit closer to the
# front edge of the live bay-map footprint.
RTG_BAY_VISUAL_ALIGNMENT_OFFSET_Y_M = -3.4


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


def gantry_actual_to_usd(position_m: float) -> float:
    """Convert engineering gantry travel to the USD controller Y position."""
    actual = _clamp(position_m, *GANTRY_LIMITS)
    work_center_scene_y = YARD_LAYOUT_START_Y_M + actual
    return (
        work_center_scene_y
        + RTG_BAY_VISUAL_ALIGNMENT_OFFSET_Y_M
        - RTG_WORK_CENTER_LOCAL_Y_M
    )


def gantry_usd_to_actual(controller_y_m: float) -> float:
    """Convert USD controller Y position back to engineering gantry travel."""
    work_center_scene_y = (
        float(controller_y_m)
        + RTG_WORK_CENTER_LOCAL_Y_M
        - RTG_BAY_VISUAL_ALIGNMENT_OFFSET_Y_M
    )
    return work_center_scene_y - YARD_LAYOUT_START_Y_M


def gantry_bay_to_usd(block: str, bay_number: int, anchor: str = "center") -> float:
    """Return the USD controller Y needed to put the spreader over a yard bay."""
    return (
        bay_scene_y_m(block, bay_number, anchor)
        + RTG_BAY_VISUAL_ALIGNMENT_OFFSET_Y_M
        - RTG_WORK_CENTER_LOCAL_Y_M
    )


def gantry_bay_id_to_usd(bay_id: str, anchor: str = "center") -> float:
    """Return the USD controller Y needed to put the spreader over a bay id."""
    return (
        bay_id_scene_y_m(bay_id, anchor)
        + RTG_BAY_VISUAL_ALIGNMENT_OFFSET_Y_M
        - RTG_WORK_CENTER_LOCAL_Y_M
    )


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
        """Set gantry work-centre travel in metres from the 1C/001 start."""
        actual = _clamp(position_m, *GANTRY_LIMITS)
        controller_y = gantry_actual_to_usd(actual)
        with Usd.EditContext(self.stage, self._session):
            self._gantry.Set(controller_y)
            # A session-layer default overrides the authored demo samples and
            # moves the complete gantry hierarchy deterministically.
            self._gantry_translate.Set(
                Gf.Vec3d(
                    GANTRY_BASE_TRANSLATE[0],
                    GANTRY_BASE_TRANSLATE[1] + controller_y,
                    GANTRY_BASE_TRANSLATE[2],
                )
            )
        return actual

    def set_gantry_bay(
        self, block: str, bay_number: int, anchor: str = "center"
    ) -> float:
        """Move gantry to a yard bay target such as 1C/025."""
        return self.set_gantry(bay_position_m(block, bay_number, anchor))

    def set_gantry_bay_id(self, bay_id: str, anchor: str = "center") -> float:
        """Move gantry to a text bay target such as '1C/025' or '1C-25'."""
        return self.set_gantry(bay_id_position_m(bay_id, anchor))

    def set_gantry_safe(self, position_m: float) -> float:
        """Set gantry travel while keeping inside the normal soft-limit envelope."""
        return self.set_gantry(_clamp(position_m, *GANTRY_SOFT_LIMITS))

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

    def set_trolley_safe(self, position_m: float) -> float:
        """Set trolley travel while keeping inside the normal soft-limit envelope."""
        return self.set_trolley(_clamp(position_m, *TROLLEY_SOFT_LIMITS))

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

    def set_hoist_safe(self, height_m: float) -> float:
        """Set hoist height while keeping the spreader at least 0.5 m above ground."""
        return self.set_hoist(_clamp(height_m, *HOIST_SOFT_LIMITS))

    def set_positions(
        self, *, gantry_m: float, trolley_m: float, hoist_m: float
    ) -> tuple[float, float, float]:
        return (
            self.set_gantry(gantry_m),
            self.set_trolley(trolley_m),
            self.set_hoist(hoist_m),
        )

    def set_positions_safe(
        self, *, gantry_m: float, trolley_m: float, hoist_m: float
    ) -> tuple[float, float, float]:
        return (
            self.set_gantry_safe(gantry_m),
            self.set_trolley_safe(trolley_m),
            self.set_hoist_safe(hoist_m),
        )

    def set_bay_positions(
        self,
        *,
        bay_id: str,
        trolley_m: float,
        hoist_m: float,
        bay_anchor: str = "center",
        safe_hoist: bool = True,
    ) -> tuple[float, float, float]:
        """Set gantry by yard bay id, plus trolley and hoist engineering values."""
        hoist = self.set_hoist_safe if safe_hoist else self.set_hoist
        return (
            self.set_gantry_bay_id(bay_id, bay_anchor),
            self.set_trolley(trolley_m),
            hoist(hoist_m),
        )


__all__ = [
    "GANTRY_LIMITS",
    "GANTRY_SOFT_LIMITS",
    "HOIST_ENGINEERING_LIMITS",
    "HOIST_SOFT_LIMITS",
    "RTGController",
    "RTG_WORK_CENTER_LOCAL_Y_M",
    "TROLLEY_ENGINEERING_LIMITS",
    "TROLLEY_SOFT_LIMITS",
    "YARD_TOTAL_LENGTH_M",
    "gantry_actual_to_usd",
    "gantry_bay_id_to_usd",
    "gantry_bay_to_usd",
    "gantry_usd_to_actual",
    "bay_id_position_m",
    "bay_position_m",
    "hoist_actual_to_usd",
    "hoist_usd_to_actual",
    "trolley_actual_to_usd",
    "trolley_usd_to_actual",
]
