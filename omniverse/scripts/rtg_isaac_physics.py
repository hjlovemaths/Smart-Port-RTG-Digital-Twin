"""Isaac Sim physics activation for the primary RTG.

This module is deliberately separate from the current demo controller.  The
demo writes session-layer transforms, while this module activates the USD
Physics skeleton in ``rtg_simready.usda`` and sends targets to the three
linear joint drives.  The main scene remains in demo mode until
``activate_rtg_physics`` is called explicitly.

Run inside an Isaac Sim Kit application after opening ``smart_port.usda``::

    exec(open(r"D:/path/to/project/omniverse/scripts/rtg_isaac_physics.py",
              encoding="utf-8").read())
    activate_rtg_physics()
    set_rtg_physics_targets(gantry_m=10.0, trolley_m=8.0, hoist_m=4.0)

The first pass uses coarse hidden box colliders.  They are intentionally easy
to replace with measured compound proxies once the isolated RTG physics test
has passed.  Do not enable this module in the regular visual demo and the
physics controller at the same time.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
import sys
import time
from typing import Any, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.append(str(SCRIPT_DIR))

# Printed at runtime so a Script Editor session can confirm that it really
# loaded this file rather than a previously cached temp-buffer copy.
PHYSICS_BUILD_ID = "2026-09-14-trolley-full-span-v12"

PRIMARY_PATH = "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC"
STATIC_RTG_PATHS = (
    "/World/PortAndRTG/RTG_STATIC_LEFT",
    "/World/PortAndRTG/RTG_STATIC_RIGHT",
)
# A static root-level offset is deliberately separate from the animated gantry
# controller.  It moves only the primary RTG and keeps move_gantry(),
# move_trolley(), and move_hoist() usable after the RTG is parked in another
# yard block.  Keeping this op out of the animated controller also avoids
# turning the RTX LiDAR capture hierarchy into a newly-authored animated prim.
PRIMARY_RTG_PLACEMENT_OP_SUFFIX = "rtgYardPlacement"
GANTRY_PATH = f"{PRIMARY_PATH}/ANIM_CTRL_RTG_GANTRY_TRAVEL"
TROLLEY_PATH = f"{GANTRY_PATH}/ANIM_CTRL_RTG_TROLLEY_TRAVEL"
HOIST_PATH = f"{TROLLEY_PATH}/ANIM_CTRL_RTG_HOIST_VERTICAL"
ARTICULATION_ROOT_PATH = GANTRY_PATH
PHYSICS_SCENE_PATH = "/World/PhysicsScene"
PHYSICS_ROOT_PATH = "/World/RTGPhysics"

GANTRY_JOINT_PATH = f"{PHYSICS_ROOT_PATH}/GantryTravelJoint"
TROLLEY_JOINT_PATH = f"{PHYSICS_ROOT_PATH}/TrolleyTravelJoint"
HOIST_JOINT_PATH = f"{PHYSICS_ROOT_PATH}/HoistVerticalJoint"
ROPE_SYSTEM_PATH = f"{TROLLEY_PATH}/RTG_DYNAMIC_HOIST_ROPES"
SPREADER_FRONT_TELESCOPE_PATH = f"{HOIST_PATH}/ANIM_CTRL_SPREADER_TELESCOPE_FRONT"
SPREADER_REAR_TELESCOPE_PATH = f"{HOIST_PATH}/ANIM_CTRL_SPREADER_TELESCOPE_REAR"

BODY_SPECS = (
    (GANTRY_PATH, 120000.0, "gantry"),
    (TROLLEY_PATH, 15000.0, "trolley"),
    (HOIST_PATH, 5000.0, "hoist_and_spreader"),
)

JOINT_SPECS = (
    (GANTRY_JOINT_PATH, GANTRY_PATH, "Y"),
    (TROLLEY_JOINT_PATH, TROLLEY_PATH, "X"),
    (HOIST_JOINT_PATH, HOIST_PATH, "Z"),
)

# These are conservative first-pass visual-space boxes.  They are not the
# final production collision model.  Each box is hidden but follows the
# corresponding rigid body because it is authored below that body prim.
COLLISION_PROXY_SPECS = (
    ("GantryCollisionProxy", GANTRY_PATH, (2.5, 0.0, 4.0), (6.5, 11.0, 8.0)),
    ("TrolleyCollisionProxy", TROLLEY_PATH, (0.0, 0.0, 0.0), (4.5, 4.0, 1.5)),
    ("HoistCollisionProxy", HOIST_PATH, (0.0, 0.0, 0.0), (4.0, 3.0, 2.5)),
)

PHYSICS_MODE_ATTRIBUTE = "rtg:physicsMode"
COLLISION_STATUS_ATTRIBUTE = "rtg:collisionStatus"
VISUAL_CONTROL_MODE = "rtg_kinematic_visual_control"

# Keep the physics controller on the same hoist calibration as
# ``rtg_live_control.py`` and ``build_rtg_simready.py``.  The checked-in
# rtg_simready layer can be older than those runtime scripts; its legacy
# mapping compressed the complete hoist command into only 1.30 USD metres and
# made a 4 m -> 14 m command almost invisible.
HOIST_ENGINEERING_LIMITS = (0.0, 18.0)
TROLLEY_ENGINEERING_LIMITS = (0.0, 18.0)
# The RTG asset's trolley controller is compressed in USD space.  Engineering
# travel is 0..18 m, but the authored trolley rail is only about 6.9 USD m
# long.  Writing -18 USD m sends the trolley beyond the gantry, which is why
# multi-position scans previously showed the spreader outside the RTG frame.
TROLLEY_USD_POSITION_LIMITS = (
    0.005003036169343086,
    -6.9031499445989670,
)
HOIST_ACTUAL_TIER_HEIGHT_M = 2.60
HOIST_VISUAL_TIER_HEIGHT_M = 0.904353 + 0.035
HOIST_USD_PER_ENGINEERING_M = (
    HOIST_VISUAL_TIER_HEIGHT_M / HOIST_ACTUAL_TIER_HEIGHT_M
)
HOIST_USD_AT_FIRST_TIER = -1.525647
HOIST_USD_AT_ENGINEERING_ZERO = (
    HOIST_USD_AT_FIRST_TIER
    - HOIST_ACTUAL_TIER_HEIGHT_M * HOIST_USD_PER_ENGINEERING_M
)
HOIST_USD_POSITION_LIMITS = (
    HOIST_USD_AT_ENGINEERING_ZERO,
    HOIST_USD_AT_ENGINEERING_ZERO
    + HOIST_ENGINEERING_LIMITS[1] * HOIST_USD_PER_ENGINEERING_M,
)
# Frame-1 local Z of the authored hoist controller.  Rope lower endpoints are
# measured against this fixed source pose, rather than against whichever pose
# happened to be active when the visual controller was reloaded.
HOIST_AUTHORED_SOURCE_Z = 4.3330589999999995
JOINT_ONLY_COLLISION_STATUS = "joint_only_no_collision"
COARSE_COLLISION_STATUS = "coarse_box_proxy_v1"

# The rope curve is authored in trolley-local coordinates.  Keep this small
# visual-only description here instead of importing the live PLC controller,
# so enabling physics cannot accidentally start a second command writer.
LOWER_HEADER_LEFT_X = 2.3456
LOWER_HEADER_RIGHT_X = 2.7524
LOWER_HEADER_FRONT_Y = -5.56
LOWER_HEADER_REAR_Y = -4.44
LOWER_HEADER_SOURCE_Z = 4.3575
LOWER_ROPE_VISIBLE_OFFSET_Z = 0.0
# The dynamic rope ends are attached to the top face of the yellow lift
# headers, not to the overall spreader bounding box.  The static RTGs use the
# same yellow header geometry as their visual attachment reference.
ROPE_YELLOW_HEADER_CLEARANCE_M = 0.0
ROPE_GREEN_MARKER_CENTER_OFFSET_M = 0.13
ROPE_ATTACHMENT_NAME_TOKEN = "YELLOW_PAIR_LIFT_HEADER"
ROPE_ANCHOR_NAME_TOKENS = (
    "YELLOW_LIFTING_LUG_TOP",
    "YELLOW_SHORT_UPRIGHT",
)
ROPE_GREEN_MARKER_NAMES = {"C4", "C5", "C6", "C7"}
# Static RTG reference offsets are kept for diagnostics and fallback tooling,
# but the active dynamic rope should keep its authored trolley-local X/Y
# endpoints.  The dynamic trolley frame is not identical to the static RTG
# yard-population frame; applying the static offsets here pushes the fixed
# upper endpoints outside the trolley platform.
STATIC_REFERENCE_ROPE_XY_OFFSETS = (
    ((2.1920, 0.5500), (0.2998, 0.5600)),
    ((2.1389, 0.5500), (0.2998, 0.5600)),
    ((2.1920, -0.5500), (0.2998, -0.5600)),
    ((2.1389, -0.5500), (0.2998, -0.5600)),
    ((0.9390, 0.5500), (0.2998, 0.5600)),
    ((0.8859, 0.5500), (0.2998, 0.5600)),
    ((0.9390, -0.5500), (0.2998, -0.5600)),
    ((0.8859, -0.5500), (0.2998, -0.5600)),
    ((-0.6825, 0.5500), (-0.2999, 0.5600)),
    ((-0.7356, 0.5500), (-0.2999, 0.5600)),
    ((-0.6825, -0.5500), (-0.2999, -0.5600)),
    ((-0.7356, -0.5500), (-0.2999, -0.5600)),
    ((-1.9355, 0.5500), (-0.2999, 0.5600)),
    ((-1.9886, 0.5500), (-0.2999, 0.5600)),
    ((-1.9355, -0.5500), (-0.2999, -0.5600)),
    ((-1.9886, -0.5500), (-0.2999, -0.5600)),
)
ROPE_SPREADER_CENTER_X = 2.5490
ROPE_SPREADER_CENTER_Y = -5.0000
ROPE_ENDPOINTS = tuple(
    (
        (
            upper[0],
            upper[1],
            8.81,
        ),
        (
            lower[0],
            lower[1],
            LOWER_HEADER_SOURCE_Z,
        ),
    )
    for upper, lower in (
        ((1.0620, -5.55), (LOWER_HEADER_LEFT_X, LOWER_HEADER_FRONT_Y)),
        ((1.0980, -5.55), (LOWER_HEADER_LEFT_X, LOWER_HEADER_FRONT_Y)),
        ((1.0620, -4.45), (LOWER_HEADER_LEFT_X, LOWER_HEADER_REAR_Y)),
        ((1.0980, -4.45), (LOWER_HEADER_LEFT_X, LOWER_HEADER_REAR_Y)),
        ((1.9120, -5.55), (LOWER_HEADER_LEFT_X, LOWER_HEADER_FRONT_Y)),
        ((1.9480, -5.55), (LOWER_HEADER_LEFT_X, LOWER_HEADER_FRONT_Y)),
        ((1.9120, -4.45), (LOWER_HEADER_LEFT_X, LOWER_HEADER_REAR_Y)),
        ((1.9480, -4.45), (LOWER_HEADER_LEFT_X, LOWER_HEADER_REAR_Y)),
        ((3.0120, -5.55), (LOWER_HEADER_RIGHT_X, LOWER_HEADER_FRONT_Y)),
        ((3.0480, -5.55), (LOWER_HEADER_RIGHT_X, LOWER_HEADER_FRONT_Y)),
        ((3.0120, -4.45), (LOWER_HEADER_RIGHT_X, LOWER_HEADER_REAR_Y)),
        ((3.0480, -4.45), (LOWER_HEADER_RIGHT_X, LOWER_HEADER_REAR_Y)),
        ((3.8620, -5.55), (LOWER_HEADER_RIGHT_X, LOWER_HEADER_FRONT_Y)),
        ((3.8980, -5.55), (LOWER_HEADER_RIGHT_X, LOWER_HEADER_FRONT_Y)),
        ((3.8620, -4.45), (LOWER_HEADER_RIGHT_X, LOWER_HEADER_REAR_Y)),
        ((3.8980, -4.45), (LOWER_HEADER_RIGHT_X, LOWER_HEADER_REAR_Y)),
    )
)

# Kept alive for the lifetime of the Kit stage.  Without a strong reference,
# the update-stream subscription can be collected and the rope appears to
# stop following the simulated hoist.
_PHYSICS_SYNC_SUBSCRIPTIONS: dict[int, Any] = {}
_PHYSICS_SYNC_BASELINES: dict[int, Any] = {}
_PHYSICS_SYNC_BASE_LOCAL_TRANSLATIONS: dict[int, tuple[float, float, float]] = {}
_ARTICULATION_CONTROLLERS: dict[int, Any] = {}
_ARTICULATION_VIEWS: dict[int, Any] = {}
_ARTICULATION_DOF_INDICES: dict[int, Any] = {}
_ARTICULATION_ERRORS: set[int] = set()
_ARTICULATION_TARGETS: dict[int, list[float]] = {}
_ARTICULATION_APPLIED_TARGETS: dict[int, tuple[float, ...]] = {}
_ENGINEERING_TARGETS: dict[int, dict[str, float]] = {}
_RTG_ANIMATION_SUBSCRIPTIONS: dict[int, Any] = {}
_RTG_ANIMATION_STATES: dict[int, dict[str, Any]] = {}
_RTG_TIMELINE_LOCK_SUBSCRIPTIONS: dict[int, Any] = {}
_RTG_TIMELINE_LOCK_STATES: dict[int, dict[str, Any]] = {}
_PHYSICS_SYNC_LAST_TICKS: dict[int, float] = {}
_PHYSICS_SYNC_LAST_POSES: dict[int, tuple[float, float, float]] = {}
_ROPE_ATTACHMENT_PROFILES: dict[int, dict[str, Any]] = {}
PHYSICS_VISUAL_SYNC_HZ = 30.0
VISIBLE_ROPE_WIDTH_M = 0.065
DEFAULT_RTG_GANTRY_M = 16.0
DEFAULT_RTG_TROLLEY_M = 1.6
DEFAULT_RTG_HOIST_M = 14.0


@dataclass(frozen=True)
class PhysicsValidation:
    valid: bool
    missing_bodies: tuple[str, ...] = ()
    missing_joints: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "missing_bodies": list(self.missing_bodies),
            "missing_joints": list(self.missing_joints),
        }


def engineering_to_usd(
    engineering_value: float,
    engineering_limits: tuple[float, float],
    usd_limits: tuple[float, float],
) -> float:
    """Map a PLC/engineering coordinate to a USD joint coordinate."""

    e0, e1 = engineering_limits
    u0, u1 = usd_limits
    if e1 == e0:
        return float(u0)
    value = min(max(float(engineering_value), min(e0, e1)), max(e0, e1))
    fraction = (value - e0) / (e1 - e0)
    return float(u0 + fraction * (u1 - u0))


def _runtime_modules():
    """Import USD modules only when the script is executed in Kit."""

    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

    return Gf, Sdf, Usd, UsdGeom, UsdPhysics


def _resolve_stage(stage: Optional[Any]) -> Any:
    if stage is not None:
        return stage
    import omni.usd  # type: ignore

    resolved = omni.usd.get_context().get_stage()
    if resolved is None:
        raise RuntimeError("Open smart_port.usda before activating RTG physics")
    return resolved


def move_primary_rtg_to_bay(
    bay_id: str = "3C/002",
    stage: Optional[Any] = None,
) -> dict[str, Any]:
    """Place only the primary RTG over a yard bay before LiDAR startup.

    ``move_gantry`` changes the animated gantry controller inside the RTG.
    This helper adds a static root translation so the complete primary RTG
    (legs, gantry, trolley, hoist, and any sensor created afterwards) is moved
    to a different yard block.  The background RTGs are not touched.

    The target Y uses the same calibrated bay-centre mapping as the live RTG
    controller.  Call this before ``start_rtg_yard_lidar``; the LiDAR service
    creates its root-level sensor from the RTG's resulting trolley pose.
    """

    resolved = _resolve_stage(stage)
    Gf, _Sdf, Usd, UsdGeom, _UsdPhysics = _runtime_modules()
    root = _valid_prim(resolved, PRIMARY_PATH)
    gantry = _valid_prim(resolved, GANTRY_PATH)
    if root is None or gantry is None:
        raise RuntimeError(
            f"Primary RTG hierarchy is incomplete: root={PRIMARY_PATH}, "
            f"gantry={GANTRY_PATH}"
        )

    # Reuse the operator-facing bay calibration instead of duplicating the
    # 3C/002 scene offsets in this module.  Import lazily so system-Python
    # tests can still import this module without pxr/Kit.
    try:
        from rtg_live_control import gantry_bay_id_to_usd
    except ModuleNotFoundError:
        from pathlib import Path as _Path

        scripts_dir = _Path(__file__).resolve().parent
        if str(scripts_dir) not in sys.path:
            sys.path.append(str(scripts_dir))
        from rtg_live_control import gantry_bay_id_to_usd

    target_controller_y = float(gantry_bay_id_to_usd(bay_id))
    try:
        import omni.timeline  # type: ignore

        sample_time = max(
            1.0,
            float(omni.timeline.get_timeline_interface().get_current_time()),
        )
    except Exception:
        sample_time = 1.0
    time_code = Usd.TimeCode(sample_time)
    cache = UsdGeom.XformCache(time_code)
    current_gantry_world = cache.GetLocalToWorldTransform(gantry).ExtractTranslation()

    root_xform = UsdGeom.Xformable(root)
    placement_op = None
    expected_op_name = f"xformOp:translate:{PRIMARY_RTG_PLACEMENT_OP_SUFFIX}"
    for op in root_xform.GetOrderedXformOps():
        if op.GetOpName() == expected_op_name:
            placement_op = op
            break
    if placement_op is None:
        placement_op = root_xform.AddTranslateOp(
            opSuffix=PRIMARY_RTG_PLACEMENT_OP_SUFFIX
        )
        current_offset = Gf.Vec3d(0.0, 0.0, 0.0)
    else:
        current_offset = placement_op.Get(Usd.TimeCode.Default())
        if current_offset is None:
            current_offset = Gf.Vec3d(0.0, 0.0, 0.0)

    # PortAndRTG and the primary RTG root are world-aligned in this project,
    # so a world-Y placement is also a root-local-Y placement.  Accumulating
    # the delta makes repeated calls (for example 3C/002 -> 3C/005) stable.
    delta_y = target_controller_y - float(current_gantry_world[1])
    new_offset = Gf.Vec3d(
        float(current_offset[0]),
        float(current_offset[1]) + delta_y,
        float(current_offset[2]),
    )
    # Use the current non-session edit target.  The runtime sensor is also
    # authored there; RTX 6.0.1 can produce an invalid GMO for renderable
    # sensors authored in the anonymous Session Layer.
    placement_op.Set(new_offset)

    post_cache = UsdGeom.XformCache(time_code)
    new_root_world = post_cache.GetLocalToWorldTransform(root).ExtractTranslation()
    new_gantry_world = post_cache.GetLocalToWorldTransform(gantry).ExtractTranslation()
    # Keep the public engineering target in the same absolute world-Y space
    # as the placement helper.  Otherwise a later move_trolley()/move_hoist()
    # call preserves the old default gantry target (16.0 m) and moves the
    # entire RTG back toward the 1C side even though the root placement op is
    # still present.
    engineering_targets = _current_engineering_targets(resolved)
    engineering_targets["gantry_m"] = target_controller_y
    _ENGINEERING_TARGETS[id(resolved)] = engineering_targets
    result = {
        "bayId": str(bay_id).strip().upper().replace("-", "/"),
        "primaryPath": PRIMARY_PATH,
        "targetControllerY": target_controller_y,
        "placementDeltaWorldM": [0.0, delta_y, 0.0],
        "placementOffsetM": [
            float(new_offset[0]),
            float(new_offset[1]),
            float(new_offset[2]),
        ],
        "rootWorldPositionM": [
            float(new_root_world[0]),
            float(new_root_world[1]),
            float(new_root_world[2]),
        ],
        "gantryWorldPositionM": [
            float(new_gantry_world[0]),
            float(new_gantry_world[1]),
            float(new_gantry_world[2]),
        ],
        "placementOp": placement_op.GetOpName(),
        "engineeringTargets": dict(engineering_targets),
        "note": "Only the primary RTG was moved; create/restart LiDAR after placement",
    }
    print("[RTG Physics] Primary RTG placed", result)
    return result


def _session_edit_context(stage: Any):
    try:
        _Gf, _Sdf, Usd, _UsdGeom, _UsdPhysics = _runtime_modules()
        session_layer = stage.GetSessionLayer()
        if session_layer is not None:
            return Usd.EditContext(stage, session_layer)
    except Exception:
        pass
    return nullcontext()


def _valid_prim(stage: Any, path: str) -> Any:
    # Isaac Sim 2025.1 exposes the strict C++ overload here and does not
    # implicitly convert Python strings to SdfPath values.
    if isinstance(path, str):
        try:
            from pxr import Sdf

            path = Sdf.Path(path)
        except ImportError:
            # Keeps the helper importable by the system Python test runner.
            pass
    prim = stage.GetPrimAtPath(path)
    if not prim or (hasattr(prim, "IsValid") and not prim.IsValid()):
        return None
    return prim


def validate_rtg_physics_stage(stage: Optional[Any] = None) -> PhysicsValidation:
    """Check that the authored RTG bodies and joints are present."""

    resolved = _resolve_stage(stage)
    missing_bodies = tuple(path for path, _mass, _role in BODY_SPECS
                           if _valid_prim(resolved, path) is None)
    missing_joints = tuple(path for path, _body, _axis in JOINT_SPECS
                           if _valid_prim(resolved, path) is None)
    return PhysicsValidation(
        valid=not missing_bodies and not missing_joints,
        missing_bodies=missing_bodies,
        missing_joints=missing_joints,
    )


def _set_attribute(prim: Any, name: str, value: Any, type_name: Any) -> None:
    attr = prim.GetAttribute(name)
    if not attr:
        attr = prim.CreateAttribute(name, type_name)
    attr.Set(value)


def _get_attribute_value(attr: Any, default: Any = None, time_code: Any = None) -> Any:
    """Read an optional USD attribute without calling Get on an invalid handle."""

    if attr is None or not attr.IsValid():
        return default
    try:
        value = attr.Get() if time_code is None else attr.Get(time_code)
    except Exception:
        return default
    return default if value is None else value


def _freeze_animation_attribute(attr: Any, time_code: Any) -> tuple[Any, bool] | None:
    """Write the current value as a stronger static session-layer opinion.

    Do not mirror every source time sample into the session layer.  Isaac Sim
    6.x treats a prim with newly-authored animated xform/points samples as
    moving render geometry; that makes RTX LiDAR return an all-zero GMO when
    MotionBVH is not enabled.  A stronger default is sufficient for the
    sensor-capture pose, while the source animation remains untouched on disk.
    """

    try:
        samples = list(attr.GetTimeSamples())
    except Exception:
        samples = []
    value = attr.Get(time_code)
    if value is None and samples:
        value = attr.Get(samples[0])
    if value is None:
        return None
    attr.Set(value)
    return value, attr.GetName() == "points"


def _set_animation_attribute_constant(attr: Any, value: Any) -> None:
    """Override a USD default and every existing animation sample.

    In USD, a weaker layer's time sample can still win over a stronger layer's
    default value at that time.  The RTG Blender payload uses time samples for
    its three controller transforms, so a command that only writes the default
    looks correct in a log but is invisible while Play is active.
    """

    attr.Set(value)
    try:
        samples = list(attr.GetTimeSamples())
    except Exception:
        samples = []
    for sample in samples:
        attr.Set(value, sample)


def _ensure_physics_scene(stage: Any, UsdPhysics: Any, Gf: Any) -> None:
    scene = UsdPhysics.Scene.Define(stage, PHYSICS_SCENE_PATH)
    scene.CreateGravityDirectionAttr(Gf.Vec3f(0.0, 0.0, -1.0)).Set(
        Gf.Vec3f(0.0, 0.0, -1.0)
    )
    scene.CreateGravityMagnitudeAttr(9.81).Set(9.81)


def _ensure_collision_proxy(
    stage: Any,
    UsdGeom: Any,
    UsdPhysics: Any,
    Gf: Any,
    Sdf: Any,
    name: str,
    parent_path: str,
    center: tuple[float, float, float],
    size: tuple[float, float, float],
) -> str:
    path = f"{parent_path}/{name}"
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1.0).Set(1.0)
    xformable = UsdGeom.Xformable(cube.GetPrim())
    prim = cube.GetPrim()

    def set_or_add_op(name: str, value: Any, add_op: Any) -> None:
        # Inspect the attribute by its USD name first.  This is robust when
        # the proxy was created by an earlier session and already has an
        # xformOpOrder authored on the prim.
        attr = prim.GetAttribute(name)
        if attr and attr.IsValid():
            attr.Set(value)
            return
        add_op().Set(value)

    set_or_add_op(
        "xformOp:translate",
        Gf.Vec3d(*center),
        xformable.AddTranslateOp,
    )
    set_or_add_op(
        "xformOp:scale",
        Gf.Vec3d(*size),
        xformable.AddScaleOp,
    )
    imageable = UsdGeom.Imageable(cube.GetPrim())
    imageable.CreateVisibilityAttr(UsdGeom.Tokens.invisible).Set(
        UsdGeom.Tokens.invisible
    )
    imageable.CreatePurposeAttr().Set(UsdGeom.Tokens.guide)
    collision = UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
    collision.CreateCollisionEnabledAttr(True).Set(True)
    _set_attribute(
        cube.GetPrim(),
        COLLISION_STATUS_ATTRIBUTE,
        COARSE_COLLISION_STATUS,
        Sdf.ValueTypeNames.Token,
    )
    return path


def _ensure_body_physics(stage: Any, UsdPhysics: Any, Sdf: Any) -> None:
    for path, mass, role in BODY_SPECS:
        prim = _valid_prim(stage, path)
        if prim is None:
            continue
        body = UsdPhysics.RigidBodyAPI.Apply(prim)
        body.CreateRigidBodyEnabledAttr(True).Set(True)
        UsdPhysics.MassAPI.Apply(prim).CreateMassAttr(float(mass)).Set(float(mass))
        _set_attribute(prim, "rtg:physicsRole", role, Sdf.ValueTypeNames.Token)
        _set_attribute(
            prim,
            COLLISION_STATUS_ATTRIBUTE,
            JOINT_ONLY_COLLISION_STATUS,
            Sdf.ValueTypeNames.Token,
        )


def _enable_joint(stage: Any, joint_path: str, UsdPhysics: Any, Sdf: Any) -> None:
    prim = _valid_prim(stage, joint_path)
    if prim is None:
        return
    _set_attribute(
        prim, "physics:jointEnabled", True, Sdf.ValueTypeNames.Bool
    )
    drive = UsdPhysics.DriveAPI.Get(prim, UsdPhysics.Tokens.linear)
    if drive:
        # The authored SimReady layer contains demonstration target samples.
        # Freeze every sample in the session layer so pressing Play cannot
        # make the Blender demo drive fight the PhysX command.
        target_attr = drive.GetTargetPositionAttr()
        target = _get_attribute_value(target_attr)
        if target is not None:
            _set_animation_attribute_constant(
                target_attr, float(target)
            )
        velocity_attr = drive.GetTargetVelocityAttr()
        if velocity_attr and velocity_attr.IsValid():
            _set_animation_attribute_constant(velocity_attr, 0.0)


def _move_articulation_root(stage: Any, UsdPhysics: Any) -> None:
    """Keep exactly one articulation root on the Gantry link."""

    old_joint_root = _valid_prim(stage, GANTRY_JOINT_PATH)
    if old_joint_root is not None:
        try:
            old_joint_root.RemoveAPI(UsdPhysics.ArticulationRootAPI)
        except Exception:
            # Older USD Python bindings may not expose RemoveAPI for this
            # schema.  The new root below still provides the intended API;
            # the diagnostic report makes the authored duplicate visible.
            pass
    root = _valid_prim(stage, ARTICULATION_ROOT_PATH)
    if root is not None:
        UsdPhysics.ArticulationRootAPI.Apply(root)


def _align_joint_anchors(
    stage: Any,
    Gf: Any,
    Sdf: Any,
    Usd: Any,
    UsdGeom: Any,
) -> dict[str, list[float]]:
    """Align joint frames to the model's current authored pose.

    The RTG source asset is placed in the yard at a non-zero offset and the
    visual scene also has a separate animation layer.  The original physics
    prototype used zero-based rig coordinates, which leaves the world-anchored
    gantry and the trolley/hoist joints disconnected when physics is enabled.
    Position drives then have to resolve a large initial error and PhysX emits
    ``disjointed body transforms``.  Keeping the current pose as joint position
    zero makes the engineering targets relative to the displayed RTG and
    prevents the initial snap.
    """

    gantry_world = _evaluated_world_transform(stage, GANTRY_PATH, Usd, UsdGeom)
    trolley_local = _evaluated_local_transform(stage, TROLLEY_PATH, Usd, UsdGeom)
    hoist_local = _evaluated_local_transform(stage, HOIST_PATH, Usd, UsdGeom)
    if gantry_world is None or trolley_local is None or hoist_local is None:
        return {}

    def vec3(matrix: Any) -> Any:
        translation = matrix.ExtractTranslation()
        return Gf.Vec3f(
            float(translation[0]),
            float(translation[1]),
            float(translation[2]),
        )

    values = {
        GANTRY_JOINT_PATH: vec3(gantry_world),
        TROLLEY_JOINT_PATH: vec3(trolley_local),
        HOIST_JOINT_PATH: vec3(hoist_local),
    }
    for joint_path, position in values.items():
        joint = _valid_prim(stage, joint_path)
        if joint is not None:
            _set_attribute(
                joint,
                "physics:localPos0",
                position,
                Sdf.ValueTypeNames.Point3f,
            )
            _set_attribute(
                joint,
                "rtg:jointAnchorAligned",
                True,
                Sdf.ValueTypeNames.Bool,
            )
    return {
        path: [float(value[0]), float(value[1]), float(value[2])]
        for path, value in values.items()
    }


def _freeze_authored_animation(stage: Any, Usd: Any, UsdGeom: Any, Sdf: Any) -> list[str]:
    """Mask Blender time samples on all visual parts driven by physics.

    The visual scene contains authored animation for the same controller
    prims.  Leaving those samples active while PhysX is running creates two
    writers for one body and is the usual cause of a collapsing articulation.
    This also masks the authored rope point samples and spreader telescope
    transforms; otherwise the rope can continue down its demo timeline while
    the physical hoist stays at its commanded pose.  The values are authored
    in the session layer and disappear on reload.
    """

    frozen: list[str] = []
    time_code = Usd.TimeCode.Default()
    visual_paths = [path for path, _mass, _role in BODY_SPECS]
    visual_paths.extend(
        [SPREADER_FRONT_TELESCOPE_PATH, SPREADER_REAR_TELESCOPE_PATH]
    )
    for body_path in visual_paths:
        prim = _valid_prim(stage, body_path)
        if prim is None:
            continue
        for op in UsdGeom.Xformable(prim).GetOrderedXformOps():
            attr = op.GetAttr()
            frozen_value = _freeze_animation_attribute(attr, time_code)
            if frozen_value is not None:
                frozen.append(f"{body_path}:{attr.GetName()}")
        _set_attribute(
            prim,
            "rtg:authoredAnimationSuppressed",
            True,
            Sdf.ValueTypeNames.Bool,
        )

    rope = _valid_prim(stage, ROPE_SYSTEM_PATH)
    if rope is not None:
        points = rope.GetAttribute("points")
        # Do not author animated BasisCurves into the session layer during
        # sensor capture.  Even a single points opinion can invalidate the
        # RTX scene acceleration structure in Isaac Sim 6.0.1.  The rope is
        # visual-only; keeping its source samples avoids sacrificing LiDAR.
        del points
        _set_attribute(
            rope,
            "rtg:authoredAnimationSuppressed",
            True,
            Sdf.ValueTypeNames.Bool,
        )
    # The left and right RTGs are background assets. Their animation samples
    # live on the same three controller nodes as the primary RTG. Freeze only
    # those nodes instead of traversing every high-resolution mesh below the
    # static models during startup.
    # Prefer the named background roots, but discover equivalent controller
    # nodes as well.  Some payload variants expose the two background RTGs
    # under a generated prim name rather than RTG_STATIC_LEFT/RIGHT.
    static_gantry_paths = {
        f"{static_path}/ANIM_CTRL_RTG_GANTRY_TRAVEL"
        for static_path in STATIC_RTG_PATHS
    }
    for prim in Usd.PrimRange(stage.GetPseudoRoot()):
        if prim.GetName() != "ANIM_CTRL_RTG_GANTRY_TRAVEL":
            continue
        prim_path = str(prim.GetPath())
        if not prim_path.startswith(f"{PRIMARY_PATH}/"):
            static_gantry_paths.add(prim_path)

    for static_gantry in sorted(static_gantry_paths):
        static_trolley = f"{static_gantry}/ANIM_CTRL_RTG_TROLLEY_TRAVEL"
        static_hoist = f"{static_trolley}/ANIM_CTRL_RTG_HOIST_VERTICAL"
        for static_body_path in (static_gantry, static_trolley, static_hoist):
            prim = _valid_prim(stage, static_body_path)
            if prim is None:
                continue
            for op in UsdGeom.Xformable(prim).GetOrderedXformOps():
                attr = op.GetAttr()
                frozen_value = _freeze_animation_attribute(attr, time_code)
                if frozen_value is not None:
                    frozen.append(f"{static_body_path}:{attr.GetName()}")
            _set_attribute(
                prim,
                "rtg:authoredAnimationSuppressed",
                True,
                Sdf.ValueTypeNames.Bool,
            )

    # Payload variants can place static hoist ropes outside the named
    # background roots.  Only lock actual BasisCurves whose path identifies
    # them as a hoist rope.  Do not include spreader Mesh ``points`` here:
    # rewriting those large arrays every frame forces expensive RTX geometry
    # rebuilds and makes Play appear CPU-bound even on a fast GPU.
    for prim in Usd.PrimRange(stage.GetPseudoRoot()):
        prim_path = str(prim.GetPath())
        if prim_path.startswith(f"{PRIMARY_PATH}/"):
            continue
        if prim.GetTypeName() != "BasisCurves":
            continue
        if "HOIST_ROPE" not in prim_path.upper():
            continue
        points = prim.GetAttribute("points")
        del points
        _set_attribute(
            prim,
            "rtg:authoredAnimationSuppressed",
            True,
            Sdf.ValueTypeNames.Bool,
        )
    return frozen


def freeze_rtg_animations(stage: Optional[Any] = None) -> dict[str, Any]:
    """Hold the whole animated yard at one pose while RTX keeps ticking.

    Isaac Sim 6.0.1 needs a real playing/advancing timeline pulse to produce a
    valid RTX LiDAR GMO.  ``set_auto_update(False)`` therefore cannot be used
    here: it makes the sensor return a zero-length or invalid GMO buffer.
    Instead, mask the visual animation samples with static session-layer
    values, keep automatic updates enabled, and let the timeline advance.  The
    RTX rotary sensor must see advancing time to produce multiple scan passes.
    """

    resolved = _resolve_stage(stage)
    Gf, Sdf, Usd, UsdGeom, _UsdPhysics = _runtime_modules()
    import omni.timeline

    timeline = omni.timeline.get_timeline_interface()
    # Timeline values are seconds in Kit.  Preserve the pose currently shown
    # by the user (for example after positioning the RTG at 3C), rather than
    # converting the USD start time code and unexpectedly jumping to another
    # animation sample.
    current_time_s = float(timeline.get_current_time())
    # RTX rotary sensors in Isaac Sim 6.0.1 do not emit a complete GMO when
    # held below 1 second after stage startup.  Keep a user-selected later
    # pose, but avoid that invalid initial interval for a freshly opened stage.
    static_time_s = max(1.0, current_time_s)
    timeline.set_auto_update(True)
    timeline.set_current_time(static_time_s)
    key = id(resolved)
    old_subscription = _RTG_TIMELINE_LOCK_SUBSCRIPTIONS.pop(key, None)
    if old_subscription is not None:
        try:
            old_subscription.unsubscribe()
        except Exception:
            pass
    with _session_edit_context(resolved):
        frozen_paths = _freeze_authored_animation(resolved, Usd, UsdGeom, Sdf)
    _RTG_TIMELINE_LOCK_STATES[key] = {
        "timeline": timeline,
        "time_s": static_time_s,
        "mode": "authored_static_pose",
    }
    return {
        "frozen_count": len(frozen_paths),
        "frozen_paths": frozen_paths,
        "mode": "authored_static_pose",
        "timeline_auto_update": True,
        "timeline_time_s": static_time_s,
        "note": "Play remains enabled; session-layer animation masks hold the pose while LiDAR time advances",
    }


def _rope_points(Gf: Any, delta: Any) -> list[Any]:
    """Transform the authored rope endpoints by the current hoist delta."""

    points: list[Any] = []
    for upper, lower in ROPE_ENDPOINTS:
        points.append(Gf.Vec3f(*upper))
        lower_point = Gf.Vec3d(
            lower[0],
            lower[1],
            lower[2] + LOWER_ROPE_VISIBLE_OFFSET_Z,
        )
        moved = delta.Transform(lower_point)
        points.append(Gf.Vec3f(float(moved[0]), float(moved[1]), float(moved[2])))
    return points


def _capture_rope_attachment_profile(
    stage: Any,
    Gf: Any,
    Usd: Any,
    UsdGeom: Any,
) -> Optional[dict[str, Any]]:
    """Measure the dynamic spreader in trolley-local coordinates.

    The authored dynamic rope used a legacy header point whose Z was below the
    visible spreader.  The static RTGs instead use the spreader's real bounds.
    Measure that same relationship here and normalize it back to the authored
    hoist source pose so later hoist commands only add a delta once.
    """

    hoist = _valid_prim(stage, HOIST_PATH)
    trolley = _valid_prim(stage, TROLLEY_PATH)
    if hoist is None or trolley is None:
        return None
    try:
        cache = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
            useExtentsHint=True,
        )
        spreader_range = Gf.Range3d()
        header_range = Gf.Range3d()
        anchor_points: list[tuple[float, float, float]] = []
        green_marker_points: list[tuple[float, float, float]] = []
        found_spreader = False
        found_header = False
        for prim in Usd.PrimRange(hoist):
            name = prim.GetName().upper()
            if not prim.IsA(UsdGeom.Boundable):
                continue
            aligned = cache.ComputeWorldBound(prim).ComputeAlignedRange()
            if ROPE_ATTACHMENT_NAME_TOKEN in name:
                header_range.UnionWith(aligned)
                found_header = True
            if any(token in name for token in ROPE_ANCHOR_NAME_TOKENS):
                anchor_points.append(
                    (float(aligned.GetMidpoint()[0]),
                     float(aligned.GetMidpoint()[1]),
                     float(aligned.GetMax()[2]))
                )
            if name in ROPE_GREEN_MARKER_NAMES:
                midpoint = aligned.GetMidpoint()
                green_marker_points.append(
                    (
                        float(midpoint[0]),
                        float(midpoint[1]),
                        float(midpoint[2]),
                    )
                )
            if "SPREAD" in name:
                spreader_range.UnionWith(aligned)
                found_spreader = True
        if not found_spreader and found_header:
            spreader_range = header_range
            found_spreader = True
        if not found_spreader:
            return None

        trolley_world = UsdGeom.Xformable(trolley).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()
        )
        trolley_inverse = trolley_world.GetInverse()
        def local_bounds(world_range: Any) -> tuple[list[float], list[float], list[float]]:
            minimum = world_range.GetMin()
            maximum = world_range.GetMax()
            local_corners = [
                trolley_inverse.Transform(Gf.Vec3d(x, y, z))
                for x in (minimum[0], maximum[0])
                for y in (minimum[1], maximum[1])
                for z in (minimum[2], maximum[2])
            ]
            return (
                [float(point[0]) for point in local_corners],
                [float(point[1]) for point in local_corners],
                [float(point[2]) for point in local_corners],
            )

        local_x, local_y, _spreader_z = local_bounds(spreader_range)
        _header_x, _header_y, header_z = local_bounds(
            header_range if found_header else spreader_range
        )
        local_anchor_points: list[tuple[float, float, float]] = []
        for world_x, world_y, world_z in anchor_points:
            local_point = trolley_inverse.Transform(
                Gf.Vec3d(world_x, world_y, world_z)
            )
            local_anchor_points.append(
                (float(local_point[0]), float(local_point[1]), float(local_point[2]))
            )
        local_green_marker_points: list[tuple[float, float, float]] = []
        for world_x, world_y, world_z in green_marker_points:
            local_point = trolley_inverse.Transform(
                Gf.Vec3d(world_x, world_y, world_z)
            )
            local_green_marker_points.append(
                (float(local_point[0]), float(local_point[1]), float(local_point[2]))
            )
        current_hoist = _evaluated_local_transform(stage, HOIST_PATH, Usd, UsdGeom)
        current_hoist_z = (
            float(current_hoist.ExtractTranslation()[2])
            if current_hoist is not None
            else HOIST_AUTHORED_SOURCE_Z
        )
        # Store the measured point in the fixed authored source frame.  This
        # remains correct even if the user reloads the module after moving the
        # hoist to a different target.
        source_lower_z = (
            max(header_z)
            + ROPE_YELLOW_HEADER_CLEARANCE_M
            + HOIST_AUTHORED_SOURCE_Z
            - current_hoist_z
        )
        source_anchor_points = [
            (
                x,
                y,
                z + HOIST_AUTHORED_SOURCE_Z - current_hoist_z,
            )
            for x, y, z in local_anchor_points
        ]
        source_green_marker_points = [
            (
                x,
                y,
                z + HOIST_AUTHORED_SOURCE_Z - current_hoist_z,
            )
            for x, y, z in local_green_marker_points
        ]
        return {
            "center_x": (min(local_x) + max(local_x)) * 0.5,
            "center_y": (min(local_y) + max(local_y)) * 0.5,
            "source_lower_z": source_lower_z,
            "source_anchor_points": source_anchor_points,
            "source_green_marker_points": source_green_marker_points,
        }
    except Exception as error:
        print(f"[RTG Physics] rope profile capture skipped: {error}")
        return None


def _rope_points_from_profile(
    Gf: Any,
    profile: dict[str, Any],
    hoist_delta_z: float,
) -> list[Any]:
    """Build cable endpoints using the authored X/Y and measured header Z.

    The visible rope endpoints must remain in the dynamic trolley's authored
    local X/Y frame.  The yellow spreader header height, however, comes from
    the live stage bounds because the loaded smart_port layer can place the
    spreader higher than the older generated rope constants.
    """

    fallback_lower_z = (
        float(profile.get("source_lower_z", LOWER_HEADER_SOURCE_Z))
        + ROPE_GREEN_MARKER_CENTER_OFFSET_M
        + LOWER_ROPE_VISIBLE_OFFSET_Z
        + hoist_delta_z
    )
    green_marker_points = profile.get("source_green_marker_points") or []
    points: list[Any] = []
    for upper, lower in ROPE_ENDPOINTS:
        points.append(Gf.Vec3f(*upper))
        if green_marker_points:
            marker_x, marker_y, marker_z = min(
                green_marker_points,
                key=lambda item: (item[0] - lower[0]) ** 2
                + (item[1] - lower[1]) ** 2,
            )
            lower_point = (
                float(marker_x),
                float(marker_y),
                float(marker_z + LOWER_ROPE_VISIBLE_OFFSET_Z + hoist_delta_z),
            )
        else:
            lower_point = (
                float(lower[0]),
                float(lower[1]),
                fallback_lower_z,
            )
        points.append(
            Gf.Vec3f(*lower_point)
        )
    return points


def _evaluated_local_transform(stage: Any, path: str, Usd: Any, UsdGeom: Any) -> Any:
    """Return a prim's evaluated local transform using USD row-vector order."""

    prim = _valid_prim(stage, path)
    if prim is None:
        return None
    time_code = Usd.TimeCode.Default()
    world = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(time_code)
    parent = prim.GetParent()
    if parent is None or not parent.IsValid():
        return world
    parent_world = UsdGeom.Xformable(parent).ComputeLocalToWorldTransform(time_code)
    return world * parent_world.GetInverse()


