"""Slot-oriented RTG control helpers built on the stable visual controller."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
import time
from typing import Any, Optional
import builtins


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.append(str(SCRIPT_DIR))

rtg = getattr(builtins, "rtg", None)
if rtg is None:
    import rtg_isaac_physics as rtg  # noqa: E402
from yard_inventory_loader import (  # noqa: E402
    DEFAULT_INVENTORY_PATH,
    YardSlot,
    find_slot,
    load_inventory,
    tier_top_z,
)


GANTRY_HOME_USD_Y = 23.9
SPREADER_WORLD_X_AT_TROLLEY_ZERO = -3.7426
SPREADER_WORLD_Y_AT_GANTRY_HOME = 29.46
# Calibrated from the rendered live-yard scene: with gantry_m=257.015,
# the user-confirmed right-side second row of bay 3C/002 is centered at
# trolley_m=5.85.  The row's measured bbox center is world_x=0.408185333,
# while the RTG spreader reference at trolley zero is -3.7426.
TROLLEY_USD_TO_WORLD_X = 0.7095359544
VISUAL_ROW_FROM_RIGHT_1_TROLLEY_M = 6.85
VISUAL_ROW_TROLLEY_PITCH_M = -1.0
REFERENCE_PICK_CONTAINER_CENTER_Z = 3.3902355
REFERENCE_PICK_HOIST_Z = 0.90
SPREADER_STACK_CLEARANCE_Z_M = 0.65
SPREADER_MARKER_NAMES = {"C4", "C5", "C6", "C7"}
SAFE_TRAVEL_HOIST_M = 14.0

GANTRY_CALIBRATION_OFFSET_M = 0.0
TROLLEY_CALIBRATION_OFFSET_M = 0.0
HOIST_CALIBRATION_OFFSET_M = 0.0

_CARRIED_CONTAINERS: dict[int, dict[str, Any]] = {}
_CARRY_SUBSCRIPTIONS: dict[int, Any] = {}
_PICK_SEQUENCE_SUBSCRIPTIONS: dict[int, Any] = {}
_PICK_SEQUENCE_STATES: dict[int, dict[str, Any]] = {}


@dataclass(frozen=True)
class RtgSlotTarget:
    slot: YardSlot
    gantry_m: float
    trolley_m: float
    hoist_pick_m: float
    hoist_clear_m: float


def gantry_for_y(world_y: float) -> float:
    return GANTRY_HOME_USD_Y + (float(world_y) - SPREADER_WORLD_Y_AT_GANTRY_HOME)


def trolley_for_x(world_x: float) -> float:
    return (float(world_x) - SPREADER_WORLD_X_AT_TROLLEY_ZERO) / TROLLEY_USD_TO_WORLD_X


def trolley_for_visual_row_from_right(visual_row_from_right: int) -> float:
    """Return calibrated trolley command for a user-visible row index.

    Calibration points from the live scene:
    row 1 from the user's right side -> trolley_m=6.85
    row 2 from the user's right side -> trolley_m=5.85
    """

    return VISUAL_ROW_FROM_RIGHT_1_TROLLEY_M + (
        int(visual_row_from_right) - 1
    ) * VISUAL_ROW_TROLLEY_PITCH_M


def hoist_for_container_center_z(container_center_z: float) -> float:
    return REFERENCE_PICK_HOIST_Z + (
        float(container_center_z) - REFERENCE_PICK_CONTAINER_CENTER_Z
    )


def container_center_z_for_hoist(hoist_m: float) -> float:
    return REFERENCE_PICK_CONTAINER_CENTER_Z + (
        float(hoist_m) - REFERENCE_PICK_HOIST_Z
    )


def _resolve_stage(stage: Optional[Any] = None) -> Any:
    if stage is not None:
        return stage
    import omni.usd  # type: ignore

    resolved = omni.usd.get_context().get_stage()
    if resolved is None:
        raise RuntimeError("No USD stage is currently open")
    return resolved


def container_prim_path(slot: YardSlot) -> str:
    return f"/World/LiveContainers/YardInventory/{slot.prim_suffix}"


def _bbox_center(stage: Any, prim_path: str) -> tuple[float, float, float]:
    from pxr import Usd, UsdGeom  # type: ignore

    prim = stage.GetPrimAtPath(prim_path)
    if prim is None or not prim.IsValid():
        raise RuntimeError(f"Missing prim: {prim_path}")
    bbox_cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
        useExtentsHint=False,
    )
    box = bbox_cache.ComputeWorldBound(prim).ComputeAlignedBox()
    center = box.GetMidpoint()
    return (float(center[0]), float(center[1]), float(center[2]))


def _spreader_marker_centers(stage: Any) -> list[tuple[float, float, float]]:
    from pxr import Usd, UsdGeom  # type: ignore

    root_path = getattr(rtg, "HOIST_PATH", "")
    root = stage.GetPrimAtPath(root_path) if root_path else None
    if root is None or not root.IsValid():
        return []
    bbox_cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
        useExtentsHint=False,
    )
    centers: list[tuple[float, float, float]] = []
    for prim in Usd.PrimRange(root):
        if prim.GetName() not in SPREADER_MARKER_NAMES:
            continue
        box = bbox_cache.ComputeWorldBound(prim).ComputeAlignedBox()
        center = box.GetMidpoint()
        centers.append((float(center[0]), float(center[1]), float(center[2])))
    return centers


def spreader_center(stage: Optional[Any] = None) -> tuple[float, float, float]:
    """Return the current visual spreader center from the green marker spheres."""

    resolved = _resolve_stage(stage)
    centers = _spreader_marker_centers(resolved)
    if centers:
        count = float(len(centers))
        return (
            sum(center[0] for center in centers) / count,
            sum(center[1] for center in centers) / count,
            sum(center[2] for center in centers) / count,
        )
    path = getattr(rtg, "HOIST_PATH", "")
    if not path:
        raise RuntimeError("Cannot resolve spreader center; RTG HOIST_PATH is unavailable")
    return _bbox_center(resolved, path)


def reset_calibration() -> dict[str, float]:
    """Clear runtime pick-position calibration offsets."""

    global GANTRY_CALIBRATION_OFFSET_M
    global TROLLEY_CALIBRATION_OFFSET_M
    global HOIST_CALIBRATION_OFFSET_M
    GANTRY_CALIBRATION_OFFSET_M = 0.0
    TROLLEY_CALIBRATION_OFFSET_M = 0.0
    HOIST_CALIBRATION_OFFSET_M = 0.0
    return calibration_status()


def calibration_status() -> dict[str, float]:
    return {
        "gantry_offset_m": GANTRY_CALIBRATION_OFFSET_M,
        "trolley_offset_m": TROLLEY_CALIBRATION_OFFSET_M,
        "hoist_offset_m": HOIST_CALIBRATION_OFFSET_M,
    }


def _container_center_from_rtg(stage: Any) -> tuple[float, float, float]:
    targets = rtg.rtg_status(stage)["engineering_m"]
    gantry_m = float(targets["gantry_m"])
    trolley_m = float(targets["trolley_m"])
    hoist_m = float(targets["hoist_m"])
    return (
        SPREADER_WORLD_X_AT_TROLLEY_ZERO + trolley_m * TROLLEY_USD_TO_WORLD_X,
        SPREADER_WORLD_Y_AT_GANTRY_HOME + (gantry_m - GANTRY_HOME_USD_Y),
        container_center_z_for_hoist(hoist_m),
    )


def _translate_op(stage: Any, prim_path: str) -> Any:
    from pxr import UsdGeom  # type: ignore

    prim = stage.GetPrimAtPath(prim_path)
    if prim is None or not prim.IsValid():
        raise RuntimeError(f"Missing live container prim: {prim_path}")
    xformable = UsdGeom.Xformable(prim)
    for op in xformable.GetOrderedXformOps():
        if op.GetOpName() == "xformOp:translate":
            return op
    return xformable.AddTranslateOp()


def _set_container_center(stage: Any, prim_path: str, center: tuple[float, float, float]) -> None:
    from pxr import Gf  # type: ignore

    _translate_op(stage, prim_path).Set(Gf.Vec3d(*center))


def _start_carry_follow(stage: Any) -> None:
    key = id(stage)
    if key in _CARRY_SUBSCRIPTIONS:
        return
    import omni.kit.app  # type: ignore

    def on_update(_event: Any) -> None:
        state = _CARRIED_CONTAINERS.get(key)
        if state is None:
            stop_container_follow(stage)
            return
        try:
            _set_container_center(
                stage,
                str(state["prim_path"]),
                _container_center_from_rtg(stage),
            )
        except Exception as error:
            print(f"[RTG Pick] carry follow skipped: {error}")

    app = omni.kit.app.get_app()
    post_update = getattr(app, "get_post_update_event_stream", None)
    stream = post_update() if callable(post_update) else app.get_update_event_stream()
    _CARRY_SUBSCRIPTIONS[key] = stream.create_subscription_to_pop(
        on_update,
        name="RTG carried container visual follow",
    )


def stop_container_follow(stage: Optional[Any] = None) -> dict[str, Any]:
    resolved = _resolve_stage(stage)
    key = id(resolved)
    subscription = _CARRY_SUBSCRIPTIONS.pop(key, None)
    if subscription is not None:
        unsubscribe = getattr(subscription, "unsubscribe", None)
        if unsubscribe:
            unsubscribe()
    return {"stopped": subscription is not None}


def slot_target(
    bay_id: str,
    row: int,
    tier: Optional[int] = None,
    *,
    inventory_path: Path | str = DEFAULT_INVENTORY_PATH,
) -> RtgSlotTarget:
    inventory = load_inventory(inventory_path)
    slot = find_slot(inventory, bay_id=bay_id, row=row, tier=tier)
    pick = hoist_for_container_center_z(slot.center_z)
    clear = pick + (
        tier_top_z(slot.row_height)
        + SPREADER_STACK_CLEARANCE_Z_M
        - slot.center_z
        + slot.height_z * 0.5
    )
    return RtgSlotTarget(
        slot=slot,
        gantry_m=gantry_for_y(slot.center_y) + GANTRY_CALIBRATION_OFFSET_M,
        trolley_m=trolley_for_x(slot.center_x) + TROLLEY_CALIBRATION_OFFSET_M,
        hoist_pick_m=pick + HOIST_CALIBRATION_OFFSET_M,
        hoist_clear_m=max(clear, pick + 1.0) + HOIST_CALIBRATION_OFFSET_M,
    )


def debug_slot_alignment(
    bay_id: str,
    row: int,
    tier: Optional[int] = None,
    stage: Optional[Any] = None,
    *,
    inventory_path: Path | str = DEFAULT_INVENTORY_PATH,
) -> dict[str, Any]:
    """Measure current spreader-vs-container alignment in world coordinates."""

    resolved = _resolve_stage(stage)
    target = slot_target(bay_id, row, tier, inventory_path=inventory_path)
    prim_path = container_prim_path(target.slot)
    container_center = _bbox_center(resolved, prim_path)
    current_spreader_center = spreader_center(resolved)
    delta = (
        container_center[0] - current_spreader_center[0],
        container_center[1] - current_spreader_center[1],
        container_center[2] - current_spreader_center[2],
    )
    result = {
        "bay_id": target.slot.bay_id,
        "row": target.slot.row,
        "tier": target.slot.tier,
        "container_prim_path": prim_path,
        "container_center": container_center,
        "spreader_center": current_spreader_center,
        "delta_world_m": delta,
        "recommended_offset_delta": {
            "gantry_m": delta[1],
            "trolley_m": delta[0] / TROLLEY_USD_TO_WORLD_X,
            "hoist_m": delta[2],
        },
        "current_calibration": calibration_status(),
    }
    print("[RTG Pick] Alignment", result)
    return result


def _bay_root_path(bay_id: str) -> str:
    block, number = bay_id.strip().upper().replace("-", "/").split("/", 1)
    return f"/World/LiveContainers/YardInventory/Bay_{block}_{int(number):03d}"


def scene_row_centers(
    bay_id: str,
    stage: Optional[Any] = None,
) -> list[dict[str, Any]]:
    """Measure rendered row positions for a bay from live USD bbox data."""

    resolved = _resolve_stage(stage)
    root_path = _bay_root_path(bay_id)
    root = resolved.GetPrimAtPath(root_path)
    if root is None or not root.IsValid():
        raise RuntimeError(f"Missing live bay prim: {root_path}")

    rows: dict[int, dict[str, Any]] = {}
    for child in root.GetChildren():
        name = child.GetName()
        if not name.startswith("R") or "_T" not in name or not name.endswith("_Container"):
            continue
        try:
            row = int(name[1:3])
            tier = int(name.split("_T", 1)[1].split("_", 1)[0])
        except Exception:
            continue
        center = _bbox_center(resolved, str(child.GetPath()))
        previous = rows.get(row)
        if previous is None or tier > int(previous["top_tier"]):
            rows[row] = {
                "inventory_row": row,
                "top_tier": tier,
                "prim_path": str(child.GetPath()),
                "center": center,
                "world_x": center[0],
                "world_y": center[1],
                "world_z": center[2],
                "trolley_m": trolley_for_x(center[0]) + TROLLEY_CALIBRATION_OFFSET_M,
            }
    result = [rows[row] for row in sorted(rows)]
    print("[RTG Pick] Scene row centers", result)
    return result


def scene_rows_from_right(
    bay_id: str,
    stage: Optional[Any] = None,
    *,
    reverse_world_x: bool = False,
) -> list[dict[str, Any]]:
    """Return measured rows sorted as visual rows from the right side."""

    rows = scene_row_centers(bay_id, stage=stage)
    sorted_rows = sorted(rows, key=lambda item: float(item["world_x"]), reverse=reverse_world_x)
    for index, item in enumerate(sorted_rows, start=1):
        item["visual_row_from_right"] = index
    print("[RTG Pick] Scene rows from right", sorted_rows)
    return sorted_rows


def scene_slot_target_from_right(
    bay_id: str,
    visual_row_from_right: int,
    stage: Optional[Any] = None,
    *,
    reverse_world_x: bool = False,
    inventory_path: Path | str = DEFAULT_INVENTORY_PATH,
) -> dict[str, Any]:
    """Build an RTG target from rendered row bbox measurements."""

    resolved = _resolve_stage(stage)
    rows = scene_rows_from_right(
        bay_id,
        stage=resolved,
        reverse_world_x=reverse_world_x,
    )
    index = int(visual_row_from_right) - 1
    if index < 0 or index >= len(rows):
        raise IndexError(
            f"visual_row_from_right={visual_row_from_right} is outside measured rows 1..{len(rows)}"
        )
    row = rows[index]
    target = slot_target(
        bay_id,
        int(row["inventory_row"]),
        int(row["top_tier"]),
        inventory_path=inventory_path,
    )
    center = row["center"]
    result = {
        "bay_id": bay_id,
        "visual_row_from_right": int(visual_row_from_right),
        "inventory_row": int(row["inventory_row"]),
        "tier": int(row["top_tier"]),
        "prim_path": row["prim_path"],
        "container_center": center,
        "gantry_m": gantry_for_y(center[1]) + GANTRY_CALIBRATION_OFFSET_M,
        "trolley_m": trolley_for_visual_row_from_right(visual_row_from_right)
        + TROLLEY_CALIBRATION_OFFSET_M,
        "hoist_pick_m": target.hoist_pick_m,
        "hoist_clear_m": target.hoist_clear_m,
    }
    print("[RTG Pick] Scene slot target", result)
    return result


def move_to_scene_row_from_right(
    bay_id: str,
    visual_row_from_right: int,
    stage: Optional[Any] = None,
    *,
    high: bool = True,
    duration_s: float = 3.0,
    reverse_world_x: bool = False,
    inventory_path: Path | str = DEFAULT_INVENTORY_PATH,
) -> dict[str, Any]:
    """Move using measured rendered container rows instead of theoretical inventory rows."""

    resolved = _resolve_stage(stage)
    target = scene_slot_target_from_right(
        bay_id,
        visual_row_from_right,
        stage=resolved,
        reverse_world_x=reverse_world_x,
        inventory_path=inventory_path,
    )
    hoist_target = (
        SAFE_TRAVEL_HOIST_M
        if high
        else float(target["hoist_pick_m"])
    )
    result = rtg.animate_to(
        gantry_m=float(target["gantry_m"]),
        trolley_m=float(target["trolley_m"]),
        hoist_m=hoist_target,
        stage=resolved,
        duration_s=duration_s,
    )
    result["scene_target"] = target
    return result


def calibrate_current_slot(
    bay_id: str,
    row: int,
    tier: Optional[int] = None,
    stage: Optional[Any] = None,
    *,
    inventory_path: Path | str = DEFAULT_INVENTORY_PATH,
    apply_z: bool = False,
) -> dict[str, Any]:
    """Use the current RTG pose to update runtime offsets for this slot."""

    global GANTRY_CALIBRATION_OFFSET_M
    global TROLLEY_CALIBRATION_OFFSET_M
    global HOIST_CALIBRATION_OFFSET_M

    report = debug_slot_alignment(
        bay_id,
        row,
        tier,
        stage=stage,
        inventory_path=inventory_path,
    )
    recommended = report["recommended_offset_delta"]
    GANTRY_CALIBRATION_OFFSET_M += float(recommended["gantry_m"])
    TROLLEY_CALIBRATION_OFFSET_M += float(recommended["trolley_m"])
    if apply_z:
        HOIST_CALIBRATION_OFFSET_M += float(recommended["hoist_m"])
    report["new_calibration"] = calibration_status()
    print("[RTG Pick] Calibration updated", report["new_calibration"])
    return report


def move_to_slot(
    bay_id: str,
    row: int,
    tier: Optional[int] = None,
    stage: Optional[Any] = None,
    *,
    high: bool = True,
    duration_s: float = 3.0,
    inventory_path: Path | str = DEFAULT_INVENTORY_PATH,
) -> dict[str, Any]:
    target = slot_target(
        bay_id,
        row,
        tier,
        inventory_path=inventory_path,
    )
    hoist = target.hoist_clear_m if high else target.hoist_pick_m
    result = rtg.animate_to(
        gantry_m=target.gantry_m,
        trolley_m=target.trolley_m,
        hoist_m=hoist,
        stage=stage,
        duration_s=duration_s,
    )
    result["slot"] = {
        "bay_id": target.slot.bay_id,
        "row": target.slot.row,
        "tier": target.slot.tier,
        "container_no": target.slot.container.container_no,
    }
    result["rtg_target"] = {
        "gantry_m": target.gantry_m,
        "trolley_m": target.trolley_m,
        "hoist_pick_m": target.hoist_pick_m,
        "hoist_clear_m": target.hoist_clear_m,
    }
    return result


def lower_to_slot(
    bay_id: str,
    row: int,
    tier: Optional[int] = None,
    stage: Optional[Any] = None,
    *,
    duration_s: float = 2.0,
    inventory_path: Path | str = DEFAULT_INVENTORY_PATH,
) -> dict[str, Any]:
    return move_to_slot(
        bay_id,
        row,
        tier,
        stage=stage,
        high=False,
        duration_s=duration_s,
        inventory_path=inventory_path,
    )


def lift_from_slot(
    bay_id: str,
    row: int,
    tier: Optional[int] = None,
    stage: Optional[Any] = None,
    *,
    duration_s: float = 2.0,
    inventory_path: Path | str = DEFAULT_INVENTORY_PATH,
) -> dict[str, Any]:
    return move_to_slot(
        bay_id,
        row,
        tier,
        stage=stage,
        high=True,
        duration_s=duration_s,
        inventory_path=inventory_path,
    )


def attach_slot_container(
    bay_id: str,
    row: int,
    tier: Optional[int] = None,
    stage: Optional[Any] = None,
    *,
    inventory_path: Path | str = DEFAULT_INVENTORY_PATH,
) -> dict[str, Any]:
    """Visually attach a live inventory container to the RTG spreader."""

    resolved = _resolve_stage(stage)
    target = slot_target(bay_id, row, tier, inventory_path=inventory_path)
    prim_path = container_prim_path(target.slot)
    _set_container_center(resolved, prim_path, _container_center_from_rtg(resolved))
    _CARRIED_CONTAINERS[id(resolved)] = {
        "prim_path": prim_path,
        "slot": target.slot,
        "attached_at": time.perf_counter(),
    }
    _start_carry_follow(resolved)
    result = {
        "attached": True,
        "prim_path": prim_path,
        "bay_id": target.slot.bay_id,
        "row": target.slot.row,
        "tier": target.slot.tier,
        "container_no": target.slot.container.container_no,
    }
    print("[RTG Pick] Attached container", result)
    return result


def detach_container(stage: Optional[Any] = None) -> dict[str, Any]:
    """Release the currently carried visual container at its current pose."""

    resolved = _resolve_stage(stage)
    key = id(resolved)
    state = _CARRIED_CONTAINERS.pop(key, None)
    stop_container_follow(resolved)
    result = {"detached": state is not None, "state": state}
    print("[RTG Pick] Detached container", result)
    return result


def stop_pick_sequence(stage: Optional[Any] = None) -> dict[str, Any]:
    resolved = _resolve_stage(stage)
    key = id(resolved)
    subscription = _PICK_SEQUENCE_SUBSCRIPTIONS.pop(key, None)
    state = _PICK_SEQUENCE_STATES.pop(key, None)
    if subscription is not None:
        unsubscribe = getattr(subscription, "unsubscribe", None)
        if unsubscribe:
            unsubscribe()
    return {"stopped": subscription is not None or state is not None, "state": state}


def pick_slot_demo(
    bay_id: str,
    row: int,
    tier: Optional[int] = None,
    stage: Optional[Any] = None,
    *,
    travel_duration_s: float = 4.0,
    lower_duration_s: float = 2.0,
    lift_duration_s: float = 2.0,
    inventory_path: Path | str = DEFAULT_INVENTORY_PATH,
) -> dict[str, Any]:
    """Run a non-blocking visual pick sequence for one inventory slot."""

    resolved = _resolve_stage(stage)
    stop_pick_sequence(resolved)
    target = slot_target(bay_id, row, tier, inventory_path=inventory_path)
    move_to_slot(
        bay_id,
        row,
        tier,
        stage=resolved,
        high=True,
        duration_s=travel_duration_s,
        inventory_path=inventory_path,
    )
    state: dict[str, Any] = {
        "bay_id": bay_id,
        "row": int(row),
        "tier": target.slot.tier,
        "phase": "travel_high",
        "start_time": time.perf_counter(),
        "travel_duration_s": float(travel_duration_s),
        "lower_duration_s": float(lower_duration_s),
        "lift_duration_s": float(lift_duration_s),
        "target": {
            "gantry_m": target.gantry_m,
            "trolley_m": target.trolley_m,
            "hoist_pick_m": target.hoist_pick_m,
            "hoist_clear_m": target.hoist_clear_m,
        },
    }
    key = id(resolved)
    _PICK_SEQUENCE_STATES[key] = state

    import omni.kit.app  # type: ignore

    def on_update(_event: Any) -> None:
        elapsed = time.perf_counter() - float(state["start_time"])
        try:
            if state["phase"] == "travel_high" and elapsed >= travel_duration_s:
                state["phase"] = "lowering"
                lower_to_slot(
                    bay_id,
                    row,
                    tier,
                    stage=resolved,
                    duration_s=lower_duration_s,
                    inventory_path=inventory_path,
                )
            elif state["phase"] == "lowering" and elapsed >= travel_duration_s + lower_duration_s:
                state["phase"] = "attached_lifting"
                attach_slot_container(
                    bay_id,
                    row,
                    tier,
                    stage=resolved,
                    inventory_path=inventory_path,
                )
                lift_from_slot(
                    bay_id,
                    row,
                    tier,
                    stage=resolved,
                    duration_s=lift_duration_s,
                    inventory_path=inventory_path,
                )
            elif (
                state["phase"] == "attached_lifting"
                and elapsed >= travel_duration_s + lower_duration_s + lift_duration_s
            ):
                state["phase"] = "complete"
                stop_pick_sequence(resolved)
                print("[RTG Pick] Pick sequence complete", state)
        except Exception as error:
            state["phase"] = "error"
            state["error"] = str(error)
            stop_pick_sequence(resolved)
            print(f"[RTG Pick] Pick sequence stopped: {error}")

    app = omni.kit.app.get_app()
    post_update = getattr(app, "get_post_update_event_stream", None)
    stream = post_update() if callable(post_update) else app.get_update_event_stream()
    _PICK_SEQUENCE_SUBSCRIPTIONS[key] = stream.create_subscription_to_pop(
        on_update,
        name="RTG visual pick slot demo",
    )
    print("[RTG Pick] Pick sequence started", state)
    return {"started": True, "state": state}


__all__ = [
    "RtgSlotTarget",
    "attach_slot_container",
    "container_center_z_for_hoist",
    "container_prim_path",
    "calibrate_current_slot",
    "calibration_status",
    "debug_slot_alignment",
    "detach_container",
    "gantry_for_y",
    "hoist_for_container_center_z",
    "lift_from_slot",
    "lower_to_slot",
    "move_to_slot",
    "move_to_scene_row_from_right",
    "pick_slot_demo",
    "reset_calibration",
    "scene_row_centers",
    "scene_rows_from_right",
    "scene_slot_target_from_right",
    "slot_target",
    "spreader_center",
    "stop_container_follow",
    "stop_pick_sequence",
    "trolley_for_x",
    "trolley_for_visual_row_from_right",
]
