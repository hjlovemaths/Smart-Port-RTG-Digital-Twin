"""Convert the authored 40 ft RTG spreader into a 20/40 ft telescopic model."""

from __future__ import annotations

import bpy


HOIST_NAME = "ANIM_CTRL_RTG_HOIST_VERTICAL"
FRONT_CTRL_NAME = "ANIM_CTRL_SPREADER_TELESCOPE_FRONT"
REAR_CTRL_NAME = "ANIM_CTRL_SPREADER_TELESCOPE_REAR"
SPREADER_PREFIX = "RTG_SPREAD_REDRAW_"

SPREADER_CENTER_Y = -5.0
FORTY_FOOT_LOCK_HALF_SPAN_M = 2.405
TWENTY_FOOT_LOCK_HALF_SPAN_M = 1.0675
TELESCOPE_TRAVEL_M = FORTY_FOOT_LOCK_HALF_SPAN_M - TWENTY_FOOT_LOCK_HALF_SPAN_M
MODEL_VERSION = 2

FRONT_GROUP_MAX_Y = -6.0
REAR_GROUP_MIN_Y = -4.0

LONGITUDINAL_SOURCE_NAMES = {
    "RTG_SPREAD_REDRAW_black_hydraulic_hose_long_+0.00",
    "RTG_SPREAD_REDRAW_black_hydraulic_hose_long_+0.10",
    "RTG_SPREAD_REDRAW_black_hydraulic_hose_long_-0.10",
    "RTG_SPREAD_REDRAW_dark_lower_shadow_under_main_beam",
    "RTG_SPREAD_REDRAW_gray_main_spreader_box_beam_long_y",
    "RTG_SPREAD_REDRAW_gray_outer_long_side_box_left",
    "RTG_SPREAD_REDRAW_gray_outer_long_side_box_right",
    "RTG_SPREAD_REDRAW_light_outer_long_side_top_edge_left",
    "RTG_SPREAD_REDRAW_light_outer_long_side_top_edge_right",
    "RTG_SPREAD_REDRAW_light_top_plate_on_gray_main_beam",
}


def local_y_bounds(obj: bpy.types.Object, hoist: bpy.types.Object) -> tuple[float, float]:
    transform = hoist.matrix_world.inverted() @ obj.matrix_world
    points = [transform @ vertex.co for vertex in obj.data.vertices]
    return min(point.y for point in points), max(point.y for point in points)


def create_controller(
    name: str,
    hoist: bpy.types.Object,
) -> bpy.types.Object:
    controller = bpy.data.objects.get(name)
    if controller is None:
        controller = bpy.data.objects.new(name, None)
        hoist.users_collection[0].objects.link(controller)
    controller.parent = hoist
    controller.matrix_parent_inverse.identity()
    controller.location = (0.0, 0.0, 0.0)
    controller.rotation_euler = (0.0, 0.0, 0.0)
    controller.scale = (1.0, 1.0, 1.0)
    controller.empty_display_type = "ARROWS"
    controller.empty_display_size = 0.30
    return controller


def create_box_mesh(
    name: str,
    dimensions: tuple[float, float, float],
) -> bpy.types.Mesh:
    half_x, half_y, half_z = (value * 0.5 for value in dimensions)
    vertices = [
        (-half_x, -half_y, -half_z),
        (half_x, -half_y, -half_z),
        (half_x, half_y, -half_z),
        (-half_x, half_y, -half_z),
        (-half_x, -half_y, half_z),
        (half_x, -half_y, half_z),
        (half_x, half_y, half_z),
        (-half_x, half_y, half_z),
    ]
    faces = [
        (0, 1, 2, 3),
        (4, 7, 6, 5),
        (0, 4, 5, 1),
        (1, 5, 6, 2),
        (2, 6, 7, 3),
        (4, 0, 3, 7),
    ]
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    return mesh


def create_box(
    name: str,
    parent: bpy.types.Object,
    center: tuple[float, float, float],
    dimensions: tuple[float, float, float],
    material: bpy.types.Material,
) -> bpy.types.Object:
    old = bpy.data.objects.get(name)
    if old is not None:
        bpy.data.objects.remove(old, do_unlink=True)
    obj = bpy.data.objects.new(name, create_box_mesh(name, dimensions))
    parent.users_collection[0].objects.link(obj)
    obj.parent = parent
    obj.matrix_parent_inverse.identity()
    obj.location = center
    obj.data.materials.append(material)
    obj["spreader:telescopicPart"] = True
    return obj


def dark_telescopic_material(source: bpy.types.Material) -> bpy.types.Material:
    name = "rtg_spreader_telescopic_inner_steel"
    material = bpy.data.materials.get(name)
    if material is None:
        material = source.copy()
        material.name = name
        material.diffuse_color = (0.12, 0.15, 0.17, 1.0)
        if material.use_nodes:
            shader = material.node_tree.nodes.get("Principled BSDF")
            if shader is not None:
                shader.inputs["Base Color"].default_value = (0.12, 0.15, 0.17, 1.0)
                shader.inputs["Metallic"].default_value = 0.55
                shader.inputs["Roughness"].default_value = 0.34
    return material


