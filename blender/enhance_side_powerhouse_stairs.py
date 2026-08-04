"""Enhance the RTG side powerhouse and access stairs.

Run from Blender with ``blender/RTG_Model.blend`` open, or in background:

    blender --background blender/RTG_Model.blend --python blender/enhance_side_powerhouse_stairs.py

The geometry is authored under a stable ``RTG_POWERHOUSE_ENHANCED_`` prefix so
the script can be rerun without accumulating duplicate parts.
"""

from __future__ import annotations

import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_ROOT / "omniverse" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.append(str(SCRIPT_DIR))


PREFIX = "RTG_POWERHOUSE_ENHANCED_"
COLLECTION_NAME = "RTG_POWERHOUSE_ENHANCED_side_powerhouse_and_stairs"
GANTRY_PARENT_NAME = "ANIM_CTRL_RTG_GANTRY_TRAVEL"
ELECTRICAL_ROOM_PREFIX = "RTG_SIDE_REDRAW_opposite_yz_electrical_room"
POWERHOUSE_Y_OFFSET = -1.66


def material(name: str, color: tuple[float, float, float, float], roughness: float = 0.65):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.roughness = roughness
    return mat


def cleanup() -> None:
    for obj in list(bpy.data.objects):
        if obj.name.startswith(PREFIX):
            bpy.data.objects.remove(obj, do_unlink=True)
    collection = bpy.data.collections.get(COLLECTION_NAME)
    if collection is not None:
        bpy.data.collections.remove(collection)


def restore_electrical_room_details() -> None:
    for obj in bpy.data.objects:
        if obj.name.startswith(ELECTRICAL_ROOM_PREFIX):
            obj.hide_viewport = False
            obj.hide_render = False


