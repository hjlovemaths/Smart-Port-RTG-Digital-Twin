"""Align the two existing static RTGs and populate the remaining yard columns."""

from __future__ import annotations

from pathlib import Path

import omni.kit.app
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt

from rtg_live_control import GANTRY_LATERAL_ALIGNMENT_X_M
from yard_coordinate_mapping import YARD_COLUMN_CENTERS_X, bay_scene_y_m


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "omniverse" / "assets" / "rtg" / "RTG_Model.usdc"
PROTOTYPE = ROOT / "omniverse" / "assets" / "rtg" / "RTG_Static_Visual.usdc"
OUTPUT = ROOT / "omniverse" / "scenes" / "static_rtg_yard_population.usda"

PORT_PATH = "/World/PortAndRTG"
STATIC_TEMPLATE_NAME = "RTG_STATIC_LEFT"
STATIC_TEMPLATE_PATH = f"/World/{STATIC_TEMPLATE_NAME}"
SOURCE_MATERIALS_PATH = "/World/_materials"
PROTOTYPE_ROOT_PATH = "/StaticRTG"
PROTOTYPE_MODEL_PATH = f"{PROTOTYPE_ROOT_PATH}/Model"
PROTOTYPE_MATERIALS_PATH = f"{PROTOTYPE_ROOT_PATH}/_materials"
SCENE_START_TIME = Usd.TimeCode(1.0)

ALL_RTG_COLUMN_CENTERS_X = tuple(
    # Use the exact lateral datum that RTGController applies to PLC poses.
    center_x + GANTRY_LATERAL_ALIGNMENT_X_M for center_x in YARD_COLUMN_CENTERS_X
)
STATIC_RTG_PLACEMENTS = (
    ("SMART_PORT_STATIC_RTG_YARD_M60", -40.0, "1C", 17),
    ("SMART_PORT_STATIC_RTG_YARD_M30", -20.0, "1C", 63),
    ("SMART_PORT_STATIC_RTG_YARD_P30", 20.0, "2C", 29),
    ("SMART_PORT_STATIC_RTG_YARD_P60", 40.0, "3C", 57),
    ("SMART_PORT_STATIC_RTG_YARD_P90", 60.0, "4C", 25),
)
ORIGINAL_STATIC_NAMES = ("RTG_STATIC_LEFT", "RTG_STATIC_RIGHT")
ANIMATED_SPREADER_PATH = f"{PROTOTYPE_MODEL_PATH}/AnimatedSpreader"
ANIMATION_CYCLE_FRAMES = 240
ANIMATION_END_FRAME = 961
TROLLEY_START_OFFSET_M = 0.0
TROLLEY_END_OFFSET_M = 6.5
HOIST_TRAVEL_M = 3.5
STATIC_ROPE_ROOT_PATH = f"{PORT_PATH}/SMART_PORT_STATIC_RTG_HOIST_ROPES"
STATIC_ROPE_MATERIAL_PATH = "/World/RTGPhysics/Materials/HoistRopeBlack"
STATIC_ROPE_WIDTH_M = 0.036
STATIC_ROPE_UPPER_Z_M = 8.81
STATIC_ROPE_LOWER_CLEARANCE_M = 0.5335
OUTER_RAIL_FESTOON_PREFIX = "RTG_TOP_REDRAW_black_festoon_loop_from_outer_true_rail_"
OUTER_RAIL_FESTOON_COUNT = 5
OUTER_RAIL_RED_BLOCK_PREFIX = (
    "RTG_TOP_REDRAW_red_brown_festoon_clamp_on_outer_rail_"
)
OUTER_RAIL_RED_BLOCK_COUNT = 5
OUTER_RAIL_FESTOON_PIN_PREFIX = "RTG_TOP_REDRAW_silver_festoon_pin_outer_"
OUTER_RAIL_FESTOON_PIN_COUNT = 5
RED_FESTOON_BLOCK_NAMES = tuple(
    f"red_festoon_trolley_clamp_{index}" for index in range(0, 6)
)

