"""Resize the RTG spreader visual width to match the live container columns."""

from __future__ import annotations

import bpy
from mathutils import Matrix, Vector


SPREADER_NAME_PREFIX = "RTG_SPREAD_REDRAW"
TARGET_WORLD_X_WIDTH_M = 1.55
MIN_VALID_WIDTH_M = 0.20


def world_bbox(objects: list[bpy.types.Object]) -> tuple[Vector, Vector]:
    points: list[Vector] = []
    for obj in objects:
        if obj.type not in {"MESH", "CURVE", "FONT"}:
            continue
        points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    if not points:
        raise RuntimeError("No renderable spreader objects found")
    min_v = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    max_v = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return min_v, max_v


def has_selected_ancestor(
    obj: bpy.types.Object, selected: set[bpy.types.Object]
) -> bool:
    parent = obj.parent
    while parent is not None:
        if parent in selected:
            return True
        parent = parent.parent
    return False


def resize_spreader_width() -> dict[str, float | int | str]:
    selected = {
        obj
        for obj in bpy.data.objects
        if obj.name.startswith(SPREADER_NAME_PREFIX)
        and obj.type in {"MESH", "CURVE", "FONT"}
    }
    if not selected:
        raise RuntimeError(f"No objects found with prefix {SPREADER_NAME_PREFIX!r}")

    # Transform only top-level selected objects so nested children do not get
    # scaled twice.
    top_level = [
        obj for obj in sorted(selected, key=lambda item: item.name)
        if not has_selected_ancestor(obj, selected)
    ]
    before_min, before_max = world_bbox(list(selected))
    before_width = before_max.x - before_min.x
    if before_width < MIN_VALID_WIDTH_M:
        raise RuntimeError(f"Spreader width is unexpectedly small: {before_width:.3f}")

    center = (before_min + before_max) * 0.5
    factor = TARGET_WORLD_X_WIDTH_M / before_width
    transform = (
        Matrix.Translation(center)
        @ Matrix.Diagonal((factor, 1.0, 1.0, 1.0))
        @ Matrix.Translation(-center)
    )
    for obj in top_level:
        obj.matrix_world = transform @ obj.matrix_world
        obj["rtg_spreader_width_resize_factor_last_run"] = factor
        obj["rtg_spreader_target_world_x_width_m"] = TARGET_WORLD_X_WIDTH_M

    bpy.context.view_layer.update()
    after_min, after_max = world_bbox(list(selected))
    after_width = after_max.x - after_min.x
    bpy.context.scene["rtg_spreader_target_world_x_width_m"] = TARGET_WORLD_X_WIDTH_M
    bpy.context.scene["rtg_spreader_previous_world_x_width_m"] = before_width
    bpy.context.scene["rtg_spreader_resize_object_count"] = len(top_level)
    if bpy.data.filepath:
        bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)
    return {
        "selectedObjects": len(selected),
        "transformedTopLevelObjects": len(top_level),
        "beforeWorldXWidthM": round(before_width, 4),
        "afterWorldXWidthM": round(after_width, 4),
        "factor": round(factor, 6),
        "centerX": round(center.x, 4),
    }


if __name__ == "__main__":
    print("Spreader width resized:", resize_spreader_width())
