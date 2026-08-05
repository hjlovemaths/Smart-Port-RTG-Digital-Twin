"""Unify side-yard paving and replace static RTGs with the primary RTG visuals.

Run in Blender:

    blender --background blender/RTG_Model.blend --python blender/unify_yard_surfaces_and_static_rtgs.py

The script is idempotent. It removes the old small decorative side-yard RTGs,
hides legacy dark side-yard paving, creates light grey yard pads/markings that
match the central generated yard, and rebuilds the left/right static RTG
visuals from the current middle RTG mesh set.
"""

from __future__ import annotations

from pathlib import Path

import bpy
from mathutils import Matrix, Vector


PRIMARY_ROOT_NAME = "RTG_PRIMARY_DYNAMIC"
STATIC_ROOT_NAMES = ("RTG_STATIC_LEFT", "RTG_STATIC_RIGHT")
STATIC_CLONE_PREFIX = "RTG_STATIC_PRIMARY_STYLE_"
UNIFIED_YARD_PREFIX = "SMART_PORT_UNIFIED_SIDEYARD_"
UNIFIED_COLLECTION_NAME = "SMART_PORT_UNIFIED_SIDEYARD_SURFACES"

PAD_TOP_Z_M = 0.10
CONTINUOUS_BASE_TOP_Z_M = 0.075
MARKING_TOP_Z_M = 0.135
GROUND_SLOT_LENGTH_M = 2.475
BAY_GAP_M = 0.20
GROUND_SLOT_PITCH_M = GROUND_SLOT_LENGTH_M + BAY_GAP_M
CONTINUOUS_BASE_MARGIN_M = 0.75
QUAYSIDE_APRON_SEAWARD_Y_M = -10.25
SIDE_YARD_REGIONS = (
    ("left_outer", -101.0, -59.0, -0.50, 441.50),
    ("left_extra_long", -57.5, -37.5, -0.50, 441.50),
    ("left_inner", -48.0, -32.0, -0.50, 441.50),
    ("right_extra_long", 37.5, 57.5, -0.50, 441.50),
    ("right_inner", 46.0, 60.0, -0.50, 441.50),
    ("right_outer", 67.0, 108.0, -0.50, 441.50),
    ("near_left_stack", -39.0, -9.0, 18.50, 153.50),
    ("near_right_stack", 9.0, 39.0, 18.50, 153.50),
    ("far_left_four", -39.0, -5.0, 202.50, 417.50),
    ("far_right_four", 5.0, 39.0, 202.50, 417.50),
    ("central_rear_stack", -40.0, 40.0, 350.00, 422.00),
)

SMALL_SIDE_RTG_PREFIXES = (
    "SMART_PORT_SIDEYARD_left_small_rtg_",
    "SMART_PORT_SIDEYARD_right_small_rtg_",
)

LEGACY_SIDE_YARD_SURFACE_PREFIXES = (
    "SMART_PORT_SIDEYARD_left_",
    "SMART_PORT_SIDEYARD_right_",
    "SMART_PORT_EXTRA_YARD_",
    "SMART_PORT_OUTER_SIDEYARD_",
)

PRIMARY_VISUAL_EXCLUDE_PREFIXES = (
    "YLOAD_",
    "LIVE_internal_",
    "Task",
)


def material(name: str, color: tuple[float, float, float, float], roughness: float = 0.72) -> bpy.types.Material:
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.roughness = roughness
    return mat


PAD_MATERIALS = (
    "YARD_LAYOUT_1C_Pad",
    "YARD_LAYOUT_2C_Pad",
    "YARD_LAYOUT_3C_Pad",
    "YARD_LAYOUT_4C_Pad",
)


def central_pad_material() -> bpy.types.Material:
    for name in PAD_MATERIALS:
        mat = bpy.data.materials.get(name)
        if mat is not None:
            return mat
    return material("YARD_LAYOUT_1C_Pad", (0.78, 0.80, 0.79, 1.0))


def white_marking_material() -> bpy.types.Material:
    return bpy.data.materials.get("YARD_LAYOUT_WhiteMarking") or material(
        "YARD_LAYOUT_WhiteMarking", (0.92, 0.94, 0.92, 1.0)
    )