# X/Y offsets are measured from the spreader centre at the primary RTG's
# initial frame. Each item contains one upper and one lower attachment offset.
STATIC_ROPE_XY_OFFSETS = (
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


def _world_center(
    cache: UsdGeom.BBoxCache, prim: Usd.Prim
) -> Gf.Vec3d:
    aligned = cache.ComputeWorldBound(prim).ComputeAlignedRange()
    return Gf.Vec3d((aligned.GetMin() + aligned.GetMax()) * 0.5)


def _matching_world_range(
    cache: UsdGeom.BBoxCache, root: Usd.Prim, name_token: str
) -> Gf.Range3d:
    result = Gf.Range3d()
    token = name_token.upper()
    for prim in Usd.PrimRange(root):
        if token not in str(prim.GetName()).upper():
            continue
        if prim.IsA(UsdGeom.Boundable):
            result.UnionWith(cache.ComputeWorldBound(prim).ComputeAlignedRange())
    if result.IsEmpty():
        raise RuntimeError(f"No boundable {name_token} geometry below {root.GetPath()}")
    return result


def _outer_rail_festoon_paths(stage: Usd.Stage, root_path: str) -> list[Sdf.Path]:
    root = stage.GetPrimAtPath(root_path)
    if not root:
        raise RuntimeError(f"Missing RTG root while removing festoon loops: {root_path}")
    paths = [
        prim.GetPath()
        for prim in Usd.PrimRange(root)
        if str(prim.GetName()).startswith(OUTER_RAIL_FESTOON_PREFIX)
        and prim.IsA(UsdGeom.Boundable)
    ]
    if len(paths) != OUTER_RAIL_FESTOON_COUNT:
        raise RuntimeError(
            f"Expected {OUTER_RAIL_FESTOON_COUNT} outer-rail festoon loops below "
            f"{root_path}, found {len(paths)}"
        )
    return paths


def _disable_outer_rail_festoon_loops(
    target_stage: Usd.Stage,
    source_stage: Usd.Stage,
    source_root_path: str,
    target_root_path: str,
) -> None:
    source_root = Sdf.Path(source_root_path)
    target_root = Sdf.Path(target_root_path)
    for source_path in _outer_rail_festoon_paths(source_stage, source_root_path):
        relative = source_path.MakeRelativePath(source_root)
        target_stage.OverridePrim(target_root.AppendPath(relative)).SetActive(False)


def _disable_outer_rail_red_blocks(
    target_stage: Usd.Stage,
    source_stage: Usd.Stage,
    source_root_path: str,
    target_root_path: str,
) -> None:
    source_root_prim = source_stage.GetPrimAtPath(source_root_path)
    if not source_root_prim:
        raise RuntimeError(
            f"Missing RTG root while removing outer-rail red blocks: {source_root_path}"
        )
    paths = [
        prim.GetPath()
        for prim in Usd.PrimRange(source_root_prim)
        if OUTER_RAIL_RED_BLOCK_PREFIX in str(prim.GetName())
    ]
    if len(paths) != OUTER_RAIL_RED_BLOCK_COUNT:
        raise RuntimeError(
            f"Expected {OUTER_RAIL_RED_BLOCK_COUNT} outer-rail red blocks below "
            f"{source_root_path}, found {len(paths)}"
        )
    source_root = Sdf.Path(source_root_path)
    target_root = Sdf.Path(target_root_path)
    for source_path in paths:
        relative = source_path.MakeRelativePath(source_root)
        target_stage.OverridePrim(target_root.AppendPath(relative)).SetActive(False)


def _disable_outer_rail_festoon_pins(
    target_stage: Usd.Stage,
    source_stage: Usd.Stage,
    source_root_path: str,
    target_root_path: str,
) -> None:
    source_root_prim = source_stage.GetPrimAtPath(source_root_path)
    if not source_root_prim:
        raise RuntimeError(
            f"Missing RTG root while removing outer-rail festoon pins: {source_root_path}"
        )
    paths = [
        prim.GetPath()
        for prim in Usd.PrimRange(source_root_prim)
        if OUTER_RAIL_FESTOON_PIN_PREFIX in str(prim.GetName())
    ]
    if len(paths) != OUTER_RAIL_FESTOON_PIN_COUNT:
        raise RuntimeError(
            f"Expected {OUTER_RAIL_FESTOON_PIN_COUNT} outer-rail festoon pins below "
            f"{source_root_path}, found {len(paths)}"
        )
    source_root = Sdf.Path(source_root_path)
    target_root = Sdf.Path(target_root_path)
    for source_path in paths:
        relative = source_path.MakeRelativePath(source_root)
        target_stage.OverridePrim(target_root.AppendPath(relative)).SetActive(False)


def _disable_red_festoon_blocks(
    target_stage: Usd.Stage,
    source_stage: Usd.Stage,
    source_root_path: str,
    target_root_path: str,
) -> None:
    source_root_prim = source_stage.GetPrimAtPath(source_root_path)
    if not source_root_prim:
        raise RuntimeError(f"Missing RTG root while removing red blocks: {source_root_path}")
    paths = [
        prim.GetPath()
        for prim in Usd.PrimRange(source_root_prim)
        if str(prim.GetName()) in RED_FESTOON_BLOCK_NAMES
        and prim.IsA(UsdGeom.Boundable)
    ]
    if len(paths) != len(RED_FESTOON_BLOCK_NAMES):
        raise RuntimeError(
            f"Expected {len(RED_FESTOON_BLOCK_NAMES)} red festoon blocks below "
            f"{source_root_path}, found {len(paths)}"
        )
    source_root = Sdf.Path(source_root_path)
    target_root = Sdf.Path(target_root_path)
    for source_path in paths:
        relative = source_path.MakeRelativePath(source_root)
        target_stage.OverridePrim(target_root.AppendPath(relative)).SetActive(False)


def _source_translation(prim: Usd.Prim) -> Gf.Vec3d:
    attr = prim.GetAttribute("xformOp:translate")
    value = attr.Get() if attr else None
    if value is None:
        return Gf.Vec3d(0.0)
    return Gf.Vec3d(value)


def _translation_for_target(
    source_stage: Usd.Stage,
    cache: UsdGeom.BBoxCache,
    source_path: str,
    target_x: float,
    target_y: float,
) -> Gf.Vec3d:
    prim = source_stage.GetPrimAtPath(source_path)
    if not prim:
        raise RuntimeError(f"Missing static RTG source prim: {source_path}")
    center = _world_center(cache, prim)
    translation = _source_translation(prim)
    return translation + Gf.Vec3d(
        target_x - center[0],
        target_y - center[1],
        0.0,
    )


def _parent_offset_for_target(
    source_stage: Usd.Stage,
    cache: UsdGeom.BBoxCache,
    source_path: str,
    target_x: float,
    target_y: float,
) -> Gf.Vec3d:
    prim = source_stage.GetPrimAtPath(source_path)
    if not prim:
        raise RuntimeError(f"Missing static RTG source prim: {source_path}")
    center = _world_center(cache, prim)
    return Gf.Vec3d(
        target_x - center[0],
        target_y - center[1],
        0.0,
    )


def _set_translation(prim: Usd.Prim, value: Gf.Vec3d) -> None:
    prim.CreateAttribute(
        "xformOp:translate", Sdf.ValueTypeNames.Double3, custom=False
    ).Set(value)


def _remap_prototype_path(path: Sdf.Path) -> Sdf.Path:
    text = str(path)
    for source_prefix, prototype_prefix in (
        (STATIC_TEMPLATE_PATH, PROTOTYPE_MODEL_PATH),
        (SOURCE_MATERIALS_PATH, PROTOTYPE_MATERIALS_PATH),
    ):
        if text == source_prefix or text.startswith(f"{source_prefix}/"):
            return Sdf.Path(f"{prototype_prefix}{text[len(source_prefix):]}")
    return path


def _remap_prototype_connections(stage: Usd.Stage) -> None:
    for prim in stage.Traverse():
        for relationship in prim.GetRelationships():
            targets = relationship.GetTargets()
            remapped = [_remap_prototype_path(path) for path in targets]
            if remapped != targets:
                relationship.SetTargets(remapped)
        for attribute in prim.GetAttributes():
            connections = attribute.GetConnections()
            remapped = [_remap_prototype_path(path) for path in connections]
            if remapped != connections:
                attribute.SetConnections(remapped)


def _freeze_prototype_at_start_frame(stage: Usd.Stage) -> int:
    model = stage.GetPrimAtPath(PROTOTYPE_MODEL_PATH)
    frozen = 0
    for prim in Usd.PrimRange(model):
        for attribute in prim.GetAttributes():
            if not attribute.GetNumTimeSamples():
                continue
            value = attribute.Get(SCENE_START_TIME)
            attribute.Clear()
            if value is not None:
                attribute.Set(value)
            frozen += 1
    return frozen


def _animation_samples() -> tuple[tuple[float, float, float], ...]:
    cycle = (
        (1.0, TROLLEY_START_OFFSET_M, 0.0),
        (37.0, TROLLEY_START_OFFSET_M, -HOIST_TRAVEL_M),
        (73.0, TROLLEY_START_OFFSET_M, 0.0),
        (121.0, TROLLEY_END_OFFSET_M, 0.0),
        (157.0, TROLLEY_END_OFFSET_M, -HOIST_TRAVEL_M),
        (193.0, TROLLEY_END_OFFSET_M, 0.0),
        (241.0, TROLLEY_START_OFFSET_M, 0.0),
    )
    samples = []
    for offset in range(0, ANIMATION_END_FRAME, ANIMATION_CYCLE_FRAMES):
        for frame, trolley_x, hoist_z in cycle:
            actual_frame = frame + offset
            if actual_frame > ANIMATION_END_FRAME:
                continue
            if samples and samples[-1][0] == actual_frame:
                samples[-1] = (actual_frame, trolley_x, hoist_z)
            else:
                samples.append((actual_frame, trolley_x, hoist_z))
    return tuple(samples)


def _is_spreader_component(name: str) -> bool:
    lowered = name.lower()
    return (
        ("rtg_spread" in lowered or "rtg_spreader" in lowered)
        and "upper_trolley" not in lowered
    )


def _group_and_animate_prototype_spreader(stage: Usd.Stage) -> tuple[int, float, float]:
    model = stage.GetPrimAtPath(PROTOTYPE_MODEL_PATH)
    if not model:
        raise RuntimeError("Static RTG prototype model is missing")
    spreader_names = []
    for child in model.GetChildren():
        name = str(child.GetName())
        if _is_spreader_component(name):
            spreader_names.append(name)

    layer = stage.GetRootLayer()
    UsdGeom.Xform.Define(stage, ANIMATED_SPREADER_PATH)
    for name in spreader_names:
        source_path = Sdf.Path(f"{PROTOTYPE_MODEL_PATH}/{name}")
        target_path = Sdf.Path(f"{ANIMATED_SPREADER_PATH}/{name}")
        Sdf.CopySpec(layer, source_path, layer, target_path)
        stage.RemovePrim(source_path)

    spreader = UsdGeom.Xformable(stage.GetPrimAtPath(ANIMATED_SPREADER_PATH))
    spreader_op = spreader.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble, "animation")
    parent_matrix = UsdGeom.Xformable(model).GetLocalTransformation(SCENE_START_TIME)
    parent_x_scale = parent_matrix.TransformDir(Gf.Vec3d(1.0, 0.0, 0.0)).GetLength()
    parent_z_scale = parent_matrix.TransformDir(Gf.Vec3d(0.0, 0.0, 1.0)).GetLength()
    for frame, trolley_x, hoist_z in _animation_samples():
        spreader_op.Set(
            Gf.Vec3d(trolley_x / parent_x_scale, 0.0, hoist_z / parent_z_scale),
            Usd.TimeCode(frame),
        )
    return len(spreader_names), parent_x_scale, parent_z_scale


