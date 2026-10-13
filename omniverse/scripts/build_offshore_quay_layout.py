"""Build the offshore quay, four access causeways, and STS relocation layer.

Run with the Kit Python environment because the source RTG asset is binary USD::

    kit.exe omni.app.mini.kit --no-window --enable omni.usd.libs \
        --enable omni.usd.core --exec omniverse/scripts/build_offshore_quay_layout.py
"""

from __future__ import annotations

from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom, Vt

try:
    import omni.kit.app
except ModuleNotFoundError:  # Kit Python can run this builder without app bootstrap.
    omni = None


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ASSET = PROJECT_ROOT / "omniverse" / "assets" / "rtg" / "RTG_Model.usdc"
STS_PROTOTYPE = (
    PROJECT_ROOT / "omniverse" / "assets" / "rtg" / "STS_Crane_Static_Visual.usdc"
)
OUTPUT_LAYER = PROJECT_ROOT / "omniverse" / "scenes" / "offshore_quay_layout.usda"
STS_PROTOTYPE_ROOT = "/STSCrane"
STS_PROTOTYPE_MODEL = f"{STS_PROTOTYPE_ROOT}/Model"
STS_PROTOTYPE_MATERIALS = f"{STS_PROTOTYPE_ROOT}/_materials"
SOURCE_MATERIALS_PATH = "/World/_materials"
STS_PROTOTYPE_FAMILIES = (
    "CODEx_STS_Crane0",
    "SMART_PORT_quay_sts_crane_0",
)

OFFSHORE_SIDE_SIGN = -1.0
YARD_WATERSIDE_X_M = -101.75
CAUSEWAY_SHORE_OVERLAP_M = 0.25
CAUSEWAY_DECK_START_X_M = (
    YARD_WATERSIDE_X_M - OFFSHORE_SIDE_SIGN * CAUSEWAY_SHORE_OVERLAP_M
)
CAUSEWAY_LENGTH_M = 120.0
CAUSEWAY_NOMINAL_BAY_SPAN = 90
OFFSHORE_QUAY_LANDSIDE_X_M = (
    CAUSEWAY_DECK_START_X_M + OFFSHORE_SIDE_SIGN * CAUSEWAY_LENGTH_M
)
OFFSHORE_QUAY_WATERSIDE_X_M = (
    OFFSHORE_QUAY_LANDSIDE_X_M + OFFSHORE_SIDE_SIGN * 20.0
)
OFFSHORE_QUAY_CENTER_X_M = (
    OFFSHORE_QUAY_LANDSIDE_X_M + OFFSHORE_QUAY_WATERSIDE_X_M
) * 0.5
OFFSHORE_QUAY_MIN_Y_M = -10.0
OFFSHORE_QUAY_MAX_Y_M = 510.0
# Match the four clear approach roads authored by build_quay_yard_traffic_layout.
CAUSEWAY_CENTER_Y_M = (-5.375, 126.05, 256.30, 386.55)
CAUSEWAY_WIDTH_M = 9.0
# Match the two white edges authored on each yard cross-road. The bridge deck
# is wider than the marked road, so its markings sit on the same ±3.7 m
# centerline offsets as the yard road and meet the shoreline markings cleanly.
CAUSEWAY_EDGE_LINE_OFFSET_M = 3.7
CAUSEWAY_EDGE_LINE_WIDTH_M = 0.18
DECK_TOP_Z_M = 0.18
DECK_THICKNESS_M = 0.28
MARKING_TOP_Z_M = DECK_TOP_Z_M + 0.025
CRANE_SEAWARD_OFFSET_M = 10.3
CRANE_GROUP_TRANSLATE_X_M = (
    OFFSHORE_QUAY_LANDSIDE_X_M
    + OFFSHORE_SIDE_SIGN * CRANE_SEAWARD_OFFSET_M
)
CRANE_WHEEL_TRACK_OFFSETS_X_M = (3.2, 8.0)
CRANE_RAIL_X_M = (
    CRANE_GROUP_TRANSLATE_X_M
    + OFFSHORE_SIDE_SIGN * CRANE_WHEEL_TRACK_OFFSETS_X_M[0],
    CRANE_GROUP_TRANSLATE_X_M
    + OFFSHORE_SIDE_SIGN * CRANE_WHEEL_TRACK_OFFSETS_X_M[1],
)
CRANE_GROUP_TRANSLATE_Y_M = 250.0
ADDITIONAL_STS_CRANE_CENTERS_Y_M = (55.0, 130.0, 370.0, 445.0)
STS_PROTOTYPE_SOURCE_CENTER = Gf.Vec2d(-38.06700134277344, -9.620000004768373)
STS_TARGET_CENTER_X_M = -241.42000000476837
CRANE_PREFIX_FAMILIES = (
    ("CODEx_STS_Crane0", "SMART_PORT_quay_sts_crane_0"),
    ("CODEx_STS_Crane1", "SMART_PORT_quay_sts_crane_1"),
    ("CODEx_STS_Crane3", "SMART_PORT_quay_sts_crane_3"),
)
WATERSIDE_TRAFFIC_LANE_CENTER_X_M = -238.0
LANDSIDE_TRAFFIC_LANE_CENTER_X_M = -225.0
TRAFFIC_LANE_WIDTH_M = 3.2

