"""Generate lightweight live container stacks from bay-map strings.

Example:
    blender --background --python omniverse/scripts/build_live_containers.py -- \
        --bay 1C/004 --pattern 123456 --container-size-ft 40
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from typing import Any

from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.append(str(SCRIPT_DIR))

from yard_coordinate_mapping import (
    BAY_GAP_M,
    BAY_LENGTH_M,
    BAY_WIDTH_M,
    TRUCK_OPERATING_LANE_CENTER_X_M,
    YARD_LAYOUT_START_Y_M,
    YARD_TOTAL_LENGTH_M,
    bay_scene_y_m,
    bay_id_scene_y_m,
    parse_bay_id,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = PROJECT_ROOT / "omniverse" / "scenes" / "live_containers.usda"
CONTAINER_ASSET_PATH = (
    PROJECT_ROOT / "omniverse" / "assets" / "resource" / "container" / "Shipping_Container.usdz"
)
# The textured USDZ is useful for branded close-ups, but the terminal yard
# view uses the deterministic per-container colour palette below. Keeping this
# switch explicit makes it possible to restore the branded asset later without
# changing inventory layout or control layers.
USE_TEXTURED_CONTAINER_MODEL = False

# Shipping_Container.usdz is authored in centimetre scene units with Y-up
# coordinates.  USD references do not automatically convert the referenced
# mesh's scene units to the root stage's metres, so the source dimensions here
# must stay in the asset's raw units.  Using metre values (2.4/2.902/6.015)
# makes the imported model roughly 100x too large in the metre-based yard.
# X is width, Y is height, and Z is the long container axis.  The live yard
# stage uses metres, Z-up coordinates, and the container length on Y.
CONTAINER_ASSET_SOURCE_SIZE_UNITS = Gf.Vec3f(240.0, 290.2, 601.5)
CONTAINER_ASSET_ROTATE_X_DEG = 90.0

LIVE_ROOT_PATH = "/World/LiveContainers"
PAD_TOP_Z_M = 0.10

CONTAINER_WIDTH_X_M = 1.306287
CONTAINER_LENGTH_20FT_Y_M = BAY_LENGTH_M
CONTAINER_LENGTH_40FT_Y_M = BAY_LENGTH_M * 2.0 + BAY_GAP_M
CONTAINER_LENGTH_Y_M = CONTAINER_LENGTH_20FT_Y_M
CONTAINER_VISUAL_HEIGHT_Z_M = 0.904353
CONTAINER_ACTUAL_HEIGHT_M = 2.60
MAX_STACK_TIERS = 6
ROW_COUNT = 6
MAX_STACK_HEIGHT_Z_M = CONTAINER_ACTUAL_HEIGHT_M * MAX_STACK_TIERS
ROW_GAP_X_M = 0.20
LEFT_INNER_SAFETY_X_M = 1.20
TRUCK_LANE_WIDTH_X_M = 5.00
# Shift the painted truck lane and its task parking line toward the container
# side without changing the rendered stack-row coordinates.
TRUCK_LANE_LATERAL_OFFSET_X_M = 0.90
RIGHT_INNER_SAFETY_X_M = 1.50
REFERENCE_LEFT_INNER_SAFETY_X_M = 2.00
REFERENCE_TRUCK_LANE_WIDTH_X_M = 3.20
REFERENCE_RIGHT_INNER_SAFETY_X_M = 2.60
REFERENCE_ROW_GAP_X_M = 0.12
STACK_FLOOR_CLEARANCE_M = 0.02
TIER_GAP_Z_M = 0.035
GROUND_MARKING_Z_M = PAD_TOP_Z_M + 0.045

CONTAINER_COLORS = (
    ("weathered_blue", Gf.Vec3f(0.04, 0.21, 0.48)),
    ("oxide_red", Gf.Vec3f(0.50, 0.11, 0.055)),
    ("warm_gray", Gf.Vec3f(0.47, 0.50, 0.46)),
    ("faded_teal", Gf.Vec3f(0.07, 0.36, 0.34)),
    ("sand_yellow", Gf.Vec3f(0.66, 0.52, 0.20)),
    # Keep the darkest test-box color readable under RTG shadows; the previous
    # near-black navy made a complete side face look missing in Composer.
    ("dark_navy", Gf.Vec3f(0.12, 0.24, 0.42)),
)
CONTAINER_MATERIAL_TRAITS = {
    "weathered_blue": (0.76, 0.08),
    "oxide_red": (0.82, 0.06),
    "warm_gray": (0.70, 0.10),
    "faded_teal": (0.79, 0.07),
    "sand_yellow": (0.74, 0.05),
    "dark_navy": (0.72, 0.10),
}


def reference_first_row_center_x_m(row_count: int) -> float:
    """Keep row 1 at the pre-tightening visual position.

    The operator reads rows from right to left.  Earlier visual checks approved
    the rightmost row position, so the tightened layout compresses rows 2..N
    toward the truck lane while row 1 stays anchored.
    """

    reference_usable_width = (
        BAY_WIDTH_M
        - REFERENCE_LEFT_INNER_SAFETY_X_M
        - REFERENCE_TRUCK_LANE_WIDTH_X_M
        - REFERENCE_RIGHT_INNER_SAFETY_X_M
    )
    reference_row_width = (
        reference_usable_width - (row_count - 1) * REFERENCE_ROW_GAP_X_M
    ) / row_count
    reference_row_pitch = reference_row_width + REFERENCE_ROW_GAP_X_M
    reference_leftmost_stack_x = (
        -BAY_WIDTH_M * 0.5
        + REFERENCE_LEFT_INNER_SAFETY_X_M
        + REFERENCE_TRUCK_LANE_WIDTH_X_M
        + reference_row_width * 0.5
    )
    return reference_leftmost_stack_x + (row_count - 1) * reference_row_pitch


def row_center_x_m(row_number: int, row_count: int = ROW_COUNT) -> float:
    """Return a rendered row centre using the same numbering as PLC tasks."""

    row = int(row_number)
    count = int(row_count)
    if not 1 <= row <= count:
        raise ValueError(f"Row {row} is outside 1-{count}")
    pitch = CONTAINER_WIDTH_X_M + ROW_GAP_X_M
    row_1_x = reference_first_row_center_x_m(count)
    return row_1_x - (row - 1) * pitch


def truck_lane_bounds_x() -> tuple[float, float]:
    """Return the laterally shifted live truck-lane edge coordinates."""

    lane_left_x = (
        -BAY_WIDTH_M * 0.5
        + LEFT_INNER_SAFETY_X_M
        + TRUCK_LANE_LATERAL_OFFSET_X_M
    )
    return lane_left_x, lane_left_x + TRUCK_LANE_WIDTH_X_M


def parse_pattern(pattern: str) -> list[int]:
    values = []
    for char in pattern.strip():
        if not char.isdigit():
            raise ValueError(f"Container pattern may only contain digits: {pattern!r}")
        value = int(char)
        if value > MAX_STACK_TIERS:
            raise ValueError(
                f"Container stack tier {value} is higher than the {MAX_STACK_TIERS}-tier limit"
            )
        values.append(value)
    if not values:
        raise ValueError("Container pattern cannot be empty")
    if len(values) != ROW_COUNT:
        raise ValueError(f"Container pattern must contain exactly {ROW_COUNT} rows")
    return values


def material(stage: Usd.Stage, name: str, color: Gf.Vec3f) -> UsdShade.Material:
    material_path = f"{LIVE_ROOT_PATH}/Looks/{name}"
    existing = UsdShade.Material.Get(stage, Sdf.Path(material_path))
    if existing:
        return existing
    mat = UsdShade.Material.Define(stage, material_path)
    shader = UsdShade.Shader.Define(stage, f"{material_path}/PreviewSurface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(color)
    profile = next(
        (profile_name for profile_name, _ in CONTAINER_COLORS if name.endswith(profile_name)),
        "",
    )
    roughness, metallic = CONTAINER_MATERIAL_TRAITS.get(profile, (0.72, 0.07))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness)
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(metallic)
    mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return mat


def imported_container_model_available() -> bool:
    return CONTAINER_ASSET_PATH.is_file()


def _container_asset_reference_path(stage: Usd.Stage) -> str:
    """Use a project-relative asset path whenever the stage has a file path."""

    if stage.GetEditTarget().GetLayer().anonymous:
        return CONTAINER_ASSET_PATH.as_posix()

    identifier = stage.GetRootLayer().identifier
    if identifier and not identifier.startswith("anon:"):
        try:
            return Path(
                os.path.relpath(
                    CONTAINER_ASSET_PATH,
                    Path(identifier).resolve().parent,
                )
            ).as_posix()
        except (OSError, ValueError):
            pass
    return CONTAINER_ASSET_PATH.as_posix()


def create_imported_container_model(
    stage: Usd.Stage,
    path: str,
    center: Gf.Vec3d,
    scale: Gf.Vec3f,
) -> bool:
    """Reference the textured container and fit it to the live visual box."""

    if not imported_container_model_available():
        return False
    model_path = f"{path}_ImportedModel"
    model = UsdGeom.Xform.Define(stage, model_path)
    prim = model.GetPrim()
    prim.GetReferences().ClearReferences()
    prim.GetReferences().AddReference(_container_asset_reference_path(stage))

    xform = UsdGeom.Xformable(prim)
    xform.ClearXformOpOrder()
    # The source is centred on X/Z but starts at Y=0.  Translate after the
    # axis conversion so the imported model sits on the requested box centre.
    _get_or_add_xform_op(
        xform,
        "xformOp:translate",
        xform.AddTranslateOp,
    ).Set(
        Gf.Vec3d(center[0], center[1], center[2] - float(scale[2]) * 0.5)
    )
    _get_or_add_xform_op(
        xform,
        "xformOp:rotateX",
        xform.AddRotateXOp,
    ).Set(CONTAINER_ASSET_ROTATE_X_DEG)
    _get_or_add_xform_op(
        xform,
        "xformOp:scale",
        xform.AddScaleOp,
    ).Set(
        Gf.Vec3f(
            float(scale[0]) / float(CONTAINER_ASSET_SOURCE_SIZE_UNITS[0]),
            float(scale[2]) / float(CONTAINER_ASSET_SOURCE_SIZE_UNITS[1]),
            float(scale[1]) / float(CONTAINER_ASSET_SOURCE_SIZE_UNITS[2]),
        )
    )
    resolved = any(model.GetPrim().GetChildren())
    if resolved:
        # Hundreds of yard boxes share one mesh/material prototype while each
        # container keeps its own root transform and live metadata.
        prim.SetInstanceable(True)
    return resolved


def set_cube_transform(cube: UsdGeom.Cube, center: Gf.Vec3d, scale: Gf.Vec3f) -> None:
    cube.CreateSizeAttr(1.0)
    xform = UsdGeom.Xformable(cube.GetPrim())
    # Inventory bays are rebuilt on stable prim paths.  Clear any old release
    # rotation from weaker layers before authoring the fresh stack transform.
    xform.ClearXformOpOrder()
    _get_or_add_xform_op(
        xform,
        "xformOp:translate",
        xform.AddTranslateOp,
    ).Set(center)
    _get_or_add_xform_op(
        xform,
        "xformOp:scale",
        xform.AddScaleOp,
    ).Set(scale)


def _get_or_add_xform_op(
    xform: UsdGeom.Xformable,
    op_name: str,
    add_op: Any,
) -> UsdGeom.XformOp:
    ordered_ops = list(xform.GetOrderedXformOps())
    for op in ordered_ops:
        if op.GetOpName() == op_name:
            return op
    attr = xform.GetPrim().GetAttribute(op_name)
    if attr:
        op = UsdGeom.XformOp(attr)
        ordered_names = {ordered_op.GetOpName() for ordered_op in ordered_ops}
        if op_name not in ordered_names:
            xform.SetXformOpOrder(ordered_ops + [op])
        return op
    return add_op()


def create_container_box(
    stage: Usd.Stage,
    path: str,
    center: Gf.Vec3d,
    scale: Gf.Vec3f,
    mat: UsdShade.Material,
    *,
    bay_id: str,
    row: int,
    tier: int,
    tiers: int,
) -> None:
    cube = UsdGeom.Cube.Define(stage, path)
    set_cube_transform(cube, center, scale)
    prim = cube.GetPrim()
    UsdShade.MaterialBindingAPI.Apply(prim).Bind(mat)
    if USE_TEXTURED_CONTAINER_MODEL and imported_container_model_available():
        # Keep the established root prim and metadata for task logic, while
        # the referenced USDZ supplies the visible, textured container.
        if create_imported_container_model(stage, path, center, scale):
            UsdGeom.Imageable(prim).CreateVisibilityAttr().Set(
                UsdGeom.Tokens.invisible
            )
    prim.CreateAttribute("live:bayId", Sdf.ValueTypeNames.String).Set(bay_id)
    prim.CreateAttribute("live:row", Sdf.ValueTypeNames.Int).Set(row)
    prim.CreateAttribute("live:tier", Sdf.ValueTypeNames.Int).Set(tier)
    prim.CreateAttribute("live:tiers", Sdf.ValueTypeNames.Int).Set(tiers)
    prim.CreateAttribute("live:source", Sdf.ValueTypeNames.Token).Set("bay_map")
    _clear_manual_release_state(prim)


def _clear_manual_release_state(prim: Usd.Prim) -> None:
    prim.CreateAttribute("live:manualReleaseMode", Sdf.ValueTypeNames.Token).Set("")
    prim.CreateAttribute("live:manualSupportPath", Sdf.ValueTypeNames.String).Set("")
    prim.CreateAttribute("live:manualToppled", Sdf.ValueTypeNames.Bool).Set(False)
    prim.CreateAttribute("live:manualWorldBoundsMin", Sdf.ValueTypeNames.String).Set("")
    prim.CreateAttribute("live:manualWorldBoundsMax", Sdf.ValueTypeNames.String).Set("")


def create_colored_cube(
    stage: Usd.Stage,
    path: str,
    center: Gf.Vec3d,
    scale: Gf.Vec3f,
    color: Gf.Vec3f,
) -> None:
    cube = UsdGeom.Cube.Define(stage, path)
    set_cube_transform(cube, center, scale)
    cube.CreateDisplayColorAttr([color])


def _mix_color(color: Gf.Vec3f, target: Gf.Vec3f, amount: float) -> Gf.Vec3f:
    return Gf.Vec3f(
        color[0] * (1.0 - amount) + target[0] * amount,
        color[1] * (1.0 - amount) + target[1] * amount,
        color[2] * (1.0 - amount) + target[2] * amount,
    )


def _material_color(mat: UsdShade.Material | None) -> Gf.Vec3f:
    if mat:
        shader = UsdShade.Shader.Get(
            mat.GetPrim().GetStage(),
            mat.GetPath().AppendChild("PreviewSurface"),
        )
        value = shader.GetInput("diffuseColor").Get() if shader else None
        if value:
            return Gf.Vec3f(value[0], value[1], value[2])
    return Gf.Vec3f(0.18, 0.31, 0.38)


def _append_box_mesh(
    points: list[Gf.Vec3f],
    face_counts: list[int],
    face_indices: list[int],
    center: tuple[float, float, float],
    size: tuple[float, float, float],
) -> None:
    cx, cy, cz = center
    hx, hy, hz = (dimension * 0.5 for dimension in size)
    start = len(points)
    points.extend(
        Gf.Vec3f(*point)
        for point in (
            (cx - hx, cy - hy, cz - hz),
            (cx + hx, cy - hy, cz - hz),
            (cx + hx, cy + hy, cz - hz),
            (cx - hx, cy + hy, cz - hz),
            (cx - hx, cy - hy, cz + hz),
            (cx + hx, cy - hy, cz + hz),
            (cx + hx, cy + hy, cz + hz),
            (cx - hx, cy + hy, cz + hz),
        )
    )
    faces = (
        (0, 1, 2, 3),
        (4, 7, 6, 5),
        (0, 4, 5, 1),
        (1, 5, 6, 2),
        (2, 6, 7, 3),
        (4, 0, 3, 7),
    )
    for face in faces:
        face_counts.append(4)
        face_indices.extend(start + index for index in face)


def create_colored_box_cluster(
    stage: Usd.Stage,
    path: str,
    origin: Gf.Vec3d,
    boxes: list[tuple[tuple[float, float, float], tuple[float, float, float]]],
    color: Gf.Vec3f,
) -> None:
    if not boxes:
        return
    points: list[Gf.Vec3f] = []
    face_counts: list[int] = []
    face_indices: list[int] = []
    for box_center, box_size in boxes:
        _append_box_mesh(points, face_counts, face_indices, box_center, box_size)
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr(points)
    mesh.CreateFaceVertexCountsAttr(face_counts)
    mesh.CreateFaceVertexIndicesAttr(face_indices)
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    mesh.CreateDisplayColorAttr([color])
    xform = UsdGeom.Xformable(mesh.GetPrim())
    _get_or_add_xform_op(
        xform,
        "xformOp:translate",
        xform.AddTranslateOp,
    ).Set(origin)


def create_container_details(
    stage: Usd.Stage,
    path: str,
    center: Gf.Vec3d,
    scale: Gf.Vec3f,
    mat: UsdShade.Material | None = None,
) -> None:
    imported_model = stage.GetPrimAtPath(f"{path}_ImportedModel")
    if imported_model and any(imported_model.GetChildren()):
        return
    base = _material_color(mat)
    dark = _mix_color(base, Gf.Vec3f(0.012, 0.014, 0.016), 0.72)
    frame = _mix_color(base, Gf.Vec3f(0.035, 0.030, 0.026), 0.35)
    faded = _mix_color(base, Gf.Vec3f(0.76, 0.76, 0.70), 0.20)
    silver = Gf.Vec3f(0.58, 0.62, 0.62)
    hinge = Gf.Vec3f(0.30, 0.33, 0.33)
    corner = _mix_color(base, Gf.Vec3f(0.18, 0.12, 0.08), 0.25)
    hole = Gf.Vec3f(0.018, 0.020, 0.020)
    rust = Gf.Vec3f(0.42, 0.13, 0.035)
    half_x = float(scale[0]) * 0.5
    half_y = float(scale[1]) * 0.5
    half_z = float(scale[2]) * 0.5
    origin = Gf.Vec3d(center[0], center[1], center[2])

    top_z = center[2] + scale[2] * 0.5 + 0.012
    create_colored_cube(
        stage,
        f"{path}_WeatheredTop",
        Gf.Vec3d(center[0], center[1], top_z),
        Gf.Vec3f(scale[0] * 0.92, scale[1] * 0.94, 0.018),
        faded,
    )

    corrugation_boxes = []
    corrugation_count = 8 if float(scale[1]) < 3.8 else 16
    corrugation_spacing = float(scale[1]) * 0.88 / corrugation_count
    first_rib_y = -float(scale[1]) * 0.44 + corrugation_spacing * 0.5
    for rib_index in range(corrugation_count):
        rib_y = first_rib_y + rib_index * corrugation_spacing
        for side_sign in (-1.0, 1.0):
            corrugation_boxes.append(
                (
                    (side_sign * (half_x + 0.014), rib_y, 0.0),
                    (0.028, corrugation_spacing * 0.34, float(scale[2]) * 0.76),
                )
            )
    for side_sign in (-1.0, 1.0):
        for rail_z in (-half_z + 0.048, half_z - 0.048):
            corrugation_boxes.append(
                (
                    (side_sign * (half_x + 0.014), 0.0, rail_z),
                    (0.035, float(scale[1]) * 0.94, 0.055),
                )
            )
    create_colored_box_cluster(
        stage,
        f"{path}_SideCorrugation",
        origin,
        corrugation_boxes,
        frame,
    )

    roof_ridges = []
    roof_ridge_count = 6 if corrugation_count == 8 else 12
    roof_spacing = float(scale[1]) * 0.86 / roof_ridge_count
    first_roof_y = -float(scale[1]) * 0.43 + roof_spacing * 0.5
    for ridge_index in range(roof_ridge_count):
        roof_ridges.append(
            (
                (0.0, first_roof_y + ridge_index * roof_spacing, half_z + 0.025),
                (float(scale[0]) * 0.84, 0.025, 0.026),
            )
        )
    create_colored_box_cluster(
        stage,
        f"{path}_RoofRidges",
        origin,
        roof_ridges,
        frame,
    )

    for side_name, side_y in (
        ("Front", center[1] - scale[1] * 0.5 - 0.012),
        ("Back", center[1] + scale[1] * 0.5 + 0.012),
    ):
        side_sign = -1.0 if side_name == "Front" else 1.0
        for rib_index in range(1, 5):
            rib_x = center[0] - scale[0] * 0.5 + rib_index * scale[0] / 5.0
            create_colored_cube(
                stage,
                f"{path}_{side_name}_Rib_{rib_index:02d}",
                Gf.Vec3d(rib_x, side_y, center[2]),
                Gf.Vec3f(0.018, 0.025, scale[2] * 0.72),
                dark,
            )
        create_colored_cube(
            stage,
            f"{path}_{side_name}_BottomLine",
            Gf.Vec3d(center[0], side_y, center[2] - scale[2] * 0.5 + 0.06),
            Gf.Vec3f(scale[0] * 0.94, 0.025, 0.050),
            dark,
        )

        if side_name == "Back":
            end_wall_frame = [
                ((-half_x + 0.055, side_sign * (half_y + 0.024), 0.0), (0.085, 0.045, float(scale[2]) * 0.92)),
                ((half_x - 0.055, side_sign * (half_y + 0.024), 0.0), (0.085, 0.045, float(scale[2]) * 0.92)),
                ((0.0, side_sign * (half_y + 0.024), half_z - 0.045), (float(scale[0]) * 0.94, 0.045, 0.070)),
                ((0.0, side_sign * (half_y + 0.024), -half_z + 0.045), (float(scale[0]) * 0.94, 0.045, 0.070)),
            ]
            create_colored_box_cluster(
                stage,
                f"{path}_{side_name}_EndWallFrame",
                origin,
                end_wall_frame,
                frame,
            )
            continue

        door_frame_boxes = [
            ((-half_x + 0.055, side_sign * (half_y + 0.024), 0.0), (0.085, 0.045, float(scale[2]) * 0.92)),
            ((half_x - 0.055, side_sign * (half_y + 0.024), 0.0), (0.085, 0.045, float(scale[2]) * 0.92)),
            ((0.0, side_sign * (half_y + 0.024), half_z - 0.045), (float(scale[0]) * 0.94, 0.045, 0.070)),
            ((0.0, side_sign * (half_y + 0.024), -half_z + 0.045), (float(scale[0]) * 0.94, 0.045, 0.070)),
            ((0.0, side_sign * (half_y + 0.030), 0.0), (0.032, 0.032, float(scale[2]) * 0.82)),
        ]
        for cross_z in (-0.19 * float(scale[2]), 0.12 * float(scale[2])):
            door_frame_boxes.append(
                ((0.0, side_sign * (half_y + 0.020), cross_z), (float(scale[0]) * 0.78, 0.030, 0.045))
            )
        create_colored_box_cluster(
            stage,
            f"{path}_{side_name}_DoorFrame",
            origin,
            door_frame_boxes,
            frame,
        )

        lock_boxes = []
        for lock_x in (-0.37, -0.13, 0.13, 0.37):
            scaled_x = lock_x * float(scale[0])
            lock_boxes.append(
                ((scaled_x, side_sign * (half_y + 0.052), 0.0), (0.025, 0.026, float(scale[2]) * 0.78))
            )
            lock_boxes.append(
                ((scaled_x, side_sign * (half_y + 0.058), -half_z * 0.25), (0.10, 0.030, 0.026))
            )
        create_colored_box_cluster(
            stage,
            f"{path}_{side_name}_LockingBars",
            origin,
            lock_boxes,
            silver,
        )
        create_colored_cube(
            stage,
            f"{path}_{side_name}_DataPlate",
            Gf.Vec3d(
                center[0] - float(scale[0]) * 0.28,
                center[1] + side_sign * (half_y + 0.060),
                center[2] - float(scale[2]) * 0.22,
            ),
            Gf.Vec3f(float(scale[0]) * 0.13, 0.018, float(scale[2]) * 0.17),
            silver,
        )

        hinge_boxes = []
        for hinge_x in (-half_x + 0.085, half_x - 0.085):
            for hinge_z in (-half_z * 0.55, 0.0, half_z * 0.55):
                hinge_boxes.append(
                    ((hinge_x, side_sign * (half_y + 0.058), hinge_z), (0.095, 0.035, 0.055))
                )
        create_colored_box_cluster(
            stage,
            f"{path}_{side_name}_DoorHinges",
            origin,
            hinge_boxes,
            hinge,
        )

    corner_boxes = []
    corner_holes = []
    for corner_x in (-half_x + 0.045, half_x - 0.045):
        for corner_y in (-half_y + 0.045, half_y - 0.045):
            for corner_z in (-half_z + 0.045, half_z - 0.045):
                corner_boxes.append(
                    ((corner_x, corner_y, corner_z), (0.115, 0.115, 0.105))
                )
                corner_holes.append(
                    ((corner_x, corner_y + (0.060 if corner_y > 0 else -0.060), corner_z), (0.040, 0.018, 0.042))
                )
    create_colored_box_cluster(stage, f"{path}_CornerCastings", origin, corner_boxes, corner)
    create_colored_box_cluster(stage, f"{path}_CornerCastingHoles", origin, corner_holes, hole)

    wear_boxes = [
        ((-half_x - 0.018, -half_y * 0.18, -half_z * 0.28), (0.018, float(scale[1]) * 0.08, float(scale[2]) * 0.09)),
        ((half_x + 0.018, half_y * 0.27, half_z * 0.22), (0.018, float(scale[1]) * 0.06, float(scale[2]) * 0.07)),
    ]
    create_colored_box_cluster(stage, f"{path}_SurfaceWear", origin, wear_boxes, rust)


def create_layout_outline(
    stage: Usd.Stage, path: str, x_min: float, x_max: float, y_min: float, y_max: float
) -> None:
    points = [
        Gf.Vec3f(x_min, y_min, PAD_TOP_Z_M + 0.08),
        Gf.Vec3f(x_max, y_min, PAD_TOP_Z_M + 0.08),
        Gf.Vec3f(x_max, y_max, PAD_TOP_Z_M + 0.08),
        Gf.Vec3f(x_min, y_max, PAD_TOP_Z_M + 0.08),
        Gf.Vec3f(x_min, y_min, PAD_TOP_Z_M + 0.08),
    ]
    curves = UsdGeom.BasisCurves.Define(stage, path)
    curves.CreateTypeAttr(UsdGeom.Tokens.linear)
    curves.CreateBasisAttr(UsdGeom.Tokens.bezier)
    curves.CreateWrapAttr(UsdGeom.Tokens.nonperiodic)
    curves.CreateCurveVertexCountsAttr([len(points)])
    curves.CreatePointsAttr(points)
    curves.CreateWidthsAttr([0.08])
    curves.SetWidthsInterpolation(UsdGeom.Tokens.constant)
    curves.CreateDisplayColorAttr([Gf.Vec3f(0.94, 0.90, 0.72)])


def create_ground_polyline(
    stage: Usd.Stage,
    path: str,
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
    color: Gf.Vec3f,
    width: float = 0.08,
) -> None:
    points: list[Gf.Vec3f] = []
    counts: list[int] = []
    z = GROUND_MARKING_Z_M + 0.035
    for start, end in segments:
        points.append(Gf.Vec3f(start[0], start[1], z))
        points.append(Gf.Vec3f(end[0], end[1], z))
        counts.append(2)
    curves = UsdGeom.BasisCurves.Define(stage, path)
    curves.CreateTypeAttr(UsdGeom.Tokens.linear)
    curves.CreateBasisAttr(UsdGeom.Tokens.bezier)
    curves.CreateWrapAttr(UsdGeom.Tokens.nonperiodic)
    curves.CreateCurveVertexCountsAttr(counts)
    curves.CreatePointsAttr(points)
    curves.CreateWidthsAttr([width])
    curves.SetWidthsInterpolation(UsdGeom.Tokens.constant)
    curves.CreateDisplayColorAttr([color])


def create_truck_lane_arrow(
    stage: Usd.Stage,
    path: str,
    center_x: float,
    center_y: float,
    size: float,
) -> None:
    white = Gf.Vec3f(0.98, 0.98, 0.90)
    create_ground_polyline(
        stage,
        path,
        [
            ((center_x, center_y - size * 0.46), (center_x, center_y + size * 0.38)),
            ((center_x, center_y + size * 0.38), (center_x - size * 0.26, center_y + size * 0.10)),
            ((center_x, center_y + size * 0.38), (center_x + size * 0.26, center_y + size * 0.10)),
        ],
        white,
        width=0.10,
    )


def create_ground_bar(
    stage: Usd.Stage,
    path: str,
    center: Gf.Vec3d,
    scale: Gf.Vec3f,
    color: Gf.Vec3f,
) -> None:
    create_colored_cube(stage, path, center, scale, color)


def create_truck_lane_markings(stage: Usd.Stage, path: str) -> None:
    yard_lane_y_min = YARD_LAYOUT_START_Y_M
    yard_lane_y_max = YARD_LAYOUT_START_Y_M + YARD_TOTAL_LENGTH_M
    yard_lane_center_y = (yard_lane_y_min + yard_lane_y_max) * 0.5
    yard_lane_length = yard_lane_y_max - yard_lane_y_min
    lane_left_x, lane_right_x = truck_lane_bounds_x()
    lane_mid_x = (lane_left_x + lane_right_x) * 0.5
    truck_lane_width = lane_right_x - lane_left_x

    create_ground_bar(
        stage,
        f"{path}/TruckLaneAsphaltSurface_1C4C_Through",
        Gf.Vec3d(lane_mid_x, yard_lane_center_y, GROUND_MARKING_Z_M - 0.012),
        Gf.Vec3f(truck_lane_width + 0.20, yard_lane_length, 0.012),
        Gf.Vec3f(0.085, 0.095, 0.090),
    )
    arrow_spacing_m = 60.0
    arrow_count = max(3, int(yard_lane_length / arrow_spacing_m))
    for arrow_index in range(arrow_count):
        arrow_y = yard_lane_y_min + (arrow_index + 0.5) * yard_lane_length / arrow_count
        create_truck_lane_arrow(
            stage,
            f"{path}/TruckLaneForwardArrow_1C4C_{arrow_index + 1:02d}",
            lane_mid_x,
            arrow_y,
            1.05,
        )

SEGMENT_DEFS = {
    "A": (0.0, 0.5, 0.55, 0.075),
    "B": (0.275, 0.25, 0.075, 0.50),
    "C": (0.275, -0.25, 0.075, 0.50),
    "D": (0.0, -0.5, 0.55, 0.075),
    "E": (-0.275, -0.25, 0.075, 0.50),
    "F": (-0.275, 0.25, 0.075, 0.50),
    "G": (0.0, 0.0, 0.55, 0.075),
}

SEGMENT_GLYPHS = {
    "0": "ABCDEF",
    "1": "BC",
    "2": "ABGED",
    "3": "ABGCD",
    "4": "FBGC",
    "5": "AFGCD",
    "6": "AFGECD",
    "7": "ABC",
    "8": "ABCDEFG",
    "9": "ABFGCD",
    "C": "AFED",
}


def create_segment_label(
    stage: Usd.Stage,
    path: str,
    text: str,
    origin: Gf.Vec3d,
    size: float,
    color: Gf.Vec3f,
) -> None:
    char_advance = 0.72 * size
    x_cursor = origin[0]
    for char_index, char in enumerate(text.upper()):
        if char == " ":
            x_cursor += char_advance * 0.65
            continue
        segments = SEGMENT_GLYPHS.get(char)
        if not segments:
            x_cursor += char_advance
            continue
        for segment in segments:
            local_x, local_y, local_w, local_h = SEGMENT_DEFS[segment]
            create_ground_bar(
                stage,
                f"{path}_Char_{char_index:02d}_{char}_Seg_{segment}",
                Gf.Vec3d(
                    x_cursor + local_x * size,
                    origin[1] + local_y * size,
                    origin[2],
                ),
                Gf.Vec3f(local_w * size, local_h * size, 0.018),
                color,
            )
        x_cursor += char_advance


def create_yard_ground_markings(
    stage: Usd.Stage,
    path: str,
    block: str,
    bay_number: int,
    bay_center_y: float,
    stack_x_min: float,
    stack_x_max: float,
    column_centers: list[float],
) -> None:
    white = Gf.Vec3f(0.92, 0.92, 0.86)
    dark = Gf.Vec3f(0.03, 0.035, 0.032)
    y_length = BAY_LENGTH_M + 1.15
    y_front = bay_center_y - y_length * 0.5
    y_back = bay_center_y + y_length * 0.5
    yard_lane_y_min = YARD_LAYOUT_START_Y_M
    yard_lane_y_max = YARD_LAYOUT_START_Y_M + YARD_TOTAL_LENGTH_M
    yard_lane_center_y = (yard_lane_y_min + yard_lane_y_max) * 0.5
    yard_lane_length = yard_lane_y_max - yard_lane_y_min
    lane_left_x, lane_right_x = truck_lane_bounds_x()
    lane_mid_x = (lane_left_x + lane_right_x) * 0.5

    truck_lane_width = lane_right_x - lane_left_x
    create_ground_bar(
        stage,
        f"{path}/TruckLaneAsphaltSurface_1C4C_Through",
        Gf.Vec3d(lane_mid_x, yard_lane_center_y, GROUND_MARKING_Z_M - 0.012),
        Gf.Vec3f(truck_lane_width + 0.20, yard_lane_length, 0.012),
        Gf.Vec3f(0.085, 0.095, 0.090),
    )

    for name, x, width, color in (
        ("StackLeftBoundary", stack_x_min, 0.060, white),
        ("StackRightBoundary", stack_x_max, 0.060, white),
    ):
        create_ground_bar(
            stage,
            f"{path}/{name}",
            Gf.Vec3d(x, bay_center_y, GROUND_MARKING_Z_M),
            Gf.Vec3f(width, y_length, 0.014),
            color,
        )

    arrow_spacing_m = 90.0
    arrow_count = max(2, int(yard_lane_length / arrow_spacing_m))
    for arrow_index in range(arrow_count):
        arrow_y = yard_lane_y_min + (arrow_index + 0.5) * yard_lane_length / arrow_count
        create_truck_lane_arrow(
            stage,
            f"{path}/TruckLaneForwardArrow_1C4C_{arrow_index + 1:02d}",
            lane_mid_x,
            arrow_y,
            1.05,
        )

    for name, y in (
        ("TruckLane1CStartStopLine", yard_lane_y_min),
        ("TruckLane4CEndStopLine", yard_lane_y_max),
    ):
        create_ground_bar(
            stage,
            f"{path}/{name}",
            Gf.Vec3d(lane_mid_x, y, GROUND_MARKING_Z_M),
            Gf.Vec3f(truck_lane_width, 0.060, 0.014),
            Gf.Vec3f(0.92, 0.92, 0.86),
        )

    sorted_columns = sorted(column_centers)
    for index in range(len(sorted_columns) - 1):
        separator_x = (sorted_columns[index] + sorted_columns[index + 1]) * 0.5
        create_ground_bar(
            stage,
            f"{path}/ContainerColumnSeparator_{index + 1:02d}",
            Gf.Vec3d(separator_x, bay_center_y, GROUND_MARKING_Z_M - 0.002),
            Gf.Vec3f(0.040, y_length, 0.010),
            Gf.Vec3f(0.72, 0.72, 0.66),
        )

    for name, y in (("BayFrontLine", y_front), ("BayBackLine", y_back)):
        create_ground_bar(
            stage,
            f"{path}/{name}",
            Gf.Vec3d((stack_x_min + stack_x_max) * 0.5, y, GROUND_MARKING_Z_M),
            Gf.Vec3f(stack_x_max - stack_x_min, 0.060, 0.014),
            white,
        )

    # Small painted bay id, styled like CCTV-visible ground markings rather
    # than a floating UI label.
    label_pad_x = lane_right_x + 0.42
    label_y = y_front + 0.72
    create_ground_bar(
        stage,
        f"{path}/BayIdDarkPaintPatch",
        Gf.Vec3d(label_pad_x + 0.34, label_y, GROUND_MARKING_Z_M - 0.004),
        Gf.Vec3f(1.55, 0.72, 0.012),
        dark,
    )
    create_segment_label(
        stage,
        f"{path}/BlockLabel_{block}",
        block,
        Gf.Vec3d(label_pad_x - 0.32, label_y + 0.08, GROUND_MARKING_Z_M + 0.014),
        0.46,
        white,
    )
    create_segment_label(
        stage,
        f"{path}/BayNumberLabel_{bay_number:03d}",
        f"{bay_number:02d}",
        Gf.Vec3d(label_pad_x + 0.26, label_y - 0.12, GROUND_MARKING_Z_M + 0.014),
        0.40,
        white,
    )


def container_length_for_size(container_size_ft: int) -> float:
    if container_size_ft == 20:
        return CONTAINER_LENGTH_20FT_Y_M
    if container_size_ft == 40:
        return CONTAINER_LENGTH_40FT_Y_M
    raise ValueError("Container size must be 20 or 40 ft")


def validate_container_bay_number(block: str, bay_number: int, container_size_ft: int) -> None:
    if container_size_ft == 20 and bay_number % 2 == 0:
        raise ValueError(
            f"20 ft containers should be placed on odd bay numbers, got {block}/{bay_number:03d}"
        )
    if container_size_ft == 40 and bay_number % 2 != 0:
        raise ValueError(
            f"40 ft containers should be placed on even bay numbers, got {block}/{bay_number:03d}"
        )


def container_center_y_for_bay(block: str, bay_number: int, container_size_ft: int) -> float:
    if container_size_ft == 20:
        return bay_scene_y_m(block, bay_number, "center")
    front_odd_bay = bay_number - 1
    rear_odd_bay = bay_number + 1
    return (
        bay_scene_y_m(block, front_odd_bay, "center")
        + bay_scene_y_m(block, rear_odd_bay, "center")
    ) * 0.5


def container_length_for_bay(block: str, bay_number: int, container_size_ft: int) -> float:
    return container_length_for_size(container_size_ft)


def occupied_bay_numbers(bay_number: int, container_size_ft: int) -> tuple[int, ...]:
    if container_size_ft == 20:
        return (bay_number,)
    return (bay_number - 1, bay_number + 1)


def build_live_container_layer(
    bay_id: str,
    pattern: str,
    *,
    container_size_ft: int = 20,
    show_truck_lane: bool = True,
    show_ground_markings: bool = False,
) -> Path:
    if OUTPUT_PATH.exists():
        OUTPUT_PATH.unlink()

    block, bay_number = parse_bay_id(bay_id)
    normalized_bay_id = f"{block}/{bay_number:03d}"
    validate_container_bay_number(block, bay_number, container_size_ft)
    heights = parse_pattern(pattern)
    container_length_y = container_length_for_bay(block, bay_number, container_size_ft)

    stage = Usd.Stage.CreateNew(str(OUTPUT_PATH))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)

    world = stage.OverridePrim("/World")
    stage.SetDefaultPrim(world)
    root = UsdGeom.Xform.Define(stage, LIVE_ROOT_PATH).GetPrim()
    root.CreateAttribute("live:bayId", Sdf.ValueTypeNames.String).Set(normalized_bay_id)
    root.CreateAttribute("live:bayMapPattern", Sdf.ValueTypeNames.String).Set(pattern)
    root.CreateAttribute("live:layoutMode", Sdf.ValueTypeNames.Token).Set(
        "six_container_columns_plus_one_truck_lane"
    )
    root.CreateAttribute("live:containerSizeFt", Sdf.ValueTypeNames.Int).Set(
        container_size_ft
    )
    root.CreateAttribute("live:physicsMode", Sdf.ValueTypeNames.Token).Set(
        "visual_only_no_collision"
    )
    root.CreateAttribute("live:maxStackHeightM", Sdf.ValueTypeNames.Float).Set(
        MAX_STACK_HEIGHT_Z_M
    )
    root.CreateAttribute("live:containerVisualHeightM", Sdf.ValueTypeNames.Float).Set(
        CONTAINER_VISUAL_HEIGHT_Z_M
    )
    root.CreateAttribute("live:containerActualHeightM", Sdf.ValueTypeNames.Float).Set(
        CONTAINER_ACTUAL_HEIGHT_M
    )

    materials = [
        material(stage, f"Container_{name}", color) for name, color in CONTAINER_COLORS
    ]

    bay_center_y = container_center_y_for_bay(block, bay_number, container_size_ft)
    row_width = CONTAINER_WIDTH_X_M
    total_width = len(heights) * row_width + (len(heights) - 1) * ROW_GAP_X_M
    required_width = (
        LEFT_INNER_SAFETY_X_M
        + TRUCK_LANE_WIDTH_X_M
        + total_width
        + RIGHT_INNER_SAFETY_X_M
    )
    if required_width > BAY_WIDTH_M:
        raise ValueError(
            f"Live container layout needs {required_width:.2f} m, "
            f"but the bay is only {BAY_WIDTH_M:.2f} m wide"
        )
    actual_stack_height = CONTAINER_ACTUAL_HEIGHT_M * max(heights)
    if actual_stack_height > MAX_STACK_HEIGHT_Z_M:
        raise ValueError(
            f"Stack height {actual_stack_height:.2f} m exceeds "
            f"{MAX_STACK_HEIGHT_Z_M:.2f} m"
        )
    row_pitch = row_width + ROW_GAP_X_M
    first_row_center_x = reference_first_row_center_x_m(len(heights))
    leftmost_stack_x = first_row_center_x - (len(heights) - 1) * row_pitch
    column_centers = [
        leftmost_stack_x + (len(heights) - row_index) * row_pitch
        for row_index in range(1, len(heights) + 1)
    ]
    container_width = CONTAINER_WIDTH_X_M

    stack_path = (
        f"{LIVE_ROOT_PATH}/Bay_{block}_{bay_number:03d}_"
        f"{container_size_ft}FT_Pattern_{pattern}"
    )
    stack = UsdGeom.Xform.Define(stage, stack_path).GetPrim()
    stack.CreateAttribute("live:block", Sdf.ValueTypeNames.String).Set(block)
    stack.CreateAttribute("live:bayNumber", Sdf.ValueTypeNames.Int).Set(bay_number)
    stack.CreateAttribute("live:rowCount", Sdf.ValueTypeNames.Int).Set(len(heights))
    stack.CreateAttribute("live:containerColumnCount", Sdf.ValueTypeNames.Int).Set(
        len(heights)
    )
    stack.CreateAttribute("live:truckLaneCount", Sdf.ValueTypeNames.Int).Set(1)
    stack.CreateAttribute("live:rowOrder", Sdf.ValueTypeNames.Token).Set(
        "right_to_left"
    )
    stack.CreateAttribute("live:firstRowCenterXM", Sdf.ValueTypeNames.Float).Set(
        first_row_center_x
    )
    stack.CreateAttribute("live:firstRowAnchorPolicy", Sdf.ValueTypeNames.Token).Set(
        "keep_reference_rightmost_row"
    )
    stack.CreateAttribute("live:rowWidthM", Sdf.ValueTypeNames.Float).Set(row_width)
    stack.CreateAttribute("live:totalWidthM", Sdf.ValueTypeNames.Float).Set(total_width)
    stack.CreateAttribute("live:leftInnerSafetyM", Sdf.ValueTypeNames.Float).Set(
        LEFT_INNER_SAFETY_X_M
    )
    stack.CreateAttribute("live:truckLaneWidthM", Sdf.ValueTypeNames.Float).Set(
        TRUCK_LANE_WIDTH_X_M
    )
    stack.CreateAttribute("live:rightInnerSafetyM", Sdf.ValueTypeNames.Float).Set(
        RIGHT_INNER_SAFETY_X_M
    )
    stack.CreateAttribute("live:containerLengthM", Sdf.ValueTypeNames.Float).Set(
        container_length_y
    )
    stack.CreateAttribute("live:containerWidthVisualM", Sdf.ValueTypeNames.Float).Set(
        container_width
    )
    stack.CreateAttribute("live:tierGapVisualM", Sdf.ValueTypeNames.Float).Set(TIER_GAP_Z_M)

    for row_index, height in enumerate(heights, start=1):
        x = column_centers[row_index - 1]
        for tier in range(1, height + 1):
            mat = materials[(row_index * 3 + tier * 5) % len(materials)]
            z = (
                PAD_TOP_Z_M
                + STACK_FLOOR_CLEARANCE_M
                + (tier - 0.5) * CONTAINER_VISUAL_HEIGHT_Z_M
                + (tier - 1) * TIER_GAP_Z_M
            )
            container_path = f"{stack_path}/R{row_index:02d}_T{tier:02d}_Container"
            create_container_box(
                stage,
                container_path,
                Gf.Vec3d(x, bay_center_y, z),
                Gf.Vec3f(container_width, container_length_y, CONTAINER_VISUAL_HEIGHT_Z_M),
                mat,
                bay_id=normalized_bay_id,
                row=row_index,
                tier=tier,
                tiers=height,
            )
            create_container_details(
                stage,
                container_path,
                Gf.Vec3d(x, bay_center_y, z),
                Gf.Vec3f(container_width, container_length_y, CONTAINER_VISUAL_HEIGHT_Z_M),
                mat,
            )

    outline_pad = 0.35
    stack_x_min = leftmost_stack_x - row_width * 0.5 - outline_pad
    stack_x_max = leftmost_stack_x + (len(heights) - 1) * row_pitch + row_width * 0.5 + outline_pad
    if show_truck_lane:
        create_truck_lane_markings(stage, f"{stack_path}/TruckLane")
    if show_ground_markings:
        create_layout_outline(
            stage,
            f"{stack_path}/BayMap_Outline",
            stack_x_min,
            stack_x_max,
            bay_center_y - container_length_y * 0.5 - outline_pad,
            bay_center_y + container_length_y * 0.5 + outline_pad,
        )
        create_yard_ground_markings(
            stage,
            f"{stack_path}/GroundMarkings",
            block,
            bay_number,
            bay_center_y,
            stack_x_min,
            stack_x_max,
            column_centers,
        )

    layer = stage.GetRootLayer()
    layer.customLayerData = {
        "description": "Runtime container instances generated from live bay-map data",
        "bayId": normalized_bay_id,
        "bayMapPattern": pattern,
        "layoutMode": "six_container_columns_plus_one_truck_lane",
        "physicsMode": "visual_only_no_collision",
        "containerCount": sum(heights),
        "containerColumnCount": len(heights),
        "truckLaneCount": 1,
        "containerActualHeightMeters": CONTAINER_ACTUAL_HEIGHT_M,
        "containerVisualHeightMeters": CONTAINER_VISUAL_HEIGHT_Z_M,
        "containerLengthMeters": container_length_y,
        "containerSizeFeet": container_size_ft,
        "leftInnerSafetyMeters": LEFT_INNER_SAFETY_X_M,
        "truckLaneWidthMeters": TRUCK_LANE_WIDTH_X_M,
        "truckLaneVisible": show_truck_lane,
        "rightInnerSafetyMeters": RIGHT_INNER_SAFETY_X_M,
        "rowOrder": "right_to_left",
        "rowHeights": ",".join(str(value) for value in heights),
    }
    layer.Save()
    return OUTPUT_PATH


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bay", default="1C/041")
    parser.add_argument("--pattern", default="413413")
    parser.add_argument("--container-size-ft", type=int, choices=(20, 40), default=20)
    parser.add_argument("--hide-truck-lane", action="store_true")
    parser.add_argument("--show-ground-markings", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else None)
    print(
        "Built live containers layer:",
        build_live_container_layer(
            args.bay,
            args.pattern,
            container_size_ft=args.container_size_ft,
            show_truck_lane=not args.hide_truck_lane,
            show_ground_markings=args.show_ground_markings,
        ),
    )


if __name__ == "__main__":
    main()
