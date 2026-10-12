"""Build USD live-container geometry from ``yard_inventory.live.json``."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Iterable, Optional

from pxr import Gf, Sdf, Usd, UsdGeom


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.append(str(SCRIPT_DIR))

from build_live_containers import (  # noqa: E402
    CONTAINER_COLORS,
    LIVE_ROOT_PATH,
    MAX_STACK_HEIGHT_Z_M,
    OUTPUT_PATH as DEFAULT_OUTPUT_PATH,
    TIER_GAP_Z_M,
    create_container_box,
    create_container_details,
    material,
)
from yard_inventory_loader import (  # noqa: E402
    DEFAULT_INVENTORY_PATH,
    YardStack,
    inventory_summary,
    iter_stacks,
    load_inventory,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STAGE_PATH = PROJECT_ROOT / "omniverse" / "scenes" / "smart_port.usda"


def _selected_stacks(
    stacks: Iterable[YardStack],
    bay_ids: Optional[set[str]],
    limit: Optional[int],
) -> list[YardStack]:
    selected: list[YardStack] = []
    normalized = {item.upper().replace("-", "/") for item in bay_ids or set()}
    for stack in stacks:
        if normalized and stack.bay_id.upper() not in normalized:
            continue
        selected.append(stack)
        if limit is not None and len(selected) >= limit:
            break
    return selected


def build_yard_inventory_layer(
    inventory_path: Path | str = DEFAULT_INVENTORY_PATH,
    output_path: Path | str = DEFAULT_OUTPUT_PATH,
    *,
    bay_ids: Optional[set[str]] = None,
    limit: Optional[int] = None,
    detailed: bool = True,
) -> dict[str, object]:
    """Create a live container USD layer from inventory JSON."""

    inventory = load_inventory(inventory_path)
    stacks = _selected_stacks(iter_stacks(inventory), bay_ids, limit)
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    # Kit keeps Sdf layers in a process-wide registry.  Removing the USD file
    # is not enough: when live inventory is reloaded in the same Isaac Sim
    # session, CreateNew() still sees the old layer identifier as occupied.
    # Reuse and clear the registered/on-disk layer so the active stage can
    # observe the rebuilt contents without a duplicate-layer exception.
    existing_layer = Sdf.Layer.Find(str(output)) or Sdf.Layer.FindOrOpen(str(output))
    if existing_layer is not None:
        existing_layer.Clear()
        stage = Usd.Stage.Open(existing_layer)
    else:
        stage = Usd.Stage.CreateNew(str(output))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = stage.OverridePrim("/World")
    stage.SetDefaultPrim(world)
    root = UsdGeom.Xform.Define(stage, LIVE_ROOT_PATH).GetPrim()
    root.CreateAttribute("live:source", Sdf.ValueTypeNames.Token).Set("yard_inventory_live_json")
    root.CreateAttribute("live:inventoryPath", Sdf.ValueTypeNames.String).Set(
        Path(inventory_path).as_posix()
    )
    root.CreateAttribute("live:physicsMode", Sdf.ValueTypeNames.Token).Set(
        "visual_only_no_collision"
    )
    root.CreateAttribute("live:maxStackHeightM", Sdf.ValueTypeNames.Float).Set(
        MAX_STACK_HEIGHT_Z_M
    )

    materials = [
        material(stage, f"Container_{name}", color) for name, color in CONTAINER_COLORS
    ]

    inventory_root = UsdGeom.Xform.Define(stage, f"{LIVE_ROOT_PATH}/YardInventory").GetPrim()
    inventory_root.CreateAttribute("live:stackCount", Sdf.ValueTypeNames.Int).Set(len(stacks))
    container_count = 0
    for stack in stacks:
        stack_path = f"{LIVE_ROOT_PATH}/YardInventory/Bay_{stack.block}_{stack.bay_number:03d}"
        stack_prim = UsdGeom.Xform.Define(stage, stack_path).GetPrim()
        stack_prim.CreateAttribute("live:bayId", Sdf.ValueTypeNames.String).Set(stack.bay_id)
        stack_prim.CreateAttribute("live:block", Sdf.ValueTypeNames.String).Set(stack.block)
        stack_prim.CreateAttribute("live:bayNumber", Sdf.ValueTypeNames.Int).Set(stack.bay_number)
        stack_prim.CreateAttribute("live:containerSizeFt", Sdf.ValueTypeNames.Int).Set(
            stack.container_size_ft
        )
        stack_prim.CreateAttribute("live:rowHeights", Sdf.ValueTypeNames.String).Set(
            ",".join(str(value) for value in stack.row_heights)
        )
        stack_prim.CreateAttribute("live:rowOrder", Sdf.ValueTypeNames.Token).Set(
            "right_to_left"
        )
        stack_prim.CreateAttribute("live:tierGapVisualM", Sdf.ValueTypeNames.Float).Set(
            TIER_GAP_Z_M
        )
        for slot in stack.slots:
            color_index = slot.container.color_index % len(materials)
            mat = materials[color_index]
            container_path = f"{stack_path}/R{slot.row:02d}_T{slot.tier:02d}_Container"
            center = Gf.Vec3d(slot.center_x, slot.center_y, slot.center_z)
            scale = Gf.Vec3f(slot.width_x, slot.length_y, slot.height_z)
            create_container_box(
                stage,
                container_path,
                center,
                scale,
                mat,
                bay_id=slot.bay_id,
                row=slot.row,
                tier=slot.tier,
                tiers=slot.row_height,
            )
            prim = stage.GetPrimAtPath(container_path)
            prim.CreateAttribute("live:containerId", Sdf.ValueTypeNames.String).Set(
                slot.container.container_id
            )
            prim.CreateAttribute("live:containerNo", Sdf.ValueTypeNames.String).Set(
                slot.container.container_no
            )
            prim.CreateAttribute("live:containerType", Sdf.ValueTypeNames.String).Set(
                slot.container.type
            )
            prim.CreateAttribute("live:containerSizeFt", Sdf.ValueTypeNames.Int).Set(
                slot.container.size_ft
            )
            prim.CreateAttribute("live:inventorySource", Sdf.ValueTypeNames.Token).Set(
                "yard_inventory.live.json"
            )
            if detailed:
                create_container_details(stage, container_path, center, scale, mat)
            container_count += 1

    layer = stage.GetRootLayer()
    source_summary = inventory_summary(inventory)
    layer.customLayerData = {
        "description": "Live yard containers generated from yard_inventory.live.json",
        "sourceInventory": Path(inventory_path).as_posix(),
        "stackCount": len(stacks),
        "containerCount": container_count,
        "sourceStackCount": source_summary["stack_count"],
        "sourceContainerCount": source_summary["container_count"],
        "physicsMode": "visual_only_no_collision",
    }
    layer.Save()
    return {
        "output_path": str(output),
        "stack_count": len(stacks),
        "container_count": container_count,
        "source_summary": source_summary,
    }


def ensure_sublayer(
    stage_or_path: object = DEFAULT_STAGE_PATH,
    layer_path: Path | str = DEFAULT_OUTPUT_PATH,
) -> dict[str, object]:
    """Ensure ``live_containers.usda`` is included as a sublayer."""

    layer = Path(layer_path).resolve()
    if hasattr(stage_or_path, "GetRootLayer"):
        root_layer = stage_or_path.GetRootLayer()
        base_dir = Path(root_layer.identifier).resolve().parent
    else:
        stage = Usd.Stage.Open(str(stage_or_path))
        if stage is None:
            raise RuntimeError(f"Could not open stage: {stage_or_path}")
        root_layer = stage.GetRootLayer()
        base_dir = Path(root_layer.identifier).resolve().parent
    relative = layer.relative_to(base_dir).as_posix()
    relative_sublayer = f"./{relative}"
    absolute_sublayer = layer.as_posix()
    paths = list(root_layer.subLayerPaths)
    added = False

    existing_live_paths = [
        path for path in paths if Path(str(path).replace("\\", "/")).name == layer.name
    ]
    relative_layer_resolved = (
        Sdf.Layer.FindRelativeToLayer(root_layer, relative_sublayer)
        or Sdf.Layer.FindRelativeToLayer(root_layer, relative)
    )
    absolute_layer_resolved = Sdf.Layer.FindOrOpen(absolute_sublayer)

    # In some Isaac/Kit sessions the relative sublayer remains listed on the
    # root layer but never appears in the stage layer stack.  Prefer the tidy
    # relative path when it resolves; otherwise replace stale entries with an
    # absolute filesystem path so the USD resolver has no ambiguity.
    wanted_sublayer = (
        relative_sublayer if relative_layer_resolved is not None else absolute_sublayer
    )
    if wanted_sublayer not in paths:
        for old_path in existing_live_paths:
            if old_path in root_layer.subLayerPaths:
                root_layer.subLayerPaths.remove(old_path)
        root_layer.subLayerPaths.append(wanted_sublayer)
        root_layer.Save()
        added = True
    return {
        "stage": root_layer.identifier,
        "layer": str(layer),
        "sublayer": wanted_sublayer,
        "added": added,
        "relative_resolved": relative_layer_resolved is not None,
        "absolute_resolved": absolute_layer_resolved is not None,
    }


def rebuild_and_attach_live_inventory(
    stage_or_path: object = DEFAULT_STAGE_PATH,
    *,
    inventory_path: Path | str = DEFAULT_INVENTORY_PATH,
    output_path: Path | str = DEFAULT_OUTPUT_PATH,
    limit: Optional[int] = None,
    detailed: bool = True,
) -> dict[str, object]:
    build = build_yard_inventory_layer(
        inventory_path=inventory_path,
        output_path=output_path,
        limit=limit,
        detailed=detailed,
    )
    attach = ensure_sublayer(stage_or_path, output_path)
    return {"build": build, "attach": attach}


def _current_stage() -> object:
    import omni.usd  # type: ignore

    stage = omni.usd.get_context().get_stage()
    if stage is None:
        raise RuntimeError("No USD stage is currently open")
    return stage


def live_inventory_status(stage: object | None = None) -> dict[str, object]:
    """Return whether the generated live container layer is visible in stage."""

    stage = stage or _current_stage()
    root_layer = stage.GetRootLayer()
    root_paths = list(root_layer.subLayerPaths)
    live_layers = [
        Sdf.Layer.FindRelativeToLayer(root_layer, path)
        or Sdf.Layer.FindOrOpen(path)
        for path in root_paths
        if Path(str(path).replace("\\", "/")).name == "live_containers.usda"
    ]
    live_layer = next((layer for layer in live_layers if layer is not None), None)
    layer_stack_ids = [layer.identifier for layer in stage.GetLayerStack()]
    live_root = stage.GetPrimAtPath(LIVE_ROOT_PATH)
    inventory_root = stage.GetPrimAtPath(f"{LIVE_ROOT_PATH}/YardInventory")
    stack_count_attr = (
        inventory_root.GetAttribute("live:stackCount") if inventory_root.IsValid() else None
    )
    stack_count = stack_count_attr.Get() if stack_count_attr and stack_count_attr.IsValid() else None
    children = list(inventory_root.GetChildren()) if inventory_root.IsValid() else []
    return {
        "stage": root_layer.identifier,
        "has_live_sublayer": any("live_containers.usda" in path for path in root_paths),
        "live_layer_resolved": live_layer is not None,
        "live_layer_identifier": live_layer.identifier if live_layer is not None else None,
        "layer_stack_has_live": any("live_containers.usda" in path for path in layer_stack_ids),
        "sublayers": root_paths,
        "live_root_valid": live_root.IsValid(),
        "inventory_root_valid": inventory_root.IsValid(),
        "stack_count": stack_count,
        "inventory_child_count": len(children),
        "first_inventory_children": [child.GetName() for child in children[:5]],
    }


def reload_live_inventory(
    stage: object | None = None,
    *,
    inventory_path: Path | str = DEFAULT_INVENTORY_PATH,
    output_path: Path | str = DEFAULT_OUTPUT_PATH,
    limit: Optional[int] = None,
    detailed: bool = True,
    reload_stage: bool = True,
) -> dict[str, object]:
    """Rebuild and attach the live inventory layer for the current Isaac stage."""

    stage = stage or _current_stage()
    result = rebuild_and_attach_live_inventory(
        stage,
        inventory_path=inventory_path,
        output_path=output_path,
        limit=limit,
        detailed=detailed,
    )
    status = live_inventory_status(stage)
    if not status.get("layer_stack_has_live"):
        root_layer = stage.GetRootLayer()
        absolute_sublayer = Path(output_path).resolve().as_posix()
        paths = [
            path
            for path in root_layer.subLayerPaths
            if Path(str(path).replace("\\", "/")).name != "live_containers.usda"
        ]
        paths.append(absolute_sublayer)
        root_layer.subLayerPaths = paths
        root_layer.Save()
        result["force_absolute_sublayer"] = absolute_sublayer
    layer = Sdf.Layer.Find(str(Path(output_path))) or Sdf.Layer.FindOrOpen(str(output_path))
    if layer is not None:
        layer.Reload(True)
    if reload_stage and hasattr(stage, "Reload"):
        stage.Reload()
    result["status"] = live_inventory_status(stage)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--attach-stage", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--detailed",
        action="store_true",
        default=True,
        help="Generate the ribbed/door detailed container appearance (default).",
    )
    args = parser.parse_args()
    result = build_yard_inventory_layer(
        inventory_path=args.inventory,
        output_path=args.output,
        limit=args.limit,
        detailed=args.detailed,
    )
    if args.attach_stage is not None:
        result["attach"] = ensure_sublayer(args.attach_stage, args.output)
    print("[Yard Inventory]", result)


if __name__ == "__main__":
    main()
