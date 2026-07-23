"""Build the real-scale 1C-4C yard-bay layout in the Blender source scene.

The generated geometry is intentionally lightweight: each block uses one
combined pad mesh, one combined marking mesh, and logical Empty anchors for
runtime container placement.  Re-running the script replaces only the owned
layout objects and leaves the RTGs, ships, roads, and surrounding port intact.
"""

from __future__ import annotations

from pathlib import Path
import sys

import bpy

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.append(str(SCRIPT_DIR))

from yard_coordinate_mapping import BAY_WIDTH_M


LAYOUT_ROOT_NAME = "YARD_LAYOUT_1C4C_REAL_SCALE"
SURFACE_COLLECTION_NAME = "YARD_LAYOUT_1C4C_SURFACES"
MARKING_COLLECTION_NAME = "YARD_LAYOUT_1C4C_MARKINGS"
ANCHOR_COLLECTION_NAME = "YARD_LAYOUT_1C4C_BAY_ANCHORS"
LABEL_COLLECTION_NAME = "YARD_LAYOUT_1C4C_LABELS"

BLOCKS = (("1C", 91), ("2C", 91), ("3C", 91), ("4C", 41))
BAY_LENGTH_M = 2.475
BAY_GAP_M = 0.20
GROUND_SLOT_BAY_STRIDE = 2
GROUND_SLOT_LENGTH_M = BAY_LENGTH_M
BLOCK_GAP_M = 5.0
START_Y_M = -0.50

APRON_WIDTH_M = 16.0
GANTRY_RAIL_X_M = 6.50
PAD_TOP_Z_M = 0.10
MARKING_TOP_Z_M = 0.135

OWNED_OBJECT_PREFIXES = (
    "YARD_1C_",
    "YARD_2C_",
    "YARD_3C_",
    "YARD_4C_",
    "SMART_PORT_1C4C_",
    "SMART_PORT_YARD_LABEL_1C4C_",
    "YARD_LAYOUT_1C4C_",
)
OWNED_COLLECTIONS = {
    "SMART_PORT_1C4C_container_fill_dense",
    "SMART_PORT_1C4C_gantry_clearance_lanes",
    "SMART_PORT_YARD_LABELS_1C4C_ground_markings",
    LAYOUT_ROOT_NAME,
    SURFACE_COLLECTION_NAME,
    MARKING_COLLECTION_NAME,
    ANCHOR_COLLECTION_NAME,
    LABEL_COLLECTION_NAME,
}

# Existing continuous side-yard families that should share the same longitudinal
# extent as the regenerated central 1C-4C layout.  Short container objects and
# local details are excluded by the minimum-span check.
CONTINUOUS_YARD_PREFIXES = (
    "SMART_PORT_EXTRA_YARD_left_long_side_yard_",
    "SMART_PORT_EXTRA_YARD_right_long_side_yard_",
    "SMART_PORT_SIDEYARD_left_",
    "SMART_PORT_SIDEYARD_right_",
    "SMART_PORT_OUTER_SIDEYARD_left_outer_circled_yard_",
    "SMART_PORT_OUTER_SIDEYARD_right_outer_circled_yard_",
    "LIVE_internal_truck_lane_",
)
CONTINUOUS_YARD_MINIMUM_OLD_SPAN_M = 350.0

# Long gaps left between the existing authored yard families after they are
# extended.  These correspond to the three blue strips visible behind the
# original short concrete slabs in the Blender overview.
INFILL_YARDS = (
    ("LEFT_CENTRAL", -32.0, -8.0),
    ("RIGHT_CENTRAL", 8.0, 37.5),
    ("RIGHT_NARROW", 60.0, 67.0),
)


def block_length(bay_count: int) -> float:
    odd_slot_count = (bay_count + 1) // 2
    return odd_slot_count * BAY_LENGTH_M + (odd_slot_count - 1) * BAY_GAP_M