def parent_keep_world(
    obj: bpy.types.Object,
    parent: bpy.types.Object,
) -> None:
    world = obj.matrix_world.copy()
    local = parent.matrix_world.inverted() @ world
    obj.parent = parent
    obj.matrix_parent_inverse.identity()
    obj.matrix_basis = local


def build_telescopic_frame(
    hoist: bpy.types.Object,
    front: bpy.types.Object,
    rear: bpy.types.Object,
) -> None:
    warm_gray = bpy.data.materials["rtg_spreader_warm_gray_painted_steel"]
    highlight = bpy.data.materials["rtg_spreader_light_edge_highlight"]
    hose_black = bpy.data.materials["rtg_spreader_hydraulic_hose_black"]
    inner_steel = dark_telescopic_material(warm_gray)

    # Outer sleeves stay fixed beneath the central rope and sheave assembly.
    create_box(
        "RTG_SPREADER_TELESCOPE_center_main_sleeve",
        hoist,
        (2.549, SPREADER_CENTER_Y, 3.01),
        (0.19, 2.50, 0.30),
        warm_gray,
    )
    create_box(
        "RTG_SPREADER_TELESCOPE_center_top_cap",
        hoist,
        (2.549, SPREADER_CENTER_Y, 3.177),
        (0.21, 2.42, 0.045),
        highlight,
    )
    for side, x in (("left", 2.213), ("right", 2.885)):
        create_box(
            f"RTG_SPREADER_TELESCOPE_center_side_sleeve_{side}",
            hoist,
            (x, SPREADER_CENTER_Y, 2.81),
            (0.075, 2.50, 0.23),
            warm_gray,
        )
        create_box(
            f"RTG_SPREADER_TELESCOPE_center_side_top_{side}",
            hoist,
            (x, SPREADER_CENTER_Y, 2.952),
            (0.085, 2.42, 0.040),
            highlight,
        )

    # Rigid inner beams move with each end carriage and disappear into the
    # larger central sleeves when the spreader contracts to 20 ft.
    arm_length = 2.26
    for end, parent, y in (
        ("front", front, -6.23),
        ("rear", rear, -3.77),
    ):
        create_box(
            f"RTG_SPREADER_TELESCOPE_inner_main_{end}",
            parent,
            (2.549, y, 3.01),
            (0.135, arm_length, 0.21),
            inner_steel,
        )
        for side, x in (("left", 2.213), ("right", 2.885)):
            create_box(
                f"RTG_SPREADER_TELESCOPE_inner_side_{end}_{side}",
                parent,
                (x, y, 2.81),
                (0.050, arm_length, 0.16),
                inner_steel,
            )
        create_box(
            f"RTG_SPREADER_TELESCOPE_hose_{end}",
            parent,
            (2.549, y, 3.235),
            (0.018, arm_length, 0.018),
            hose_black,
        )


def main() -> None:
    hoist = bpy.data.objects.get(HOIST_NAME)
    if hoist is None:
        raise RuntimeError(f"Missing hoist controller: {HOIST_NAME}")
    if int(hoist.get("rtg_spreader_telescoping_version", 0)) == MODEL_VERSION:
        print("RTG spreader telescoping model is already installed")
        return

    front = create_controller(FRONT_CTRL_NAME, hoist)
    rear = create_controller(REAR_CTRL_NAME, hoist)
    bpy.context.view_layer.update()

    moved_front = 0
    moved_rear = 0
    hidden_longitudinal = 0
    for obj in list(hoist.children_recursive):
        if obj.type != "MESH" or not obj.name.startswith(SPREADER_PREFIX):
            continue
        if obj.name in LONGITUDINAL_SOURCE_NAMES:
            obj.hide_viewport = True
            obj.hide_render = True
            hidden_longitudinal += 1
            continue
        min_y, max_y = local_y_bounds(obj, hoist)
        center_y = (min_y + max_y) * 0.5
        if center_y <= FRONT_GROUP_MAX_Y:
            parent_keep_world(obj, front)
            moved_front += 1
        elif center_y >= REAR_GROUP_MIN_Y:
            parent_keep_world(obj, rear)
            moved_rear += 1

    build_telescopic_frame(hoist, front, rear)

    front["spreader:twentyFootTranslateY"] = TELESCOPE_TRAVEL_M
    front["spreader:fortyFootTranslateY"] = 0.0
    rear["spreader:twentyFootTranslateY"] = -TELESCOPE_TRAVEL_M
    rear["spreader:fortyFootTranslateY"] = 0.0
    hoist["rtg_spreader_telescoping_version"] = MODEL_VERSION
    hoist["rtg_spreader_default_size_ft"] = 40
    hoist["rtg_spreader_telescopic_travel_m"] = TELESCOPE_TRAVEL_M

    bpy.context.view_layer.update()
    bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)
    print(
        "Installed RTG 20/40 ft telescopic spreader: "
        f"travel={TELESCOPE_TRAVEL_M:.4f} m per end, "
        f"frontObjects={moved_front}, rearObjects={moved_rear}, "
        f"replacedLongitudinal={hidden_longitudinal}"
    )


if __name__ == "__main__":
    main()
