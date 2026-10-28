"""Read-only inventory queries; never import or start a twin receiver.

Use the inventory already selected by the current stage/driver. An unavailable
live snapshot must never silently fall back to simulation data.
"""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any

from yard_coordinate_mapping import bay_center_m, bay_scene_y_m, validate_bay
from yard_inventory import BLOCK_BAY_COUNTS, MAX_TIERS, ROW_COUNT, YardInventory, physical_footprint

from .agent_api import normalize_bay_id


_YARD_ROOT = "/World/LiveContainers/YardInventory"


def _attr(prim: Any, name: str, default: Any = None) -> Any:
    if prim:
        attr = prim.GetAttribute(name)
        if attr:
            value = attr.Get()
            if value is not None:
                return value
    return default


def read_inventory_snapshot(stage: Any, project_root: Path) -> tuple[YardInventory, dict[str, Any]]:
    """Take a fresh snapshot on Kit's main thread without writing USD or disk."""
    driver = getattr(builtins, "_smart_port_rtg_panel_facing_driver", None)
    if driver is None or getattr(driver, "_inventory_stage_id", None) != id(stage):
        # The PLC-free simulator owns a receiver instance without starting the
        # live receiver's own update subscription or publishing its builtin.
        from .scene_runtime import existing_module

        module = existing_module("rtg_simulation_driver")
        simulation = getattr(module, "_simulation", None) if module else None
        simulated_driver = getattr(simulation, "_driver", None)
        if simulation and getattr(simulation, "_subscription", None) is not None and simulated_driver:
            driver = simulated_driver
    if driver is not None and getattr(driver, "_inventory_stage_id", None) == id(stage):
        if driver._inventory_load_error:
            raise RuntimeError(f"Active inventory could not be loaded: {driver._inventory_load_error}")
        inventory = YardInventory(
            stacks=driver._inventory.stacks,
            seed=driver._inventory.seed,
            created_at_utc=driver._inventory.created_at_utc,
            updated_at_utc=driver._inventory.updated_at_utc,
        )
        source = {
            "kind": "active_driver_inventory",
            "mode": driver._inventory_mode,
            "inventory_file": str(driver._inventory_path),
            "coverage_complete": bool(driver._inventory_full_sync_ready),
            "sync_note": driver._inventory_sync_reason,
        }
    else:
        root = stage.GetPrimAtPath(_YARD_ROOT)
        live_root = stage.GetPrimAtPath("/World/LiveContainers")
        raw_path = _attr(root, "inventory:path")
        mode = _attr(root, "inventory:mode")
        if not raw_path:
            raw_path = _attr(live_root, "live:inventoryPath")
        if not raw_path:
            for layer in stage.GetLayerStack():
                raw_path = layer.customLayerData.get("sourceInventory")
                if raw_path:
                    break
        if not raw_path:
            raw_path = "omniverse/data/yard_inventory.live.json"
        path = Path(str(raw_path))
        if not path.is_absolute():
            candidates = [project_root / path]
            for layer in stage.GetLayerStack():
                if layer.realPath:
                    candidates.append(Path(layer.realPath).parent / path)
            path = next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
        if not path.is_file():
            raise FileNotFoundError(f"Selected inventory snapshot is unavailable: {path}")
        inventory = YardInventory.load(path)
        mode = mode or (
            "simulation" if path.name == "yard_inventory.sim.json" else
            "live" if path.name == "yard_inventory.live.json" else "snapshot"
        )
        source = {
            "kind": "inventory_json_snapshot",
            "mode": str(mode),
            "inventory_file": str(path.resolve()),
            "coverage_complete": mode == "simulation" or bool(_attr(root, "inventory:startupSyncReady", False)),
            "sync_note": _attr(root, "inventory:syncReason"),
        }
    source["updated_at_utc"] = inventory.updated_at_utc
    source["is_live_runtime"] = source["kind"] == "active_driver_inventory" and source["mode"] == "live"
    if not source["coverage_complete"] and not source["sync_note"]:
        source["sync_note"] = "Unrecorded bays have unknown occupancy; this source does not confirm a complete yard snapshot."
    return inventory, source


def _stack_summary(stack: dict[str, Any]) -> dict[str, Any]:
    heights = [len(row) for row in stack["rows"]]
    return {
        "bay_id": stack["bayId"],
        "size_ft": int(stack["containerSizeFt"]),
        "physical_bays": sorted(physical_footprint(stack["block"], stack["bayNumber"], stack["containerSizeFt"])),
        "bay_map": "".join(str(height) for height in heights),
        "row_heights": heights,
        "container_count": sum(heights),
        "updated_at_utc": stack.get("updatedAtUtc"),
        "record_source": stack.get("source"),
    }