def yellow_marking_material() -> bpy.types.Material:
    return bpy.data.materials.get("YARD_LAYOUT_YellowMarking") or material(
        "YARD_LAYOUT_YellowMarking", (1.0, 0.86, 0.08, 1.0)
    )


def cyan_marking_material() -> bpy.types.Material:
    return bpy.data.materials.get("smart_port_neon_cyan_emissive") or material(
        "smart_port_neon_cyan_emissive", (0.0, 0.92, 1.0, 1.0)
    )


def object_bounds(obj: bpy.types.Object) -> tuple[float, float, float, float, float, float]:
    points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    return (
        min(point.x for point in points),
        max(point.x for point in points),
        min(point.y for point in points),
        max(point.y for point in points),
        min(point.z for point in points),
        max(point.z for point in points),
    )


def combined_bounds(objects: list[bpy.types.Object]) -> tuple[float, float, float, float, float, float]:
    boxes = [object_bounds(obj) for obj in objects if obj.type == "MESH"]
    if not boxes:
        raise RuntimeError("No mesh bounds available")
    return (
        min(box[0] for box in boxes),
        max(box[1] for box in boxes),
        min(box[2] for box in boxes),
        max(box[3] for box in boxes),
        min(box[4] for box in boxes),
        max(box[5] for box in boxes),
    )


def descendants(root: bpy.types.Object) -> list[bpy.types.Object]:
    result: list[bpy.types.Object] = []
    stack = list(root.children)
    while stack:
        obj = stack.pop()
        result.append(obj)
        stack.extend(obj.children)
    return result


def remove_object_tree(obj: bpy.types.Object) -> int:
    count = 0
    for child in list(obj.children):
        count += remove_object_tree(child)
    bpy.data.objects.remove(obj, do_unlink=True)
    return count + 1


def ensure_collection(name: str) -> bpy.types.Collection:
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(collection)
    return collection


def add_box_geometry(vertices: list[tuple[float, float, float]], faces: list[tuple[int, int, int, int]], center, size) -> None:
    cx, cy, cz = center
    sx, sy, sz = (value * 0.5 for value in size)
    start = len(vertices)
    vertices.extend(
        (
            (cx - sx, cy - sy, cz - sz),
            (cx + sx, cy - sy, cz - sz),
            (cx + sx, cy + sy, cz - sz),
            (cx - sx, cy + sy, cz - sz),
            (cx - sx, cy - sy, cz + sz),
            (cx + sx, cy - sy, cz + sz),
            (cx + sx, cy + sy, cz + sz),
            (cx - sx, cy + sy, cz + sz),
        )
    )
    faces.extend(
        (
            (start + 0, start + 1, start + 2, start + 3),
            (start + 4, start + 7, start + 6, start + 5),
            (start + 0, start + 4, start + 5, start + 1),
            (start + 1, start + 5, start + 6, start + 2),
            (start + 2, start + 6, start + 7, start + 3),
            (start + 4, start + 0, start + 3, start + 7),
        )
    )


def create_boxes_object(
    name: str,
    boxes: list[tuple[tuple[float, float, float], tuple[float, float, float]]],
    mat: bpy.types.Material,
    collection: bpy.types.Collection,
) -> bpy.types.Object:
    old = bpy.data.objects.get(name)
    if old is not None:
        remove_object_tree(old)
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int, int]] = []
    for center, size in boxes:
        add_box_geometry(vertices, faces, center, size)
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(mat)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    return obj


def hide_legacy_side_yard_surfaces() -> tuple[int, int]:
    deleted_small_rtgs = 0
    hidden_surfaces = 0
    for obj in list(bpy.data.objects):
        name = obj.name
        if name.startswith(SMALL_SIDE_RTG_PREFIXES):
            deleted_small_rtgs += remove_object_tree(obj)
            continue
        if not name.startswith(LEGACY_SIDE_YARD_SURFACE_PREFIXES):
            continue
        if "container" in name.lower():
            continue
        if name.startswith(SMALL_SIDE_RTG_PREFIXES):
            deleted_small_rtgs += remove_object_tree(obj)
            continue
        if any(token in name for token in ("ground", "base", "boundary", "bay_line", "smart_route", "yard_", "edge", "lane_x", "bay_y")):
            obj.hide_viewport = True
            obj.hide_render = True
            obj["replaced_by_unified_sideyard"] = True
            hidden_surfaces += 1
    return deleted_small_rtgs, hidden_surfaces


