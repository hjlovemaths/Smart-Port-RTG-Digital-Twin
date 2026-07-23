"""Build the SimReady metadata and dynamic-rope layer for the primary RTG.

The Blender-exported asset remains the source of truth for gantry, trolley, and
hoist animation.  This stronger layer adds disabled physics prototypes, black
dynamic ropes, and visibility overrides for the duplicate silver rope meshes.
"""

from __future__ import annotations

from pathlib import Path
import sys

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.append(str(SCRIPT_DIR))

from yard_coordinate_mapping import (
    GANTRY_HARD_LIMITS_M,
    GANTRY_SOFT_LIMITS_M,
    YARD_TOTAL_LENGTH_M,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = PROJECT_ROOT / "omniverse" / "scenes" / "rtg_simready.usda"

PRIMARY_PATH = "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC"
GANTRY_PATH = f"{PRIMARY_PATH}/ANIM_CTRL_RTG_GANTRY_TRAVEL"
TROLLEY_PATH = f"{GANTRY_PATH}/ANIM_CTRL_RTG_TROLLEY_TRAVEL"
HOIST_PATH = f"{TROLLEY_PATH}/ANIM_CTRL_RTG_HOIST_VERTICAL"
JOINTS_PATH = "/World/RTGPhysics"
ROPE_SYSTEM_PATH = f"{TROLLEY_PATH}/RTG_DYNAMIC_HOIST_ROPES"

# Rope endpoints measured from the Blender source in trolley-local coordinates.
# Each tuple is (fixed upper trolley attachment, moving lower spreader attachment).
# Lower endpoints converge on the four visible yellow lifting-header corners
# requested in visual review, rather than the older sheave-centre placeholders.
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

HOIST_TARGETS = ((1, 0.0), (35, -0.45), (70, 0.85), (230, 0.85))
GANTRY_TARGETS = ((1, 0.0), (159, 0.0), (230, 4.2))
TROLLEY_TARGETS = ((1, 0.0), (90, 0.0), (150, -2.25), (230, -2.25))

# Full engineering gantry travel spans the 1C/001 seaside start through the
# 4C/041 bay end.  The authored demo samples below still move only 4.2 m.
GANTRY_LIMITS = GANTRY_HARD_LIMITS_M
GANTRY_SOFT_LIMITS = GANTRY_SOFT_LIMITS_M
GANTRY_MAX_VELOCITY = 1.50
TROLLEY_LIMITS = (-2.25, 0.0)
TROLLEY_SOFT_LIMITS = (-2.15, -0.10)
TROLLEY_MAX_VELOCITY = 1.00
HOIST_LIMITS = (-0.45, 0.85)
HOIST_SOFT_LIMITS = (-0.40, 0.80)
HOIST_MAX_VELOCITY = 0.90

# Engineering coordinates exposed to PLC/ROS2/WPF.  The current visual model
# is not authored at the full physical travel, so these values are mapped
# linearly onto the validated USD controller ranges above.
TROLLEY_ENGINEERING_LIMITS = (0.0, 18.0)
TROLLEY_ENGINEERING_SOFT_LIMITS = (0.3, 17.7)
TROLLEY_USD_AT_ENGINEERING_LIMITS = (0.0, -2.25)
TROLLEY_ZERO_DESCRIPTION = "left end near control tower"
HOIST_ENGINEERING_LIMITS = (0.0, 15.0)
HOIST_ENGINEERING_SOFT_LIMITS = (0.5, 14.7)
HOIST_USD_AT_ENGINEERING_LIMITS = (-0.45, 0.85)
HOIST_ZERO_DESCRIPTION = "spreader at ground level"
# The Blender guide curves end at LOWER_HEADER_SOURCE_Z, while the measured
# upper surface of the yellow lifting header is approximately Z=3.78.  Put the
# curve centreline on that yellow top face so the rendered rope visually binds
# to the four blue-marked header corners.
LOWER_ROPE_VISIBLE_OFFSET_Z = -0.58
LEGACY_ROPE_PATHS = tuple(
    f"/World/PortAndRTG/RTG_BOUND_HOIST_ROPE_{group:02d}_{strand}__USD_MESH"
    for group in range(8)
    for strand in range(2)
)


def apply_body(stage: Usd.Stage, path: str, mass_kg: float, body_name: str) -> None:
    prim = stage.OverridePrim(path)
    UsdPhysics.RigidBodyAPI.Apply(prim).CreateRigidBodyEnabledAttr(True)
    UsdPhysics.MassAPI.Apply(prim).CreateMassAttr(mass_kg)
    prim.CreateAttribute("rtg:physicsRole", Sdf.ValueTypeNames.Token).Set(body_name)
    prim.CreateAttribute("rtg:collisionStatus", Sdf.ValueTypeNames.Token).Set(
        "proxy_pending"
    )


def define_prismatic_joint(
    stage: Usd.Stage,
    name: str,
    axis: str,
    body1: str,
    lower: float,
    upper: float,
    local_pos0: Gf.Vec3f,
    body0: str | None = None,
    local_rot0: Gf.Quatf | None = None,
    stiffness: float = 100000.0,
    damping: float = 20000.0,
    max_force: float = 10000000.0,
    soft_lower: float | None = None,
    soft_upper: float | None = None,
    max_velocity: float | None = None,
) -> tuple[UsdPhysics.PrismaticJoint, UsdPhysics.DriveAPI]:
    joint = UsdPhysics.PrismaticJoint.Define(stage, f"{JOINTS_PATH}/{name}")
    if body0:
        joint.CreateBody0Rel().SetTargets([Sdf.Path(body0)])
    joint.CreateBody1Rel().SetTargets([Sdf.Path(body1)])
    joint.CreateLocalPos0Attr(local_pos0)
    joint.CreateLocalPos1Attr(Gf.Vec3f(0.0))
    joint.CreateLocalRot0Attr(local_rot0 or Gf.Quatf(1.0))
    joint.CreateLocalRot1Attr(Gf.Quatf(1.0))
    joint.CreateAxisAttr(axis)
    joint.CreateLowerLimitAttr(lower)
    joint.CreateUpperLimitAttr(upper)
    joint.CreateBreakForceAttr(sys.float_info.max)
    joint.CreateBreakTorqueAttr(sys.float_info.max)

    drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), UsdPhysics.Tokens.linear)
    drive.CreateTypeAttr(UsdPhysics.Tokens.force)
    drive.CreateTargetPositionAttr(0.0)
    drive.CreateTargetVelocityAttr(0.0)
    drive.CreateStiffnessAttr(stiffness)
    drive.CreateDampingAttr(damping)
    drive.CreateMaxForceAttr(max_force)

    joint.GetPrim().CreateAttribute("rtg:axis", Sdf.ValueTypeNames.Token).Set(axis)
    joint.GetPrim().CreateAttribute("rtg:controlMode", Sdf.ValueTypeNames.Token).Set(
        "position_drive"
    )
    if soft_lower is not None:
        joint.GetPrim().CreateAttribute(
            "rtg:softLowerLimit", Sdf.ValueTypeNames.Float
        ).Set(soft_lower)
    if soft_upper is not None:
        joint.GetPrim().CreateAttribute(
            "rtg:softUpperLimit", Sdf.ValueTypeNames.Float
        ).Set(soft_upper)
    if max_velocity is not None:
        joint.GetPrim().CreateAttribute(
            "rtg:maxVelocity", Sdf.ValueTypeNames.Float
        ).Set(max_velocity)
    return joint, drive


