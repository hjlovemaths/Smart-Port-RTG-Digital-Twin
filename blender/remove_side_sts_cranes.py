"""Remove the left and right STS cranes from the Blender source model.

Run in Blender:

    blender --background blender/RTG_Model.blend --python blender/remove_side_sts_cranes.py

The script is idempotent. It deletes the two side STS crane object sets, plus
the leftover static RTG lower-frame pieces in those same side STS footprints,
and keeps the center crane unchanged.
"""

from __future__ import annotations

import bpy
from mathutils import Vector


SIDE_STS_PREFIXES = (
    "CODEx_STS_Crane0",
    "CODEx_STS_Crane3",
)

SIDE_RTG_REMNANT_BOXES = (
    {
        "name": "left",
        "prefix": "RTG_STATIC_PRIMARY_STYLE_left_",
        "bounds": (-45.0, -30.0, -24.0, 6.0, -0.5, 16.0),
    },
    {
        "name": "right",
        "prefix": "RTG_STATIC_PRIMARY_STYLE_right_",
        "bounds": (34.0, 50.0, -24.0, 6.0, -0.5, 16.0),
    },
)


def remove_prefixed_objects(prefix: str) -> int:
    count = 0
    for obj in list(bpy.data.objects):
        if obj.name.startswith(prefix):
            bpy.data.objects.remove(obj, do_unlink=True)
            count += 1
    return count


def object_world_bounds(obj: bpy.types.Object) -> tuple[float, float, float, float, float, float]:
    if not obj.bound_box:
        loc = obj.matrix_world.translation
        return (loc.x, loc.x, loc.y, loc.y, loc.z, loc.z)

    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    xs = [corner.x for corner in corners]
    ys = [corner.y for corner in corners]
    zs = [corner.z for corner in corners]
    return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))


def bounds_overlap(
    bounds_a: tuple[float, float, float, float, float, float],
    bounds_b: tuple[float, float, float, float, float, float],
) -> bool:
    return (
        bounds_a[0] <= bounds_b[1]
        and bounds_a[1] >= bounds_b[0]
        and bounds_a[2] <= bounds_b[3]
        and bounds_a[3] >= bounds_b[2]
        and bounds_a[4] <= bounds_b[5]
        and bounds_a[5] >= bounds_b[4]
    )


def remove_side_rtg_remnants() -> dict[str, list[str]]:
    removed: dict[str, list[str]] = {}
    for config in SIDE_RTG_REMNANT_BOXES:
        side = str(config["name"])
        prefix = str(config["prefix"])
        target_bounds = config["bounds"]
        side_removed: list[str] = []

        for obj in list(bpy.data.objects):
            if not obj.name.startswith(prefix):
                continue
            if bounds_overlap(object_world_bounds(obj), target_bounds):
                side_removed.append(obj.name)
                bpy.data.objects.remove(obj, do_unlink=True)

        removed[side] = side_removed
    return removed


def save_model() -> None:
    if not bpy.data.filepath:
        raise RuntimeError("Open blender/RTG_Model.blend before running this script")
    bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)


def main() -> None:
    result = {
        "side_sts": {prefix: remove_prefixed_objects(prefix) for prefix in SIDE_STS_PREFIXES},
        "side_rtg_remnants": remove_side_rtg_remnants(),
    }
    bpy.context.scene["side_sts_cranes_removed"] = True
    bpy.context.view_layer.update()
    save_model()
    print("Removed side STS crane leftovers:", result)


if __name__ == "__main__":
    main()