def powerhouse_loc(loc: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = loc
    return (-x, y + POWERHOUSE_Y_OFFSET, z)


def ensure_collection() -> bpy.types.Collection:
    collection = bpy.data.collections.new(COLLECTION_NAME)
    bpy.context.scene.collection.children.link(collection)
    return collection


def cube(
    collection: bpy.types.Collection,
    name: str,
    loc: tuple[float, float, float],
    scale: tuple[float, float, float],
    mat,
    *,
    rot_z: float = 0.0,
    parent=None,
) -> bpy.types.Object:
    full_name = f"{PREFIX}{name}"
    mesh = bpy.data.meshes.new(f"{full_name}_mesh")
    verts = [
        (-0.5, -0.5, -0.5),
        (0.5, -0.5, -0.5),
        (0.5, 0.5, -0.5),
        (-0.5, 0.5, -0.5),
        (-0.5, -0.5, 0.5),
        (0.5, -0.5, 0.5),
        (0.5, 0.5, 0.5),
        (-0.5, 0.5, 0.5),
    ]
    faces = [
        (0, 1, 2, 3),
        (4, 7, 6, 5),
        (0, 4, 5, 1),
        (1, 5, 6, 2),
        (2, 6, 7, 3),
        (3, 7, 4, 0),
    ]
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(full_name, mesh)
    obj.location = powerhouse_loc(loc)
    obj.rotation_euler[2] = rot_z
    obj.scale = scale
    obj.data.materials.append(mat)
    collection.objects.link(obj)
    if parent is not None:
        obj.parent = parent
        obj.matrix_parent_inverse.identity()
    obj["rtg_detail_role"] = "enhanced_side_powerhouse_stairs"
    return obj


def cylinder_mesh(radius: float, depth: float, vertices: int) -> tuple[list[tuple[float, float, float]], list[tuple[int, ...]]]:
    bottom_z = -depth * 0.5
    top_z = depth * 0.5
    verts: list[tuple[float, float, float]] = []
    for z in (bottom_z, top_z):
        for i in range(vertices):
            angle = (math.tau * i) / vertices
            verts.append((math.cos(angle) * radius, math.sin(angle) * radius, z))
    faces: list[tuple[int, ...]] = []
    faces.append(tuple(range(vertices - 1, -1, -1)))
    faces.append(tuple(range(vertices, vertices * 2)))
    for i in range(vertices):
        j = (i + 1) % vertices
        faces.append((i, j, vertices + j, vertices + i))
    return verts, faces


def cylinder(
    collection: bpy.types.Collection,
    name: str,
    loc: tuple[float, float, float],
    radius: float,
    depth: float,
    mat,
    *,
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    vertices: int = 24,
    parent=None,
) -> bpy.types.Object:
    full_name = f"{PREFIX}{name}"
    mesh = bpy.data.meshes.new(f"{full_name}_mesh")
    verts, faces = cylinder_mesh(radius, depth, vertices)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(full_name, mesh)
    obj.location = powerhouse_loc(loc)
    obj.rotation_euler = rotation
    obj.data.materials.append(mat)
    collection.objects.link(obj)
    if parent is not None:
        obj.parent = parent
        obj.matrix_parent_inverse.identity()
    obj["rtg_detail_role"] = "enhanced_side_powerhouse_stairs"
    return obj


def beam_between(
    collection: bpy.types.Collection,
    name: str,
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    thickness: tuple[float, float],
    mat,
    *,
    parent=None,
) -> bpy.types.Object:
    """Create an X-constant rectangular beam between two Y/Z points."""

    sx, sy, sz = start
    ex, ey, ez = end
    mid = ((sx + ex) * 0.5, (sy + ey) * 0.5, (sz + ez) * 0.5)
    dy = ey - sy
    dz = ez - sz
    length = math.sqrt(dy * dy + dz * dz)
    angle = math.atan2(dz, dy)
    obj = cube(
        collection,
        name,
        mid,
        (thickness[0], length, thickness[1]),
        mat,
        parent=parent,
    )
    obj.rotation_euler[0] = -angle
    return obj


def rail_box(
    collection: bpy.types.Collection,
    name: str,
    x: float,
    y: float,
    z: float,
    size: tuple[float, float, float],
    mat,
    parent,
) -> None:
    cube(collection, name, (x, y, z), size, mat, parent=parent)


def add_platform_railing(
    collection: bpy.types.Collection,
    name: str,
    *,
    x_outer: float,
    y_min: float,
    y_max: float,
    z_deck: float,
    length_x: float,
    mat_yellow,
    parent,
) -> None:
    rail_z_top = z_deck + 0.58
    rail_z_mid = z_deck + 0.36
    post_z = z_deck + 0.29
    post_h = 0.58
    xs = (x_outer - length_x * 0.50, x_outer + length_x * 0.50)
    for side, x in enumerate(xs):
        rail_box(
            collection,
            f"{name}_xrail_top_{side}",
            x,
            (y_min + y_max) * 0.5,
            rail_z_top,
            (0.035, y_max - y_min, 0.035),
            mat_yellow,
            parent,
        )
        rail_box(
            collection,
            f"{name}_xrail_mid_{side}",
            x,
            (y_min + y_max) * 0.5,
            rail_z_mid,
            (0.026, y_max - y_min, 0.026),
            mat_yellow,
            parent,
        )
    for y in (y_min, (y_min + y_max) * 0.5, y_max):
        for x in xs:
            rail_box(
                collection,
                f"{name}_post_x{x:.2f}_y{y:.2f}",
                x,
                y,
                post_z,
                (0.035, 0.035, post_h),
                mat_yellow,
                parent,
            )
    for y, tag in ((y_min, "front"), (y_max, "rear")):
        rail_box(
            collection,
            f"{name}_{tag}_top",
            x_outer,
            y,
            rail_z_top,
            (length_x, 0.035, 0.035),
            mat_yellow,
            parent,
        )


def build() -> dict[str, object]:
    cleanup()
    restore_electrical_room_details()
    collection = ensure_collection()

    parent = bpy.data.objects.get(GANTRY_PARENT_NAME)
    if parent is None:
        raise RuntimeError(f"Missing RTG gantry parent object: {GANTRY_PARENT_NAME}")

    mat_room = material("RTG_POWERHOUSE_warm_white_panel", (0.72, 0.70, 0.64, 1.0))
    mat_trim = material("RTG_POWERHOUSE_light_roof_trim", (0.90, 0.88, 0.80, 1.0))
    mat_shadow = material("RTG_POWERHOUSE_dark_recess", (0.035, 0.038, 0.035, 1.0))
    mat_glass = material("RTG_POWERHOUSE_green_tinted_window", (0.06, 0.42, 0.30, 1.0))
    mat_blue = material("RTG_POWERHOUSE_rtg_blue_support", (0.0, 0.30, 0.72, 1.0))
    mat_yellow = material("RTG_POWERHOUSE_safety_yellow", (1.0, 0.74, 0.02, 1.0), roughness=0.5)
    mat_deck = material("RTG_POWERHOUSE_galvanized_deck", (0.62, 0.63, 0.58, 1.0))

    # Main side power/electrical room, scaled to be clearly visible from the
    # RTG side view while still fitting between the lower beam and the leg.
    cube(collection, "room_main_body", (6.24, -3.32, 1.58), (0.88, 2.62, 1.18), mat_room, parent=parent)
    cube(collection, "room_roof_cap", (6.24, -3.32, 2.24), (1.02, 2.82, 0.14), mat_trim, parent=parent)
    cube(collection, "room_bottom_blue_saddle", (6.24, -3.32, 0.93), (1.10, 2.88, 0.16), mat_blue, parent=parent)
    cube(collection, "room_floor_steel_grillage", (6.24, -3.32, 0.76), (1.00, 2.72, 0.08), mat_deck, parent=parent)

    for i, y in enumerate((-4.42, -3.86, -3.30, -2.74, -2.18)):
        cube(collection, f"underside_support_beam_{i:02d}", (6.24, y, 0.60), (1.06, 0.055, 0.14), mat_deck, parent=parent)

    # Door, windows and louvers on the visible outside face.
    cube(collection, "front_door_panel", (6.705, -4.22, 1.34), (0.035, 0.42, 0.72), mat_trim, parent=parent)
    cube(collection, "front_door_window", (6.728, -4.22, 1.56), (0.024, 0.24, 0.18), mat_glass, parent=parent)
    cube(collection, "side_window_left", (6.728, -3.52, 1.65), (0.024, 0.34, 0.24), mat_glass, parent=parent)
    cube(collection, "side_window_right", (6.728, -3.08, 1.65), (0.024, 0.34, 0.24), mat_glass, parent=parent)
    cube(collection, "louver_recess", (6.73, -2.36, 1.48), (0.028, 0.48, 0.52), mat_shadow, parent=parent)
    for i in range(7):
        cube(
            collection,
            f"louver_slat_{i:02d}",
            (6.755, -2.36, 1.28 + i * 0.07),
            (0.026, 0.43, 0.018),
            mat_deck,
            parent=parent,
        )

    # Roof equipment and access deck, echoing the reference's busy top details.
    cube(collection, "roof_service_deck", (6.66, -3.32, 2.38), (0.66, 2.50, 0.08), mat_deck, parent=parent)
    cylinder(collection, "roof_exhaust_fan", (6.55, -2.40, 2.61), 0.18, 0.16, mat_blue, rotation=(math.pi / 2, 0, 0), parent=parent)
    cylinder(collection, "roof_small_vent", (6.18, -3.96, 2.56), 0.10, 0.18, mat_shadow, vertices=18, parent=parent)
    add_platform_railing(
        collection,
        "roof_deck_railing",
        x_outer=6.78,
        y_min=-4.52,
        y_max=-2.12,
        z_deck=2.42,
        length_x=0.50,
        mat_yellow=mat_yellow,
        parent=parent,
    )

    bpy.context.scene["rtg_powerhouse_enhanced"] = "side powerhouse body, roof platform, railings, louvers"
    bpy.context.view_layer.update()
    if bpy.data.filepath:
        bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)

    result = {
        "collection": COLLECTION_NAME,
        "object_count": len(collection.objects),
        "parent": parent.name,
    }
    print("Enhanced RTG side powerhouse and stairs:", result)
    return result


if __name__ == "__main__":
    build()
