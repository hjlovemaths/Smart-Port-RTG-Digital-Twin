"""Remove the left and right STS cranes from the Blender source model.

Run in Blender:

    blender --background blender/RTG_Model.blend --python blender/remove_side_sts_cranes.py

The script is idempotent. It deletes the two side STS crane object sets and
keeps the center crane unchanged.
"""

from __future__ import annotations

import bpy


SIDE_STS_PREFIXES = (
    "CODEx_STS_Crane0",
    "CODEx_STS_Crane3",
)


def remove_prefixed_objects(prefix: str) -> int:
    count = 0
    for obj in list(bpy.data.objects):
        if obj.name.startswith(prefix):
            bpy.data.objects.remove(obj, do_unlink=True)
            count += 1
    return count


def save_model() -> None:
    if not bpy.data.filepath:
        raise RuntimeError("Open blender/RTG_Model.blend before running this script")
    bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)


def main() -> None:
    result = {prefix: remove_prefixed_objects(prefix) for prefix in SIDE_STS_PREFIXES}
    bpy.context.scene["side_sts_cranes_removed"] = True
    bpy.context.view_layer.update()
    save_model()
    print("Removed side STS cranes:", result)


if __name__ == "__main__":
    main()
