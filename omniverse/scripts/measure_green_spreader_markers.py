"""Measure small green marker meshes near the RTG spreader."""

from __future__ import annotations

import bpy
from mathutils import Vector


def bounds(obj: bpy.types.Object) -> tuple[Vector, Vector, Vector, Vector]:
    points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    mn = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    mx = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    center = (mn + mx) * 0.5
    return mn, mx, center, mx - mn


def green_score(obj: bpy.types.Object) -> float:
    score = 0.0
    name = obj.name.lower()
    if "green" in name or "marker" in name or "c3" in name:
        score += 2.0
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None:
            continue
        mat_name = mat.name.lower()
        color = tuple(mat.diffuse_color)
        if "green" in mat_name or "marker" in mat_name:
            score += 8.0
        if color[1] > 0.45 and color[0] < 0.35 and color[2] < 0.35:
            score += 8.0
        if color[1] > 0.55 and color[0] < 0.55 and color[2] < 0.55:
            score += 4.0
    return score


def main() -> None:
    candidates = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        score = green_score(obj)
        if score <= 0:
            continue
        mn, mx, center, dims = bounds(obj)
        # Keep a wide spreader/RTG neighbourhood; the imported hierarchy can
        # place these markers either in trolley-local or world-like coords.
        if not (-10.0 <= center.x <= 6.0 and -8.0 <= center.y <= 9.0 and 0.0 <= center.z <= 9.5):
            continue
        candidates.append((score, obj, mn, mx, center, dims))

    print("GREEN_MARKER_CANDIDATES", len(candidates))
    for score, obj, mn, mx, center, dims in sorted(candidates, key=lambda item: (-item[0], item[1].name))[:120]:
        mats = [slot.material.name for slot in obj.material_slots if slot.material]
        colors = [
            tuple(round(channel, 3) for channel in slot.material.diffuse_color)
            for slot in obj.material_slots
            if slot.material
        ]
        print(
            obj.name,
            "score=", round(score, 2),
            "center=", tuple(round(v, 4) for v in center),
            "min=", tuple(round(v, 4) for v in mn),
            "max=", tuple(round(v, 4) for v in mx),
            "dims=", tuple(round(v, 4) for v in dims),
            "materials=", mats[:4],
            "colors=", colors[:4],
        )


if __name__ == "__main__":
    main()