def _evaluated_world_transform(stage: Any, path: str, Usd: Any, UsdGeom: Any) -> Any:
    """Return a prim's evaluated local-to-world transform."""

    prim = _valid_prim(stage, path)
    if prim is None:
        return None
    return UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
        Usd.TimeCode.Default()
    )


def _try_initialize_articulation_controller(stage: Any) -> bool:
    """Initialize Isaac's runtime articulation controller after Play."""

    key = id(stage)
    if key in _ARTICULATION_CONTROLLERS:
        return True
    try:
        import omni.timeline  # type: ignore

        if not omni.timeline.get_timeline_interface().is_playing():
            return False
        import numpy as np  # type: ignore
        from isaacsim.core.api.controllers.articulation_controller import (
            ArticulationController,
        )
        from isaacsim.core.prims import Articulation
        from isaacsim.core.utils.types import ArticulationAction

        view = Articulation(
            prim_paths_expr=ARTICULATION_ROOT_PATH,
            name="rtg_primary_articulation_view",
        )
        controller = ArticulationController()
        controller.initialize(view)

        names = getattr(view, "dof_names", None)
        if callable(names):
            names = names()
        names = list(names or [])
        # The gantry travel joint is the root body's joint to the world.  It
        # drives the articulation root itself and therefore is not exposed as
        # an articulation DOF by PhysX.  Only the two child joints appear in
        # ``Articulation.dof_names`` and should be sent through the runtime
        # articulation controller.
        wanted = ("TrolleyTravelJoint", "HoistVerticalJoint")
        if names:
            normalized = {str(name).split("/")[-1]: index for index, name in enumerate(names)}
            indices = [normalized.get(name, -1) for name in wanted]
            if any(index < 0 for index in indices):
                raise RuntimeError(f"RTG DOF names are {names}, expected {wanted}")
        else:
            # The authored RTG articulation has these two child DOFs in
            # traversal order when a legacy view does not expose names.
            indices = [0, 1]

        _ARTICULATION_VIEWS[key] = view
        _ARTICULATION_CONTROLLERS[key] = (controller, ArticulationAction, np)
        _ARTICULATION_DOF_INDICES[key] = np.asarray(indices, dtype=np.int32)
        print(
            "[RTG Physics] Articulation Controller initialized",
            {"dof_names": names, "indices": indices},
        )
        return True
    except Exception as error:
        if key not in _ARTICULATION_ERRORS:
            _ARTICULATION_ERRORS.add(key)
            print(f"[RTG Physics] Articulation Controller unavailable: {error}")
        return False


