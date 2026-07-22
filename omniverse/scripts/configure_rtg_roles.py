"""Separate the three yard RTGs into one dynamic primary and two static visuals.

Run this script from Blender with ``blender/RTG_Model.blend`` open.  It is
idempotent: re-running it keeps the same hierarchy and only repairs misplaced
objects.  World transforms are preserved so the two static RTGs do not jump
when detached from the animated middle RTG controllers.
"""

from __future__ import annotations

from pathlib import Path

import bpy


PRIMARY_ROOT_NAME = "RTG_PRIMARY_DYNAMIC"
GANTRY_CTRL_NAME = "ANIM_CTRL_RTG_GANTRY_TRAVEL"
TROLLEY_CTRL_NAME = "ANIM_CTRL_RTG_TROLLEY_TRAVEL"
HOIST_CTRL_NAME = "ANIM_CTRL_RTG_HOIST_VERTICAL"

STATIC_SIDES = {
    "LEFT": "CODEx_left_yard_detailed_rtg_",
    "RIGHT": "CODEx_right_yard_detailed_rtg_",
}

# Small details authored after the original controller hierarchy was created.
# They visually belong to the primary gantry but were left at the scene root,
# so they stayed behind when gantry travel started.
PRIMARY_GANTRY_ACCESSORY_PREFIXES = (
    "silver_conduit_above_beam_",
    "under_beam_floodlight_",
)


def link_empty(name: str, parent: bpy.types.Object | None = None) -> bpy.types.Object:
    obj = bpy.data.objects.get(name)
    if obj is None:
        obj = bpy.data.objects.new(name, None)
        bpy.context.scene.collection.objects.link(obj)
    elif obj.type != "EMPTY":
        raise TypeError(f"Expected EMPTY for {name}, found {obj.type}")

    obj.empty_display_type = "PLAIN_AXES"
    obj.empty_display_size = 1.0
    if obj.parent is not parent:
        world = obj.matrix_world.copy()
        obj.parent = parent
        obj.matrix_world = world
    return obj


def reparent_keep_world(obj: bpy.types.Object, parent: bpy.types.Object) -> bool:
    if obj.parent is parent:
        return False
    world = obj.matrix_world.copy()
    obj.parent = parent
    obj.matrix_world = world
    return True


def has_ancestor(obj: bpy.types.Object, ancestor_name: str) -> bool:
    current = obj.parent
    while current is not None:
        if current.name == ancestor_name:
            return True
        current = current.parent
    return False


def save_recovery_copy() -> Path | None:
    if not bpy.data.filepath:
        return None
    project_root = Path(bpy.data.filepath).parent.parent
    backup = project_root / "tmp" / "blender_backups" / "RTG_Model_before_role_split.blend"
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(backup), copy=True)
    return backup


def configure_roles() -> None:
    backup = save_recovery_copy()

    gantry = bpy.data.objects.get(GANTRY_CTRL_NAME)
    trolley = bpy.data.objects.get(TROLLEY_CTRL_NAME)
    hoist = bpy.data.objects.get(HOIST_CTRL_NAME)
    missing = [
        name
        for name, obj in (
            (GANTRY_CTRL_NAME, gantry),
            (TROLLEY_CTRL_NAME, trolley),
            (HOIST_CTRL_NAME, hoist),
        )
        if obj is None
    ]
    if missing:
        raise RuntimeError(f"Missing primary RTG controllers: {missing}")

    primary_root = link_empty(PRIMARY_ROOT_NAME)
    primary_root["rtg_id"] = "RTG_PRIMARY_01"
    primary_root["rtg_role"] = "primary_dynamic"
    primary_root["rtg_description"] = "Middle RTG selected as the SimReady production asset"
    reparent_keep_world(gantry, primary_root)

    primary_accessories = [
        obj
        for obj in bpy.data.objects
        if obj.name.startswith(PRIMARY_GANTRY_ACCESSORY_PREFIXES)
    ]
    repaired_primary_accessories = sum(
        int(reparent_keep_world(obj, gantry)) for obj in primary_accessories
    )

    for controller, system in (
        (gantry, "gantry_travel"),
        (trolley, "trolley_travel"),
        (hoist, "hoist_vertical"),
    ):
        controller["rtg_id"] = "RTG_PRIMARY_01"
        controller["rtg_role"] = "primary_dynamic"
        controller["rtg_motion_system"] = system

    moved_counts: dict[str, int] = {}
    object_counts: dict[str, int] = {}

    for side, prefix in STATIC_SIDES.items():
        root = link_empty(f"RTG_STATIC_{side}")
        root["rtg_id"] = f"RTG_STATIC_{side}_01"
        root["rtg_role"] = "static_visual"
        root["physics_enabled"] = False

        groups = {
            "gantry": link_empty(f"RTG_STATIC_{side}_GANTRY_VISUAL", root),
            "trolley": link_empty(f"RTG_STATIC_{side}_TROLLEY_VISUAL", root),
            "hoist": link_empty(f"RTG_STATIC_{side}_HOIST_VISUAL", root),
        }

        objects = [obj for obj in bpy.data.objects if obj.name.startswith(prefix)]
        moved = 0
        for obj in objects:
            if (
                has_ancestor(obj, HOIST_CTRL_NAME)
                or "ROPE" in obj.name.upper()
                or obj.parent is groups["hoist"]
            ):
                target = groups["hoist"]
            elif has_ancestor(obj, TROLLEY_CTRL_NAME) or obj.parent is groups["trolley"]:
                target = groups["trolley"]
            else:
                target = groups["gantry"]
            moved += int(reparent_keep_world(obj, target))

        object_counts[side] = len(objects)
        moved_counts[side] = moved

    bpy.context.view_layer.update()

    misplaced = []
    for side, prefix in STATIC_SIDES.items():
        for obj in bpy.data.objects:
            if obj.name.startswith(prefix) and has_ancestor(obj, PRIMARY_ROOT_NAME):
                misplaced.append(obj.name)
    if misplaced:
        raise RuntimeError(f"Static RTG objects remain under the primary controller: {misplaced[:10]}")

    bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)
    print("RTG role configuration complete")
    print("Primary:", PRIMARY_ROOT_NAME, "at current frame", bpy.context.scene.frame_current)
    print(
        "Primary gantry accessories:",
        len(primary_accessories),
        "reparented:",
        repaired_primary_accessories,
    )
    print("Static object counts:", object_counts)
    print("Reparented this run:", moved_counts)
    print("Recovery copy:", backup)


if __name__ == "__main__":
    configure_roles()
