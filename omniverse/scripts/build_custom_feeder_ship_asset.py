"""Extract the user's authored feeder ship from the main RTG USD asset.

The source scene stores the ship as many top-level component prims. This
builder packages those components into a centered, Z-up asset so a scene layer
can reference it like the other feeder ships.
"""

from __future__ import annotations

from pathlib import Path
import random

from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ASSET = PROJECT_ROOT / "omniverse" / "assets" / "rtg" / "RTG_Model.usdc"
OUTPUT_ASSET = PROJECT_ROOT / "omniverse" / "assets" / "ship" / "Custom_Feeder_Ship.usdc"
YARD_CONTAINER_STAGE = PROJECT_ROOT / "omniverse" / "scenes" / "live_containers.usda"
SOURCE_PREFIXES = (
    # The authored ship is split into several top-level component groups in
    # RTG_Model.usdc. Keep the left-side groups together so the hull/bow is
    # not lost when the ship is packaged as a standalone asset.
    "CODEx_left_reference_feeder_ship_clone_v2_",
    "CODEx_left_reference_feeder_ship_clone_front_v3_",
    "CODEx_left_reference_feeder_ship_clone_lower_",
    "CODEx_left_reference_feeder_ship_clone_above_",
    "CODEx_left_reference_feeder_ship_clone_stern_",
)
SOURCE_MATERIALS = "/World/_materials"
OUTPUT_ROOT = "/CustomFeederShip"
OUTPUT_MODEL = f"{OUTPUT_ROOT}/Model"
OUTPUT_MATERIALS = f"{OUTPUT_ROOT}/_materials"
CONTAINER_MATERIALS = "/World/LiveContainers/Looks"
OUTPUT_CONTAINER_MATERIALS = f"{OUTPUT_ROOT}/ContainerLooks"
CONTAINER_PROTOTYPES = f"{OUTPUT_ROOT}/ContainerPrototypes"

CONTAINER_COMPONENT_MARKERS = (
    "_clone_front_v3_front_container",
    "_clone_v2_reference_container",
)
CONTAINER_PROTOTYPE_SOURCES = {
    20: ("/World/LiveContainers/YardInventory/Bay_3C_005", "R01_T01_Container"),
    40: ("/World/LiveContainers/YardInventory/Bay_3C_002", "R01_T01_Container"),
}
CONTAINER_COLORS = (
    "weathered_blue",
    "oxide_red",
    "warm_gray",
    "faded_teal",
    "sand_yellow",
    "dark_navy",
)
SHIP_SCALE = 2.0
CONTAINER_RENDER_SCALE = 0.5
CONTAINER_TIER_PITCH = 0.76
CONTAINER_TOTAL_TIERS = 6
# Lower the independent container instances into the recessed hold.  The
# existing tier spacing is preserved; this places three full tiers below the
# deck while keeping clearance above the hold floor.
CARGO_HOLD_CONTAINER_OFFSET_Z = -1.4
CARGO_HOLD_X_MIN = -25.0
CARGO_HOLD_X_MAX = 16.6
CARGO_HOLD_Y_HALF = 5.5
AFT_DECK_X_MIN = -34.0
CARGO_HOLD_BOTTOM_Z = 4.45
CARGO_HOLD_DECK_Z = 7.5
CARGO_HOLD_WALL_THICKNESS = 0.30
# These limits are in the authored source-ship coordinates used while the
# independent boxes are collected, not in the centered output-asset frame.
BRIDGE_CONTAINER_EXCLUSION_X_MIN = -47.0
BRIDGE_CONTAINER_EXCLUSION_X_MAX = -40.0
BRIDGE_CONTAINER_EXCLUSION_Y_MIN = -17.6
BRIDGE_CONTAINER_EXCLUSION_Y_MAX = -11.1
# Five 40 ft rows are repacked into the available longitudinal cargo span.
# The last center stays just forward of the wheelhouse exclusion zone.
FORTY_FOOT_ROW_SOURCE_X = (-60.8, -58.0, -55.2, -52.4, -49.6)
# Six transverse lanes across the hold, centered inside the side walls.
FORTY_FOOT_ROW_SOURCE_Y = (-16.64, -15.74, -14.84)
FORTY_FOOT_SIDE_SOURCE_Y = (-13.94, -13.04, -12.14)
# Keep the surviving 20 ft row just forward of the fifth 40 ft row instead of
# letting the two lengths overlap at the transition to the wheelhouse.
TWENTY_FOOT_FORWARD_SOURCE_X = -47.55
REPOSITIONED_CARGO_CELL_FRAME_X = (-24.1, -18.8, -13.2, -7.6, -2.0, 3.5)
HIDDEN_CARGO_CELL_FRAME_PREFIX = (
    "CODEx_left_reference_feeder_ship_clone_v2_cargo_cell_cross_fram"
)
HIDDEN_FLOATING_SHIP_COMPONENT_PREFIXES = (
    "CODEx_left_reference_feeder_ship_clone_v2_water_long_side_walkw",
    "CODEx_left_reference_feeder_ship_clone_v2_quay_long_side_walkwa",
)
HIDDEN_STERN_SIDE_SHELL_NAMES = (
    "CODEx_reference_feeder_ship_above_stern_port_above_water_stern_side_shell_mesh_001",
    "CODEx_reference_feeder_ship_above_stern_starboard_above_water_stern_side_shell_mesh_001",
)
HIDDEN_PRIM_NAMES = ("Cube_5507",)
BRIDGE_DECK_Z = 7.40
BRIDGE_DECK_X_MIN = 3.7
BRIDGE_DECK_X_MAX = 19.6
BRIDGE_DECK_Y_HALF = 6.25
DRIVER_BASEMENT_FLOOR_Z = 4.72
HIDDEN_CARGO_HOLD_BEAM_PREFIXES = (
    # These are the original transverse steel members inside the stern cargo
    # hold.  They intersect the lowest container tier and block top-down
    # spreader access, so they are removed from the operational cargo area.
    "CODEx_left_reference_feeder_ship_clone_above_stern_stern_intern",
    "CODEx_left_reference_feeder_ship_clone_above_stern_stern_low_fl",
)
HIDDEN_CARGO_DECK_PRIM = (
    f"{OUTPUT_MODEL}/"
    "CODEx_left_reference_feeder_ship_clone_v2_reference_main_cargo_"
    "/CODEx_reference_feeder_ship_v2_reference_main_cargo_deck_mesh_001"
)

