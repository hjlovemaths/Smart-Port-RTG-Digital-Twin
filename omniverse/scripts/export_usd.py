"""Export the current Blender scene as the project's static validation USD asset."""

from pathlib import Path

import bpy


PROJECT_ROOT = Path(bpy.path.abspath("//")).parent
OUTPUT_PATH = PROJECT_ROOT / "omniverse" / "assets" / "rtg" / "RTG_Model.usdc"

# Blender-only backdrop geometry is replaced by an Omniverse dome/environment.
EXCLUDED_OBJECTS = {
    "CODEx_showcase_sea_blue_hemisphere_dome",
    "CODEx_showcase_light_blue_round_base_top",
}
KEEP_CAMERA = "Omniverse_User_View"
SEA_SURFACE_OBJECT = "CODEx_showcase_inside_circular_sea_surface"

# Static yard containers are intentionally omitted. Runtime applications will
# create container instances from live terminal data instead. This does not
# include the hoisted YLOAD container, the truck load, or ship deck cargo.
GROUND_CONTAINER_PREFIXES = (
    "SMART_PORT_yard_container_",
    "SMART_PORT_EXTRA_YARD_container_",
    "SMART_PORT_OUTER_SIDEYARD_container_",
    "SMART_PORT_SIDEYARD_left_container_",
    "SMART_PORT_SIDEYARD_right_container_",
    "SMART_PORT_SIDEYARD_left_rotated_container_",
    "SMART_PORT_SIDEYARD_right_rotated_container_",
    "SMART_PORT_rotated_quay_container_",
    "SMART_PORT_four_yard_far_stack_",
)


def is_ground_container(obj: bpy.types.Object) -> bool:
    return obj.name.startswith(GROUND_CONTAINER_PREFIXES)


def export_static_usd() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    view_layer = bpy.context.view_layer
    original_selection = [obj for obj in view_layer.objects if obj.select_get()]
    original_active = view_layer.objects.active
    depsgraph = bpy.context.evaluated_depsgraph_get()
    source_objects = list(view_layer.objects)
    temporary_objects = []
    temporary_collection = bpy.data.collections.new("__USD_EXPORT_TEMP_CURVES__")
    bpy.context.scene.collection.children.link(temporary_collection)
    sea_uv_created = False

    # The authored sea surface is a large single polygon without UVs. Add a
    # temporary planar UV set so the Omniverse water normal map can tile across
    # it, then remove the UV set after export to leave the .blend untouched.
    sea = bpy.data.objects.get(SEA_SURFACE_OBJECT)
    if sea is not None and sea.type == "MESH" and not sea.data.uv_layers:
        mesh = sea.data
        uv_layer = mesh.uv_layers.new(name="USD_WaterUV")
        xs = [vertex.co.x for vertex in mesh.vertices]
        ys = [vertex.co.y for vertex in mesh.vertices]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        size_x = max(max_x - min_x, 1.0e-6)
        size_y = max(max_y - min_y, 1.0e-6)
        for loop in mesh.loops:
            co = mesh.vertices[loop.vertex_index].co
            uv_layer.data[loop.index].uv = (
                (co.x - min_x) / size_x,
                (co.y - min_y) / size_y,
            )
        sea_uv_created = True

    # The Blender file contains hidden backup ships, construction references,
    # and inspection cameras.  The USD exporter otherwise walks the whole
    # scene, so explicitly select only what the active Blender view layer shows.
    for obj in source_objects:
        obj.select_set(False)

    selected_count = 0
    converted_curve_count = 0
    for obj in source_objects:
        is_kept_camera = obj.type == "CAMERA" and obj.name == KEEP_CAMERA
        is_regular_visible_object = (
            obj.type != "CAMERA"
            and obj.type != "LIGHT"
            and obj.name not in EXCLUDED_OBJECTS
            and not is_ground_container(obj)
            and obj.visible_get(view_layer=view_layer)
        )
        if is_regular_visible_object and obj.type == "CURVE":
            # Native BasisCurves are omitted below because Omniverse interprets
            # their width differently.  A temporary evaluated mesh preserves
            # Blender's bevel, taper, modifiers, materials, and world transform.
            evaluated = obj.evaluated_get(depsgraph)
            mesh = bpy.data.meshes.new_from_object(
                evaluated,
                preserve_all_data_layers=True,
                depsgraph=depsgraph,
            )
            temporary = bpy.data.objects.new(f"{obj.name}__USD_MESH", mesh)
            temporary.matrix_world = evaluated.matrix_world.copy()
            temporary_collection.objects.link(temporary)
            temporary.select_set(True)
            temporary_objects.append(temporary)
            selected_count += 1
            converted_curve_count += 1
        elif is_kept_camera or is_regular_visible_object:
            obj.select_set(True)
            selected_count += 1

    try:
        bpy.ops.wm.usd_export(
            filepath=str(OUTPUT_PATH),
            selected_objects_only=True,
            export_animation=False,
            export_materials=True,
            # Omniverse supplies the stage lighting. Blender preview lights and
            # their viewport gizmos are intentionally omitted.
            export_lights=False,
            export_cameras=True,
            # Export Blender curves as evaluated meshes. Native USD BasisCurves
            # can combine width and object scale differently in Omniverse,
            # turning thin cables and rails into oversized tubes.
            export_curves=False,
            # Match the authored Blender viewport while the explicit selection
            # above filters hidden backup/reference objects.
            evaluation_mode="VIEWPORT",
            generate_preview_surface=True,
            export_custom_properties=True,
            root_prim_path="/World",
        )
    finally:
        for obj in temporary_objects:
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        bpy.data.collections.remove(temporary_collection)

        if sea_uv_created:
            sea.data.uv_layers.remove(sea.data.uv_layers["USD_WaterUV"])

        for obj in view_layer.objects:
            obj.select_set(False)
        for obj in original_selection:
            if obj.name in view_layer.objects:
                obj.select_set(True)
        view_layer.objects.active = original_active

    print(
        f"Exported {selected_count} visible Blender objects to {OUTPUT_PATH}; "
        f"temporarily converted {converted_curve_count} curves to meshes"
    )


if __name__ == "__main__":
    export_static_usd()