CONCRETE = Gf.Vec3f(0.018, 0.035, 0.06)
CONCRETE_EDGE = Gf.Vec3f(0.64, 0.65, 0.61)
WHITE = Gf.Vec3f(0.88, 0.90, 0.87)
YELLOW = Gf.Vec3f(0.96, 0.70, 0.04)
RAIL = Gf.Vec3f(0.055, 0.060, 0.062)
PIER = Gf.Vec3f(0.22, 0.24, 0.24)
FENDER = Gf.Vec3f(0.035, 0.040, 0.045)


def define_cube(
    stage: Usd.Stage,
    path: str,
    center: tuple[float, float, float],
    size: tuple[float, float, float],
    color: Gf.Vec3f,
) -> UsdGeom.Cube:
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1.0)
    cube.CreateDisplayColorAttr([color])
    xform = UsdGeom.Xformable(cube.GetPrim())
    xform.AddTranslateOp().Set(Gf.Vec3d(*center))
    xform.AddScaleOp().Set(Gf.Vec3f(*size))
    return cube


def build_decks(stage: Usd.Stage, root_path: str) -> None:
    center_z = DECK_TOP_Z_M - DECK_THICKNESS_M * 0.5
    platform_width = abs(
        OFFSHORE_QUAY_WATERSIDE_X_M - OFFSHORE_QUAY_LANDSIDE_X_M
    )
    platform_length = OFFSHORE_QUAY_MAX_Y_M - OFFSHORE_QUAY_MIN_Y_M
    define_cube(
        stage,
        f"{root_path}/Decks/OffshoreQuayPlatform",
        (
            OFFSHORE_QUAY_CENTER_X_M,
            (OFFSHORE_QUAY_MIN_Y_M + OFFSHORE_QUAY_MAX_Y_M) * 0.5,
            center_z,
        ),
        (platform_width, platform_length, DECK_THICKNESS_M),
        CONCRETE,
    )

    causeway_length = abs(
        OFFSHORE_QUAY_LANDSIDE_X_M - CAUSEWAY_DECK_START_X_M
    )
    causeway_center_x = (
        OFFSHORE_QUAY_LANDSIDE_X_M + CAUSEWAY_DECK_START_X_M
    ) * 0.5
    for index, y in enumerate(CAUSEWAY_CENTER_Y_M, start=1):
        define_cube(
            stage,
            f"{root_path}/Decks/Causeway_{index:02d}",
            (causeway_center_x, y, center_z),
            (causeway_length, CAUSEWAY_WIDTH_M, DECK_THICKNESS_M),
            CONCRETE,
        )


def build_road_markings(stage: Usd.Stage, root_path: str) -> None:
    causeway_length = abs(
        OFFSHORE_QUAY_LANDSIDE_X_M - CAUSEWAY_DECK_START_X_M
    )
    causeway_center_x = (
        OFFSHORE_QUAY_LANDSIDE_X_M + CAUSEWAY_DECK_START_X_M
    ) * 0.5
    edge_offset = CAUSEWAY_EDGE_LINE_OFFSET_M
    for index, y in enumerate(CAUSEWAY_CENTER_Y_M, start=1):
        for side, offset in (("Left", -edge_offset), ("Right", edge_offset)):
            define_cube(
                stage,
                f"{root_path}/Markings/Causeway_{index:02d}_{side}Edge",
                (causeway_center_x, y + offset, MARKING_TOP_Z_M),
                (
                    causeway_length - 2.0 * CAUSEWAY_SHORE_OVERLAP_M,
                    CAUSEWAY_EDGE_LINE_WIDTH_M,
                    0.025,
                ),
                WHITE,
            )

    platform_length = OFFSHORE_QUAY_MAX_Y_M - OFFSHORE_QUAY_MIN_Y_M
    for lane_label, lane_center_x in (
        ("Waterside", WATERSIDE_TRAFFIC_LANE_CENTER_X_M),
        ("Landside", LANDSIDE_TRAFFIC_LANE_CENTER_X_M),
    ):
        for edge_label, edge_offset in (
            ("LeftEdge", -TRAFFIC_LANE_WIDTH_M * 0.5),
            ("RightEdge", TRAFFIC_LANE_WIDTH_M * 0.5),
        ):
            define_cube(
                stage,
                f"{root_path}/Markings/{lane_label}Lane_{edge_label}",
                (lane_center_x + edge_offset, 250.0, MARKING_TOP_Z_M),
                (0.16, platform_length - 2.0, 0.025),
                WHITE,
            )


