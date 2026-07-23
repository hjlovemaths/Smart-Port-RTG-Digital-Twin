"""Inspect spreader/yellow attachment candidates for rope endpoint tuning."""

from __future__ import annotations

import bpy
from mathutils import Vector


def bounds(obj: bpy.types.Object) -> tuple[Vector, Vector, Vector]:
    points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    mn = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    mx = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return mn, mx, mx - mn


def material_score(obj: bpy.types.Object) -> float:
    score = 0.0
    for slot in obj.material_slots:
        mat = slot.material
        if not mat:
            continue
        name = mat.name.lower()
        color = tuple(mat.diffuse_color)
        if "yellow" in name or "safety" in name:
            score += 10.0
        # yellow-ish diffuse color
        if color[0] > 0.55 and color[1] > 0.40 and color[2] < 0.25:
            score += 5.0
    return score


def interesting(obj: bpy.types.Object) -> bool:
    name = obj.name.lower()
    if obj.type != "MESH":
        return False
    if name.startswith(("yard_", "smart_port_1c4c", "smart_port_extra")):
        return False
    tokens = ("spread", "spreader", "hook", "twist", "lock", "yellow", "hoist", "c31", "c30")
    return material_score(obj) > 0 or any(token in name for token in tokens)


def main() -> None:
    redraw_objects = [
        obj
        for obj in bpy.data.objects
        if obj.type == "MESH" and obj.name.startswith("RTG_SPREAD_REDRAW")
    ]
    print("RTG_SPREAD_REDRAW_OBJECTS", len(redraw_objects))
    for obj in sorted(redraw_objects, key=lambda item: item.name)[:220]:
        mn, mx, dims = bounds(obj)
        mats = [slot.material.name for slot in obj.material_slots if slot.material]
        print(
            "REDRAW",
            obj.name,
            "min=", tuple(round(v, 4) for v in mn),
            "max=", tuple(round(v, 4) for v in mx),
            "dims=", tuple(round(v, 4) for v in dims),
            "materials=", mats[:4],
        )

    candidates = []
    for obj in bpy.data.objects:
        if not interesting(obj):
            continue
        mn, mx, dims = bounds(obj)
        # Focus on the RTG hoist/spreader zone in authored local/world coords.
        if not (-8.0 <= mn.y <= 1.0 or -8.0 <= mx.y <= 1.0):
            continue
        if not (2.0 <= mn.z <= 7.0 or 2.0 <= mx.z <= 7.0):
            continue
        candidates.append((obj, mn, mx, dims, material_score(obj)))

    print("SPREADER_ANCHOR_CANDIDATES", len(candidates))
    for obj, mn, mx, dims, score in sorted(
        candidates, key=lambda item: (-item[4], item[0].name)
    )[:180]:
        mats = [slot.material.name for slot in obj.material_slots if slot.material]
        print(
            obj.name,
            "score=", round(score, 2),
            "min=", tuple(round(v, 4) for v in mn),
            "max=", tuple(round(v, 4) for v in mx),
            "dims=", tuple(round(v, 4) for v in dims),
            "materials=", mats[:4],
        )


if __name__ == "__main__":
    main()
