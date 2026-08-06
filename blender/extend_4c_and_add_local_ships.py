"""Extend the 4C yard tail and add local ship-model copies.

Run in Blender:

    blender --background blender/RTG_Model.blend --python blender/extend_4c_and_add_local_ships.py

The script assumes the yard layout/unified side-yard scripts have already
rebuilt the paving. It adds several lightweight copies of the local feeder ship
model along the shoreline and nearby sea, sharing source mesh/curve data.
"""

from __future__ import annotations

from pathlib import Path

import bpy
from mathutils import Matrix, Vector


SOURCE_SHIP_PREFIX = "CODEx_left_reference_feeder_ship_clone_v2_"
GENERATED_SHIP_PREFIX = "SMART_PORT_LOCAL_SHIP_"
GENERATED_COLLECTION_NAME = "SMART_PORT_EXTRA_LOCAL_SHIPS"

SHIP_PLACEMENTS = (
    {"name": "4c40_shoreline_west", "center": (-56.0, 501.5, 0.0), "rotation_deg": 0.0, "scale": 0.96},
    {"name": "4c40_shoreline_east", "center": (42.0, 502.0, 0.0), "rotation_deg": 0.0, "scale": 0.96},
    {"name": "4c40_near_sea_mid", "center": (-8.0, 526.0, 0.0), "rotation_deg": -4.0, "scale": 0.90},
    {"name": "4c40_near_sea_east", "center": (70.0, 542.0, 0.0), "rotation_deg": 5.0, "scale": 0.82},
)


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
        raise RuntimeError(f"No visible source ship bounds for prefix {SOURCE_SHIP_PREFIX}")
    return (
        min(box[0] for box in boxes),
        max(box[1] for box in boxes),
        min(box[2] for box in boxes),
        max(box[3] for box in boxes),
        min(box[4] for box in boxes),
        max(box[5] for box in boxes),
    )


def ensure_collection(name: str) -> bpy.types.Collection:
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(collection)
    return collection


def clear_generated_ships() -> int:
    removed = 0
    for obj in list(bpy.data.objects):
        if obj.name.startswith(GENERATED_SHIP_PREFIX):
            bpy.data.objects.remove(obj, do_unlink=True)
            removed += 1
    collection = bpy.data.collections.get(GENERATED_COLLECTION_NAME)
    if collection is not None:
        bpy.data.collections.remove(collection)
    return removed


def source_ship_objects() -> list[bpy.types.Object]:
    return [
        obj
        for obj in bpy.data.objects
        if obj.name.startswith(SOURCE_SHIP_PREFIX)
        and obj.type in {"MESH", "CURVE", "FONT"}
        and not obj.hide_viewport
    ]


def clone_ship(
    source_objects: list[bpy.types.Object],
    source_center: Vector,
    placement: dict[str, object],
    collection: bpy.types.Collection,
) -> int:
    name = str(placement["name"])
    target = Vector(placement["center"])
    angle = float(placement["rotation_deg"]) * 3.141592653589793 / 180.0
    scale = float(placement["scale"])
    transform = (
        Matrix.Translation(target)
        @ Matrix.Rotation(angle, 4, "Z")
        @ Matrix.Diagonal((scale, scale, scale, 1.0))
        @ Matrix.Translation(-source_center)
    )

    created = 0
    root = bpy.data.objects.new(f"{GENERATED_SHIP_PREFIX}{name}_ROOT", None)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = 1.5
    root["source_ship_prefix"] = SOURCE_SHIP_PREFIX
    root["placement_role"] = name
    collection.objects.link(root)

    for source in source_objects:
        clone = source.copy()
        if source.data is not None:
            clone.data = source.data
        clone.animation_data_clear()
        clone.name = source.name.replace(SOURCE_SHIP_PREFIX, f"{GENERATED_SHIP_PREFIX}{name}_", 1)
        clone.hide_viewport = False
        clone.hide_render = False
        clone["source_ship_object"] = source.name
        clone["source_ship_prefix"] = SOURCE_SHIP_PREFIX
        clone["generated_ship_role"] = name
        collection.objects.link(clone)
        clone.parent = root
        clone.matrix_parent_inverse = root.matrix_world.inverted()
        clone.matrix_world = transform @ source.matrix_world
        created += 1
    return created


def save_recovery_copy() -> Path | None:
    if not bpy.data.filepath:
        return None
    project_root = Path(bpy.data.filepath).parent.parent
    backup = project_root / "tmp" / "blender_backups" / "RTG_Model_before_4c_extension_and_extra_ships.blend"
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(backup), copy=True)
    return backup


def main() -> None:
    backup = save_recovery_copy()
    removed = clear_generated_ships()
    collection = ensure_collection(GENERATED_COLLECTION_NAME)
    sources = source_ship_objects()
    bounds = combined_bounds(sources)
    source_center = Vector(((bounds[0] + bounds[1]) * 0.5, (bounds[2] + bounds[3]) * 0.5, 0.0))
    created_by_ship = {
        placement["name"]: clone_ship(sources, source_center, placement, collection)
        for placement in SHIP_PLACEMENTS
    }

    bpy.context.scene["smart_port_4c_extension_bays"] = 40
    bpy.context.scene["smart_port_extra_local_ships"] = len(SHIP_PLACEMENTS)
    bpy.context.view_layer.update()
    if bpy.data.filepath:
        bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)

    print(
        "4C extension ship placement complete:",
        {
            "backup": str(backup) if backup else None,
            "removed_old_generated_objects": removed,
            "source_ship_objects": len(sources),
            "created_by_ship": created_by_ship,
        },
    )


if __name__ == "__main__":
    main()