def bay_local_start(bay_number: int) -> float:
    bay = int(bay_number)
    if bay % 2 == 1:
        odd_slot_index = (bay - 1) // 2
        return odd_slot_index * (BAY_LENGTH_M + BAY_GAP_M)
    return bay_local_start(bay - 1)


def bay_local_end(bay_number: int) -> float:
    bay = int(bay_number)
    if bay % 2 == 1:
        return bay_local_start(bay) + BAY_LENGTH_M
    return bay_local_end(bay + 1)


def bay_local_center(bay_number: int) -> float:
    return (bay_local_start(bay_number) + bay_local_end(bay_number)) * 0.5


def odd_ground_slot_numbers(bay_count: int) -> list[int]:
    """Ground marks use the real-yard convention: odd bay numbers only.

    Each odd mark represents a 40 ft work slot that visually spans two 20 ft
    bay units.  If the block ends on an odd bay, the final marker is kept as a
    terminal marker so the operator still sees the documented end bay.
    """

    return list(range(1, bay_count + 1, GROUND_SLOT_BAY_STRIDE))


def ground_slot_bounds(block_start_y: float, block_end_y: float, odd_bay_number: int) -> tuple[float, float]:
    slot_start = block_start_y + bay_local_start(odd_bay_number)
    slot_end = min(slot_start + GROUND_SLOT_LENGTH_M, block_end_y)
    return slot_start, slot_end


def get_material(name: str, color: tuple[float, float, float, float], metallic=0.0, roughness=0.65):
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.diffuse_color = color
    material.metallic = metallic
    material.roughness = roughness
    return material


def ensure_collection(name: str, parent: bpy.types.Collection) -> bpy.types.Collection:
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
    if collection.name not in parent.children:
        parent.children.link(collection)
    return collection


def add_box_geometry(vertices, faces, center, size) -> None:
    cx, cy, cz = center
    sx, sy, sz = (dimension * 0.5 for dimension in size)
    start = len(vertices)
    vertices.extend(
        (
            (cx - sx, cy - sy, cz - sz),
            (cx + sx, cy - sy, cz - sz),
            (cx + sx, cy + sy, cz - sz),
            (cx - sx, cy + sy, cz - sz),
            (cx - sx, cy - sy, cz + sz),
            (cx + sx, cy - sy, cz + sz),
            (cx + sx, cy + sy, cz + sz),
            (cx - sx, cy + sy, cz + sz),
        )
    )
    faces.extend(
        (
            (start + 0, start + 1, start + 2, start + 3),
            (start + 4, start + 7, start + 6, start + 5),
            (start + 0, start + 4, start + 5, start + 1),
            (start + 1, start + 5, start + 6, start + 2),
            (start + 2, start + 6, start + 7, start + 3),
            (start + 4, start + 0, start + 3, start + 7),
        )
    )


def create_boxes_object(name, boxes, material, collection) -> bpy.types.Object:
    vertices = []
    faces = []
    for center, size in boxes:
        add_box_geometry(vertices, faces, center, size)
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(material)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    return obj


def create_text(name, body, location, size, material, collection) -> bpy.types.Object:
    curve = bpy.data.curves.new(f"{name}_font", type="FONT")
    curve.body = body
    curve.align_x = "CENTER"
    curve.align_y = "CENTER"
    curve.size = size
    curve.extrude = 0.012
    curve.bevel_depth = 0.006
    curve.materials.append(material)
    obj = bpy.data.objects.new(name, curve)
    obj.location = location
    collection.objects.link(obj)
    return obj


def remove_previous_layout() -> int:
    removed = 0
    for obj in list(bpy.data.objects):
        if obj.name.startswith(OWNED_OBJECT_PREFIXES):
            bpy.data.objects.remove(obj, do_unlink=True)
            removed += 1
    for collection in list(bpy.data.collections):
        if collection.name in OWNED_COLLECTIONS:
            bpy.data.collections.remove(collection)
    return removed