def _apply_articulation_targets(stage: Any) -> bool:
    """Apply the latest USD-mapped targets through Isaac's runtime API."""

    key = id(stage)
    # The authored RTG hierarchy cannot be discovered as a PhysX articulation
    # in the stable visual-control mode.  Its targets are consumed by the
    # visual follower only; attempting to construct an Articulation view here
    # produces repeated "pattern list did not match any articulations" errors.
    root = _valid_prim(stage, ARTICULATION_ROOT_PATH)
    mode = (
        _get_attribute_value(root.GetAttribute(PHYSICS_MODE_ATTRIBUTE))
        if root
        else None
    )
    if mode == VISUAL_CONTROL_MODE:
        return False
    target = _ARTICULATION_TARGETS.get(key)
    if target is None or not _try_initialize_articulation_controller(stage):
        return False
    controller_data = _ARTICULATION_CONTROLLERS.get(key)
    indices = _ARTICULATION_DOF_INDICES.get(key)
    if controller_data is None or indices is None:
        return False
    signature = tuple(float(value) for value in target)
    if _ARTICULATION_APPLIED_TARGETS.get(key) == signature:
        return True
    controller, action_type, np = controller_data
    # ``target`` is kept in the project order (gantry, trolley, hoist), while
    # the articulation controller only receives the two child DOFs (trolley,
    # hoist).  Gantry is already driven by its USD root joint drive above.
    # The scene's strongest sublayer authors the hoist joint in the same
    # absolute body-coordinate system returned by ``get_joint_positions``.
    # ``set_rtg_physics_targets`` normalizes into that coordinate system before
    # storing ``target``; send the same values to the runtime controller so
    # the USD drive and ArticulationAction cannot fight each other.
    articulation_targets = np.asarray(
        [target[1], target[2]],
        dtype=np.float32,
    )
    action = action_type(
        joint_positions=articulation_targets,
        joint_indices=indices,
    )
    controller.apply_action(action)
    _ARTICULATION_APPLIED_TARGETS[key] = signature
    print(
        "[RTG Physics] Articulation action",
        {
            "usd_targets": [float(target[1]), float(target[2])],
            "runtime_targets": [float(value) for value in articulation_targets],
        },
    )
    return True