def _build_self_contained_static_prototype(source_stage: Usd.Stage) -> None:
    source_layer = source_stage.GetRootLayer()
    if PROTOTYPE.exists():
        PROTOTYPE.unlink()

    prototype_stage = Usd.Stage.CreateNew(str(PROTOTYPE))
    prototype_stage.SetMetadata("metersPerUnit", 1.0)
    prototype_stage.SetMetadata("upAxis", UsdGeom.Tokens.z)
    root = UsdGeom.Xform.Define(prototype_stage, PROTOTYPE_ROOT_PATH)
    root.AddTranslateOp().Set(Gf.Vec3d(0.0))
    prototype_stage.SetDefaultPrim(root.GetPrim())

    Sdf.CopySpec(
        source_layer,
        Sdf.Path(STATIC_TEMPLATE_PATH),
        prototype_stage.GetRootLayer(),
        Sdf.Path(PROTOTYPE_MODEL_PATH),
    )
    Sdf.CopySpec(
        source_layer,
        Sdf.Path(SOURCE_MATERIALS_PATH),
        prototype_stage.GetRootLayer(),
        Sdf.Path(PROTOTYPE_MATERIALS_PATH),
    )
    _disable_red_festoon_blocks(
        prototype_stage,
        prototype_stage,
        PROTOTYPE_MODEL_PATH,
        PROTOTYPE_MODEL_PATH,
    )
    _disable_outer_rail_red_blocks(
        prototype_stage,
        prototype_stage,
        PROTOTYPE_MODEL_PATH,
        PROTOTYPE_MODEL_PATH,
    )
    _disable_outer_rail_festoon_pins(
        prototype_stage,
        prototype_stage,
        PROTOTYPE_MODEL_PATH,
        PROTOTYPE_MODEL_PATH,
    )
    frozen_sample_count = _freeze_prototype_at_start_frame(prototype_stage)
    spreader_count, parent_x_scale, parent_z_scale = (
        _group_and_animate_prototype_spreader(prototype_stage)
    )
    root.GetPrim().CreateAttribute(
        "rtg:animatedSpreaderComponentCount", Sdf.ValueTypeNames.Int, custom=True
    ).Set(spreader_count)
    root.GetPrim().CreateAttribute(
        "rtg:animationCycleFrames", Sdf.ValueTypeNames.Int, custom=True
    ).Set(ANIMATION_CYCLE_FRAMES)
    root.GetPrim().CreateAttribute(
        "rtg:animationTarget", Sdf.ValueTypeNames.String, custom=True
    ).Set("complete spreader only; trolley platform remains fixed")
    root.GetPrim().CreateAttribute(
        "rtg:frozenSourceAnimationAttributeCount",
        Sdf.ValueTypeNames.Int,
        custom=True,
    ).Set(frozen_sample_count)
    root.GetPrim().CreateAttribute(
        "rtg:animationParentXScale", Sdf.ValueTypeNames.Double, custom=True
    ).Set(parent_x_scale)
    root.GetPrim().CreateAttribute(
        "rtg:animationParentZScale", Sdf.ValueTypeNames.Double, custom=True
    ).Set(parent_z_scale)
    _remap_prototype_connections(prototype_stage)
    prototype_stage.GetRootLayer().Save()