def apply_engineering_mapping(
    joint: UsdPhysics.PrismaticJoint,
    engineering_limits: tuple[float, float],
    usd_at_engineering_limits: tuple[float, float],
    zero_description: str,
    engineering_soft_limits: tuple[float, float] | None = None,
) -> None:
    """Describe the real equipment coordinate mapped onto a USD joint."""
    prim = joint.GetPrim()
    prim.CreateAttribute("rtg:engineeringUnit", Sdf.ValueTypeNames.Token).Set("m")
    prim.CreateAttribute(
        "rtg:engineeringLowerLimit", Sdf.ValueTypeNames.Float
    ).Set(engineering_limits[0])
    prim.CreateAttribute(
        "rtg:engineeringUpperLimit", Sdf.ValueTypeNames.Float
    ).Set(engineering_limits[1])
    prim.CreateAttribute(
        "rtg:usdPositionAtEngineeringLower", Sdf.ValueTypeNames.Float
    ).Set(usd_at_engineering_limits[0])
    prim.CreateAttribute(
        "rtg:usdPositionAtEngineeringUpper", Sdf.ValueTypeNames.Float
    ).Set(usd_at_engineering_limits[1])
    prim.CreateAttribute(
        "rtg:engineeringZeroDescription", Sdf.ValueTypeNames.String
    ).Set(zero_description)
    if engineering_soft_limits is not None:
        prim.CreateAttribute(
            "rtg:engineeringSoftLowerLimit", Sdf.ValueTypeNames.Float
        ).Set(engineering_soft_limits[0])
        prim.CreateAttribute(
            "rtg:engineeringSoftUpperLimit", Sdf.ValueTypeNames.Float
        ).Set(engineering_soft_limits[1])