def build_edges_and_rails(stage: Usd.Stage, root_path: str) -> None:
    platform_length = OFFSHORE_QUAY_MAX_Y_M - OFFSHORE_QUAY_MIN_Y_M
    for label, x in (
        (
            "LandsideEdge",
            OFFSHORE_QUAY_LANDSIDE_X_M + OFFSHORE_SIDE_SIGN * 0.25,
        ),
        (
            "WatersideEdge",
            OFFSHORE_QUAY_WATERSIDE_X_M - OFFSHORE_SIDE_SIGN * 0.25,
        ),
    ):
        define_cube(
            stage,
            f"{root_path}/Edges/{label}",
            (x, 250.0, DECK_TOP_Z_M + 0.09),
            (0.35, platform_length, 0.18),
            CONCRETE_EDGE,
        )

    for index, x in enumerate(CRANE_RAIL_X_M, start=1):
        define_cube(
            stage,
            f"{root_path}/Rails/STS_Rail_{index:02d}",
            (x, 250.0, DECK_TOP_Z_M + 0.045),
            (0.13, platform_length - 4.0, 0.09),
            RAIL,
        )

    for index, y in enumerate(range(0, 501, 20), start=1):
        define_cube(
            stage,
            f"{root_path}/Edges/Bollard_{index:02d}",
            (
                OFFSHORE_QUAY_WATERSIDE_X_M - OFFSHORE_SIDE_SIGN * 0.72,
                float(y),
                DECK_TOP_Z_M + 0.26,
            ),
            (0.34, 0.34, 0.52),
            YELLOW,
        )
        define_cube(
            stage,
            f"{root_path}/Edges/Fender_{index:02d}",
            (
                OFFSHORE_QUAY_WATERSIDE_X_M + OFFSHORE_SIDE_SIGN * 0.12,
                float(y),
                -0.35,
            ),
            (0.42, 1.3, 1.25),
            FENDER,
        )


def build_supports(stage: Usd.Stage, root_path: str) -> None:
    for causeway_index, y in enumerate(CAUSEWAY_CENTER_Y_M, start=1):
        support_x = CAUSEWAY_DECK_START_X_M + OFFSHORE_SIDE_SIGN * 8.5
        support_index = 1
        while (
            OFFSHORE_SIDE_SIGN * (OFFSHORE_QUAY_LANDSIDE_X_M - support_x)
            > 3.0
        ):
            define_cube(
                stage,
                f"{root_path}/Supports/Causeway_{causeway_index:02d}_Pier_{support_index:02d}",
                (support_x, y, -0.90),
                (1.8, 1.4, 2.15),
                PIER,
            )
            support_x += OFFSHORE_SIDE_SIGN * 12.0
            support_index += 1
    for row_index, x in enumerate(
        (
            OFFSHORE_QUAY_LANDSIDE_X_M + OFFSHORE_SIDE_SIGN * 4.0,
            OFFSHORE_QUAY_WATERSIDE_X_M - OFFSHORE_SIDE_SIGN * 4.0,
        ),
        start=1,
    ):
        for support_index, y in enumerate(range(0, 501, 40), start=1):
            define_cube(
                stage,
                f"{root_path}/Supports/Platform_Row_{row_index:02d}_Pier_{support_index:02d}",
                (float(x), y, -0.95),
                (1.6, 1.6, 2.25),
                PIER,
            )