def rebuild_unified_side_yards() -> dict[str, int]:
    collection = ensure_collection(UNIFIED_COLLECTION_NAME)
    for obj in list(bpy.data.objects):
        if obj.name.startswith(UNIFIED_YARD_PREFIX):
            remove_object_tree(obj)

    pad_mat = central_pad_material()
    white_mat = white_marking_material()
    yellow_mat = yellow_marking_material()
    cyan_mat = cyan_marking_material()
    base_boxes = []
    quayside_apron_boxes = []
    pad_boxes = []
    separator_boxes = []
    boundary_boxes = []
    cyan_boxes = []

    min_x = min(region[1] for region in SIDE_YARD_REGIONS) - CONTINUOUS_BASE_MARGIN_M
    max_x = max(region[2] for region in SIDE_YARD_REGIONS) + CONTINUOUS_BASE_MARGIN_M
    min_y = min(region[3] for region in SIDE_YARD_REGIONS) - CONTINUOUS_BASE_MARGIN_M
    max_y = max(region[4] for region in SIDE_YARD_REGIONS) + CONTINUOUS_BASE_MARGIN_M
    base_boxes.append(
        (
            ((min_x + max_x) * 0.5, (min_y + max_y) * 0.5, CONTINUOUS_BASE_TOP_Z_M * 0.5),
            (max_x - min_x, max_y - min_y, CONTINUOUS_BASE_TOP_Z_M),
        )
    )
    quayside_apron_boxes.append(
        (
            (
                (min_x + max_x) * 0.5,
                (QUAYSIDE_APRON_SEAWARD_Y_M + min_y) * 0.5,
                CONTINUOUS_BASE_TOP_Z_M * 0.5,
            ),
            (max_x - min_x, min_y - QUAYSIDE_APRON_SEAWARD_Y_M, CONTINUOUS_BASE_TOP_Z_M),
        )
    )

    for region, min_x, max_x, start_y, end_y in SIDE_YARD_REGIONS:
        width = max_x - min_x
        center_x = (min_x + max_x) * 0.5
        y = start_y
        while y < end_y:
            slot_end = min(y + GROUND_SLOT_LENGTH_M, end_y)
            pad_boxes.append(
                (
                    (center_x, (y + slot_end) * 0.5, PAD_TOP_Z_M * 0.5),
                    (width, slot_end - y, PAD_TOP_Z_M),
                )
            )
            separator_boxes.append(
                (
                    (center_x, slot_end, MARKING_TOP_Z_M),
                    (width - 0.50, 0.035, 0.012),
                )
            )
            y += GROUND_SLOT_PITCH_M

        boundary_boxes.extend(
            (
                ((min_x, (start_y + end_y) * 0.5, MARKING_TOP_Z_M), (0.075, end_y - start_y, 0.014)),
                ((max_x, (start_y + end_y) * 0.5, MARKING_TOP_Z_M), (0.075, end_y - start_y, 0.014)),
                ((center_x, start_y, MARKING_TOP_Z_M), (width, 0.075, 0.014)),
                ((center_x, end_y, MARKING_TOP_Z_M), (width, 0.075, 0.014)),
            )
        )
        for ratio in (0.35, 0.65):
            x = min_x + width * ratio
            cyan_boxes.append(
                (
                    (x, (start_y + end_y) * 0.5, MARKING_TOP_Z_M + 0.012),
                    (0.045, end_y - start_y, 0.012),
                )
            )

    create_boxes_object(f"{UNIFIED_YARD_PREFIX}continuous_light_grey_base", base_boxes, pad_mat, collection)
    create_boxes_object(f"{UNIFIED_YARD_PREFIX}quayside_apron_extension", quayside_apron_boxes, pad_mat, collection)
    create_boxes_object(f"{UNIFIED_YARD_PREFIX}light_grey_40ft_slots", pad_boxes, pad_mat, collection)
    create_boxes_object(f"{UNIFIED_YARD_PREFIX}white_slot_separators", separator_boxes, white_mat, collection)
    create_boxes_object(f"{UNIFIED_YARD_PREFIX}yellow_boundaries", boundary_boxes, yellow_mat, collection)
    create_boxes_object(f"{UNIFIED_YARD_PREFIX}cyan_lane_lines", cyan_boxes, cyan_mat, collection)
    return {
        "regions": len(SIDE_YARD_REGIONS),
        "continuous_base": len(base_boxes),
        "quayside_apron": len(quayside_apron_boxes),
        "pad_slots": len(pad_boxes),
        "separators": len(separator_boxes),
        "boundaries": len(boundary_boxes),
        "cyan_lines": len(cyan_boxes),
    }