def rope_points(hoist_offset: float) -> list[Gf.Vec3f]:
    """Return interleaved upper/lower rope endpoints for one hoist position."""
    points: list[Gf.Vec3f] = []
    for upper, lower in ROPE_ENDPOINTS:
        points.append(Gf.Vec3f(*upper))
        points.append(
            Gf.Vec3f(
                lower[0],
                lower[1],
                lower[2] + LOWER_ROPE_VISIBLE_OFFSET_Z + hoist_offset,
            )
        )
    return points


def define_dynamic_hoist_ropes(stage: Usd.Stage) -> UsdGeom.BasisCurves:
    """Create 16 visual ropes with fixed tops and hoist-driven bottoms."""
    curves = UsdGeom.BasisCurves.Define(stage, ROPE_SYSTEM_PATH)
    curves.CreateTypeAttr(UsdGeom.Tokens.linear)
    curves.CreateBasisAttr(UsdGeom.Tokens.bezier)
    curves.CreateWrapAttr(UsdGeom.Tokens.nonperiodic)
    curves.CreateCurveVertexCountsAttr([2] * len(ROPE_ENDPOINTS))
    curves.CreateWidthsAttr([0.036])
    curves.SetWidthsInterpolation(UsdGeom.Tokens.constant)
    curves.CreateDisplayColorAttr([Gf.Vec3f(0.012, 0.014, 0.018)])

    points_attr = curves.CreatePointsAttr(rope_points(0.0))
    for frame, hoist_offset in HOIST_TARGETS:
        points_attr.Set(rope_points(hoist_offset), Usd.TimeCode(frame))

    material = UsdShade.Material.Define(
        stage, f"{JOINTS_PATH}/Materials/HoistRopeBlack"
    )
    shader = UsdShade.Shader.Define(
        stage, f"{JOINTS_PATH}/Materials/HoistRopeBlack/PreviewSurface"
    )
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(0.008, 0.010, 0.014)
    )
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.72)
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    UsdShade.MaterialBindingAPI.Apply(curves.GetPrim()).Bind(material)

    prim = curves.GetPrim()
    prim.CreateAttribute("rtg:systemRole", Sdf.ValueTypeNames.Token).Set(
        "hoist_rope_visual"
    )
    prim.CreateAttribute("rtg:upperAttachmentBody", Sdf.ValueTypeNames.String).Set(
        TROLLEY_PATH
    )
    prim.CreateAttribute("rtg:lowerAttachmentBody", Sdf.ValueTypeNames.String).Set(
        HOIST_PATH
    )
    prim.CreateAttribute("rtg:collisionEnabled", Sdf.ValueTypeNames.Bool).Set(False)
    return curves


def hide_legacy_rope_exports(stage: Usd.Stage) -> None:
    """Hide the 16 static Blender rope meshes replaced by dynamic curves."""
    for path in LEGACY_ROPE_PATHS:
        prim = stage.OverridePrim(path)
        UsdGeom.Imageable(prim).CreateVisibilityAttr(UsdGeom.Tokens.invisible)
        prim.CreateAttribute("rtg:replacedByDynamicRopes", Sdf.ValueTypeNames.Bool).Set(
            True
        )


def disable_prototype_physics(stage: Usd.Stage) -> None:
    """Keep prototype physics metadata from overriding Blender animation."""
    for body_path in (GANTRY_PATH, TROLLEY_PATH, HOIST_PATH):
        body_prim = stage.OverridePrim(body_path)
        UsdPhysics.RigidBodyAPI.Apply(body_prim).CreateRigidBodyEnabledAttr(False)

    for joint_name in ("GantryTravelJoint", "TrolleyTravelJoint", "HoistVerticalJoint"):
        joint_prim = stage.GetPrimAtPath(f"{JOINTS_PATH}/{joint_name}")
        joint_prim.CreateAttribute("physics:jointEnabled", Sdf.ValueTypeNames.Bool).Set(
            False
        )
    stage.OverridePrim(GANTRY_PATH).CreateAttribute(
        "rtg:demoMotionMode", Sdf.ValueTypeNames.Token
    ).Set(
        "blender_authored_animation"
    )