def remap_source_paths(path: Sdf.Path) -> Sdf.Path:
    text = str(path)
    if text == SOURCE_MATERIALS or text.startswith(f"{SOURCE_MATERIALS}/"):
        return Sdf.Path(f"{OUTPUT_MATERIALS}{text[len(SOURCE_MATERIALS):]}")
    if text == CONTAINER_MATERIALS or text.startswith(f"{CONTAINER_MATERIALS}/"):
        return Sdf.Path(
            f"{OUTPUT_CONTAINER_MATERIALS}{text[len(CONTAINER_MATERIALS):]}"
        )
    return path


def is_container_component(name: str) -> bool:
    return any(marker in name for marker in CONTAINER_COMPONENT_MARKERS)


def is_container_body(bounds: Gf.Range3d) -> int | None:
    if bounds.IsEmpty():
        return None
    size = bounds.GetMax() - bounds.GetMin()
    # The authored ship has 20 ft and 40 ft bodies along local X. The other
    # container-named prims are corrugations, corner details, or locking bars.
    if size[0] > 1.45 and size[1] > 0.65 and size[2] > 0.60:
        return 20 if size[0] < 2.5 else 40
    return None


def copy_container_prototype(
    source: Usd.Stage,
    destination: Usd.Stage,
    size_ft: int,
) -> Sdf.Path:
    source_root, source_name = CONTAINER_PROTOTYPE_SOURCES[size_ft]
    source_parent = source.GetPrimAtPath(source_root)
    source_body = source.GetPrimAtPath(f"{source_root}/{source_name}")
    if not source_parent.IsValid() or not source_body.IsValid():
        raise RuntimeError(f"Missing {size_ft} ft yard container prototype: {source_root}")

    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    bounds = cache.ComputeWorldBound(source_body).ComputeAlignedRange()
    center = (bounds.GetMin() + bounds.GetMax()) * 0.5

    prototype_path = Sdf.Path(f"{CONTAINER_PROTOTYPES}/Container{size_ft}ft")
    prototype = destination.DefinePrim(prototype_path, "Xform")
    prototype.SetSpecifier(Sdf.SpecifierClass)
    geometry = UsdGeom.Xform.Define(destination, f"{prototype_path}/Geometry")
    geometry.AddTranslateOp().Set(Gf.Vec3d(-center[0], -center[1], -center[2]))

    for child in source_parent.GetChildren():
        if not child.GetName().startswith(source_name):
            continue
        Sdf.CopySpec(
            source.GetRootLayer(),
            child.GetPath(),
            destination.GetRootLayer(),
            Sdf.Path(f"{geometry.GetPath()}/{child.GetName()}"),
        )
    return prototype_path


