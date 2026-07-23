"""Measure RTG spreader yellow anchor points in trolley-local coordinates."""

from __future__ import annotations

import bpy
from mathutils import Vector


TROLLEY_OBJECT_NAME = "ANIM_CTRL_RTG_TROLLEY_TRAVEL"
TARGET_PREFIXES = (
    "RTG_SPREAD_REDRAW_yellow_pair_lift_header",
    "RTG_SPREAD_REDRAW_yellow_lifting_lug_top",
    "RTG_SPREAD_REDRAW_yellow_short_upright",
)


def bbox_points(obj: bpy.types.Object) -> list[Vector]:
    return [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]


def local_bounds(obj: bpy.types.Object, inv_parent) -> tuple[Vector, Vector, Vector]:
    points = [inv_parent @ point for point in bbox_points(obj)]
    mn = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    mx = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return mn, mx, mx - mn


def main() -> None:
    trolley = bpy.data.objects.get(TROLLEY_OBJECT_NAME)
    if trolley is None:
        raise RuntimeError(f"Missing {TROLLEY_OBJECT_NAME}")
    inv_trolley = trolley.matrix_world.inverted()
    print("TROLLEY", TROLLEY_OBJECT_NAME)
    print("matrix_world", [[round(v, 5) for v in row] for row in trolley.matrix_world])

    for obj in sorted(bpy.data.objects, key=lambda item: item.name):
        if obj.type != "MESH" or not obj.name.startswith(TARGET_PREFIXES):
            continue
        mn, mx, dims = local_bounds(obj, inv_trolley)
        center = (mn + mx) * 0.5
        print(
            obj.name,
            "local_min=", tuple(round(v, 4) for v in mn),
            "local_max=", tuple(round(v, 4) for v in mx),
            "local_center=", tuple(round(v, 4) for v in center),
            "local_dims=", tuple(round(v, 4) for v in dims),
        )


if __name__ == "__main__":
    main()