def build_layer() -> Path:
    if OUTPUT_PATH.exists():
        OUTPUT_PATH.unlink()

    stage = Usd.Stage.CreateNew(str(OUTPUT_PATH))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    stage.SetStartTimeCode(1)
    stage.SetEndTimeCode(230)
    stage.SetTimeCodesPerSecond(24)
    stage.SetFramesPerSecond(24)

    world = stage.OverridePrim("/World")
    stage.SetDefaultPrim(world)

    physics_scene = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
    physics_scene.CreateGravityDirectionAttr(Gf.Vec3f(0.0, 0.0, -1.0))
    physics_scene.CreateGravityMagnitudeAttr(9.81)

    UsdGeom.Scope.Define(stage, JOINTS_PATH)

    apply_body(stage, GANTRY_PATH, 120000.0, "gantry")
    apply_body(stage, TROLLEY_PATH, 15000.0, "trolley")
    apply_body(stage, HOIST_PATH, 5000.0, "hoist_and_spreader")

    # The Blender-exported gantry controller is rotated 180 degrees around Z.
    # Matching that rotation on the world-side joint frame preserves local +Y.
    gantry_joint, gantry_drive = define_prismatic_joint(
        stage,
        "GantryTravelJoint",
        "Y",
        GANTRY_PATH,
        *GANTRY_LIMITS,
        Gf.Vec3f(0.0, 4.160424, 0.0),
        local_rot0=Gf.Quatf(0.0, Gf.Vec3f(0.0, 0.0, 1.0)),
        stiffness=5000000.0,
        damping=1500000.0,
        max_force=50000000.0,
        soft_lower=GANTRY_SOFT_LIMITS[0],
        soft_upper=GANTRY_SOFT_LIMITS[1],
        max_velocity=GANTRY_MAX_VELOCITY,
    )
    UsdPhysics.ArticulationRootAPI.Apply(gantry_joint.GetPrim())
    apply_engineering_mapping(
        gantry_joint,
        GANTRY_LIMITS,
        GANTRY_LIMITS,
        "1C/001 seaside bay-start origin; positive travel follows bay order",
        GANTRY_SOFT_LIMITS,
    )
    gantry_joint.GetPrim().CreateAttribute(
        "rtg:engineeringCommandMode", Sdf.ValueTypeNames.Token
    ).Set("bay_or_metre")
    gantry_joint.GetPrim().CreateAttribute(
        "rtg:yardEndBay", Sdf.ValueTypeNames.String
    ).Set("4C/041")

    trolley_joint, trolley_drive = define_prismatic_joint(
        stage,
        "TrolleyTravelJoint",
        "X",
        TROLLEY_PATH,
        *TROLLEY_LIMITS,
        Gf.Vec3f(-2.25, 0.0, 0.0),
        body0=GANTRY_PATH,
        stiffness=160000.0,
        damping=35000.0,
        max_force=8000000.0,
        soft_lower=TROLLEY_SOFT_LIMITS[0],
        soft_upper=TROLLEY_SOFT_LIMITS[1],
        max_velocity=TROLLEY_MAX_VELOCITY,
    )
    apply_engineering_mapping(
        trolley_joint,
        TROLLEY_ENGINEERING_LIMITS,
        TROLLEY_USD_AT_ENGINEERING_LIMITS,
        TROLLEY_ZERO_DESCRIPTION,
        TROLLEY_ENGINEERING_SOFT_LIMITS,
    )

    hoist_joint, hoist_drive = define_prismatic_joint(
        stage,
        "HoistVerticalJoint",
        "Z",
        HOIST_PATH,
        *HOIST_LIMITS,
        Gf.Vec3f(0.0, 0.0, 0.85),
        body0=TROLLEY_PATH,
        stiffness=180000.0,
        damping=40000.0,
        max_force=10000000.0,
        soft_lower=HOIST_SOFT_LIMITS[0],
        soft_upper=HOIST_SOFT_LIMITS[1],
        max_velocity=HOIST_MAX_VELOCITY,
    )
    apply_engineering_mapping(
        hoist_joint,
        HOIST_ENGINEERING_LIMITS,
        HOIST_USD_AT_ENGINEERING_LIMITS,
        HOIST_ZERO_DESCRIPTION,
        HOIST_ENGINEERING_SOFT_LIMITS,
    )

    # Position targets mirror the existing Blender demonstration sequence.
    for frame, value in HOIST_TARGETS:
        hoist_drive.GetTargetPositionAttr().Set(value, Usd.TimeCode(frame))
    for frame, value in TROLLEY_TARGETS:
        trolley_drive.GetTargetPositionAttr().Set(value, Usd.TimeCode(frame))
    for frame, value in GANTRY_TARGETS:
        gantry_drive.GetTargetPositionAttr().Set(value, Usd.TimeCode(frame))

    disable_prototype_physics(stage)
    define_dynamic_hoist_ropes(stage)
    hide_legacy_rope_exports(stage)

    layer = stage.GetRootLayer()
    layer.customLayerData = {
        "description": "SimReady motion layer for the primary middle RTG",
        "primaryRtg": PRIMARY_PATH,
        "collisionStatus": "proxy_pending",
        "ropeSystem": ROPE_SYSTEM_PATH,
        "ropeCount": len(ROPE_ENDPOINTS),
        "hiddenLegacyRopeCount": len(LEGACY_ROPE_PATHS),
        "yardGantryTravelMeters": YARD_TOTAL_LENGTH_M,
    }
    layer.Save()
    return OUTPUT_PATH


if __name__ == "__main__":
    print("Built RTG SimReady layer:", build_layer())