def _set_visual_controller_transforms(
    stage: Any,
    targets: list[float],
    Gf: Any,
    Usd: Any,
) -> dict[str, list[float]]:
    """Write all three visible RTG controller translations in the session layer.

    After ``move_primary_rtg_to_bay`` the public gantry target remains a yard
    world coordinate, while the animated controller must receive the local
    remainder after subtracting the static root placement.
    """

    paths_and_axes = (
        (GANTRY_PATH, 1),
        (TROLLEY_PATH, 0),
        (HOIST_PATH, 2),
    )
    applied: dict[str, list[float]] = {}
    with _session_edit_context(stage):
        for path, axis in paths_and_axes:
            prim = _valid_prim(stage, path)
            if prim is None:
                continue
            attr = prim.GetAttribute("xformOp:translate")
            if not attr or not attr.IsValid():
                continue
            value = attr.Get(Usd.TimeCode.Default())
            if value is None:
                value = Gf.Vec3d(0.0, 0.0, 0.0)
            vector = [float(value[0]), float(value[1]), float(value[2])]
            target_value = float(
                targets[0 if path == GANTRY_PATH else 1 if path == TROLLEY_PATH else 2]
            )
            if path == GANTRY_PATH:
                root = _valid_prim(stage, PRIMARY_PATH)
                placement_y = 0.0
                if root is not None:
                    placement_name = (
                        f"xformOp:translate:{PRIMARY_RTG_PLACEMENT_OP_SUFFIX}"
                    )
                    placement_attr = root.GetAttribute(placement_name)
                    placement = _get_attribute_value(
                        placement_attr,
                        time_code=Usd.TimeCode.Default(),
                    )
                    if placement is not None:
                        placement_y = float(placement[1])
                target_value -= placement_y
            vector[axis] = target_value
            new_value = Gf.Vec3d(*vector)
            # The Blender payload has samples on these same attributes.  Keep
            # every sample equal to the command so Play cannot restore the
            # authored animation on the next frame.
            _set_animation_attribute_constant(attr, new_value)
            applied[path] = [float(item) for item in new_value]
    return applied