def crane_group_matrix() -> Gf.Matrix4d:
    matrix = Gf.Matrix4d(1.0)
    matrix.SetRotate(
        Gf.Rotation(Gf.Vec3d(0.0, 0.0, 1.0), OFFSHORE_SIDE_SIGN * 90.0)
    )
    matrix.SetTranslateOnly(
        Gf.Vec3d(
            CRANE_GROUP_TRANSLATE_X_M,
            CRANE_GROUP_TRANSLATE_Y_M,
            0.0,
        )
    )
    return matrix


def _remap_sts_path(path: Sdf.Path) -> Sdf.Path:
    text = str(path)
    if text == SOURCE_MATERIALS_PATH or text.startswith(f"{SOURCE_MATERIALS_PATH}/"):
        return Sdf.Path(f"{STS_PROTOTYPE_MATERIALS}{text[len(SOURCE_MATERIALS_PATH):]}")
    return path


def _build_sts_prototype(source_stage: Usd.Stage) -> int:
    if STS_PROTOTYPE.exists():
        STS_PROTOTYPE.unlink()
    prototype = Usd.Stage.CreateNew(str(STS_PROTOTYPE))
    prototype.SetMetadata("metersPerUnit", 1.0)
    prototype.SetMetadata("upAxis", UsdGeom.Tokens.z)
    root = UsdGeom.Xform.Define(prototype, STS_PROTOTYPE_ROOT)
    prototype.SetDefaultPrim(root.GetPrim())
    model = UsdGeom.Xform.Define(prototype, STS_PROTOTYPE_MODEL)
    model.AddTranslateOp().Set(
        Gf.Vec3d(-STS_PROTOTYPE_SOURCE_CENTER[0], -STS_PROTOTYPE_SOURCE_CENTER[1], 0.0)
    )

    source_world = source_stage.GetPrimAtPath("/World")
    copied = 0
    for child in source_world.GetChildren():
        name = str(child.GetName())
        if not name.startswith(STS_PROTOTYPE_FAMILIES):
            continue
        Sdf.CopySpec(
            source_stage.GetRootLayer(),
            child.GetPath(),
            prototype.GetRootLayer(),
            Sdf.Path(f"{STS_PROTOTYPE_MODEL}/{name}"),
        )
        copied += 1
    Sdf.CopySpec(
        source_stage.GetRootLayer(),
        Sdf.Path(SOURCE_MATERIALS_PATH),
        prototype.GetRootLayer(),
        Sdf.Path(STS_PROTOTYPE_MATERIALS),
    )
    for prim in prototype.Traverse():
        for relationship in prim.GetRelationships():
            targets = relationship.GetTargets()
            remapped = [_remap_sts_path(path) for path in targets]
            if remapped != targets:
                relationship.SetTargets(remapped)
        for attribute in prim.GetAttributes():
            connections = attribute.GetConnections()
            remapped = [_remap_sts_path(path) for path in connections]
            if remapped != connections:
                attribute.SetConnections(remapped)
    root.GetPrim().CreateAttribute(
        "smartPort:sourceComponentCount", Sdf.ValueTypeNames.Int, custom=True
    ).Set(copied)
    prototype.GetRootLayer().Save()
    return copied


def add_four_sts_cranes(stage: Usd.Stage, source_stage: Usd.Stage) -> int:
    _build_sts_prototype(source_stage)
    reference = Sdf.Reference(
        "../assets/rtg/STS_Crane_Static_Visual.usdc", STS_PROTOTYPE_ROOT
    )
    for index, center_y in enumerate(ADDITIONAL_STS_CRANE_CENTERS_Y_M, start=1):
        prim = stage.DefinePrim(
            f"/World/PortAndRTG/SMART_PORT_ADDITIONAL_STS_CRANE_{index:02d}",
            "Xform",
        )
        prim.GetReferences().AddReference(reference)
        prim.SetInstanceable(True)
        xform = UsdGeom.Xformable(prim)
        xform.AddTranslateOp().Set(Gf.Vec3d(STS_TARGET_CENTER_X_M, center_y, 0.0))
        xform.AddRotateZOp().Set(OFFSHORE_SIDE_SIGN * 90.0)
        prim.CreateAttribute(
            "smartPort:offshoreQuayAdditional", Sdf.ValueTypeNames.Bool, custom=True
        ).Set(True)
    return len(ADDITIONAL_STS_CRANE_CENTERS_Y_M)