def _define_static_hoist_ropes(
    stage: Usd.Stage,
    source_stage: Usd.Stage,
    cache: UsdGeom.BBoxCache,
) -> None:
    source_root = source_stage.GetPrimAtPath(STATIC_TEMPLATE_PATH)
    source_center = _world_center(cache, source_root)
    spreader_range = _matching_world_range(cache, source_root, "SPREAD")
    spreader_center = spreader_range.GetMidpoint()
    spreader_offset_x = spreader_center[0] - source_center[0]
    spreader_offset_y = spreader_center[1] - source_center[1]
    lower_z = spreader_range.GetMax()[2] + STATIC_ROPE_LOWER_CLEARANCE_M

    UsdGeom.Xform.Define(stage, STATIC_ROPE_ROOT_PATH)
    for rtg_name, target_x, block, bay in STATIC_RTG_PLACEMENTS:
        target_x += GANTRY_LATERAL_ALIGNMENT_X_M
        target_y = bay_scene_y_m(block, bay)
        center_x = target_x + spreader_offset_x
        center_y = target_y + spreader_offset_y

        def rope_points(trolley_x: float, hoist_z: float) -> Vt.Vec3fArray:
            points = []
            for upper_xy, lower_xy in STATIC_ROPE_XY_OFFSETS:
                points.extend(
                    (
                        Gf.Vec3f(
                            center_x + upper_xy[0],
                            center_y + upper_xy[1],
                            STATIC_ROPE_UPPER_Z_M,
                        ),
                        Gf.Vec3f(
                            center_x + trolley_x + lower_xy[0],
                            center_y + lower_xy[1],
                            lower_z + hoist_z,
                        ),
                    )
                )
            return Vt.Vec3fArray(points)

        path = f"{STATIC_ROPE_ROOT_PATH}/{rtg_name}_HOIST_ROPES"
        curves = UsdGeom.BasisCurves.Define(stage, path)
        curves.CreateTypeAttr(UsdGeom.Tokens.linear)
        curves.CreateBasisAttr(UsdGeom.Tokens.bezier)
        curves.CreateWrapAttr(UsdGeom.Tokens.nonperiodic)
        curves.CreateCurveVertexCountsAttr(Vt.IntArray([2] * len(STATIC_ROPE_XY_OFFSETS)))
        points_attr = curves.CreatePointsAttr()
        for frame, trolley_x, hoist_z in _animation_samples():
            points_attr.Set(rope_points(trolley_x, hoist_z), Usd.TimeCode(frame))
        curves.CreateWidthsAttr(Vt.FloatArray([STATIC_ROPE_WIDTH_M]))
        curves.SetWidthsInterpolation(UsdGeom.Tokens.constant)
        curves.CreateDisplayColorPrimvar(UsdGeom.Tokens.constant).Set(
            Vt.Vec3fArray([Gf.Vec3f(0.008, 0.01, 0.014)])
        )
        UsdShade.MaterialBindingAPI.Apply(curves.GetPrim())
        curves.GetPrim().CreateRelationship(
            "material:binding", custom=False
        ).SetTargets([Sdf.Path(STATIC_ROPE_MATERIAL_PATH)])
        curves.GetPrim().CreateAttribute(
            "rtg:systemRole", Sdf.ValueTypeNames.Token, custom=True
        ).Set("static_hoist_rope_visual")
        curves.GetPrim().CreateAttribute(
            "rtg:runtimeControlled", Sdf.ValueTypeNames.Bool, custom=True
        ).Set(False)