def add_container_instances(
    source: Usd.Stage,
    yard: Usd.Stage,
    destination: Usd.Stage,
    selected_children: list[Usd.Prim],
) -> int:
    prototypes = {
        size_ft: copy_container_prototype(yard, destination, size_ft)
        for size_ft in CONTAINER_PROTOTYPE_SOURCES
    }
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    body_half_heights = {}
    for size_ft, (source_root, source_name) in CONTAINER_PROTOTYPE_SOURCES.items():
        body = yard.GetPrimAtPath(f"{source_root}/{source_name}")
        bounds = cache.ComputeWorldBound(body).ComputeAlignedRange()
        body_half_heights[size_ft] = float((bounds.GetMax()[2] - bounds.GetMin()[2]) * 0.5)

    rows: list[tuple[int, Gf.Vec3d]] = []
    base_raw_z = float("inf")
    for child in selected_children:
        if not is_container_component(child.GetName()):
            continue
        bounds = cache.ComputeWorldBound(child).ComputeAlignedRange()
        size_ft = is_container_body(bounds)
        if size_ft is None:
            continue
        center = (bounds.GetMin() + bounds.GetMax()) * 0.5
        rows.append((size_ft, center))
        base_raw_z = min(
            base_raw_z,
            float(center[2]) - body_half_heights[size_ft],
        )

    # The source arrangement has a partial fifth 40 ft row hidden below the
    # wheelhouse. Repack all 40 ft boxes into five complete rows before the
    # driver-area exclusion is applied, and fill any missing positions using
    # the complete row pattern already present in the source.
    forty_groups: dict[float, list[tuple[float, float]]] = {}
    for size_ft, center in rows:
        if size_ft != 40:
            continue
        key = round(float(center[0]), 2)
        forty_groups.setdefault(key, []).append(
            (float(center[1]), float(center[2]))
        )
    if forty_groups:
        source_row_x = sorted(forty_groups)
        canonical_pattern = max(forty_groups.values(), key=len)
        source_y_values = sorted({round(y, 2) for y, _ in canonical_pattern})
        canonical_keys = set()
        for y, z in canonical_pattern:
            y_index = source_y_values.index(round(y, 2))
            canonical_keys.add(
                (FORTY_FOOT_ROW_SOURCE_Y[min(y_index, len(FORTY_FOOT_ROW_SOURCE_Y) - 1)], round(z, 2))
            )
        # Replace the source 40 ft entries completely. One source row is
        # partially occupied by the wheelhouse and its transverse offsets do
        # not match the regular rows, so retaining it would create duplicates
        # after repacking. The regular pattern gives five boxes per row.
        repacked_rows = [
            (size_ft, center) for size_ft, center in rows if size_ft != 40
        ]
        for target_x in FORTY_FOOT_ROW_SOURCE_X:
            for y, z in sorted(canonical_keys):
                repacked_rows.append((40, Gf.Vec3d(target_x, y, z)))
        rows = repacked_rows

    # Build a complete six-tier 40 ft grid. Two tiers sit below the deck and
    # four tiers intentionally rise above it, using the same yard prototypes
    # and the existing vertical pitch.
    if forty_groups:
        base_40_z = min(
            float(center[2])
            for size_ft, center in rows
            if size_ft == 40
        )
        all_40_y = sorted(
            set(FORTY_FOOT_ROW_SOURCE_Y + FORTY_FOOT_SIDE_SOURCE_Y)
        )
        rows = [
            (size_ft, center) for size_ft, center in rows if size_ft != 40
        ]
        for target_x in FORTY_FOOT_ROW_SOURCE_X:
            for y in all_40_y:
                for tier in range(CONTAINER_TOTAL_TIERS):
                    rows.append(
                        (
                            40,
                            Gf.Vec3d(
                                target_x,
                                y,
                                base_40_z + tier * CONTAINER_TIER_PITCH,
                            ),
                        )
                    )

    body_count = 0
    # Use a seeded generator so the result looks naturally mixed while every
    # rebuild remains reproducible.  The box geometry and dimensions still
    # come directly from the 3C yard prototypes.
    color_rng = random.Random(20260922)
    stack_colors: dict[tuple[float, float], str] = {}
    for size_ft, center in rows:
        if size_ft == 20:
            continue
        if size_ft == 20 and -48.5 <= float(center[0]) <= -47.5:
            center = Gf.Vec3d(
                TWENTY_FOOT_FORWARD_SOURCE_X, center[1], center[2]
            )
        if (
            BRIDGE_CONTAINER_EXCLUSION_X_MIN <= float(center[0]) <= BRIDGE_CONTAINER_EXCLUSION_X_MAX
            and BRIDGE_CONTAINER_EXCLUSION_Y_MIN <= float(center[1]) <= BRIDGE_CONTAINER_EXCLUSION_Y_MAX
        ):
            continue
        instance_path = Sdf.Path(f"{OUTPUT_MODEL}/ShipContainer{body_count:03d}")
        instance = UsdGeom.Xform.Define(destination, instance_path)
        instance_prim = instance.GetPrim()
        # Materialize the prototype into this container instead of inheriting
        # it.  The ship cargo must remain visibly and interactively separate:
        # each box gets its own Geometry hierarchy and can be selected or
        # manipulated independently in Omniverse.
        Sdf.CopySpec(
            destination.GetRootLayer(),
            Sdf.Path(f"{prototypes[size_ft]}/Geometry"),
            destination.GetRootLayer(),
            Sdf.Path(f"{instance_path}/Geometry"),
        )
        instance_prim.CreateAttribute(
            "ship:containerSizeFt", Sdf.ValueTypeNames.Int, custom=True
        ).Set(size_ft)
        # Keep one color for a complete vertical stack at the same x/y slot.
        # This prevents a side view from reading as four independently
        # colored faces while retaining mixed yard-style colors across slots.
        stack_key = (round(float(center[0]), 3), round(float(center[1]), 3))
        container_color = stack_colors.setdefault(
            stack_key, color_rng.choice(CONTAINER_COLORS)
        )
        instance_prim.CreateAttribute(
            "ship:containerColor", Sdf.ValueTypeNames.String, custom=True
        ).Set(container_color)
        translate = instance.AddTranslateOp()
        rotate = instance.AddRotateZOp()
        # The ship parent scales by 2. Keep container dimensions unchanged by
        # using a local 0.5 scale, spread positions over the enlarged deck,
        # and preserve the original vertical stacking pitch. The lowest tier
        # is anchored to the scaled cargo base plane.
        translate.Set(
            Gf.Vec3d(
                center[0],
                center[1],
                0.5 * (base_raw_z + center[2]) + CARGO_HOLD_CONTAINER_OFFSET_Z,
            )
        )
        rotate.Set(90.0)
        instance.AddScaleOp().Set(
            Gf.Vec3d(
                CONTAINER_RENDER_SCALE,
                CONTAINER_RENDER_SCALE,
                CONTAINER_RENDER_SCALE,
            )
        )
        instance.SetXformOpOrder([translate, rotate, instance.GetOrderedXformOps()[-1]])

        # Override the yard prototype's body material per box while keeping
        # the shared 3C corrugation/locking details and their dark materials.
        body = destination.OverridePrim(f"{instance_path}/Geometry/R01_T01_Container")
        body.CreateRelationship("material:binding").SetTargets(
            [
                Sdf.Path(
                    f"{OUTPUT_CONTAINER_MATERIALS}/Container_"
                    f"{container_color}"
                )
            ]
        )
        body_count += 1
    return body_count