def primary_visual_meshes() -> list[bpy.types.Object]:
    primary = bpy.data.objects.get(PRIMARY_ROOT_NAME)
    if primary is None:
        raise RuntimeError(f"Missing {PRIMARY_ROOT_NAME}")
    meshes = []
    for obj in descendants(primary):
        if obj.type != "MESH":
            continue
        if not obj.visible_get():
            continue
        if obj.name.startswith(PRIMARY_VISUAL_EXCLUDE_PREFIXES):
            continue
        meshes.append(obj)
    return meshes


def rebuild_static_rtgs_from_primary() -> dict[str, int]:
    primary_meshes = primary_visual_meshes()
    primary_bounds = combined_bounds(primary_meshes)
    primary_center = Vector(((primary_bounds[0] + primary_bounds[1]) * 0.5, (primary_bounds[2] + primary_bounds[3]) * 0.5, 0.0))
    result = {}

    for root_name in STATIC_ROOT_NAMES:
        root = bpy.data.objects.get(root_name)
        if root is None:
            root = bpy.data.objects.new(root_name, None)
            bpy.context.scene.collection.objects.link(root)

        old_meshes = [obj for obj in descendants(root) if obj.type == "MESH"]
        old_bounds = combined_bounds(old_meshes) if old_meshes else primary_bounds
        target_center = Vector(((old_bounds[0] + old_bounds[1]) * 0.5, (old_bounds[2] + old_bounds[3]) * 0.5, 0.0))

        for child in list(root.children):
            remove_object_tree(child)

        delta = Matrix.Translation(target_center - primary_center)
        side_key = root_name.removeprefix("RTG_STATIC_").lower()
        created = 0
        for source in primary_meshes:
            clone = source.copy()
            clone.data = source.data
            clone.animation_data_clear()
            clone.name = f"{STATIC_CLONE_PREFIX}{side_key}_{source.name}"
            clone["rtg_id"] = f"{root_name}_01"
            clone["rtg_role"] = "static_visual_primary_style"
            clone["source_primary_object"] = source.name
            bpy.context.scene.collection.objects.link(clone)
            clone.parent = root
            clone.matrix_parent_inverse = root.matrix_world.inverted()
            clone.matrix_world = delta @ source.matrix_world
            created += 1
        root["rtg_role"] = "static_visual_primary_style"
        root["source_visual_model"] = PRIMARY_ROOT_NAME
        result[root_name] = created
    return result


def save_model() -> None:
    if not bpy.data.filepath:
        raise RuntimeError("Open blender/RTG_Model.blend before running this script")
    bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)


def main() -> None:
    deleted_small_rtgs, hidden_surfaces = hide_legacy_side_yard_surfaces()
    yard_result = rebuild_unified_side_yards()
    rtg_result = rebuild_static_rtgs_from_primary()
    bpy.context.scene["unified_sideyard_style"] = "matches central 1C-4C light grey yard slots"
    bpy.context.scene["unified_sideyard_small_rtgs_deleted"] = deleted_small_rtgs
    bpy.context.scene["unified_sideyard_legacy_surfaces_hidden"] = hidden_surfaces
    bpy.context.scene["static_rtgs_source_visual_model"] = PRIMARY_ROOT_NAME
    bpy.context.view_layer.update()
    save_model()
    print("Unified side-yard surfaces:", yard_result)
    print("Deleted small side-yard RTG objects:", deleted_small_rtgs)
    print("Hidden legacy side-yard surface objects:", hidden_surfaces)
    print("Rebuilt static RTGs:", rtg_result)


if __name__ == "__main__":
    main()
