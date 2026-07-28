"""Shorten the RTG from its visual left side while keeping the right side fixed."""

from __future__ import annotations

import math

import bpy
from mathutils import Vector


ROOT_NAME = "RTG_PRIMARY_DYNAMIC"
GANTRY_NAME = "ANIM_CTRL_RTG_GANTRY_TRAVEL"
TROLLEY_NAME = "ANIM_CTRL_RTG_TROLLEY_TRAVEL"

WORLD_LEFT_INSET_M = 2.35
RIGHT_FIXED_LOCAL_X = -5.0
LEFT_RIGID_LOCAL_X = 5.0
APPLIED_INSET_PROPERTY = "rtg_left_span_inset_world_m"
EPSILON = 1.0e-5


def remap_gantry_x(value: float, local_inset: float) -> float:
    if value <= RIGHT_FIXED_LOCAL_X:
        return value
    if value >= LEFT_RIGID_LOCAL_X:
        return value - local_inset
    ratio = (value - RIGHT_FIXED_LOCAL_X) / (
        LEFT_RIGID_LOCAL_X - RIGHT_FIXED_LOCAL_X
    )
    return value - local_inset * ratio


def remap_object_point(
    point: Vector,
    object_to_gantry: object,
    gantry_to_object: object,
    local_inset: float,
) -> Vector:
    gantry_point = object_to_gantry @ point
    gantry_point.x = remap_gantry_x(gantry_point.x, local_inset)
    return gantry_to_object @ gantry_point


def deform_mesh(
    obj: bpy.types.Object,
    gantry: bpy.types.Object,
    local_inset: float,
) -> int:
    if obj.data.users > 1:
        obj.data = obj.data.copy()
    if obj.data.shape_keys:
        raise RuntimeError(f"Cannot safely deform shape-key mesh: {obj.name}")

    object_to_gantry = gantry.matrix_world.inverted() @ obj.matrix_world
    gantry_to_object = obj.matrix_world.inverted() @ gantry.matrix_world
    for vertex in obj.data.vertices:
        vertex.co = remap_object_point(
            vertex.co,
            object_to_gantry,
            gantry_to_object,
            local_inset,
        )
    obj.data.update()
    return len(obj.data.vertices)


def deform_curve(
    obj: bpy.types.Object,
    gantry: bpy.types.Object,
    local_inset: float,
) -> int:
    if obj.data.users > 1:
        obj.data = obj.data.copy()

    object_to_gantry = gantry.matrix_world.inverted() @ obj.matrix_world
    gantry_to_object = obj.matrix_world.inverted() @ gantry.matrix_world
    point_count = 0
    for spline in obj.data.splines:
        for point in spline.points:
            weight = point.co.w
            remapped = remap_object_point(
                point.co.xyz,
                object_to_gantry,
                gantry_to_object,
                local_inset,
            )
            point.co = (*remapped, weight)
            point_count += 1
        for point in spline.bezier_points:
            point.co = remap_object_point(
                point.co,
                object_to_gantry,
                gantry_to_object,
                local_inset,
            )
            point.handle_left = remap_object_point(
                point.handle_left,
                object_to_gantry,
                gantry_to_object,
                local_inset,
            )
            point.handle_right = remap_object_point(
                point.handle_right,
                object_to_gantry,
                gantry_to_object,
                local_inset,
            )
            point_count += 1
    obj.data.update_tag()
    return point_count


def main() -> None:
    root = bpy.data.objects.get(ROOT_NAME)
    gantry = bpy.data.objects.get(GANTRY_NAME)
    trolley = bpy.data.objects.get(TROLLEY_NAME)
    if root is None or gantry is None or trolley is None:
        raise RuntimeError("RTG root, gantry, or trolley controller is missing")

    previous_inset = float(root.get(APPLIED_INSET_PROPERTY, 0.0))
    if math.isclose(previous_inset, WORLD_LEFT_INSET_M, abs_tol=EPSILON):
        print(f"RTG left span already shortened by {previous_inset:.2f} m")
        return
    if not math.isclose(previous_inset, 0.0, abs_tol=EPSILON):
        raise RuntimeError(
            f"RTG already has a different left inset: {previous_inset:.3f} m"
        )

    local_x_world_length = (gantry.matrix_world.to_3x3() @ Vector((1, 0, 0))).length
    if local_x_world_length <= EPSILON:
        raise RuntimeError("RTG gantry local X axis has zero world length")
    local_inset = WORLD_LEFT_INSET_M / local_x_world_length

    excluded = {trolley, *trolley.children_recursive}
    changed_objects = 0
    changed_points = 0
    for obj in gantry.children_recursive:
        if obj in excluded or not obj.visible_get():
            continue
        if obj.type == "MESH":
            changed_points += deform_mesh(obj, gantry, local_inset)
            changed_objects += 1
        elif obj.type == "CURVE":
            changed_points += deform_curve(obj, gantry, local_inset)
            changed_objects += 1

    root[APPLIED_INSET_PROPERTY] = WORLD_LEFT_INSET_M
    root["rtg_left_span_right_fixed_local_x"] = RIGHT_FIXED_LOCAL_X
    root["rtg_left_span_left_rigid_local_x"] = LEFT_RIGID_LOCAL_X
    bpy.context.view_layer.update()
    bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)
    print(
        f"Shortened RTG visual left side by {WORLD_LEFT_INSET_M:.2f} m "
        f"({local_inset:.6f} gantry-local units); "
        f"changed {changed_objects} objects and {changed_points} points"
    )


if __name__ == "__main__":
    main()