def add_recessed_cargo_hold(stage: Usd.Stage) -> None:
    """Create a visible recessed hold around the enlarged cargo footprint."""
    hold = UsdGeom.Xform.Define(stage, f"{OUTPUT_ROOT}/CargoHold")
    material_path = Sdf.Path(f"{OUTPUT_MATERIALS}/CargoHoldDarkSteel")
    material = UsdShade.Material.Define(stage, material_path)
    shader = UsdShade.Shader.Define(stage, f"{material_path}/PreviewSurface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(0.025, 0.045, 0.060)
    )
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.55)
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.48)
    shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput().ConnectToSource(shader.GetOutput("surface"))

    center_x = 0.5 * (CARGO_HOLD_X_MIN + CARGO_HOLD_X_MAX)
    half_x = 0.5 * (CARGO_HOLD_X_MAX - CARGO_HOLD_X_MIN)
    wall_center_z = 0.5 * (CARGO_HOLD_BOTTOM_Z + CARGO_HOLD_DECK_Z)
    wall_half_z = 0.5 * (CARGO_HOLD_DECK_Z - CARGO_HOLD_BOTTOM_Z)

    pieces = (
        ("HoldFloor", center_x, 0.0, CARGO_HOLD_BOTTOM_Z, 2.0 * half_x, 2.0 * CARGO_HOLD_Y_HALF, 0.30),
        ("HoldWallPort", center_x, -CARGO_HOLD_Y_HALF, wall_center_z, 2.0 * half_x, CARGO_HOLD_WALL_THICKNESS, 2.0 * wall_half_z),
        ("HoldWallStarboard", center_x, CARGO_HOLD_Y_HALF, wall_center_z, 2.0 * half_x, CARGO_HOLD_WALL_THICKNESS, 2.0 * wall_half_z),
        ("HoldWallBow", CARGO_HOLD_X_MAX, 0.0, wall_center_z, CARGO_HOLD_WALL_THICKNESS, 2.0 * CARGO_HOLD_Y_HALF, 2.0 * wall_half_z),
        ("HoldWallStern", CARGO_HOLD_X_MIN, 0.0, wall_center_z, CARGO_HOLD_WALL_THICKNESS, 2.0 * CARGO_HOLD_Y_HALF, 2.0 * wall_half_z),
    )
    for name, x, y, z, sx, sy, sz in pieces:
        cube = UsdGeom.Cube.Define(stage, f"{hold.GetPath()}/{name}")
        cube.CreateSizeAttr(1.0)
        cube.AddTranslateOp().Set(Gf.Vec3d(x, y, z))
        cube.AddScaleOp().Set(Gf.Vec3f(sx, sy, sz))
        cube.SetXformOpOrder(cube.GetOrderedXformOps())
        cube.GetPrim().CreateAttribute(
            "ship:cargoHoldPart", Sdf.ValueTypeNames.String, custom=True
        ).Set(name)
        UsdShade.MaterialBindingAPI(cube.GetPrim()).Bind(material)