def update_rtg_physics_visuals(
    stage: Optional[Any] = None,
    *,
    force: bool = False,
) -> bool:
    """Make the visible rope follow the evaluated PhysX hoist pose.

    The rope curve is a visual BasisCurves prim, not a rigid body.  Its
    authored timeline therefore cannot follow a prismatic joint by itself.
    This function is intentionally lightweight and only updates 32 endpoint
    points; it is safe to call from Kit's per-frame update stream.
    """

    resolved = _resolve_stage(stage)
    key = id(resolved)
    now = time.perf_counter()
    last_tick = _PHYSICS_SYNC_LAST_TICKS.get(key)
    if (
        not force
        and last_tick is not None
        and now - last_tick < 1.0 / PHYSICS_VISUAL_SYNC_HZ
    ):
        return True
    _PHYSICS_SYNC_LAST_TICKS[key] = now
    _apply_articulation_targets(resolved)
    Gf, _Sdf, Usd, UsdGeom, _UsdPhysics = _runtime_modules()
    commanded = _ARTICULATION_TARGETS.get(key)
    if commanded is not None and len(commanded) >= 3:
        _set_visual_controller_transforms(resolved, commanded, Gf, Usd)
    current = _evaluated_local_transform(resolved, HOIST_PATH, Usd, UsdGeom)
    if current is None:
        return False
    baseline = _PHYSICS_SYNC_BASELINES.get(key)
    if baseline is None:
        baseline = current
        _PHYSICS_SYNC_BASELINES[key] = current
    current_z = float(current.ExtractTranslation()[2])
    delta = Gf.Matrix4d().SetTranslate(
        Gf.Vec3d(0.0, 0.0, current_z - HOIST_AUTHORED_SOURCE_Z)
    )

    # Some imported RTG assets expose a valid PhysX articulation but do not
    # propagate the simulated hoist body transform to the visible controller
    # prim.  Keep that visible prim and its rope endpoints on the same
    # commanded absolute Z target.  If PhysX does propagate the pose, this is
    # the same pose and therefore does not introduce a second visual motion.
    if commanded is not None:
        desired_hoist_z = float(commanded[2])
        hoist = _valid_prim(resolved, HOIST_PATH)
        if hoist is not None:
            xformable = UsdGeom.Xformable(hoist)
            for op in xformable.GetOrderedXformOps():
                if op.GetOpType() != UsdGeom.XformOp.TypeTranslate:
                    continue
                value = op.Get()
                if value is None:
                    continue
                if abs(float(value[2]) - desired_hoist_z) > 1e-5:
                    _set_animation_attribute_constant(
                        op.GetAttr(),
                        Gf.Vec3d(
                            float(value[0]), float(value[1]), desired_hoist_z
                        ),
                    )
                break
        delta = Gf.Matrix4d().SetTranslate(
            Gf.Vec3d(
                0.0,
                0.0,
                desired_hoist_z - HOIST_AUTHORED_SOURCE_Z,
            )
        )
    rope = _valid_prim(resolved, ROPE_SYSTEM_PATH)
    if rope is None:
        return False
    points = rope.GetAttribute("points")
    if not points:
        return False
    translation = current.ExtractTranslation()
    if commanded is not None:
        translation = Gf.Vec3d(
            float(translation[0]),
            float(translation[1]),
            float(commanded[2]),
        )
    pose = (
        round(float(translation[0]), 5),
        round(float(translation[1]), 5),
        round(float(translation[2]), 5),
    )
    if _PHYSICS_SYNC_LAST_POSES.get(key) == pose:
        return True
    with _session_edit_context(resolved):
        profile = _ROPE_ATTACHMENT_PROFILES.get(key)
        if profile is not None:
            rope_values = _rope_points_from_profile(
                Gf,
                profile,
                float(delta.ExtractTranslation()[2]),
            )
        else:
            rope_values = _rope_points(Gf, delta)
        points.Set(rope_values)
        imageable = UsdGeom.Imageable(rope)
        visibility = imageable.GetVisibilityAttr()
        if visibility and visibility.IsValid():
            _set_animation_attribute_constant(
                visibility, UsdGeom.Tokens.inherited
            )
        widths = rope.GetAttribute("widths")
        if widths and widths.IsValid():
            _set_animation_attribute_constant(
                widths, [float(VISIBLE_ROPE_WIDTH_M)]
            )
    _PHYSICS_SYNC_LAST_POSES[key] = pose
    return True