def hide_legacy_four_yard() -> int:
    hidden = 0
    for obj in bpy.data.objects:
        if obj.name.startswith("SMART_PORT_four_yard_"):
            obj.hide_viewport = True
            obj.hide_render = True
            obj["yard_layout_replaced"] = True
            hidden += 1
    return hidden


def world_y_bounds(obj: bpy.types.Object) -> tuple[float, float]:
    points = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
    return min(point.y for point in points), max(point.y for point in points)


def remap_mesh_world_y(
    obj: bpy.types.Object, target_start_y: float, target_end_y: float
) -> None:
    if obj.data.users > 1:
        obj.data = obj.data.copy()
    source_start_y, source_end_y = world_y_bounds(obj)
    source_span = source_end_y - source_start_y
    if source_span <= 1.0e-6:
        return
    target_span = target_end_y - target_start_y
    inverse = obj.matrix_world.inverted()
    for vertex in obj.data.vertices:
        world = obj.matrix_world @ vertex.co
        ratio = (world.y - source_start_y) / source_span
        world.y = target_start_y + ratio * target_span
        vertex.co = inverse @ world
    obj.data.update()


def extend_continuous_yards(target_start_y: float, target_end_y: float) -> list[str]:
    extended = []
    for obj in bpy.data.objects:
        if obj.type != "MESH" or not obj.name.startswith(CONTINUOUS_YARD_PREFIXES):
            continue
        source_start_y, source_end_y = world_y_bounds(obj)
        if source_end_y - source_start_y < CONTINUOUS_YARD_MINIMUM_OLD_SPAN_M:
            continue
        if "yard_original_y_min" not in obj:
            obj["yard_original_y_min"] = source_start_y
            obj["yard_original_y_max"] = source_end_y
        remap_mesh_world_y(obj, target_start_y, target_end_y)
        obj["yard_target_y_min"] = target_start_y
        obj["yard_target_y_max"] = target_end_y
        obj["yard_target_length_m"] = target_end_y - target_start_y
        extended.append(obj.name)

    # The outer side-yard end caps are horizontal, so they are repositioned
    # rather than stretched by the long-object pass above.
    for obj in bpy.data.objects:
        if obj.type != "MESH" or not obj.name.startswith(
            (
                "SMART_PORT_OUTER_SIDEYARD_left_outer_circled_yard_",
                "SMART_PORT_OUTER_SIDEYARD_right_outer_circled_yard_",
            )
        ):
            continue
        target_y = None
        if "front_yellow_edge" in obj.name:
            target_y = target_start_y
        elif "back_yellow_edge" in obj.name:
            target_y = target_end_y
        if target_y is None:
            continue
        current_start, current_end = world_y_bounds(obj)
        obj.location.y += target_y - (current_start + current_end) * 0.5
        obj["yard_target_end_marker_y"] = target_y
        extended.append(obj.name)
    return extended


def save_recovery_copy() -> Path | None:
    if not bpy.data.filepath:
        return None
    project_root = Path(bpy.data.filepath).parent.parent
    backup = project_root / "tmp" / "blender_backups" / "RTG_Model_before_real_scale_yard.blend"
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(backup), copy=True)
    return backup