def hide_cargo_hold_clearance_beams(stage: Usd.Stage) -> int:
    hidden = 0
    for prim in stage.Traverse():
        if any(
            prim.GetName().startswith(prefix)
            for prefix in HIDDEN_CARGO_HOLD_BEAM_PREFIXES
        ):
            prim.SetActive(False)
            hidden += 1
    return hidden


def add_driver_basement_and_bridge_deck(stage: Usd.Stage) -> None:
    material_path = f"{OUTPUT_MATERIALS}/DriverDeckSteel"
    material = UsdShade.Material.Define(stage, material_path)
    shader = UsdShade.Shader.Define(stage, f"{material_path}/PreviewSurface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(0.20, 0.24, 0.25)
    )
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.72)
    shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput().ConnectToSource(shader.GetOutput("surface"))

    x_center = 0.5 * (BRIDGE_DECK_X_MIN + BRIDGE_DECK_X_MAX)
    x_size = BRIDGE_DECK_X_MAX - BRIDGE_DECK_X_MIN

    # A solid platform around the elevated wheelhouse.  The original cargo
    # deck is intentionally open in this asset, so this platform restores the
    # walkable deck only where the bridge and its access area belong.
    pieces = (
        ("BridgeDeck", x_center, 0.0, BRIDGE_DECK_Z, x_size, 2.0 * BRIDGE_DECK_Y_HALF, 0.24),
        ("DriverBasementFloor", x_center, 0.0, DRIVER_BASEMENT_FLOOR_Z, x_size - 0.30, 2.0 * (BRIDGE_DECK_Y_HALF - 0.25), 0.18),
        ("DriverBasementPortWall", x_center, -BRIDGE_DECK_Y_HALF + 0.12, 6.05, x_size - 0.30, 0.24, 2.70),
        ("DriverBasementStarboardWall", x_center, BRIDGE_DECK_Y_HALF - 0.12, 6.05, x_size - 0.30, 0.24, 2.70),
        ("DriverBasementCargoSeparationWall", BRIDGE_DECK_X_MIN + 0.12, 0.0, 6.05, 0.24, 2.0 * (BRIDGE_DECK_Y_HALF - 0.12), 2.70),
    )
    basement = UsdGeom.Xform.Define(stage, f"{OUTPUT_ROOT}/DriverBasementAndBridgeDeck")
    for name, x, y, z, sx, sy, sz in pieces:
        cube = UsdGeom.Cube.Define(stage, f"{basement.GetPath()}/{name}")
        cube.CreateSizeAttr(1.0)
        cube.AddTranslateOp().Set(Gf.Vec3d(x, y, z))
        cube.AddScaleOp().Set(Gf.Vec3f(sx, sy, sz))
        cube.SetXformOpOrder(cube.GetOrderedXformOps())
        cube.GetPrim().CreateAttribute(
            "ship:bridgeAreaPart", Sdf.ValueTypeNames.String, custom=True
        ).Set(name)
        UsdShade.MaterialBindingAPI(cube.GetPrim()).Bind(material)