def start_rtg_physics_visual_sync(stage: Optional[Any] = None) -> dict[str, Any]:
    """Subscribe the rope visual to Kit updates after physics activation."""

    resolved = _resolve_stage(stage)
    key = id(resolved)
    if key in _PHYSICS_SYNC_SUBSCRIPTIONS:
        return {"started": True, "already_running": True}
    import omni.kit.app  # type: ignore

    _Gf, _Sdf, Usd, UsdGeom, _UsdPhysics = _runtime_modules()
    if key not in _ROPE_ATTACHMENT_PROFILES:
        profile = _capture_rope_attachment_profile(
            resolved, _Gf, Usd, UsdGeom
        )
        if profile is not None:
            _ROPE_ATTACHMENT_PROFILES[key] = profile
    baseline = _evaluated_local_transform(resolved, HOIST_PATH, Usd, UsdGeom)
    if baseline is not None:
        _PHYSICS_SYNC_BASELINES[key] = baseline
        translation = baseline.ExtractTranslation()
        _PHYSICS_SYNC_BASE_LOCAL_TRANSLATIONS[key] = (
            float(translation[0]),
            float(translation[1]),
            float(translation[2]),
        )

    def on_update(_event: Any) -> None:
        try:
            update_rtg_physics_visuals(resolved)
        except Exception as error:
            # Do not kill Kit's update stream because a stage is being closed.
            print(f"[RTG Physics] visual sync skipped: {error}")

    app = omni.kit.app.get_app()
    post_update = getattr(app, "get_post_update_event_stream", None)
    stream = post_update() if callable(post_update) else app.get_update_event_stream()
    subscription = stream.create_subscription_to_pop(
        on_update,
        name="RTG physics post-physics visual sync",
    )
    _PHYSICS_SYNC_SUBSCRIPTIONS[key] = subscription
    return {"started": True, "already_running": False}


def stop_rtg_physics_visual_sync(stage: Optional[Any] = None) -> dict[str, Any]:
    """Stop the Kit update subscription for a stage."""

    resolved = _resolve_stage(stage)
    key = id(resolved)
    subscription = _PHYSICS_SYNC_SUBSCRIPTIONS.pop(key, None)
    _PHYSICS_SYNC_BASELINES.pop(key, None)
    _PHYSICS_SYNC_BASE_LOCAL_TRANSLATIONS.pop(key, None)
    _PHYSICS_SYNC_LAST_TICKS.pop(key, None)
    _PHYSICS_SYNC_LAST_POSES.pop(key, None)
    _ROPE_ATTACHMENT_PROFILES.pop(key, None)
    if subscription is not None:
        unsubscribe = getattr(subscription, "unsubscribe", None)
        if unsubscribe:
            unsubscribe()
    return {"stopped": subscription is not None}


def diagnose_rtg_physics(stage: Optional[Any] = None) -> dict[str, Any]:
    """Print a compact runtime report for a non-moving RTG articulation."""

    resolved = _resolve_stage(stage)
    Gf, Sdf, Usd, UsdGeom, UsdPhysics = _runtime_modules()
    del Gf, UsdGeom
    report: dict[str, Any] = {
        "timeline_playing": None,
        "stage_validation": validate_rtg_physics_stage(resolved).as_dict(),
        "bodies": {},
        "joints": {},
        "controller": {"initialized": False},
    }
    try:
        import omni.timeline  # type: ignore

        report["timeline_playing"] = bool(
            omni.timeline.get_timeline_interface().is_playing()
        )
    except Exception as error:
        report["timeline_error"] = str(error)

    for path, _mass, role in BODY_SPECS:
        prim = _valid_prim(resolved, path)
        if prim is None:
            continue
        rigid = prim.GetAttribute("physics:rigidBodyEnabled")
        mass = prim.GetAttribute("physics:mass")
        report["bodies"][role] = {
            "path": path,
            "valid": True,
            "rigid_body_enabled": _get_attribute_value(rigid),
            "mass": (
                float(_get_attribute_value(mass))
                if _get_attribute_value(mass) is not None
                else None
            ),
        }

    for joint_path, _body, _axis in JOINT_SPECS:
        joint = _valid_prim(resolved, joint_path)
        if joint is None:
            continue
        drive = UsdPhysics.DriveAPI.Get(joint, UsdPhysics.Tokens.linear)

        def relationship_targets(name: str) -> list[str]:
            relationship = joint.GetRelationship(name)
            if not relationship:
                return []
            return [str(target) for target in relationship.GetTargets()]

        item: dict[str, Any] = {
            "valid": True,
            "joint_enabled": bool(
                _get_attribute_value(
                    joint.GetAttribute("physics:jointEnabled"),
                    False,
                )
            ),
            "body0": relationship_targets("physics:body0"),
            "body1": relationship_targets("physics:body1"),
            "local_pos0": str(
                _get_attribute_value(joint.GetAttribute("physics:localPos0"), "")
            ),
            "local_pos1": str(
                _get_attribute_value(joint.GetAttribute("physics:localPos1"), "")
            ),
        }
        if drive:
            target = _get_attribute_value(drive.GetTargetPositionAttr())
            item["drive_target"] = float(target) if target is not None else None
            stiffness = _get_attribute_value(drive.GetStiffnessAttr())
            damping = _get_attribute_value(drive.GetDampingAttr())
            item["drive_stiffness"] = (
                float(stiffness) if stiffness is not None else None
            )
            item["drive_damping"] = (
                float(damping) if damping is not None else None
            )
        report["joints"][joint_path] = item

    key = id(resolved)
    if _try_initialize_articulation_controller(resolved):
        controller, _action, _np = _ARTICULATION_CONTROLLERS[key]
        view = _ARTICULATION_VIEWS[key]
        names = getattr(view, "dof_names", None)
        if callable(names):
            names = names()
        # Isaac Sim 6 may return joint positions as a batched array, e.g.
        # shape (1, 2), while older versions return a flat vector. Normalize
        # both forms before converting values to Python floats.
        joint_positions = _np.asarray(view.get_joint_positions(), dtype=float).reshape(-1)
        report["controller"] = {
            "initialized": True,
            "dof_names": list(names or []),
            "indices": list(_ARTICULATION_DOF_INDICES[key]),
            "joint_positions": [float(value) for value in joint_positions],
            "controller_type": type(controller).__name__,
        }
    else:
        report["controller"]["error"] = "See the first Articulation Controller unavailable log"

    print("[RTG Physics] Diagnostic report", report)
    return report


def _disable_collision_proxies(stage: Any, Sdf: Any) -> None:
    """Leave first-pass proxies present but inert when returning to demo mode."""

    for name, parent, _center, _size in COLLISION_PROXY_SPECS:
        prim = _valid_prim(stage, f"{parent}/{name}")
        if prim is not None:
            _set_attribute(
                prim,
                "physics:collisionEnabled",
                False,
                Sdf.ValueTypeNames.Bool,
            )


def _read_mapping(stage: Any, joint_path: str) -> tuple[tuple[float, float], tuple[float, float]]:
    """Read the authored engineering-to-USD mapping from a joint."""

    prim = _valid_prim(stage, joint_path)
    if prim is None:
        raise RuntimeError(f"Missing joint: {joint_path}")

    if joint_path == HOIST_JOINT_PATH:
        # Use the current live-yard calibration even when a stale generated
        # rtg_simready layer is still composed into the open stage.
        return HOIST_ENGINEERING_LIMITS, HOIST_USD_POSITION_LIMITS
    if joint_path == TROLLEY_JOINT_PATH:
        # Use the calibrated full trolley span from rtg_live_control.  The
        # authored rtg_simready layer may still contain the older 0..-2.25
        # mapping, so do not read that stale mapping from the joint.
        return TROLLEY_ENGINEERING_LIMITS, TROLLEY_USD_POSITION_LIMITS

    def value(name: str, fallback: float) -> float:
        attr = prim.GetAttribute(name)
        result = _get_attribute_value(attr)
        return float(result if result is not None else fallback)

    return (
        (
            value("rtg:engineeringLowerLimit", 0.0),
            value("rtg:engineeringUpperLimit", 1.0),
        ),
        (
            value("rtg:usdPositionAtEngineeringLower", 0.0),
            value("rtg:usdPositionAtEngineeringUpper", 1.0),
        ),
    )


def _joint_target_offset(stage: Any, joint_path: str) -> float:
    """Return the authored absolute-coordinate offset for a joint target."""

    # The calibrated hoist mapping above already returns the absolute local Z
    # coordinate used by the visible controller and smart_port animation.
    # Adding physics:localPos0 again shifts every command upward by the parked
    # pose (about 4.33 m), which both hides most of the requested travel and
    # disconnects the rope endpoint from the visible spreader.
    del stage, joint_path
    return 0.0


