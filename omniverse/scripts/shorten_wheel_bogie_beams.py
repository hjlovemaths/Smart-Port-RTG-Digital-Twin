"""Shorten RTG wheel bogie beams after widening the RTG span.

The RTG roots were widened on Blender X to fit a six-column yard layout.  That
also made small bogie beams look too long.  This script targets every object
whose name contains ``wheel_bogie_beam`` and scales its local X until its world
X length is a compact visual size, while preserving its world-space centre.
"""

from __future__ import annotations

import bpy
from mathutils import Vector


NAME_TOKEN = "wheel_bogie_beam"
TARGET_WORLD_X_LENGTH_M = 1.55
MIN_WORLD_X_LENGTH_M = 0.05


def world_bbox(obj: bpy.types.Object) -> tuple[Vector, Vector]:
    points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    min_v = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    max_v = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return min_v, max_v


def set_world_center(obj: bpy.types.Object, target_center: Vector) -> None:
    min_v, max_v = world_bbox(obj)
    current_center = (min_v + max_v) * 0.5
    delta_world = target_center - current_center
    if obj.parent:
        delta_local = obj.parent.matrix_world.inverted().to_3x3() @ delta_world
    else:
        delta_local = delta_world
    obj.location += delta_local


def shorten_beam(obj: bpy.types.Object) -> dict[str, float | str]:
    before_min, before_max = world_bbox(obj)
    before_center = (before_min + before_max) * 0.5
    before_x_length = before_max.x - before_min.x
    if before_x_length < MIN_WORLD_X_LENGTH_M:
        raise RuntimeError(f"{obj.name} has invalid world X length: {before_x_length:.4f}")

    factor = TARGET_WORLD_X_LENGTH_M / before_x_length
    obj.scale.x *= factor
    bpy.context.view_layer.update()
    set_world_center(obj, before_center)
    bpy.context.view_layer.update()

    after_min, after_max = world_bbox(obj)
    after_x_length = after_max.x - after_min.x
    obj["rtg_target_world_x_length_m"] = TARGET_WORLD_X_LENGTH_M
    obj["rtg_previous_world_x_length_m"] = before_x_length
    obj["rtg_bogie_beam_shorten_factor_last_run"] = factor
    obj["rtg_bogie_beam_note"] = "Shortened after RTG span widening"
    return {
        "object": obj.name,
        "before_world_x_m": round(before_x_length, 4),
        "after_world_x_m": round(after_x_length, 4),
        "scale_x": round(obj.scale.x, 6),
    }


def shorten_wheel_bogie_beams() -> list[dict[str, float | str]]:
    beams = [
        obj
        for obj in bpy.data.objects
        if NAME_TOKEN in obj.name.lower() and obj.type == "MESH"
    ]
    if not beams:
        raise RuntimeError(f"No objects found containing {NAME_TOKEN!r}")
    results = [shorten_beam(obj) for obj in sorted(beams, key=lambda item: item.name)]
    bpy.context.scene["rtg_wheel_bogie_beam_target_world_x_m"] = TARGET_WORLD_X_LENGTH_M
    bpy.context.scene["rtg_wheel_bogie_beam_count"] = len(results)
    if bpy.data.filepath:
        bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)
    return results


if __name__ == "__main__":
    print("Wheel bogie beams shortened:", shorten_wheel_bogie_beams())
