"""PLC-free RTG task simulator for Omniverse Script Editor.

The live receiver is intentionally kept PLC-first.  This module provides a
separate, deterministic source of the same ``RtgTwinState`` snapshots so the
whole workflow can be exercised without a TCP publisher:

    exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_simulation_driver.py", encoding="utf-8").read())
    start_simulation()
    publish_simulated_task(work_type="UNLOAD", start_pos="3C6323",
                           bay_map="441000", container_size=20)

The generated snapshots go through ``OmniverseRtgTwinDriver.apply_state``;
therefore the normal bay-map renderer, truck visual, spreader attachment and
operation state machine are all used by the simulation.
"""

from __future__ import annotations

import sys
import time
import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


SCRIPT_DIR = Path(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts")
if not (SCRIPT_DIR / "rtg_task_state_machine.py").is_file():
    SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from rtg_task_state_machine import (  # noqa: E402
    OperationState,
    OperationTask,
    PlcOperationSnapshot,
    RtgOperationStateMachine,
    TaskSlot,
    WORK_TYPE_LOAD,
    WORK_TYPE_SWITCH,
    WORK_TYPE_UNLOAD,
)
from yard_coordinate_mapping import bay_position_m  # noqa: E402


SIMULATION_RTG_NAME = "RTG_SIM_220"
SIMULATION_TICK_SECONDS = 0.05
GANTRY_TRAVEL_SECONDS = 2.5
TROLLEY_TRAVEL_SECONDS = 1.8
HOIST_TRAVEL_SECONDS = 1.4
LOCK_SETTLE_SECONDS = 0.45
UNLOCK_SETTLE_SECONDS = 0.45
# The live visual compresses one physical container tier to the authored USD
# pitch, but PLC hoist values remain engineering metres.  Tier 1 therefore
# starts at 2.5 m and tier 4 must be picked at 10.0 m, not at the old fixed
# 2.5 m target.
PHYSICAL_TIER_HEIGHT_M = 2.50
FIRST_TIER_PICK_HOIST_M = 2.50
# The simulated truck trailer top is slightly above the tier-1 yard top.
# This is the engineering hoist value that puts the spreader contact plane on
# the truck box when the outbound box is released.
TRUCK_PLACE_HOIST_M = 3.64
TRUCK_APPROACH_SECONDS = 2.5
TRUCK_SETTLE_SECONDS = 0.8
TRUCK_EXIT_SECONDS = 2.0


def _parse_position(value: str) -> TaskSlot:
    text = str(value).strip().upper().replace("/", "").replace("-", "")
    if len(text) < 4 or text[0] not in "1234" or text[1] != "C":
        raise ValueError(f"Position must look like 3C6323, got {value!r}")
    digits = text[2:]
    if len(digits) < 3:
        raise ValueError(f"Position needs bay, row and tier, got {value!r}")
    return TaskSlot(
        field=text[:2],
        bay=int(digits[:-2]),
        row=int(digits[-2]),
        tier=int(digits[-1]),
    )


def _normalize_work_type(value: str) -> str:
    text = str(value or "").strip().upper()
    if text in ("REHANDLE", "SWITCH"):
        return WORK_TYPE_SWITCH
    if text in ("LOAD", "INBOUND", "IMPORT"):
        return WORK_TYPE_LOAD
    if text in ("UNLOAD", "OUTBOUND", "EXPORT"):
        return WORK_TYPE_UNLOAD
    raise ValueError("work_type must be LOAD, UNLOAD, or SWITCH")


def _task_from_payload(payload: Dict[str, Any]) -> OperationTask:
    work_type = _normalize_work_type(payload.get("work_type") or payload.get("workType"))
    start_value = payload.get("start_pos") or payload.get("startPos")
    end_value = payload.get("end_pos") or payload.get("endPos")
    if work_type == WORK_TYPE_LOAD:
        target = _parse_position(end_value or start_value)
        source = TaskSlot(target.field, target.bay)
    elif work_type == WORK_TYPE_UNLOAD:
        source = _parse_position(start_value)
        target = TaskSlot(source.field, source.bay)
    else:
        source = _parse_position(start_value)
        target = _parse_position(end_value)
    return OperationTask(
        task_id=str(payload.get("task_id") or payload.get("taskId") or int(time.time() * 1000)),
        work_type=work_type,
        source=source,
        target=target,
        vision_required=bool(payload.get("vision_required", payload.get("visionRequired", False))),
    )


@dataclass
class SimulationTask:
    operation: OperationTask
    task_payload: Dict[str, Any]
    bay_map: str
    container_size: int


class RtgSimulationDriver:
    """Generate a PLC-shaped state stream and apply it on Kit's main thread."""

    def __init__(self) -> None:
        self._subscription = None
        self._driver = None
        self._task: Optional[SimulationTask] = None
        self._machine: Optional[RtgOperationStateMachine] = None
        self._started_at = 0.0
        self._state_entered_at = 0.0
        self._last_state = None
        self._current_trolley = 0.67
        self._current_hoist = 6.0
        self._current_bay = 1
        self._current_gantry = 0.0
        self._lock = False
        self._unlock_requested_at = 0.0

    @property
    def state(self) -> Optional[str]:
        return self._machine.state.value if self._machine else None

    def start(self) -> "RtgSimulationDriver":
        if self._subscription is not None:
            return self
        # Kit keeps imported Python modules alive between Script Editor runs.
        # Reload both runtime pieces so the height targets and the visible
        # grab/place animation are used immediately instead of a stale cached
        # receiver/controller class.
        for module_name in ("rtg_live_control", "rtg_twin_state_receiver"):
            module = sys.modules.get(module_name)
            if module is not None:
                importlib.reload(module)
        try:
            import omni.kit.app  # type: ignore

            stream = omni.kit.app.get_app().get_update_event_stream()
            self._subscription = stream.create_subscription_to_pop(
                self._on_update, name="SmartPortRtgSimulationDriver"
            )
            print("[RTG Simulation] started (no PLC/TCP required)")
        except Exception as exc:
            print(f"[RTG Simulation] unable to start update loop: {exc}")
        return self

    def stop(self) -> None:
        self._subscription = None
        self._task = None
        self._machine = None
        if self._driver is not None:
            self._driver.stop()
        self._driver = None
        print("[RTG Simulation] stopped")

    def publish(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        operation = _task_from_payload(payload)
        bay_map = str(payload.get("bay_map") or payload.get("bayMap") or "123456")
        if len(bay_map) != 6 or not bay_map.isdigit():
            raise ValueError("bay_map must be six digits, e.g. 441000")
        container_size = int(payload.get("container_size", payload.get("containerSize", 20)) or 20)
        if container_size not in (20, 40):
            raise ValueError("container_size must be 20 or 40")

        task_payload = {
            "taskId": operation.task_id,
            "workType": operation.work_type,
            "startPos": payload.get("start_pos") or payload.get("startPos"),
            "endPos": payload.get("end_pos") or payload.get("endPos"),
            "containerSize": container_size,
            "containerHeight": payload.get("container_height", payload.get("containerHeight", 2.9)),
            "containerNo": payload.get("container_no", payload.get("containerNo", "SIM-" + operation.task_id)),
            "bayMap": bay_map,
            "state": "ACTIVE",
            "visionRequired": operation.vision_required,
        }
        self._task = SimulationTask(operation, task_payload, bay_map, container_size)
        self._machine = RtgOperationStateMachine(
            operation,
            truck_approach_seconds=TRUCK_APPROACH_SECONDS,
            truck_settle_seconds=TRUCK_SETTLE_SECONDS,
            truck_exit_seconds=TRUCK_EXIT_SECONDS,
        )
        self._started_at = time.monotonic()
        self._state_entered_at = self._started_at
        self._last_state = None
        self._current_bay = operation.source.bay
        self._current_gantry = bay_position_m(operation.source.field, operation.source.bay)
        self._current_trolley = 0.67
        self._current_hoist = 6.0
        self._lock = False
        print(
            f"[RTG Simulation] published {operation.work_type} task={operation.task_id} "
            f"source={operation.source} target={operation.target} bayMap={bay_map}"
        )
        return dict(task_payload)

    def clear(self) -> None:
        self._task = None
        self._machine = None
        print("[RTG Simulation] task cleared")

    def _on_update(self, _event: Any) -> None:
        if self._task is None or self._machine is None:
            return
        now = time.monotonic()
        pose = self._make_pose(now)
        transition = self._machine.update(
            PlcOperationSnapshot(
                field=self._task.operation.source.field,
                bay=self._current_bay,
                trolley_m=self._current_trolley,
                hoist_m=self._current_hoist,
                locked=self._lock,
                vision_confirmed=True,
            ),
            now,
        )
        if transition is not None:
            self._state_entered_at = now
            self._last_state = transition.current
            print(
                f"[RTG Simulation] {transition.previous.value} -> "
                f"{transition.current.value}: {transition.reason}"
            )
        state = {
            "rtgName": SIMULATION_RTG_NAME,
            "timestampUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "pose": pose,
            "task": dict(self._task.task_payload),
        }
        try:
            if self._driver is None:
                from rtg_twin_state_receiver import OmniverseRtgTwinDriver

                self._driver = OmniverseRtgTwinDriver()
            self._driver.apply_state(state)
        except Exception as exc:
            print(f"[RTG Simulation] apply failed: {exc}")

    def _make_pose(self, now: float) -> Dict[str, Any]:
        assert self._task is not None and self._machine is not None
        operation = self._task.operation
        state = self._machine.state
        elapsed = max(0.0, now - self._state_entered_at)
        source = operation.source
        target = operation.target
        if state == OperationState.GANTRY_TO_SOURCE:
            progress = min(1.0, elapsed / GANTRY_TRAVEL_SECONDS)
            self._current_bay = source.bay if progress >= 1.0 else 1
            self._current_gantry = bay_position_m(source.field, source.bay) * progress
        else:
            self._current_bay = source.bay
            self._current_gantry = bay_position_m(source.field, source.bay)

        if state in (OperationState.TROLLEY_TO_SOURCE, OperationState.HOIST_TO_PICK):
            trolley_target = self._machine.source_trolley_m
            self._current_trolley = self._ramp(self._current_trolley, trolley_target, elapsed)
        elif state in (OperationState.TROLLEY_TO_TARGET, OperationState.HOIST_TO_PLACE):
            trolley_target = self._machine.target_trolley_m
            self._current_trolley = self._ramp(self._current_trolley, trolley_target, elapsed)

        if state == OperationState.HOIST_TO_PICK:
            pick_target = self._pick_hoist_target_m()
            progress = min(1.0, max(0.0, elapsed / HOIST_TRAVEL_SECONDS))
            self._current_hoist = 6.0 + (pick_target - 6.0) * progress
            # Lock only after the spreader has reached the tier-appropriate
            # contact height and the twist-lock has had time to settle.
            self._lock = elapsed >= HOIST_TRAVEL_SECONDS + LOCK_SETTLE_SECONDS
        elif state in (OperationState.LOCK, OperationState.HOIST_UP, OperationState.TROLLEY_TO_TARGET):
            self._lock = True
            self._current_hoist = 6.0
        elif state == OperationState.HOIST_TO_PLACE:
            place_target = self._place_hoist_target_m()
            progress = min(1.0, max(0.0, elapsed / HOIST_TRAVEL_SECONDS))
            # Keep the box locked while lowering, but do not let the state
            # machine release it until the truck/target contact height is
            # actually reached.
            self._lock = elapsed >= HOIST_TRAVEL_SECONDS
            self._current_hoist = 6.0 + (place_target - 6.0) * progress
        elif state == OperationState.UNLOCK:
            self._lock = elapsed < UNLOCK_SETTLE_SECONDS
            self._current_hoist = self._place_hoist_target_m()
        elif state in (OperationState.HOIST_SAFE, OperationState.TRUCK_EXITING, OperationState.COMPLETE):
            self._lock = False
            self._current_hoist = 6.0
        else:
            self._lock = False
            self._current_hoist = 6.0

        return {
            "currentField": source.field,
            "currentBay": self._current_bay,
            "bigcarElecDistance": round(self._current_gantry, 3),
            "trolleyMeter": round(self._current_trolley, 3),
            "hoistMeter": round(self._current_hoist, 3),
            "lockState": int(self._lock),
            "unlockState": int(not self._lock),
            "alarmStatus": 0,
            "tool20f": int(self._task.container_size == 20),
            "tool40f": int(self._task.container_size == 40),
        }

    def _pick_hoist_target_m(self) -> float:
        """Return the engineering height needed to contact the source box."""

        assert self._task is not None
        operation = self._task.operation
        if operation.work_type == WORK_TYPE_LOAD:
            # LOAD picks the box that arrived on the truck.
            return TRUCK_PLACE_HOIST_M
        tier = operation.source.tier or 1
        return FIRST_TIER_PICK_HOIST_M + (max(1, tier) - 1) * PHYSICAL_TIER_HEIGHT_M

    def _place_hoist_target_m(self) -> float:
        """Return the engineering height for the destination contact plane."""

        assert self._task is not None
        operation = self._task.operation
        if operation.work_type == WORK_TYPE_UNLOAD:
            return TRUCK_PLACE_HOIST_M
        tier = operation.target.tier or 1
        return FIRST_TIER_PICK_HOIST_M + (max(1, tier) - 1) * PHYSICAL_TIER_HEIGHT_M

    @staticmethod
    def _ramp(current: float, target: float, elapsed: float) -> float:
        if abs(target - current) <= 0.01:
            return target
        progress = min(1.0, max(0.0, elapsed / TROLLEY_TRAVEL_SECONDS))
        return current + (target - current) * progress


_simulation: Optional[RtgSimulationDriver] = None


def start_simulation() -> RtgSimulationDriver:
    """Start the PLC-free update loop."""
    global _simulation
    if _simulation is None:
        _simulation = RtgSimulationDriver()
    return _simulation.start()


def stop_simulation() -> None:
    global _simulation
    if _simulation is not None:
        _simulation.stop()
        _simulation = None


def publish_simulated_task(
    *,
    work_type: str,
    start_pos: Optional[str] = None,
    end_pos: Optional[str] = None,
    bay_map: str = "123456",
    container_size: int = 20,
    task_id: Optional[str] = None,
    container_no: Optional[str] = None,
    vision_required: bool = False,
) -> Dict[str, Any]:
    """Publish one simulated LOAD/UNLOAD/SWITCH task to the running loop."""
    simulator = start_simulation()
    return simulator.publish(
        {
            "work_type": work_type,
            "start_pos": start_pos,
            "end_pos": end_pos,
            "bay_map": bay_map,
            "container_size": container_size,
            "task_id": task_id,
            "container_no": container_no,
            "vision_required": vision_required,
        }
    )


def clear_simulated_task() -> None:
    if _simulation is not None:
        _simulation.clear()


def get_simulation_status() -> Dict[str, Any]:
    if _simulation is None or _simulation._task is None:
        return {"running": False, "state": None, "task": None}
    return {
        "running": _simulation._subscription is not None,
        "state": _simulation.state,
        "task": dict(_simulation._task.task_payload),
    }


if __name__ == "__main__":
    print("Use start_simulation() and publish_simulated_task(...) in Kit Script Editor.")
