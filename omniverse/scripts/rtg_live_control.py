"""Runtime command helper for the primary RTG.

Run this module inside Omniverse Kit and pass the current USD stage to
``RTGController``. Commands are written to the anonymous session layer, keeping
the project USD files unchanged while the application is running.
"""

from __future__ import annotations

import math
from pathlib import Path
import sys
import time

from pxr import Gf, Usd, UsdGeom, UsdPhysics

try:
    from yard_coordinate_mapping import (
        BAY_GAP_M,
        BAY_LENGTH_M,
        GANTRY_HARD_LIMITS_M,
        RTG_LATERAL_ALIGNMENT_X_M,
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
        BAY_GAP_M,
        BAY_LENGTH_M,
        GANTRY_HARD_LIMITS_M,
        RTG_LATERAL_ALIGNMENT_X_M,
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
SPREADER_FRONT_TELESCOPE_PATH = (
    f"{HOIST_PATH}/ANIM_CTRL_SPREADER_TELESCOPE_FRONT"
)
SPREADER_REAR_TELESCOPE_PATH = (
    f"{HOIST_PATH}/ANIM_CTRL_SPREADER_TELESCOPE_REAR"
)
# The authored spreader is 40 ft long in the compact yard scale.  Moving both
# telescopic ends inward by half the 40-to-20 ft length difference makes its
# overall Y length exactly equal to the rendered 20 ft container.
SPREADER_40FT_LENGTH_M = BAY_LENGTH_M * 2.0 + BAY_GAP_M
SPREADER_20FT_LENGTH_M = BAY_LENGTH_M
SPREADER_TELESCOPE_TRAVEL_M = (
    SPREADER_40FT_LENGTH_M - SPREADER_20FT_LENGTH_M
) * 0.5
# Reach the opposite ISO endpoint during the visible gantry approach, without
# snapping on the task's first render frame.
SPREADER_TELESCOPE_SPEED_M_S = 0.60
# This is deliberately separate from the Blender-authored transform.  The
# authored attribute contains timeline samples, which override a default live
# value as the stage plays.  A dedicated additive op remains stable at every
# timeline frame.
SPREADER_LIVE_TELESCOPE_OP_SUFFIX = "rtgLiveTelescoping"
# The PLC receiver drives travel in Y; X is the fixed lateral datum for the
# active primary RTG. Static RTGs use the unchanged logical yard-column datum.
_yard_mapping = sys.modules.get("yard_coordinate_mapping")
PRIMARY_RTG_LATERAL_ALIGNMENT_X_M = getattr(
    _yard_mapping,
    "PRIMARY_RTG_LATERAL_ALIGNMENT_X_M",
    RTG_LATERAL_ALIGNMENT_X_M - 1.75,
)
GANTRY_LATERAL_ALIGNMENT_X_M = PRIMARY_RTG_LATERAL_ALIGNMENT_X_M
GANTRY_BASE_TRANSLATE = Gf.Vec3d(GANTRY_LATERAL_ALIGNMENT_X_M, 0.0, 0.0)
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
TROLLEY_ENGINEERING_LIMITS = (0.0, 18.06)
TROLLEY_SOFT_LIMITS = TROLLEY_ENGINEERING_LIMITS
# These USD positions come directly from the validated task.txt animation.
# Only the live PLC conversion uses this table; the RTG model, container rows,
# truck model, and scripted animation remain on their authored coordinates.
TROLLEY_TRUCK_STOP_USD_X = 0.005003036169343086
TROLLEY_PLC_STOP_CALIBRATION = (
    (0.48, TROLLEY_TRUCK_STOP_USD_X),  # shifted task-truck centre
    (1.00, TROLLEY_TRUCK_STOP_USD_X),  # hold the real 0.63 m reading at truck
    (3.71, -1.7939748547136125),  # row 6
    (6.66, -2.8158098726906840),  # row 5
    (9.50, -3.8376448906677547),  # row 4
    (12.35, -4.8594799086448250), # row 3
    (15.19, -5.8813149266218950), # row 2
    (18.06, -6.9031499445989670), # row 1
)
TROLLEY_USD_LIMITS = (
    TROLLEY_PLC_STOP_CALIBRATION[0][1],
    TROLLEY_PLC_STOP_CALIBRATION[-1][1],
)
HOIST_ENGINEERING_LIMITS = (0.0, 18.0)
# The configured RTG hoist limit is 18 m.  Keep the live controller aligned
# with the task-state clearance calculation, especially for a sixth-tier box.
HOIST_SOFT_LIMITS = (0.5, 18.0)
# One physical container tier is 2.6 m. Match that increment to the
# compressed visual container pitch used by the live stack.
HOIST_ACTUAL_TIER_HEIGHT_M = 2.60
HOIST_VISUAL_TIER_HEIGHT_M = 0.904353 + 0.035
HOIST_USD_PER_ENGINEERING_M = (
    HOIST_VISUAL_TIER_HEIGHT_M / HOIST_ACTUAL_TIER_HEIGHT_M
)
HOIST_FIRST_TIER_ENGINEERING_M = 2.60
# Tier-1 top is Z=1.024353 and the spreader lock contact plane is Z=2.55,
# so this offset puts the lock feet directly on the first-layer box.
HOIST_USD_AT_FIRST_TIER = -1.525647
HOIST_USD_AT_ENGINEERING_ZERO = (
    HOIST_USD_AT_FIRST_TIER
    - HOIST_FIRST_TIER_ENGINEERING_M * HOIST_USD_PER_ENGINEERING_M
)
HOIST_USD_LIMITS = (
    HOIST_USD_AT_ENGINEERING_ZERO,
    HOIST_USD_AT_ENGINEERING_ZERO
    + HOIST_ENGINEERING_LIMITS[1] * HOIST_USD_PER_ENGINEERING_M,
)
# The current Omniverse visual alignment expects the gantry controller Y to
# match the yard bay scene Y directly.  Keep this explicit so bay commands such
# as 1C/005 park the RTG on the visible bay-map footprint rather than behind it.
RTG_WORK_CENTER_LOCAL_Y_M = 0.0
# Small visual tuning offset for the authored RTG wheel/leg footprint.  Negative
# moves the RTG toward the 1C seaside/start side so the tyres sit closer to the
# front edge of the live bay-map footprint.
RTG_BAY_VISUAL_ALIGNMENT_OFFSET_Y_M = -3.4
# The visible yard numbering and RTG footprint were calibrated together with
# the -3.4 m offset above.  The authored 20-ft bay labels are half a paired
# (40-ft) slot apart from the corresponding even-bay centre: the old zero
# correction therefore put a PLC ``currentBay=63`` stop visually over the
# ``64`` number.  Shift bay-addressed stops by one 20-ft slot toward the
# seaside so the short-beam/spreader centre is over the middle of the printed
# ``63`` label and the 20-ft box center.  Continuous big-car metre commands
# intentionally keep their original calibration.
RTG_BAY_CENTERLINE_CORRECTION_Y_M = -1.3375
# The blue lower-portal crossbeam ``lower_portal_crossbeam_x+5.8`` spans
# Y=1.625..8.375 in its gantry parent, so its vertical centreline is Y=5.0.
# A 40 ft bay stop must put that physical line, not the RTG root or the beam
# object's origin at Y=10.0, on the paired-even-bay/container centre.
RTG_LOWER_PORTAL_CROSSBEAM_CENTER_LOCAL_Y_M = 5.0
# The fixed gantry visual offset is -3.4 m. Together with the base correction,
# this value makes the 40 ft controller Y exactly ``bay_scene_y - 5.0``:
# ``controller_y + lower_portal_crossbeam_center_y == container_center_y``.
RTG_40FT_BAY_CENTERLINE_FINE_OFFSET_Y_M = -0.2625
# The 20 ft telescope geometry is slightly biased toward the land-side/right
# edge of a short box.  Apply this small bay-travel-only correction while the
# 20 ft end-stop is active; 40 ft work keeps the validated centreline above.
SPREADER_20FT_CENTER_OFFSET_Y_M = -0.30


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


def _move_toward(value: float, target: float, max_delta: float) -> float:
    if value < target:
        return min(value + max_delta, target)
    return max(value - max_delta, target)


def _linear_map(
    value: float,
    source: tuple[float, float],
    target: tuple[float, float],
) -> float:
    ratio = (value - source[0]) / (source[1] - source[0])
    return target[0] + ratio * (target[1] - target[0])


def _piecewise_map(
    value: float, points: tuple[tuple[float, float], ...]
) -> float:
    if value <= points[0][0]:
        return points[0][1]
    for (source_a, target_a), (source_b, target_b) in zip(points, points[1:]):
        if value <= source_b:
            return _linear_map(
                value,
                (source_a, source_b),
                (target_a, target_b),
            )
    return points[-1][1]


def _piecewise_inverse(
    value: float, points: tuple[tuple[float, float], ...]
) -> float:
    for (source_a, target_a), (source_b, target_b) in zip(points, points[1:]):
        if math.isclose(target_a, target_b):
            if math.isclose(value, target_a):
                return source_a
            continue
        if min(target_a, target_b) <= value <= max(target_a, target_b):
            return _linear_map(
                value,
                (target_a, target_b),
                (source_a, source_b),
            )
    if value > max(point[1] for point in points):
        return points[0][0]
    return points[-1][0]


def trolley_actual_to_usd(position_m: float) -> float:
    value = _clamp(position_m, *TROLLEY_ENGINEERING_LIMITS)
    return _piecewise_map(value, TROLLEY_PLC_STOP_CALIBRATION)


def trolley_usd_to_actual(position_usd: float) -> float:
    value = _clamp(position_usd, min(TROLLEY_USD_LIMITS), max(TROLLEY_USD_LIMITS))
    return _piecewise_inverse(value, TROLLEY_PLC_STOP_CALIBRATION)


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


def gantry_actual_to_bay_center_m(
    position_m: float,
    container_size_ft: int | None,
) -> float:
    """Undo bay-stop visual calibration for business-bay resolution.

    ``get_positions()`` intentionally returns the continuous engineering
    coordinate, but bay-addressed stops include visual centreline corrections.
    Reversing those corrections recovers the yard coordinate beneath the
    physical gantry centre, which is what current-bay reporting must use.
    """

    correction = RTG_BAY_CENTERLINE_CORRECTION_Y_M
    if container_size_ft == 40:
        correction += RTG_40FT_BAY_CENTERLINE_FINE_OFFSET_Y_M
    elif container_size_ft == 20:
        correction += SPREADER_20FT_CENTER_OFFSET_Y_M
    return float(position_m) - correction


def gantry_bay_to_usd(
    block: str,
    bay_number: int,
    anchor: str = "center",
    *,
    center_offset_y: float = 0.0,
    container_size_ft: int | None = None,
) -> float:
    """Return the Y that puts the RTG short-beam centre on a yard bay line.

    The 40 ft even-bay centreline needs only a small adjustment from the
    established printed-bay calibration; removing the correction entirely
    parks the RTG visibly behind the long-box red centreline.
    """

    bay_centre_correction = (
        RTG_BAY_CENTERLINE_CORRECTION_Y_M
        + (
            RTG_40FT_BAY_CENTERLINE_FINE_OFFSET_Y_M
            if container_size_ft == 40
            else 0.0
        )
    )
    return (
        bay_scene_y_m(block, bay_number, anchor)
        + RTG_BAY_VISUAL_ALIGNMENT_OFFSET_Y_M
        + bay_centre_correction
        + float(center_offset_y)
        - RTG_WORK_CENTER_LOCAL_Y_M
    )


def gantry_bay_id_to_usd(bay_id: str, anchor: str = "center") -> float:
    """Return the Y that puts the RTG short-beam centre on a bay-id line."""
    return (
        bay_id_scene_y_m(bay_id, anchor)
        + RTG_BAY_VISUAL_ALIGNMENT_OFFSET_Y_M
        + RTG_BAY_CENTERLINE_CORRECTION_Y_M
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
        self._spreader_front_translate = self._spreader_translate_attr(
            SPREADER_FRONT_TELESCOPE_PATH
        )
        self._spreader_rear_translate = self._spreader_translate_attr(
            SPREADER_REAR_TELESCOPE_PATH
        )
        self._spreader_offset_m = self._read_spreader_offset()
        self._spreader_last_update_at = time.monotonic()
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

    def _spreader_translate_attr(self, prim_path: str):
        prim = self.stage.GetPrimAtPath(prim_path)
        if not prim:
            return None
        xform = UsdGeom.Xformable(prim)
        attr_name = f"xformOp:translate:{SPREADER_LIVE_TELESCOPE_OP_SUFFIX}"
        live_attr = prim.GetAttribute(attr_name)
        if live_attr:
            return live_attr
        with Usd.EditContext(self.stage, self._session):
            # Do not reuse the authored translate operation.  It has timeline
            # samples and would reset to the animation value on the next
            # viewport frame.  This separate additive translate persists for
            # the live 20/40 ft command without disturbing authored animation.
            op = xform.AddTranslateOp(
                UsdGeom.XformOp.PrecisionDouble,
                SPREADER_LIVE_TELESCOPE_OP_SUFFIX,
            )
            attr = op.GetAttr()
            attr.Set(Gf.Vec3d(0.0, 0.0, 0.0))
            return attr

    def _read_spreader_offset(self) -> float:
        if not self._spreader_front_translate:
            return 0.0
        value = self._spreader_front_translate.Get()
        if value is None:
            return 0.0
        return _clamp(
            float(value[1]),
            0.0,
            SPREADER_TELESCOPE_TRAVEL_M,
        )

    def current_spreader_size_ft(self) -> float | None:
        """Return the actual rendered spreader length without changing it."""

        if not self._spreader_front_translate or not self._spreader_rear_translate:
            return None
        self._spreader_offset_m = self._read_spreader_offset()
        contraction = self._spreader_offset_m / SPREADER_TELESCOPE_TRAVEL_M
        return 40.0 - 20.0 * contraction

    def _set_gantry_controller_y(self, controller_y: float) -> None:
        """Write one already-calibrated controller Y to both gantry controls."""
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

    def set_gantry(self, position_m: float) -> float:
        """Set gantry work-centre travel in metres from the 1C/001 start."""
        actual = _clamp(position_m, *GANTRY_LIMITS)
        self._set_gantry_controller_y(gantry_actual_to_usd(actual))
        return actual

    def set_gantry_bay(
        self,
        block: str,
        bay_number: int,
        anchor: str = "center",
        *,
        center_offset_y: float = 0.0,
        container_size_ft: int | None = None,
    ) -> float:
        """Centre the RTG short beam on a yard bay target such as 1C/025."""
        actual = bay_position_m(block, bay_number, anchor)
        self._set_gantry_controller_y(
            gantry_bay_to_usd(
                block,
                bay_number,
                anchor,
                center_offset_y=center_offset_y,
                container_size_ft=container_size_ft,
            )
        )
        return actual

    def set_gantry_bay_id(self, bay_id: str, anchor: str = "center") -> float:
        """Centre the RTG short beam on a text target such as '1C/025'."""
        actual = bay_id_position_m(bay_id, anchor)
        self._set_gantry_controller_y(gantry_bay_id_to_usd(bay_id, anchor))
        return actual

    def set_gantry_safe(self, position_m: float) -> float:
        """Set gantry travel while keeping inside the normal soft-limit envelope."""
        return self.set_gantry(_clamp(position_m, *GANTRY_SOFT_LIMITS))

    def set_trolley(self, position_m: float) -> float:
        """Set PLC trolley travel against the validated task animation stops."""
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

    def set_hoist(self, height_m: float, *, carried_load: bool = False) -> float:
        """Set the PLC hoist value and update the rendered hoist atomically.

        ``carried_load`` is retained for the PLC-free simulator, whose locked
        snapshots report the suspended container's bottom-face height.  Real
        PLC snapshots report the spreader contact height directly and must
        call this method without that simulation-only compensation.
        """
        actual = _clamp(height_m, *HOIST_ENGINEERING_LIMITS)
        drive_value = hoist_actual_to_usd(actual)
        visual_value = drive_value
        if carried_load:
            visual_value += HOIST_ACTUAL_TIER_HEIGHT_M * HOIST_USD_PER_ENGINEERING_M
        with Usd.EditContext(self.stage, self._session):
            # Keep the drive target in engineering metres.  Only simulated
            # bottom-face snapshots receive the visual one-tier compensation.
            self._hoist.Set(drive_value)
            self._hoist_translate.Set(
                Gf.Vec3d(
                    HOIST_BASE_TRANSLATE[0],
                    HOIST_BASE_TRANSLATE[1],
                    HOIST_BASE_TRANSLATE[2] + visual_value,
                )
            )
            self._ropes.GetPointsAttr().Set(_rope_points(visual_value))
        return actual

    def set_hoist_safe(self, height_m: float, *, carried_load: bool = False) -> float:
        """Set hoist height while keeping the spreader at least 0.5 m above ground."""
        return self.set_hoist(
            _clamp(height_m, *HOIST_SOFT_LIMITS),
            carried_load=carried_load,
        )

    def set_spreader_size(
        self,
        size_ft: int,
        *,
        smooth: bool = True,
    ) -> float | None:
        """Move both telescopic end carriages to the requested ISO box length."""
        if size_ft not in (20, 40):
            raise ValueError(f"Unsupported spreader size: {size_ft!r}")
        if not self._spreader_front_translate or not self._spreader_rear_translate:
            return None

        now = time.monotonic()
        elapsed = _clamp(now - self._spreader_last_update_at, 0.0, 0.5)
        self._spreader_last_update_at = now
        target = SPREADER_TELESCOPE_TRAVEL_M if size_ft == 20 else 0.0
        if smooth:
            offset = _move_toward(
                self._spreader_offset_m,
                target,
                SPREADER_TELESCOPE_SPEED_M_S * elapsed,
            )
        else:
            offset = target

        with Usd.EditContext(self.stage, self._session):
            self._spreader_front_translate.Set(Gf.Vec3d(0.0, offset, 0.0))
            self._spreader_rear_translate.Set(Gf.Vec3d(0.0, -offset, 0.0))
        self._spreader_offset_m = offset

        # Report the composed live control value rather than merely the value
        # requested above, so status output cannot claim a visual endpoint
        # that USD rejected or failed to compose.
        return self.current_spreader_size_ft()

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

    def get_positions(self) -> tuple[float, float, float]:
        """Return the current engineering positions of the three RTG axes.

        This is intentionally based on the same drive attributes written by
        the live controller, so a manual operator can take over from the last
        PLC pose without an initial movement jump.
        """

        gantry_value = self._gantry.Get()
        trolley_value = self._trolley.Get()
        hoist_value = self._hoist.Get()
        gantry = gantry_usd_to_actual(float(gantry_value or 0.0))
        trolley = trolley_usd_to_actual(float(trolley_value or 0.0))
        hoist = hoist_usd_to_actual(float(hoist_value or 0.0))
        return gantry, trolley, hoist

    def get_gantry_bay_center_m(self) -> float:
        """Return the gantry-centre yard coordinate used to resolve a bay."""

        gantry_m, _trolley_m, _hoist_m = self.get_positions()
        size = self.current_spreader_size_ft()
        size_ft = None if size is None else (20 if float(size) < 30.0 else 40)
        return gantry_actual_to_bay_center_m(gantry_m, size_ft)

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
    "RTG_BAY_CENTERLINE_CORRECTION_Y_M",
    "RTG_WORK_CENTER_LOCAL_Y_M",
    "SPREADER_20FT_CENTER_OFFSET_Y_M",
    "SPREADER_20FT_LENGTH_M",
    "SPREADER_40FT_LENGTH_M",
    "TROLLEY_ENGINEERING_LIMITS",
    "TROLLEY_SOFT_LIMITS",
    "SPREADER_TELESCOPE_TRAVEL_M",
    "YARD_TOTAL_LENGTH_M",
    "gantry_actual_to_usd",
    "gantry_actual_to_bay_center_m",
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