def describe_bay(inventory: YardInventory, bay_id: str, source: dict[str, Any]) -> dict[str, Any]:
    requested = normalize_bay_id(bay_id)
    block, bay_text = requested.split("/")
    bay_number = int(bay_text)
    validate_bay(block, bay_number)
    size_ft = 20 if bay_number % 2 else 40
    footprint = physical_footprint(block, bay_number, size_ft)
    matches = [
        stack for stack in inventory.stacks.values()
        if footprint & physical_footprint(stack["block"], stack["bayNumber"], stack["containerSizeFt"])
    ]
    matches.sort(key=lambda stack: stack["bayId"])
    stacks = []
    known_physical_bays: set[str] = set()
    for raw_stack in matches:
        entry = _stack_summary(raw_stack)
        known_physical_bays.update(entry["physical_bays"])
        entry["containers"] = [
            {
                "row": row_index,
                "tier": tier,
                "container_no": str(container.get("containerNo") or ""),
                "container_id": str(container.get("containerId") or ""),
                "size_ft": int(container.get("sizeFt", entry["size_ft"])),
                "type": str(container.get("type") or ""),
                "height_m": container.get("heightM"),
                "record_source": container.get("source"),
            }
            for row_index, row in enumerate(raw_stack["rows"], start=1)
            for tier, container in enumerate(row, start=1)
        ]
        entry["layout_ascii"] = "\n".join(
            ["     R1 R2 R3 R4 R5 R6"]
            + [
                f"T{tier}:  " + "  ".join("X" if height >= tier else "." for height in entry["row_heights"])
                for tier in range(MAX_TIERS, 0, -1)
            ]
        )
        stacks.append(entry)
    complete = bool(source.get("coverage_complete")) or footprint <= known_physical_bays
    count = sum(stack["container_count"] for stack in stacks)
    return {
        "requested_bay_id": requested,
        "logical_size_ft": size_ft,
        "physical_bays": sorted(footprint),
        "status": "occupied" if count else "empty" if complete else "unknown",
        "coverage_complete_for_requested_bay": complete,
        "container_count": count,
        "logical_bay_ids": [stack["bay_id"] for stack in stacks],
        "row_order": "R1 to R6, right to left in the configured yard layout",
        "tier_order": "T1 is the bottom; ASCII layouts show the top tier first",
        "map_legend": "Each bay_map digit is the height of row R1 through R6; X = occupied, . = empty",
        "coverage_note": "40 ft logical bays have even numbers and cover the two adjacent odd-numbered 20 ft bays. Counts include each physical container once.",
        "configured_layout_position_m": {
            "gantry_center": round(bay_center_m(block, bay_number), 3),
            "scene_y_center": round(bay_scene_y_m(block, bay_number), 3),
        },
        "stacks": stacks,
        "source": source,
    }


def describe_yard(inventory: YardInventory, source: dict[str, Any], block: str | None = None) -> dict[str, Any]:
    if block is not None:
        block = str(block).strip().upper()
        if block not in BLOCK_BAY_COUNTS:
            raise ValueError("block must be 1C, 2C, 3C, or 4C")
    blocks = []
    for block_name, maximum in BLOCK_BAY_COUNTS.items():
        if block is not None and block_name != block:
            continue
        stacks = [
            _stack_summary(stack) for stack in inventory.stacks.values()
            if stack["block"] == block_name and any(stack["rows"])
        ]
        stacks.sort(key=lambda stack: stack["bay_id"])
        counts = {size: sum(stack["container_count"] for stack in stacks if stack["size_ft"] == size) for size in (20, 40)}
        blocks.append({
            "block": block_name,
            "bay_range": f"001-{maximum:03d}",
            "occupied_logical_bay_count": len(stacks),
            "container_count": sum(counts.values()),
            "containers_20ft": counts[20],
            "containers_40ft": counts[40],
            "teu": counts[20] + counts[40] * 2,
            "occupied_bays": stacks,
        })
    return {
        "container_count": sum(item["container_count"] for item in blocks),
        "occupied_logical_bay_count": sum(item["occupied_logical_bay_count"] for item in blocks),
        "row_count": ROW_COUNT,
        "maximum_tiers": MAX_TIERS,
        "blocks": blocks,
        "source": source,
    }


def query_bay(stage: Any, project_root: Path, bay_id: str) -> dict[str, Any]:
    inventory, source = read_inventory_snapshot(stage, project_root)
    return describe_bay(inventory, bay_id, source)


def query_yard(stage: Any, project_root: Path, block: str | None = None) -> dict[str, Any]:
    inventory, source = read_inventory_snapshot(stage, project_root)
    return describe_yard(inventory, source, block)