def activate_rtg_physics(
    stage: Optional[Any] = None,
    *,
    create_collision_proxies: bool = False,
) -> dict[str, Any]:
    """Enable the RTG articulation in the current session layer.

    The function is explicit and reversible so the normal visual demo keeps
    working.  It does not start the simulation; press Play in Isaac Sim after
    activation, then send drive targets with ``set_rtg_physics_targets``.
    """

    resolved = _resolve_stage(stage)
    validation = validate_rtg_physics_stage(resolved)
    if not validation.valid:
        raise RuntimeError(f"RTG physics stage is incomplete: {validation.as_dict()}")

    Gf, Sdf, _Usd, UsdGeom, UsdPhysics = _runtime_modules()
    created_proxies: list[str] = []
    with _session_edit_context(resolved):
        _ensure_physics_scene(resolved, UsdPhysics, Gf)
        _ensure_body_physics(resolved, UsdPhysics, Sdf)
        _move_articulation_root(resolved, UsdPhysics)
        frozen_animation = _freeze_authored_animation(
            resolved, _Usd, UsdGeom, Sdf
        )
        aligned_anchors = _align_joint_anchors(
            resolved, Gf, Sdf, _Usd, UsdGeom
        )
        # The live-yard hoist mapping is authored in the visible controller's
        # absolute local-Z frame.  Apply the complete 18 m calibrated range in
        # the session layer before enabling the drive.
        hoist_joint = _valid_prim(resolved, HOIST_JOINT_PATH)
        if hoist_joint is not None:
            _engineering_limits, usd_limits = _read_mapping(
                resolved, HOIST_JOINT_PATH
            )
            hoist_offset = _joint_target_offset(resolved, HOIST_JOINT_PATH)
            _set_attribute(
                hoist_joint,
                "physics:lowerLimit",
                hoist_offset + usd_limits[0],
                Sdf.ValueTypeNames.Float,
            )
            _set_attribute(
                hoist_joint,
                "physics:upperLimit",
                hoist_offset + usd_limits[1],
                Sdf.ValueTypeNames.Float,
            )
        # A previous session may already contain first-pass proxies. Make the
        # safe joint-only default idempotent before optionally recreating and
        # enabling them below.
        _disable_collision_proxies(resolved, Sdf)

        if create_collision_proxies:
            for name, parent, center, size in COLLISION_PROXY_SPECS:
                created_proxies.append(
                    _ensure_collision_proxy(
                        resolved, UsdGeom, UsdPhysics, Gf, Sdf,
                        name, parent, center, size,
                    )
                )

        collision_status = (
            COARSE_COLLISION_STATUS
            if create_collision_proxies
            else JOINT_ONLY_COLLISION_STATUS
        )
        for body_path, _mass, _role in BODY_SPECS:
            body = _valid_prim(resolved, body_path)
            if body is not None:
                _set_attribute(
                    body,
                    COLLISION_STATUS_ATTRIBUTE,
                    collision_status,
                    Sdf.ValueTypeNames.Token,
                )

        for joint_path, _body, _axis in JOINT_SPECS:
            _enable_joint(resolved, joint_path, UsdPhysics, Sdf)

        root = _valid_prim(resolved, ARTICULATION_ROOT_PATH)
        _set_attribute(root, PHYSICS_MODE_ATTRIBUTE, "isaac_physics", Sdf.ValueTypeNames.Token)
        _set_attribute(
            root,
            COLLISION_STATUS_ATTRIBUTE,
            collision_status,
            Sdf.ValueTypeNames.Token,
        )

    report = {
        "build_id": PHYSICS_BUILD_ID,
        "mode": "isaac_physics",
        "articulation_root": ARTICULATION_ROOT_PATH,
        "bodies": [path for path, _mass, _role in BODY_SPECS],
        "joints": [path for path, _body, _axis in JOINT_SPECS],
        "collision_proxies": created_proxies,
        "collision_mode": collision_status,
        "authored_animation_suppressed": frozen_animation,
        "static_animation_lock_count": sum(
            1 for item in frozen_animation
            if not item.startswith(f"{PRIMARY_PATH}:")
            and not item.startswith(f"{PRIMARY_PATH}/")
        ),
        "joint_anchors_aligned": aligned_anchors,
        "simulation_started": False,
    }
    try:
        report["visual_sync"] = start_rtg_physics_visual_sync(resolved)
    except Exception as error:
        # Physics can still be activated in a non-standard Kit shell.  The
        # explicit update function remains available for that environment.
        report["visual_sync"] = {"started": False, "error": str(error)}
        print(f"[RTG Physics] visual sync unavailable: {error}")
    print("[RTG Physics] Activated", report)
    return report


def activate_rtg_visual_control(stage: Optional[Any] = None) -> dict[str, Any]:
    """Enable stable code-driven RTG motion for the authored asset.

    The imported RTG has animated controller parents and non-uniform scale.
    PhysX therefore converts its rigid bodies to kinematic bodies and cannot
    drive the authored hierarchy as a dynamic articulation.  This mode keeps
    the same engineering-to-USD mapping and rope follower, but leaves the
    visible controller under one deterministic session-layer writer.
    """

    resolved = _resolve_stage(stage)
    validation = validate_rtg_physics_stage(resolved)
    if not validation.valid:
        raise RuntimeError(f"RTG visual stage is incomplete: {validation.as_dict()}")
    _Gf, Sdf, Usd, UsdGeom, _UsdPhysics = _runtime_modules()
    with _session_edit_context(resolved):
        frozen_animation = _freeze_authored_animation(
            resolved, Usd, UsdGeom, Sdf
        )
        root = _valid_prim(resolved, ARTICULATION_ROOT_PATH)
        if root is not None:
            _set_attribute(
                root,
                PHYSICS_MODE_ATTRIBUTE,
                VISUAL_CONTROL_MODE,
                Sdf.ValueTypeNames.Token,
            )
            _set_attribute(
                root,
                "rtg:authoredAnimationSuppressed",
                True,
                Sdf.ValueTypeNames.Bool,
            )
    profile = _capture_rope_attachment_profile(resolved, _Gf, Usd, UsdGeom)
    if profile is not None:
        _ROPE_ATTACHMENT_PROFILES[id(resolved)] = profile
    report = {
        "build_id": PHYSICS_BUILD_ID,
        "mode": VISUAL_CONTROL_MODE,
        "articulation_root": ARTICULATION_ROOT_PATH,
        "authored_animation_suppressed": frozen_animation,
        "physics_backend": "disabled_for_authored_animated_hierarchy",
        "rope_attachment_profile": profile,
    }
    try:
        report["visual_sync"] = start_rtg_physics_visual_sync(resolved)
    except Exception as error:
        report["visual_sync"] = {"started": False, "error": str(error)}
    print("[RTG Physics] Visual control activated", report)
    return report


def deactivate_rtg_physics(stage: Optional[Any] = None) -> dict[str, Any]:
    """Return the current stage to the non-physics Blender demo mode."""

    resolved = _resolve_stage(stage)
    Gf, Sdf, _Usd, _UsdGeom, UsdPhysics = _runtime_modules()
    del Gf
    stop_rtg_physics_visual_sync(resolved)
    key = id(resolved)
    _ARTICULATION_CONTROLLERS.pop(key, None)
    _ARTICULATION_VIEWS.pop(key, None)
    _ARTICULATION_DOF_INDICES.pop(key, None)
    _ARTICULATION_TARGETS.pop(key, None)
    _ARTICULATION_APPLIED_TARGETS.pop(key, None)
    _ENGINEERING_TARGETS.pop(key, None)
    stop_rtg_animation(resolved)
    _ARTICULATION_ERRORS.discard(key)
    with _session_edit_context(resolved):
        for path, _mass, _role in BODY_SPECS:
            prim = _valid_prim(resolved, path)
            if prim is not None:
                UsdPhysics.RigidBodyAPI.Apply(prim).CreateRigidBodyEnabledAttr(False).Set(False)
        for joint_path, _body, _axis in JOINT_SPECS:
            prim = _valid_prim(resolved, joint_path)
            if prim is not None:
                _set_attribute(prim, "physics:jointEnabled", False, Sdf.ValueTypeNames.Bool)
        _disable_collision_proxies(resolved, Sdf)
        root = _valid_prim(resolved, ARTICULATION_ROOT_PATH)
        if root is not None:
            _set_attribute(root, PHYSICS_MODE_ATTRIBUTE, "blender_authored_animation", Sdf.ValueTypeNames.Token)
            _set_attribute(root, "rtg:authoredAnimationSuppressed", False, Sdf.ValueTypeNames.Bool)

    report = {"mode": "blender_authored_animation", "simulation_started": False}
    print("[RTG Physics] Deactivated", report)
    return report


def set_rtg_physics_targets(
    gantry_m: float,
    trolley_m: float,
    hoist_m: float,
    stage: Optional[Any] = None,
) -> dict[str, float]:
    """Send position targets in engineering metres to the three linear drives."""

    resolved = _resolve_stage(stage)
    root = _valid_prim(resolved, ARTICULATION_ROOT_PATH)
    mode = (
        _get_attribute_value(root.GetAttribute(PHYSICS_MODE_ATTRIBUTE))
        if root
        else None
    )
    if mode == VISUAL_CONTROL_MODE:
        return set_rtg_visual_targets(
            gantry_m=gantry_m,
            trolley_m=trolley_m,
            hoist_m=hoist_m,
            stage=resolved,
        )
    auto_activated = False
    if mode != "isaac_physics":
        # Make this public entry point safe to call after reopening the scene.
        # It prevents a common failure mode where the target is written while
        # the timeline is still using the Blender-authored animation layer.
        activate_rtg_physics(resolved)
        auto_activated = True
    _Gf, _Sdf, _Usd, _UsdGeom, UsdPhysics = _runtime_modules()
    values = {
        GANTRY_JOINT_PATH: gantry_m,
        TROLLEY_JOINT_PATH: trolley_m,
        HOIST_JOINT_PATH: hoist_m,
    }
    result: dict[str, float] = {}
    with _session_edit_context(resolved):
        for joint_path, engineering_value in values.items():
            engineering_limits, usd_limits = _read_mapping(resolved, joint_path)
            usd_value = engineering_to_usd(
                engineering_value, engineering_limits, usd_limits
            )
            usd_value += _joint_target_offset(resolved, joint_path)
            joint = _valid_prim(resolved, joint_path)
            if joint is None:
                raise RuntimeError(f"Missing joint: {joint_path}")
            drive = UsdPhysics.DriveAPI.Get(joint, UsdPhysics.Tokens.linear)
            if not drive:
                raise RuntimeError(f"Missing linear drive: {joint_path}")
            _set_animation_attribute_constant(
                drive.GetTargetPositionAttr(), usd_value
            )
            _set_animation_attribute_constant(drive.GetTargetVelocityAttr(), 0.0)
            result[joint_path] = usd_value
    _ARTICULATION_TARGETS[id(resolved)] = [
        result.get(GANTRY_JOINT_PATH, 0.0),
        result.get(TROLLEY_JOINT_PATH, 0.0),
        result.get(HOIST_JOINT_PATH, 0.0),
    ]
    _remember_engineering_targets(resolved, gantry_m, trolley_m, hoist_m)
    # If Play is already active, the controller can receive the command in
    # the same call. Otherwise the Kit update callback applies it once the
    # articulation has been initialized by the running physics scene.
    controller_applied = _apply_articulation_targets(resolved)
    # Also author the visible transform now, rather than waiting for the next
    # Kit update. This makes a Script Editor command immediately observable.
    visual_applied = False
    try:
        visual_applied = update_rtg_physics_visuals(resolved, force=True)
    except Exception as error:
        print(f"[RTG Physics] immediate visual update failed: {error}")
    print(
        "[RTG Physics] Targets",
        {
            "build_id": PHYSICS_BUILD_ID,
            "values": result,
            "auto_activated": auto_activated,
            "controller_applied": controller_applied,
            "visual_applied": visual_applied,
            "note": "controller will apply on the next Kit update after Play" if not controller_applied else "applied",
        },
    )
    return result