def add_cargo_hold_perimeter_deck(stage: Usd.Stage) -> None:
    material = UsdShade.Material.Get(
        stage, Sdf.Path(f"{OUTPUT_MATERIALS}/DriverDeckSteel")
    )
    deck = UsdGeom.Xform.Define(stage, f"{OUTPUT_ROOT}/CargoHoldPerimeterDeck")
    x_center = 0.5 * (CARGO_HOLD_X_MIN + CARGO_HOLD_X_MAX)
    x_span = CARGO_HOLD_X_MAX - CARGO_HOLD_X_MIN
    y_edge = CARGO_HOLD_Y_HALF + 0.55
    pieces = (
        ("PortWalkway", x_center, -y_edge, BRIDGE_DECK_Z, x_span, 1.0, 0.24),
        ("StarboardWalkway", x_center, y_edge, BRIDGE_DECK_Z, x_span, 1.0, 0.24),
        ("SternWalkway", CARGO_HOLD_X_MIN - 0.55, 0.0, BRIDGE_DECK_Z, 0.9, 2.0 * (CARGO_HOLD_Y_HALF + 0.55), 0.24),
        ("BowWalkway", CARGO_HOLD_X_MAX + 0.55, 0.0, BRIDGE_DECK_Z, 0.9, 2.0 * (CARGO_HOLD_Y_HALF + 0.55), 0.24),
        ("AftDeckFill", 0.5 * (AFT_DECK_X_MIN + CARGO_HOLD_X_MIN), 0.0, BRIDGE_DECK_Z, CARGO_HOLD_X_MIN - AFT_DECK_X_MIN, 2.0 * (CARGO_HOLD_Y_HALF + 0.55), 0.24),
    )
    for name, x, y, z, sx, sy, sz in pieces:
        cube = UsdGeom.Cube.Define(stage, f"{deck.GetPath()}/{name}")
        cube.CreateSizeAttr(1.0)
        cube.AddTranslateOp().Set(Gf.Vec3d(x, y, z))
        cube.AddScaleOp().Set(Gf.Vec3f(sx, sy, sz))
        cube.SetXformOpOrder(cube.GetOrderedXformOps())
        cube.GetPrim().CreateAttribute(
            "ship:cargoPerimeterDeck", Sdf.ValueTypeNames.String, custom=True
        ).Set(name)
        UsdShade.MaterialBindingAPI(cube.GetPrim()).Bind(material)


