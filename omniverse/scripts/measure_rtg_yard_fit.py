"""Report yard ground-slot counts and RTG lower beam fit.

This is a read-only helper for visual calibration.  It does not edit the
Blender file.
"""

from __future__ import annotations

import bpy
from mathutils import Vector


FORTY_FOOT_WORK_SLOT_M = 6.0 * 2.0 + 0.20


def world_bounds(obj: bpy.types.Object) -> tuple[Vector, Vector]:
    points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    mn = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    mx = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return mn, mx


def dims_xyz(obj: bpy.types.Object) -> tuple[float, float, float]:
    mn, mx = world_bounds(obj)
    dims = mx - mn
    return dims.x, dims.y, dims.z


def report_yard_labels() -> None:
    label_prefix = "YARD_LAYOUT_1C4C_LABEL_"
    by_block: dict[str, list[int]] = {}
    for obj in bpy.data.objects:
        if not obj.name.startswith(label_prefix):
            continue
        block = obj.get("yard_block")
        bay_number = obj.get("bay_number")
        if block and isinstance(bay_number, int):
            by_block.setdefault(str(block), []).append(int(bay_number))
    print("YARD_ODD_LABELS")
    for block in sorted(by_block):
        labels = sorted(by_block[block])
        print(
            block,
            "count=", len(labels),
            "first=", labels[:5],
            "last=", labels[-5:],
        )


def report_candidate_beams() -> None:
    keywords = ("lower_portal_crossbeam", "wheel_bogie_beam")
    candidates = [
        obj
        for obj in bpy.data.objects
        if obj.type == "MESH" and any(keyword in obj.name.lower() for keyword in keywords)
    ]
    print("RTG_LOWER_BEAM_CANDIDATES count=", len(candidates))
    for obj in sorted(candidates, key=lambda item: item.name):
        dims = dims_xyz(obj)
        if max(dims) < 0.5:
            continue
        ratio_to_40ft = dims[1] / FORTY_FOOT_WORK_SLOT_M
        print(
            obj.name,
            "dims_xyz_m=",
            tuple(round(value, 3) for value in dims),
            "y_vs_40ft_slot=",
            round(ratio_to_40ft, 3),
        )


if __name__ == "__main__":
    report_yard_labels()
    report_candidate_beams()