def relocate_sts_cranes(stage: Usd.Stage, source_stage: Usd.Stage) -> int:
    source_world = source_stage.GetPrimAtPath("/World")
    if not source_world:
        raise RuntimeError(f"Missing /World in {SOURCE_ASSET}")

    group_op_name = "xformOp:transform:offshoreQuay"
    group_matrix = crane_group_matrix()
    relocated = 0
    for source_prim in source_world.GetChildren():
        name = source_prim.GetName()
        if not any(name.startswith(prefixes) for prefixes in CRANE_PREFIX_FAMILIES):
            continue
        source_xform = UsdGeom.Xformable(source_prim)
        original_order = [op.GetOpName() for op in source_xform.GetOrderedXformOps()]
        target = stage.OverridePrim(f"/World/PortAndRTG/{name}")
        target.CreateAttribute(
            group_op_name,
            Sdf.ValueTypeNames.Matrix4d,
        ).Set(group_matrix)
        order = [group_op_name, *[name for name in original_order if name != group_op_name]]
        target.CreateAttribute(
            "xformOpOrder",
            Sdf.ValueTypeNames.TokenArray,
        ).Set(order)
        target.CreateAttribute(
            "smartPort:offshoreQuayRelocated",
            Sdf.ValueTypeNames.Bool,
            custom=True,
        ).Set(True)
        relocated += 1
    return relocated


def build() -> dict[str, object]:
    if OUTPUT_LAYER.exists():
        OUTPUT_LAYER.unlink()
    stage = Usd.Stage.CreateNew(str(OUTPUT_LAYER))
    stage.SetMetadata("metersPerUnit", 1.0)
    stage.SetMetadata("upAxis", UsdGeom.Tokens.z)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    root_path = "/World/SMART_PORT_OFFSHORE_QUAY"
    root = UsdGeom.Xform.Define(stage, root_path).GetPrim()
    root.CreateAttribute(
        "smartPort:layoutType",
        Sdf.ValueTypeNames.String,
        custom=True,
    ).Set("offshore quay with four access causeways")
    root.CreateAttribute(
        "smartPort:yardWatersideX",
        Sdf.ValueTypeNames.Double,
        custom=True,
    ).Set(YARD_WATERSIDE_X_M)
    root.CreateAttribute(
        "smartPort:offshoreQuayRange",
        Sdf.ValueTypeNames.String,
        custom=True,
    ).Set("X=-241.5..-221.5; Y=-10..510; access causeways X=-221.5..-101.5")
    root.CreateAttribute(
        "smartPort:causewayLengthMeters",
        Sdf.ValueTypeNames.Double,
        custom=True,
    ).Set(CAUSEWAY_LENGTH_M)
    root.CreateAttribute(
        "smartPort:causewayNominalBaySpan",
        Sdf.ValueTypeNames.Int,
        custom=True,
    ).Set(CAUSEWAY_NOMINAL_BAY_SPAN)
    root.CreateAttribute(
        "smartPort:causewayCentersY",
        Sdf.ValueTypeNames.DoubleArray,
        custom=True,
    ).Set(Vt.DoubleArray(CAUSEWAY_CENTER_Y_M))
    root.CreateAttribute(
        "smartPort:craneSeawardOffsetMeters",
        Sdf.ValueTypeNames.Double,
        custom=True,
    ).Set(CRANE_SEAWARD_OFFSET_M)
    root.CreateAttribute(
        "smartPort:craneRailX",
        Sdf.ValueTypeNames.DoubleArray,
        custom=True,
    ).Set(Vt.DoubleArray(CRANE_RAIL_X_M))

    build_decks(stage, root_path)
    build_road_markings(stage, root_path)
    build_edges_and_rails(stage, root_path)
    build_supports(stage, root_path)
    source_stage = Usd.Stage.Open(str(SOURCE_ASSET), Usd.Stage.LoadAll)
    relocated = relocate_sts_cranes(stage, source_stage)
    added = add_four_sts_cranes(stage, source_stage)
    stage.GetRootLayer().Save()
    return {
        "output": str(OUTPUT_LAYER),
        "causeways": len(CAUSEWAY_CENTER_Y_M),
        "relocated_sts_prims": relocated,
        "additional_sts_cranes": added,
        "quay_x": (OFFSHORE_QUAY_LANDSIDE_X_M, OFFSHORE_QUAY_WATERSIDE_X_M),
    }


try:
    print(f"OFFSHORE_QUAY_RESULT {build()}")
finally:
    if omni is not None:
        omni.kit.app.get_app().post_quit(0)