def add_bridge_access_deck(stage: Usd.Stage) -> None:
    material_path = f"{OUTPUT_MATERIALS}/CargoAccessDeck"
    material = UsdShade.Material.Define(stage, material_path)
    shader = UsdShade.Shader.Define(stage, f"{material_path}/PreviewSurface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(0.30, 0.34, 0.35)
    )
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.68)
    shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput().ConnectToSource(shader.GetOutput("surface"))

    deck = UsdGeom.Cube.Define(
        stage, f"{OUTPUT_ROOT}/DriverBasementAndBridgeDeck/BridgeAccessDeck"
    )
    deck.CreateSizeAttr(1.0)
    deck.AddTranslateOp().Set(Gf.Vec3d(5.95, 0.0, 7.43))
    deck.AddScaleOp().Set(Gf.Vec3f(4.7, 12.2, 0.18))
    deck.SetXformOpOrder(deck.GetOrderedXformOps())
    deck.GetPrim().CreateAttribute(
        "ship:bridgeAreaPart", Sdf.ValueTypeNames.String, custom=True
    ).Set("BridgeAccessDeck")
    UsdShade.MaterialBindingAPI(deck.GetPrim()).Bind(material)


def reposition_cargo_cell_frames(stage: Usd.Stage) -> int:
    hidden = 0
    for prim in stage.Traverse():
        if prim.GetName().startswith(HIDDEN_CARGO_CELL_FRAME_PREFIX):
            prim.SetActive(False)
            hidden += 1
    return hidden


def hide_floating_ship_components(stage: Usd.Stage) -> int:
    hidden = 0
    for prim in stage.Traverse():
        if any(
            prim.GetName().startswith(prefix)
            for prefix in HIDDEN_FLOATING_SHIP_COMPONENT_PREFIXES
        ):
            prim.SetActive(False)
            hidden += 1
    return hidden


def hide_stern_side_shells(stage: Usd.Stage) -> int:
    hidden = 0
    for prim in stage.Traverse():
        if prim.GetName() in HIDDEN_STERN_SIDE_SHELL_NAMES:
            prim.SetActive(False)
            hidden += 1
    return hidden


def hide_named_prims(stage: Usd.Stage) -> int:
    hidden = 0
    for prim in stage.Traverse():
        if prim.GetName() in HIDDEN_PRIM_NAMES:
            prim.SetActive(False)
            hidden += 1
    return hidden