def build() -> dict[str, object]:
    source_stage = Usd.Stage.Open(str(SOURCE), Usd.Stage.LoadAll)
    if not source_stage:
        raise RuntimeError(f"Could not open {SOURCE}")
    _build_self_contained_static_prototype(source_stage)
    cache = UsdGeom.BBoxCache(
        SCENE_START_TIME,
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
        useExtentsHint=True,
    )

    if OUTPUT.exists():
        OUTPUT.unlink()
    stage = Usd.Stage.CreateNew(str(OUTPUT))
    stage.SetMetadata("metersPerUnit", 1.0)
    stage.SetMetadata("upAxis", UsdGeom.Tokens.z)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    stage.OverridePrim(PORT_PATH)

    _disable_outer_rail_festoon_loops(
        stage,
        source_stage,
        "/World/RTG_PRIMARY_DYNAMIC",
        f"{PORT_PATH}/RTG_PRIMARY_DYNAMIC",
    )
    for source_name in (
        "RTG_PRIMARY_DYNAMIC",
        "RTG_STATIC_LEFT",
        "RTG_STATIC_RIGHT",
    ):
        _disable_red_festoon_blocks(
            stage,
            source_stage,
            f"/World/{source_name}",
            f"{PORT_PATH}/{source_name}",
        )
        _disable_outer_rail_red_blocks(
            stage,
            source_stage,
            f"/World/{source_name}",
            f"{PORT_PATH}/{source_name}",
        )
        _disable_outer_rail_festoon_pins(
            stage,
            source_stage,
            f"/World/{source_name}",
            f"{PORT_PATH}/{source_name}",
        )

    layout = stage.DefinePrim("/World/SMART_PORT_STATIC_RTG_YARD_POPULATION", "Scope")
    layout.CreateAttribute(
        "rtg:columnCentersX", Sdf.ValueTypeNames.DoubleArray, custom=True
    ).Set(Vt.DoubleArray(ALL_RTG_COLUMN_CENTERS_X))
    layout.CreateAttribute(
        "rtg:layoutRule", Sdf.ValueTypeNames.String, custom=True
    ).Set("five static RTGs use different yard blocks and bay positions")

    for source_name in ORIGINAL_STATIC_NAMES:
        source_override = stage.OverridePrim(f"{PORT_PATH}/{source_name}")
        UsdGeom.Imageable(source_override).CreateVisibilityAttr().Set(
            UsdGeom.Tokens.invisible
        )

    placed = []
    source_reference = Sdf.Reference(
        "../assets/rtg/RTG_Static_Visual.usdc", PROTOTYPE_ROOT_PATH
    )
    for name, target_x, block, bay in STATIC_RTG_PLACEMENTS:
        target_x += GANTRY_LATERAL_ALIGNMENT_X_M
        target_y = bay_scene_y_m(block, bay)
        target = stage.DefinePrim(f"{PORT_PATH}/{name}", "Xform")
        target.GetReferences().AddReference(source_reference)
        target.SetInstanceable(True)
        _set_translation(
            target,
            _parent_offset_for_target(
                source_stage,
                cache,
                STATIC_TEMPLATE_PATH,
                target_x,
                target_y,
            ),
        )
        target.CreateAttribute(
            "rtg:yardColumnCenterX", Sdf.ValueTypeNames.Double, custom=True
        ).Set(target_x)
        target.CreateAttribute(
            "rtg:parkingCenterY", Sdf.ValueTypeNames.Double, custom=True
        ).Set(target_y)
        target.CreateAttribute(
            "rtg:yardBlock", Sdf.ValueTypeNames.String, custom=True
        ).Set(block)
        target.CreateAttribute(
            "rtg:bayNumber", Sdf.ValueTypeNames.Int, custom=True
        ).Set(bay)
        target.CreateAttribute(
            "rtg:role", Sdf.ValueTypeNames.String, custom=True
        ).Set("static_visual")
        target.CreateAttribute(
            "rtg:runtimeControlled", Sdf.ValueTypeNames.Bool, custom=True
        ).Set(False)
        placed.append(name)

    _define_static_hoist_ropes(stage, source_stage, cache)

    stage.GetRootLayer().Save()
    return {
        "output": str(OUTPUT),
        "prototype": str(PROTOTYPE),
        "parking_bays": [f"{block}/{bay:03d}" for _, _, block, bay in STATIC_RTG_PLACEMENTS],
        "static_placed": placed,
    }


try:
    print(f"STATIC_RTG_YARD_RESULT {build()}")
finally:
    omni.kit.app.get_app().post_quit(0)