def set_rtg_visual_targets(
    gantry_m: float,
    trolley_m: float,
    hoist_m: float,
    stage: Optional[Any] = None,
) -> dict[str, float]:
    """Move the authored RTG hierarchy through the stable visual controller."""

    resolved = _resolve_stage(stage)
    root = _valid_prim(resolved, ARTICULATION_ROOT_PATH)
    mode = (
        _get_attribute_value(root.GetAttribute(PHYSICS_MODE_ATTRIBUTE))
        if root
        else None
    )
    if mode != VISUAL_CONTROL_MODE:
        activate_rtg_visual_control(resolved)
    values = {
        GANTRY_JOINT_PATH: gantry_m,
        TROLLEY_JOINT_PATH: trolley_m,
        HOIST_JOINT_PATH: hoist_m,
    }
    result: dict[str, float] = {}
    for joint_path, engineering_value in values.items():
        engineering_limits, usd_limits = _read_mapping(resolved, joint_path)
        result[joint_path] = engineering_to_usd(
            engineering_value, engineering_limits, usd_limits
        ) + _joint_target_offset(resolved, joint_path)
    _ARTICULATION_TARGETS[id(resolved)] = [
        result.get(GANTRY_JOINT_PATH, 0.0),
        result.get(TROLLEY_JOINT_PATH, 0.0),
        result.get(HOIST_JOINT_PATH, 0.0),
    ]
    _remember_engineering_targets(resolved, gantry_m, trolley_m, hoist_m)
    visual_applied = update_rtg_physics_visuals(resolved, force=True)
    print(
        "[RTG Physics] Visual targets",
        {
            "build_id": PHYSICS_BUILD_ID,
            "values": result,
            "engineering_m": _ENGINEERING_TARGETS[id(resolved)],
            "visual_applied": visual_applied,
            "note": "applied",
        },
    )
    return result


def _remember_engineering_targets(
    stage: Any,
    gantry_m: float,
    trolley_m: float,
    hoist_m: float,
) -> None:
    _ENGINEERING_TARGETS[id(stage)] = {
        "gantry_m": float(gantry_m),
        "trolley_m": float(trolley_m),
        "hoist_m": float(hoist_m),
    }


def _current_engineering_targets(stage: Any) -> dict[str, float]:
    return dict(
        _ENGINEERING_TARGETS.get(
            id(stage),
            {
                "gantry_m": DEFAULT_RTG_GANTRY_M,
                "trolley_m": DEFAULT_RTG_TROLLEY_M,
                "hoist_m": DEFAULT_RTG_HOIST_M,
            },
        )
    )


def move_to(
    gantry_m: Optional[float] = None,
    trolley_m: Optional[float] = None,
    hoist_m: Optional[float] = None,
    stage: Optional[Any] = None,
) -> dict[str, float]:
    """Move the stable visual RTG controller, preserving omitted axes."""

    resolved = _resolve_stage(stage)
    targets = _current_engineering_targets(resolved)
    if gantry_m is not None:
        targets["gantry_m"] = float(gantry_m)
    if trolley_m is not None:
        targets["trolley_m"] = float(trolley_m)
    if hoist_m is not None:
        targets["hoist_m"] = float(hoist_m)
    return set_rtg_visual_targets(stage=resolved, **targets)


def move_gantry(gantry_m: float, stage: Optional[Any] = None) -> dict[str, float]:
    """Move only the RTG gantry in engineering metres."""

    return move_to(gantry_m=gantry_m, stage=stage)


def move_trolley(trolley_m: float, stage: Optional[Any] = None) -> dict[str, float]:
    """Move only the trolley while preserving the current gantry world Y."""

    resolved = _resolve_stage(stage)
    targets = _current_engineering_targets(resolved)
    gantry_m = float(targets.get("gantry_m", DEFAULT_RTG_GANTRY_M))
    try:
        _Gf, _Sdf, Usd, UsdGeom, _UsdPhysics = _runtime_modules()
        gantry = _valid_prim(resolved, GANTRY_PATH)
        if gantry is not None:
            matrix = UsdGeom.Xformable(gantry).ComputeLocalToWorldTransform(
                Usd.TimeCode.Default()
            )
            gantry_m = float(matrix.ExtractTranslation()[1])
    except Exception:
        # Keep the cached target as a fallback for partially loaded stages.
        pass
    return set_rtg_visual_targets(
        gantry_m=gantry_m,
        trolley_m=float(trolley_m),
        hoist_m=float(targets.get("hoist_m", DEFAULT_RTG_HOIST_M)),
        stage=resolved,
    )


def move_hoist(hoist_m: float, stage: Optional[Any] = None) -> dict[str, float]:
    """Move only the hoist/spreader in engineering metres."""

    return move_to(hoist_m=hoist_m, stage=stage)


def stop_rtg_animation(stage: Optional[Any] = None) -> dict[str, Any]:
    """Cancel the active smooth RTG animation for a stage, if any."""

    resolved = _resolve_stage(stage)
    key = id(resolved)
    subscription = _RTG_ANIMATION_SUBSCRIPTIONS.pop(key, None)
    state = _RTG_ANIMATION_STATES.pop(key, None)
    if subscription is not None:
        unsubscribe = getattr(subscription, "unsubscribe", None)
        if unsubscribe:
            unsubscribe()
    return {
        "stopped": subscription is not None or state is not None,
        "last_state": state,
    }


def animate_to(
    gantry_m: Optional[float] = None,
    trolley_m: Optional[float] = None,
    hoist_m: Optional[float] = None,
    stage: Optional[Any] = None,
    *,
    duration_s: float = 3.0,
) -> dict[str, Any]:
    """Smoothly interpolate RTG engineering targets over Kit update frames."""

    if duration_s <= 0.0:
        return {
            "started": False,
            "immediate": True,
            "targets": move_to(
                gantry_m=gantry_m,
                trolley_m=trolley_m,
                hoist_m=hoist_m,
                stage=stage,
            ),
        }

    resolved = _resolve_stage(stage)
    activate_rtg_visual_control(resolved)
    stop_rtg_animation(resolved)

    start = _current_engineering_targets(resolved)
    end = dict(start)
    if gantry_m is not None:
        end["gantry_m"] = float(gantry_m)
    if trolley_m is not None:
        end["trolley_m"] = float(trolley_m)
    if hoist_m is not None:
        end["hoist_m"] = float(hoist_m)

    key = id(resolved)
    state: dict[str, Any] = {
        "start": start,
        "end": end,
        "duration_s": float(duration_s),
        "start_time": time.perf_counter(),
        "done": False,
    }
    _RTG_ANIMATION_STATES[key] = state

    import omni.kit.app  # type: ignore

    def smoothstep(value: float) -> float:
        value = max(0.0, min(1.0, value))
        return value * value * (3.0 - 2.0 * value)

    def on_update(_event: Any) -> None:
        try:
            elapsed = time.perf_counter() - float(state["start_time"])
            alpha = smoothstep(elapsed / float(state["duration_s"]))
            current = {
                axis: float(state["start"][axis])
                + (float(state["end"][axis]) - float(state["start"][axis])) * alpha
                for axis in ("gantry_m", "trolley_m", "hoist_m")
            }
            set_rtg_visual_targets(stage=resolved, **current)
            if elapsed >= float(state["duration_s"]):
                set_rtg_visual_targets(stage=resolved, **state["end"])
                state["done"] = True
                stop_rtg_animation(resolved)
                print("[RTG Physics] Animation complete", state)
        except Exception as error:
            print(f"[RTG Physics] animation stopped after error: {error}")
            stop_rtg_animation(resolved)

    app = omni.kit.app.get_app()
    post_update = getattr(app, "get_post_update_event_stream", None)
    stream = post_update() if callable(post_update) else app.get_update_event_stream()
    subscription = stream.create_subscription_to_pop(
        on_update,
        name="RTG smooth visual target animation",
    )
    _RTG_ANIMATION_SUBSCRIPTIONS[key] = subscription
    print("[RTG Physics] Animation started", state)
    return {
        "started": True,
        "start": start,
        "end": end,
        "duration_s": float(duration_s),
    }


def rtg_start(
    stage: Optional[Any] = None,
    *,
    gantry_m: float = DEFAULT_RTG_GANTRY_M,
    trolley_m: float = DEFAULT_RTG_TROLLEY_M,
    hoist_m: float = DEFAULT_RTG_HOIST_M,
    play: bool = True,
) -> dict[str, Any]:
    """Enable stable visual RTG control and move to a known first target."""

    resolved = _resolve_stage(stage)
    report = activate_rtg_visual_control(resolved)
    if play:
        try:
            import omni.timeline

            omni.timeline.get_timeline_interface().play()
            report["timeline_playing"] = True
        except Exception as error:
            report["timeline_playing"] = False
            report["timeline_error"] = str(error)
    report["targets"] = move_to(
        gantry_m=gantry_m,
        trolley_m=trolley_m,
        hoist_m=hoist_m,
        stage=resolved,
    )
    report["engineering_m"] = _current_engineering_targets(resolved)
    print("[RTG Physics] RTG control ready", report)
    return report


def rtg_status(stage: Optional[Any] = None) -> dict[str, Any]:
    """Return the current command cache plus evaluated controller poses."""

    resolved = _resolve_stage(stage)
    return {
        "build_id": PHYSICS_BUILD_ID,
        "engineering_m": _current_engineering_targets(resolved),
        "usd_targets": get_rtg_physics_targets(resolved),
        "state": get_rtg_physics_state(resolved),
    }


def get_rtg_physics_targets(stage: Optional[Any] = None) -> dict[str, float]:
    """Read the currently commanded USD drive targets for diagnostics."""

    resolved = _resolve_stage(stage)
    _Gf, _Sdf, _Usd, _UsdGeom, UsdPhysics = _runtime_modules()
    result: dict[str, float] = {}
    for joint_path, _body, _axis in JOINT_SPECS:
        joint = _valid_prim(resolved, joint_path)
        if joint is None:
            continue
        drive = UsdPhysics.DriveAPI.Get(joint, UsdPhysics.Tokens.linear)
        if drive:
            value = _get_attribute_value(drive.GetTargetPositionAttr())
            result[joint_path] = float(value if value is not None else 0.0)
    return result


def get_rtg_physics_state(stage: Optional[Any] = None) -> dict[str, Any]:
    """Read body poses after a physics step for diagnostics.

    This is intentionally based on evaluated USD transforms, so it works with
    both Isaac Sim's normal timeline stepping and a Kit physics update.  It is
    not a replacement for joint-force sensors; those are added in the next
    integration phase.
    """

    resolved = _resolve_stage(stage)
    _Gf, _Sdf, Usd, UsdGeom, _UsdPhysics = _runtime_modules()
    time_code = Usd.TimeCode.Default()
    result: dict[str, Any] = {}
    for path, _mass, role in BODY_SPECS:
        prim = _valid_prim(resolved, path)
        if prim is None:
            continue
        matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(time_code)
        translation = matrix.ExtractTranslation()
        result[role] = {
            "prim_path": path,
            "world_position_m": [
                float(translation[0]),
                float(translation[1]),
                float(translation[2]),
            ],
        }
    return result


__all__ = [
    "PHYSICS_BUILD_ID",
    "activate_rtg_physics",
    "diagnose_rtg_physics",
    "deactivate_rtg_physics",
    "activate_rtg_visual_control",
    "freeze_rtg_animations",
    "move_primary_rtg_to_bay",
    "animate_to",
    "engineering_to_usd",
    "get_rtg_physics_state",
    "get_rtg_physics_targets",
    "move_gantry",
    "move_hoist",
    "move_to",
    "move_trolley",
    "rtg_start",
    "rtg_status",
    "set_rtg_physics_targets",
    "set_rtg_visual_targets",
    "start_rtg_physics_visual_sync",
    "stop_rtg_physics_visual_sync",
    "stop_rtg_animation",
    "update_rtg_physics_visuals",
    "validate_rtg_physics_stage",
]