def build() -> dict[str, object]:
    source = Usd.Stage.Open(str(SOURCE_ASSET), Usd.Stage.LoadAll)
    if source is None:
        raise RuntimeError(f"Could not open source asset: {SOURCE_ASSET}")
    yard = Usd.Stage.Open(str(YARD_CONTAINER_STAGE), Usd.Stage.LoadAll)
    if yard is None:
        raise RuntimeError(f"Could not open yard container stage: {YARD_CONTAINER_STAGE}")

    OUTPUT_ASSET.parent.mkdir(parents=True, exist_ok=True)
    if OUTPUT_ASSET.exists():
        OUTPUT_ASSET.unlink()
    stage = Usd.Stage.CreateNew(str(OUTPUT_ASSET))
    stage.SetMetadata("metersPerUnit", 1.0)
    stage.SetMetadata("upAxis", UsdGeom.Tokens.z)

    root = UsdGeom.Xform.Define(stage, OUTPUT_ROOT)
    stage.SetDefaultPrim(root.GetPrim())
    model = UsdGeom.Xform.Define(stage, OUTPUT_MODEL)

    source_world = source.GetPrimAtPath("/World")
    selected_children = [
        child
        for child in source_world.GetChildren()
        if any(child.GetName().startswith(prefix) for prefix in SOURCE_PREFIXES)
    ]
    if not selected_children:
        raise RuntimeError("No authored feeder ship components matched SOURCE_PREFIXES")

    # Recompute the transform from the complete selected ship, so adding hull
    # or bow pieces cannot change its placement relative to the scene.
    bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    min_point = Gf.Vec3d(float("inf"), float("inf"), float("inf"))
    max_point = Gf.Vec3d(float("-inf"), float("-inf"), float("-inf"))
    for child in selected_children:
        bounds = bbox_cache.ComputeWorldBound(child).ComputeAlignedRange()
        if bounds.IsEmpty():
            continue
        minimum = bounds.GetMin()
        maximum = bounds.GetMax()
        min_point = Gf.Vec3d(
            min(min_point[0], minimum[0]),
            min(min_point[1], minimum[1]),
            min(min_point[2], minimum[2]),
        )
        max_point = Gf.Vec3d(
            max(max_point[0], maximum[0]),
            max(max_point[1], maximum[1]),
            max(max_point[2], maximum[2]),
        )
    center = (min_point + max_point) * 0.5
    model_offset = Gf.Vec3d(-center[0], -center[1], -min_point[2])
    # USD evaluates this translate/scale order with the local geometry
    # scaling before the translation. Scale the centering offset as well so
    # the enlarged ship remains centered at the same asset origin.
    model.AddTranslateOp().Set(model_offset * SHIP_SCALE)
    model.AddScaleOp().Set(Gf.Vec3d(SHIP_SCALE, SHIP_SCALE, SHIP_SCALE))

    copied = 0
    for child in selected_children:
        if is_container_component(child.GetName()):
            continue
        Sdf.CopySpec(
            source.GetRootLayer(),
            child.GetPath(),
            stage.GetRootLayer(),
            Sdf.Path(f"{OUTPUT_MODEL}/{child.GetName()}"),
        )
        copied += 1

    # Open the original main cargo-deck surface so the recessed hold and its
    # containers remain visible from above. The surrounding hull/deck pieces
    # are kept intact.
    cargo_deck = stage.OverridePrim(HIDDEN_CARGO_DECK_PRIM)
    if not cargo_deck.IsValid():
        raise RuntimeError(f"Missing cargo deck prim: {HIDDEN_CARGO_DECK_PRIM}")
    cargo_deck.SetActive(False)

    Sdf.CopySpec(
        source.GetRootLayer(),
        Sdf.Path(SOURCE_MATERIALS),
        stage.GetRootLayer(),
        Sdf.Path(OUTPUT_MATERIALS),
    )
    Sdf.CopySpec(
        yard.GetRootLayer(),
        Sdf.Path(CONTAINER_MATERIALS),
        stage.GetRootLayer(),
        Sdf.Path(OUTPUT_CONTAINER_MATERIALS),
    )
    container_count = add_container_instances(
        source, yard, stage, selected_children
    )
    hidden_beam_count = hide_cargo_hold_clearance_beams(stage)
    add_recessed_cargo_hold(stage)
    add_driver_basement_and_bridge_deck(stage)
    add_cargo_hold_perimeter_deck(stage)
    add_bridge_access_deck(stage)
    hidden_cell_frame_count = reposition_cargo_cell_frames(stage)
    hidden_floating_component_count = hide_floating_ship_components(stage)
    hidden_stern_shell_count = hide_stern_side_shells(stage)
    hidden_named_prim_count = hide_named_prims(stage)
    for prim in stage.Traverse():
        for relationship in prim.GetRelationships():
            targets = relationship.GetTargets()
            remapped = [remap_source_paths(path) for path in targets]
            if remapped != targets:
                relationship.SetTargets(remapped)
        for attribute in prim.GetAttributes():
            connections = attribute.GetConnections()
            remapped = [remap_source_paths(path) for path in connections]
            if remapped != connections:
                attribute.SetConnections(remapped)

    root.GetPrim().CreateAttribute(
        "ship:sourceAsset", Sdf.ValueTypeNames.String, custom=True
    ).Set(str(SOURCE_ASSET))
    root.GetPrim().CreateAttribute(
        "ship:sourceComponentCount", Sdf.ValueTypeNames.Int, custom=True
    ).Set(copied)
    root.GetPrim().CreateAttribute(
        "ship:containerInstanceCount", Sdf.ValueTypeNames.Int, custom=True
    ).Set(container_count)
    stage.GetRootLayer().Save()
    return {
        "output": str(OUTPUT_ASSET),
        "component_count": copied,
        "container_instance_count": container_count,
        "hidden_cargo_hold_beam_count": hidden_beam_count,
        "hidden_cargo_cell_frame_count": hidden_cell_frame_count,
        "hidden_floating_ship_component_count": hidden_floating_component_count,
        "hidden_stern_shell_count": hidden_stern_shell_count,
        "hidden_named_prim_count": hidden_named_prim_count,
    }


if __name__ == "__main__":
    print(f"CUSTOM_FEEDER_SHIP_RESULT {build()}")