def build_yard_layout() -> dict[str, object]:
    backup = save_recovery_copy()
    removed = remove_previous_layout()
    legacy_hidden = hide_legacy_four_yard()

    scene_collection = bpy.context.scene.collection
    root_collection = ensure_collection(LAYOUT_ROOT_NAME, scene_collection)
    surface_collection = ensure_collection(SURFACE_COLLECTION_NAME, root_collection)
    marking_collection = ensure_collection(MARKING_COLLECTION_NAME, root_collection)
    anchor_collection = ensure_collection(ANCHOR_COLLECTION_NAME, root_collection)
    label_collection = ensure_collection(LABEL_COLLECTION_NAME, root_collection)

    asphalt = get_material("YARD_LAYOUT_Asphalt", (0.055, 0.065, 0.075, 1.0), roughness=0.88)
    pad_materials = {
        "1C": get_material("YARD_LAYOUT_1C_Pad", (0.12, 0.18, 0.20, 1.0), roughness=0.82),
        "2C": get_material("YARD_LAYOUT_2C_Pad", (0.14, 0.20, 0.18, 1.0), roughness=0.82),
        "3C": get_material("YARD_LAYOUT_3C_Pad", (0.18, 0.18, 0.13, 1.0), roughness=0.82),
        "4C": get_material("YARD_LAYOUT_4C_Pad", (0.18, 0.14, 0.16, 1.0), roughness=0.82),
    }
    white = get_material("YARD_LAYOUT_WhiteMarking", (0.90, 0.92, 0.90, 1.0), roughness=0.55)
    bay_number_gray = get_material("YARD_LAYOUT_BayNumberGrey", (0.68, 0.70, 0.68, 1.0), roughness=0.48)
    bay_number_backing = get_material("YARD_LAYOUT_BayNumberDarkBacking", (0.030, 0.035, 0.035, 1.0), roughness=0.72)
    yellow = get_material("YARD_LAYOUT_YellowMarking", (0.95, 0.62, 0.03, 1.0), roughness=0.50)
    rail = get_material("YARD_LAYOUT_GantryRail", (0.035, 0.040, 0.045, 1.0), metallic=0.75, roughness=0.35)
    infill_materials = {
        "LEFT_CENTRAL": get_material("YARD_LAYOUT_LeftCentralInfill", (0.115, 0.125, 0.135, 1.0), roughness=0.86),
        "RIGHT_CENTRAL": get_material("YARD_LAYOUT_RightCentralInfill", (0.105, 0.120, 0.130, 1.0), roughness=0.86),
        "RIGHT_NARROW": get_material("YARD_LAYOUT_RightNarrowInfill", (0.125, 0.130, 0.135, 1.0), roughness=0.86),
    }

    total_length = sum(block_length(count) for _, count in BLOCKS) + BLOCK_GAP_M * (len(BLOCKS) - 1)
    target_end_y = START_Y_M + total_length
    extended_continuous_yards = extend_continuous_yards(START_Y_M, target_end_y)
    apron_center_y = START_Y_M + total_length * 0.5
    apron = create_boxes_object(
        "YARD_LAYOUT_1C4C_CONTINUOUS_APRON",
        [((0.0, apron_center_y, 0.025), (APRON_WIDTH_M, total_length, 0.05))],
        asphalt,
        surface_collection,
    )
    apron["total_length_m"] = total_length
    apron["block_gap_m"] = BLOCK_GAP_M

    infill_results = []
    for infill_name, x_min, x_max in INFILL_YARDS:
        width = x_max - x_min
        center_x = (x_min + x_max) * 0.5
        infill = create_boxes_object(
            f"YARD_LAYOUT_1C4C_INFILL_{infill_name}",
            [((center_x, apron_center_y, 0.055), (width, total_length, 0.05))],
            infill_materials[infill_name],
            surface_collection,
        )
        infill["yard_infill_role"] = infill_name
        infill["x_min_m"] = x_min
        infill["x_max_m"] = x_max
        infill["target_length_m"] = total_length

        infill_boundaries = create_boxes_object(
            f"YARD_LAYOUT_1C4C_INFILL_{infill_name}_BOUNDARIES",
            [
                ((x_min + 0.04, apron_center_y, MARKING_TOP_Z_M), (0.08, total_length, 0.02)),
                ((x_max - 0.04, apron_center_y, MARKING_TOP_Z_M), (0.08, total_length, 0.02)),
            ],
            yellow,
            marking_collection,
        )
        infill_boundaries["yard_infill_role"] = infill_name
        infill_results.append(
            {"name": infill_name, "x_min": x_min, "x_max": x_max, "width": width}
        )

    root = bpy.data.objects.new("YARD_LAYOUT_1C4C_ROOT", None)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = 0.5
    root_collection.objects.link(root)
    root["layout_version"] = 3
    root["bay_length_m"] = BAY_LENGTH_M
    root["bay_width_m"] = BAY_WIDTH_M
    root["bay_gap_m"] = BAY_GAP_M
    root["ground_mark_rule"] = "odd bay marks only; one visual ground slot spans two 20ft bay units"
    root["ground_slot_length_m"] = GROUND_SLOT_LENGTH_M
    root["block_gap_m"] = BLOCK_GAP_M
    root["total_length_m"] = total_length

    block_results = []
    current_y = START_Y_M
    for block_name, bay_count in BLOCKS:
        length = block_length(bay_count)
        end_y = current_y + length
        center_y = current_y + length * 0.5

        ground_slot_boxes = []
        ground_slot_separator_boxes = []
        bay_number_panel_boxes = []
        marker_panels = []
        for odd_bay_number in odd_ground_slot_numbers(bay_count):
            slot_start, slot_end = ground_slot_bounds(current_y, end_y, odd_bay_number)
            slot_length = slot_end - slot_start
            slot_center = (slot_start + slot_end) * 0.5
            ground_slot_boxes.append(
                ((0.0, slot_center, PAD_TOP_Z_M - 0.015), (BAY_WIDTH_M, slot_length, 0.03))
            )
            if slot_end < end_y - 1.0e-4:
                gap_center = slot_end + BAY_GAP_M * 0.5
                ground_slot_separator_boxes.append(
                    ((0.0, gap_center, MARKING_TOP_Z_M), (BAY_WIDTH_M, 0.055, 0.015))
                )

        for index in range(bay_count):
            bay_number = index + 1
            bay_center = current_y + bay_local_center(bay_number)

            anchor = bpy.data.objects.new(
                f"YARD_LAYOUT_1C4C_ANCHOR_{block_name}_BAY_{bay_number:03d}", None
            )
            anchor.location = (0.0, bay_center, PAD_TOP_Z_M)
            anchor.empty_display_type = "PLAIN_AXES"
            anchor.empty_display_size = 0.08
            anchor.parent = root
            anchor["yard_block"] = block_name
            anchor["bay_number"] = bay_number
            anchor["bay_id"] = f"{block_name}/{bay_number:03d}"
            anchor["bay_length_m"] = BAY_LENGTH_M
            anchor["bay_width_m"] = BAY_WIDTH_M
            anchor["ground_mark_visible"] = bay_number % GROUND_SLOT_BAY_STRIDE == 1
            anchor_collection.objects.link(anchor)

        pad = create_boxes_object(
            f"YARD_LAYOUT_1C4C_{block_name}_{bay_count:03d}_ODD_BAY_40FT_SLOTS",
            ground_slot_boxes,
            pad_materials[block_name],
            surface_collection,
        )
        pad.parent = root
        pad["yard_block"] = block_name
        pad["bay_count"] = bay_count
        pad["ground_slot_rule"] = "odd bay marks, one visual slot spans two 20ft bay units"
        pad["ground_slot_count"] = len(ground_slot_boxes)
        pad["ground_slot_length_m"] = GROUND_SLOT_LENGTH_M
        pad["block_start_y_m"] = current_y
        pad["block_end_y_m"] = end_y
        pad["block_length_m"] = length

        if ground_slot_separator_boxes:
            separators = create_boxes_object(
                f"YARD_LAYOUT_1C4C_{block_name}_ODD_BAY_40FT_SLOT_SEPARATORS",
                ground_slot_separator_boxes,
                white,
                marking_collection,
            )
            separators.parent = root
            separators["yard_block"] = block_name
            separators["ground_slot_rule"] = "separator between visible odd bay 40ft slots"

        boundary_boxes = [
            ((-BAY_WIDTH_M * 0.5, center_y, MARKING_TOP_Z_M), (0.08, length, 0.02)),
            ((BAY_WIDTH_M * 0.5, center_y, MARKING_TOP_Z_M), (0.08, length, 0.02)),
            ((0.0, current_y, MARKING_TOP_Z_M), (BAY_WIDTH_M, 0.08, 0.02)),
            ((0.0, end_y, MARKING_TOP_Z_M), (BAY_WIDTH_M, 0.08, 0.02)),
        ]
        boundaries = create_boxes_object(
            f"YARD_LAYOUT_1C4C_{block_name}_OUTER_BOUNDARIES",
            boundary_boxes,
            yellow,
            marking_collection,
        )
        boundaries.parent = root

        rails = create_boxes_object(
            f"YARD_LAYOUT_1C4C_{block_name}_GANTRY_RAILS",
            [
                ((-GANTRY_RAIL_X_M, center_y, MARKING_TOP_Z_M), (0.10, length, 0.035)),
                ((GANTRY_RAIL_X_M, center_y, MARKING_TOP_Z_M), (0.10, length, 0.035)),
            ],
            rail,
            marking_collection,
        )
        rails.parent = root

        label_numbers = odd_ground_slot_numbers(bay_count)
        for bay_number in label_numbers:
            slot_start, slot_end = ground_slot_bounds(current_y, end_y, bay_number)
            bay_center = (slot_start + slot_end) * 0.5
            bay_number_panel_boxes.append(
                ((0.0, bay_center, MARKING_TOP_Z_M + 0.004), (2.25, 1.55, 0.018))
            )
            label = create_text(
                f"YARD_LAYOUT_1C4C_LABEL_{block_name}_BAY_{bay_number:03d}",
                f"{bay_number:02d}",
                (0.0, bay_center, MARKING_TOP_Z_M + 0.030),
                0.95,
                bay_number_gray,
                label_collection,
            )
            label.parent = root
            label["yard_block"] = block_name
            label["bay_number"] = bay_number
            label["ground_mark_rule"] = "odd bay number centered in a 40ft visual slot"

        bay_number_panels = create_boxes_object(
            f"YARD_LAYOUT_1C4C_{block_name}_ODD_BAY_NUMBER_BACKINGS",
            bay_number_panel_boxes,
            bay_number_backing,
            marking_collection,
        )
        bay_number_panels.parent = root
        bay_number_panels["yard_block"] = block_name
        bay_number_panels["ground_mark_rule"] = "dark backing behind enlarged grey odd bay numbers"

        for suffix, label_y in (("START", current_y + 3.0), ("END", end_y - 3.0)):
            marker_panels.append(((-5.20, label_y, MARKING_TOP_Z_M), (1.8, 2.2, 0.025)))
            block_label = create_text(
                f"YARD_LAYOUT_1C4C_LABEL_{block_name}_{suffix}",
                block_name,
                (-5.20, label_y, MARKING_TOP_Z_M + 0.02),
                1.05,
                yellow,
                label_collection,
            )
            block_label.parent = root
        panels = create_boxes_object(
            f"YARD_LAYOUT_1C4C_{block_name}_LABEL_PANELS",
            marker_panels,
            asphalt,
            marking_collection,
        )
        panels.parent = root

        block_results.append(
            {
                "name": block_name,
                "bays": bay_count,
                "start_y": current_y,
                "end_y": end_y,
                "length": length,
            }
        )
        current_y = end_y + BLOCK_GAP_M

    bpy.context.scene["yard_layout_spec"] = (
        "1C-3C:91 bays; 4C:41 bays; ground shows odd bay marks only "
        "(01,03,...); each visible ground slot spans two 20ft bay units; "
        "bay gap 0.2m; block gap 5m"
    )
    bpy.context.scene["yard_layout_total_length_m"] = total_length
    bpy.context.view_layer.update()
    if bpy.data.filepath:
        bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)

    result = {
        "backup": str(backup) if backup else None,
        "removed_old_objects": removed,
        "hidden_legacy_objects": legacy_hidden,
        "extended_continuous_yards": len(extended_continuous_yards),
        "infill_yards": infill_results,
        "total_length_m": total_length,
        "total_bays": sum(count for _, count in BLOCKS),
        "blocks": block_results,
    }
    print("Real-scale yard layout complete:", result)
    return result


if __name__ == "__main__":
    build_yard_layout()
