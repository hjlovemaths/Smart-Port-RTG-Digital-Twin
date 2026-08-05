"""Replace the side STS cranes with the center STS crane visual model.

Run in Blender:

    blender --background blender/RTG_Model.blend --python blender/replace_side_sts_cranes.py

The script is idempotent. It removes the authored left/right STS crane objects
and rebuilds them by translating exact copies of the center STS crane along the
rail direction. Geometry, labels, lights, and camera helpers stay identical to
the center crane; only the X position changes.
"""

from __future__ import annotations

import bpy
from mathutils import Matrix, Vector


SOURCE_PREFIX = "CODEx_STS_Crane1"
TARGETS = (
    "CODEx_STS_Crane0",
    "CODEx_STS_Crane3",
)
COPY_TYPES = {"MESH", "CURVE", "FONT", "LIGHT", "CAMERA"}


def visible_crane_objects(prefix: str) -> list[bpy.types.Object]:
    return [
        obj
        for obj in bpy.data.objects
        if obj.name.startswith(prefix)
        and obj.type in COPY_TYPES
        and not obj.hide_viewport
        and not obj.hide_render
    ]


def object_bounds(obj: bpy.types.Object) -> tuple[float, float, float, float, float, float] | None:
    if obj.type not in {"MESH", "CURVE", "FONT"} or not hasattr(obj, "bound_box"):
        return None
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
    boxes = [box for obj in objects if (box := object_bounds(obj)) is not None]
    if not boxes:
        raise RuntimeError("No visible bounds available for STS crane objects")
    return (
        min(box[0] for box in boxes),
        max(box[1] for box in boxes),
        min(box[2] for box in boxes),
        max(box[3] for box in boxes),
        min(box[4] for box in boxes),
        max(box[5] for box in boxes),
    )


def remove_prefixed_objects(prefix: str) -> int:
    count = 0
    for obj in list(bpy.data.objects):
        if obj.name.startswith(prefix):
            bpy.data.objects.remove(obj, do_unlink=True)
            count += 1
    return count


def link_clone_to_source_collections(source: bpy.types.Object, clone: bpy.types.Object) -> None:
    collections = list(source.users_collection) or [bpy.context.scene.collection]
    for collection in collections:
        collection.objects.link(clone)


def clone_source_to_target(
    source_objects: list[bpy.types.Object],
    source_bounds: tuple[float, float, float, float, float, float],
    target_prefix: str,
    target_bounds: tuple[float, float, float, float, float, float],
) -> int:
    source_center_x = (source_bounds[0] + source_bounds[1]) * 0.5
    target_center_x = (target_bounds[0] + target_bounds[1]) * 0.5
    delta = Matrix.Translation(Vector((target_center_x - source_center_x, 0.0, 0.0)))

    created = 0
    for source in source_objects:
        clone = source.copy()
        if source.data is not None:
            clone.data = source.data
        clone.animation_data_clear()
        clone.name = source.name.replace(SOURCE_PREFIX, target_prefix, 1)
        clone.parent = None
        clone.matrix_world = delta @ source.matrix_world
        clone["source_sts_visual_object"] = source.name
        clone["source_sts_visual_prefix"] = SOURCE_PREFIX
        clone["sts_visual_role"] = "translated_exact_center_crane"
        link_clone_to_source_collections(source, clone)
        created += 1
    return created


def save_model() -> None:
    if not bpy.data.filepath:
        raise RuntimeError("Open blender/RTG_Model.blend before running this script")
    bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)


def main() -> None:
    source_objects = visible_crane_objects(SOURCE_PREFIX)
    source_bounds = combined_bounds(source_objects)
    result = {}

    for target_prefix in TARGETS:
        target_objects = visible_crane_objects(target_prefix)
        target_bounds = combined_bounds(target_objects)
        removed = remove_prefixed_objects(target_prefix)
        created = clone_source_to_target(
            source_objects,
            source_bounds,
            target_prefix,
            target_bounds,
        )
        result[target_prefix] = {
            "removed": removed,
            "created": created,
        }

    bpy.context.scene["side_sts_cranes_source_visual_model"] = SOURCE_PREFIX
    bpy.context.view_layer.update()
    save_model()
    print("Replaced side STS cranes:", result)


if __name__ == "__main__":
    main()
