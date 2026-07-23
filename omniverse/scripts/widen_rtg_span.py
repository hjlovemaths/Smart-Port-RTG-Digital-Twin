"""Widen the three RTG model roots to a realistic cross-span.

The original Blender RTG was visually attractive but too narrow for a yard
layout that contains six container rows plus a truck lane.  This script scales
only the RTG roots on Blender X, keeps each root's current world centre fixed,
and records metadata so the change is explicit and repeatable.
"""

from __future__ import annotations

import math
from pathlib import Path

import bpy
from mathutils import Vector


RTG_ROOT_NAMES = ("RTG_PRIMARY_DYNAMIC", "RTG_STATIC_LEFT", "RTG_STATIC_RIGHT")
TARGET_SPAN_X_M = 20.0
MIN_VALID_SPAN_M = 1.0


def has_ancestor(obj: bpy.types.Object, ancestor: bpy.types.Object) -> bool:
    parent = obj.parent
    while parent is not None:
        if parent == ancestor:
            return True
        parent = parent.parent
    return False


def descendant_objects(root: bpy.types.Object) -> list[bpy.types.Object]:
    return [
        obj
        for obj in bpy.data.objects
        if obj == root or has_ancestor(obj, root)
    ]


def root_x_bounds(root: bpy.types.Object) -> tuple[float, float]:
    xs: list[float] = []
    for obj in descendant_objects(root):
        if obj.type not in {"MESH", "CURVE", "FONT"}:
            continue
        for corner in obj.bound_box:
            xs.append((obj.matrix_world @ Vector(corner)).x)
    if not xs:
        raise RuntimeError(f"No renderable descendants found under {root.name}")
    return min(xs), max(xs)


def widen_root(root: bpy.types.Object) -> dict[str, float | str]:
    before_min, before_max = root_x_bounds(root)
    before_span = before_max - before_min
    if before_span < MIN_VALID_SPAN_M:
        raise RuntimeError(f"{root.name} span is unexpectedly small: {before_span:.3f}")

    center_x = (before_min + before_max) * 0.5
    factor = TARGET_SPAN_X_M / before_span
    root.location.x = center_x - factor * (center_x - root.location.x)
    root.scale.x *= factor

    root["rtg_target_span_x_m"] = TARGET_SPAN_X_M
    root["rtg_previous_span_x_m"] = before_span
    root["rtg_span_scale_factor_last_run"] = factor
    root["rtg_span_scaling_note"] = (
        "Scaled on Blender X to fit six container rows plus a truck lane"
    )

    bpy.context.view_layer.update()
    after_min, after_max = root_x_bounds(root)
    after_span = after_max - after_min
    return {
        "root": root.name,
        "before_span_x_m": round(before_span, 4),
        "after_span_x_m": round(after_span, 4),
        "center_x_m": round(center_x, 4),
        "scale_factor": round(factor, 6),
        "root_location_x": round(root.location.x, 4),
        "root_scale_x": round(root.scale.x, 6),
    }


def widen_rtg_span() -> list[dict[str, float | str]]:
    results: list[dict[str, float | str]] = []
    for root_name in RTG_ROOT_NAMES:
        root = bpy.data.objects.get(root_name)
        if root is None:
            raise RuntimeError(f"Missing RTG root: {root_name}")
        results.append(widen_root(root))

    bpy.context.scene["rtg_target_span_x_m"] = TARGET_SPAN_X_M
    bpy.context.scene["rtg_span_update"] = (
        "Primary and static RTG roots widened for realistic six-row yard layout"
    )
    if bpy.data.filepath:
        bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)
    return results


if __name__ == "__main__":
    print("RTG span widen complete:", widen_rtg_span())
