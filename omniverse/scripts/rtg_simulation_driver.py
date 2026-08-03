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

import builtins
import sys
import time
import traceback
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


# Script Editor executes this file into its existing global namespace.  Stop a
# previous update subscription before redefining the module globals; otherwise
# the old simulation keeps applying stale pose and spreader-size commands.
_previous_simulation = globals().get("_simulation")
if _previous_simulation is not None:
    _previous_simulation.stop()


SCRIPT_DIR = Path(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts")
if not (SCRIPT_DIR / "rtg_task_state_machine.py").is_file():
    SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


def _load_state_machine_module_from_source() -> types.ModuleType:
    """Load the state machine source on every Script Editor execution."""

    module_name = "rtg_task_state_machine"
    module_path = SCRIPT_DIR / f"{module_name}.py"
    module = types.ModuleType(module_name)
    module.__file__ = str(module_path)
    module.__package__ = ""
    sys.modules[module_name] = module
    exec(
        compile(module_path.read_text(encoding="utf-8"), str(module_path), "exec"),
        module.__dict__,
    )
    return module


_state_machine_module = _load_state_machine_module_from_source()
OperationState = _state_machine_module.OperationState
OperationTask = _state_machine_module.OperationTask
PlcOperationSnapshot = _state_machine_module.PlcOperationSnapshot
RtgOperationStateMachine = _state_machine_module.RtgOperationStateMachine
TaskSlot = _state_machine_module.TaskSlot
WORK_TYPE_LOAD = _state_machine_module.WORK_TYPE_LOAD
WORK_TYPE_SWITCH = _state_machine_module.WORK_TYPE_SWITCH
WORK_TYPE_UNLOAD = _state_machine_module.WORK_TYPE_UNLOAD
bay_heights_from_pattern = _state_machine_module.bay_heights_from_pattern
resolve_container_height_m = _state_machine_module.resolve_container_height_m

from yard_coordinate_mapping import bay_position_m  # noqa: E402


SIMULATION_RTG_NAME = "RTG_SIM_220"
SIMULATION_TICK_SECONDS = 0.05
# Use the yard's bay numbers as the operator-facing gantry distance unit.
# At the default setting, a move from bay 43 to bay 66 takes 23 seconds.
GANTRY_SPEED_BAYS_PER_SECOND = 1.0
TROLLEY_TRAVEL_SECONDS = 1.8
HOIST_TRAVEL_SECONDS = 1.4
LOCK_SETTLE_SECONDS = 0.45
UNLOCK_SETTLE_SECONDS = 0.45
# The live visual compresses one physical container tier to the authored USD
# pitch, but PLC hoist values remain engineering metres.  The yard model uses
# a 2.6 m physical tier, so tier 1's top is 2.6 m and tier 4's top is 10.4 m.
PHYSICAL_TIER_HEIGHT_M = 2.60
FIRST_TIER_PICK_HOIST_M = 2.60
# Keep the empty spreader at the configured 18 m hoist limit between jobs.
# This must satisfy ``RtgOperationStateMachine.safe_hoist_m`` for a sixth-tier
# placement, otherwise the first task remains in HOIST_SAFE and blocks its queue.
WAITING_HOIST_M = 18.0
# The simulated truck trailer top is slightly above the tier-1 yard top.
# This is the engineering hoist value that puts the spreader contact plane on
# the truck box when the outbound box is released.
# The real PLC reports a 4.32 m spreader contact height on a truck-mounted box.
TRUCK_PLACE_HOIST_M = 4.32
TRUCK_APPROACH_SECONDS = 2.5
TRUCK_SETTLE_SECONDS = 0.8
# Keep this equal to the task-truck renderer's exit animation duration.  A
# shorter state duration would clear the truck root before its exit is drawn.
TRUCK_EXIT_SECONDS = 4.0


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
    container_height_m = resolve_container_height_m(
        payload.get("container_height", payload.get("containerHeight")),
        payload.get("container_type", payload.get("containerType")),
    )
    return OperationTask(
        task_id=str(payload.get("task_id") or payload.get("taskId") or int(time.time() * 1000)),
        work_type=work_type,
        source=source,
        target=target,
        vision_required=bool(payload.get("vision_required", payload.get("visionRequired", False))),
        bay_heights=bay_heights_from_pattern(
            payload.get("bay_map") or payload.get("bayMap")
        ),
        container_height_m=container_height_m,
    )


@dataclass
class SimulationTask:
    operation: OperationTask
    task_payload: Dict[str, Any]
    bay_map: str
    container_size: int


def _load_receiver_module_from_source() -> Any:
    """Load the project receiver source without Kit's stale module cache."""

    # Inventory restoration imports these geometry helpers lazily.  Kit keeps
    # normal imports alive across Script Editor executions, so reloading only
    # the receiver would still call an older set_cube_transform implementation
    # and try to add xformOp:translate a second time.
    geometry_module_name = "build_live_containers"
    geometry_module_path = SCRIPT_DIR / f"{geometry_module_name}.py"
    geometry_module = types.ModuleType(geometry_module_name)
    geometry_module.__file__ = str(geometry_module_path)
    geometry_module.__package__ = ""
    sys.modules[geometry_module_name] = geometry_module
    exec(
        compile(
            geometry_module_path.read_text(encoding="utf-8"),
            str(geometry_module_path),
            "exec",
        ),
        geometry_module.__dict__,
    )

    module_name = "rtg_twin_state_receiver"
    module_path = SCRIPT_DIR / f"{module_name}.py"
    previous = sys.modules.get(module_name)
    previous_stop = getattr(previous, "stop_live_driver", None)
    if callable(previous_stop):
        previous_stop()

    module = types.ModuleType(module_name)
    module.__file__ = str(module_path)
    module.__package__ = ""
    sys.modules[module_name] = module
    exec(
        compile(
            module_path.read_text(encoding="utf-8"),
            str(module_path),
            "exec",
        ),
        module.__dict__,
    )
    return module


def _simulation_task_snapshot(
    task: SimulationTask,
    machine: RtgOperationStateMachine,
) -> Dict[str, Any]:
    """Add the simulator state-machine phase to a normal task snapshot."""

    payload = dict(task.task_payload)
    payload.update(
        {
            "stateSource": "SIMULATION",
            "operationState": machine.state.value,
            "visualPhase": machine.visual_phase(),
        }
    )
    return payload


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
        self._current_gantry_bay = 1.0
        self._gantry_motion_start_m: Optional[float] = None
        self._gantry_motion_start_bay: Optional[float] = None
        self._gantry_motion_target_m: Optional[float] = None
        self._gantry_motion_duration_seconds = 0.0
        self._lock = False
        self._unlock_requested_at = 0.0
        self._pending_tasks: list[Dict[str, Any]] = []
        self._submission_sequence = 0
        self._next_task_start_pending = False
        self._manual_pause_announced = False

    @staticmethod
    def _lightweight_test_inventory(receiver_module: Any) -> Any:
        """Create three adjacent six-box bays for PLC-free testing."""

        inventory = receiver_module.YardInventory.empty()
        for bay_number in (19, 21, 23):
            inventory.reconcile_task_snapshot(
                {
                    "taskId": f"SIM-STARTUP-TEST-YARD-{bay_number:03d}",
                    "workType": "UNLOAD",
                    "startPos": f"1C{bay_number}51",
                    "bayMap": "111111",
                    "containerSize": 20,
                    "containerHeight": 2.60,
                }
            )
        return inventory

    @property
    def state(self) -> Optional[str]:
        return self._machine.state.value if self._machine else None

    def start(self) -> "RtgSimulationDriver":
        if self._subscription is not None:
            return self
        try:
            import omni.kit.app  # type: ignore

            receiver_module = _load_receiver_module_from_source()
            self._driver = receiver_module.OmniverseRtgTwinDriver(
                inventory_mode=receiver_module.INVENTORY_MODE_SIMULATION,
                inventory_autosave=False,
            )
            # PLC-free development starts with three compact adjacent test bays,
            # not the complete persistent inventory with hundreds of boxes.
            # Published tasks can still replace/add their addressed test bay.
            self._driver._inventory = self._lightweight_test_inventory(
                receiver_module
            )
            self._driver._inventory_load_error = None
            inventory_status = self._driver.restore_inventory_scene(force=True)
            stream = omni.kit.app.get_app().get_update_event_stream()
            self._subscription = stream.create_subscription_to_pop(
                self._on_update, name="SmartPortRtgSimulationDriver"
            )
            print(
                "[RTG Simulation] started (no PLC/TCP required) "
                f"inventory={inventory_status or 'stage unavailable'}"
            )
        except Exception as exc:
            print(f"[RTG Simulation] unable to start update loop: {exc}")
            traceback.print_exc()
        return self

    def stop(self) -> None:
        self._subscription = None
        self._task = None
        self._machine = None
        self._pending_tasks.clear()
        self._next_task_start_pending = False
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
            "containerHeight": operation.container_height_m,
            "containerType": payload.get("container_type", payload.get("containerType")),
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
            placement_load_bottom_m=self._place_load_bottom_hoist_m(),
        )
        self._started_at = time.monotonic()
        self._state_entered_at = self._started_at
        self._last_state = None
        # Preserve the completed task's RTG pose.  The next task moves from
        # this waiting position instead of redrawing the machine at a fixed
        # initial bay, trolley, and hoist coordinate.
        self._current_bay = 0
        self._gantry_motion_start_m = None
        self._gantry_motion_start_bay = None
        self._gantry_motion_target_m = None
        self._gantry_motion_duration_seconds = 0.0
        self._lock = False
        # Begin with an explicit gantry phase.  The task stack may render now,
        # but the truck, trolley, hoist, and grab sequence remain dormant
        # until the gantry has reached the source bay.
        transition = self._machine.update(
            PlcOperationSnapshot(
                field=operation.source.field,
                bay=self._current_bay,
                trolley_m=self._current_trolley,
                hoist_m=self._current_hoist,
                locked=self._lock,
                vision_confirmed=True,
            ),
            self._started_at,
        )
        if transition is not None:
            self._state_entered_at = self._started_at
            self._last_state = transition.current
        print(
            f"[RTG Simulation] published {operation.work_type} task={operation.task_id} "
            f"source={operation.source} target={operation.target} bayMap={bay_map}"
        )
        # Apply the task envelope before the update stream advances the state
        # machine.  The receiver begins a smooth 20/40 ft transition while
        # the gantry visibly travels toward its source bay.
        self._apply_render_state(self._make_pose(self._started_at))
        return dict(task_payload)

    def clear(self) -> None:
        self._task = None
        self._machine = None
        self._pending_tasks.clear()
        self._next_task_start_pending = False
        print("[RTG Simulation] task cleared")

    def submit(self, payload: Dict[str, Any], *, wait_for_previous: bool) -> Dict[str, Any]:
        """Publish now or queue a task until the current job has fully parked."""

        if not wait_for_previous:
            return self.publish(payload)

        queued_payload = dict(payload)
        if not (queued_payload.get("task_id") or queued_payload.get("taskId")):
            self._submission_sequence += 1
            queued_payload["task_id"] = (
                f"SIM-{int(time.time() * 1000)}-{self._submission_sequence}"
            )
        operation = _task_from_payload(queued_payload)
        ready_for_new_task = (
            self._machine is None
            or (
                self._machine.state == OperationState.COMPLETE
                and self._current_hoist >= WAITING_HOIST_M - 0.01
            )
        )
        if ready_for_new_task and not self._pending_tasks:
            return self.publish(queued_payload)

        self._pending_tasks.append(queued_payload)
        print(
            f"[RTG Simulation] queued task={operation.task_id} "
            f"behind={len(self._pending_tasks)} active={self.state}"
        )
        return {
            "taskId": operation.task_id,
            "state": "QUEUED",
            "queuedBehind": len(self._pending_tasks),
        }

    def _on_update(self, _event: Any) -> None:
        # Remote manual mode owns all three RTG axes.  The simulation must not
        # apply its next task pose behind the operator's back; doing so caused
        # a correctly targeted manual move to drift away from its bay on the
        # following frame.
        if bool(getattr(builtins, "_SMART_PORT_RTG_REMOTE_MANUAL_ACTIVE", False)):
            if not self._manual_pause_announced:
                self._manual_pause_announced = True
                print("[RTG Simulation] paused while remote manual mode is active")
            return
        self._manual_pause_announced = False
        if self._task is None or self._machine is None:
            return

        # Give Kit one frame to present the completed task before the next
        # queued task creates its working stack and vehicle visuals.
        if self._next_task_start_pending:
            self._next_task_start_pending = False
            if self._start_next_queued_task_if_ready():
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
            # Re-sync the full sim inventory at the first frame of a new task.
            # This is the safe moment: no LiveTaskStack, no animated prims.
            # A concurrently running live receiver shares the same USD stage
            # and may have replaced the sim bays with its own live bays.
            if transition.current is OperationState.VALIDATE_TASK:
                restore = getattr(self._driver, "restore_inventory_scene", None)
                if callable(restore):
                    restore(force=True)
        self._apply_render_state(pose)
        if self._next_task_is_ready():
            self._next_task_start_pending = True

    def _next_task_is_ready(self) -> bool:
        return bool(
            self._pending_tasks
            and self._machine is not None
            and self._machine.state == OperationState.COMPLETE
            and self._current_hoist >= WAITING_HOIST_M - 0.01
        )

    def _start_next_queued_task_if_ready(self) -> bool:
        """Start one queued task only after the prior RTG reaches high wait."""

        if (
            not self._pending_tasks
            or self._machine is None
            or self._machine.state != OperationState.COMPLETE
            or self._current_hoist < WAITING_HOIST_M - 0.01
        ):
            return False
        payload = self._pending_tasks.pop(0)
        print(
            f"[RTG Simulation] previous task complete; starting queued task "
            f"remaining={len(self._pending_tasks)}"
        )
        self.publish(payload)
        return True

    def _apply_render_state(self, pose: Dict[str, Any]) -> None:
        """Send one already-built pose to the live renderer immediately."""

        assert self._task is not None and self._machine is not None
        state = {
            "rtgName": SIMULATION_RTG_NAME,
            "timestampUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "pose": pose,
            "task": _simulation_task_snapshot(self._task, self._machine),
        }
        try:
            if self._driver is None:
                receiver_module = _load_receiver_module_from_source()
                self._driver = receiver_module.OmniverseRtgTwinDriver(
                    inventory_mode=receiver_module.INVENTORY_MODE_SIMULATION
                )
            applied = self._driver.apply_state(state)
            if applied is not None and self._last_state is None:
                print(
                    "[RTG Simulation] task preflight "
                    f"spreader={applied.get('spreaderSizeFt')}"
                    f"->{applied.get('spreaderTargetSizeFt')}ft"
                )
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
            target_gantry = bay_position_m(source.field, source.bay)
            if self._gantry_motion_target_m != target_gantry:
                self._gantry_motion_start_m = self._current_gantry
                self._gantry_motion_start_bay = self._current_gantry_bay
                self._gantry_motion_target_m = target_gantry
                self._gantry_motion_duration_seconds = abs(
                    float(source.bay) - self._current_gantry_bay
                ) / GANTRY_SPEED_BAYS_PER_SECOND
            start_gantry = self._gantry_motion_start_m
            if start_gantry is None:
                start_gantry = self._current_gantry
                self._gantry_motion_start_m = start_gantry
            start_bay = self._gantry_motion_start_bay
            if start_bay is None:
                start_bay = self._current_gantry_bay
                self._gantry_motion_start_bay = start_bay
            duration = self._gantry_motion_duration_seconds
            progress = 1.0 if duration <= 0.0 else min(1.0, elapsed / duration)
            # A one-bay-per-second command must stay linear rather than use an
            # easing curve, otherwise the visible speed varies during travel.
            self._current_gantry = start_gantry + (
                target_gantry - start_gantry
            ) * progress
            self._current_gantry_bay = start_bay + (
                float(source.bay) - start_bay
            ) * progress
            # Keep publishing continuous gantry metres for motion control, but
            # expose the nearest bay for the status overlay while travelling.
            # The receiver keeps using the metres during GANTRY_TO_SOURCE, so
            # this display value cannot snap the RTG to its final bay.
            self._current_bay = (
                source.bay
                if progress >= 1.0
                else max(1, int(round(self._current_gantry_bay)))
            )

        if state in (OperationState.TROLLEY_TO_SOURCE, OperationState.HOIST_TO_PICK):
            trolley_target = self._machine.source_trolley_m
            self._current_trolley = self._ramp(self._current_trolley, trolley_target, elapsed)
        elif state in (OperationState.TROLLEY_TO_TARGET, OperationState.HOIST_TO_PLACE):
            trolley_target = self._machine.target_trolley_m
            self._current_trolley = self._ramp(self._current_trolley, trolley_target, elapsed)

        if state == OperationState.HOIST_TO_SOURCE_CLEARANCE:
            approach_height = self._source_approach_hoist_target_m()
            self._current_hoist = self._ramp(
                self._current_hoist,
                approach_height,
                elapsed,
            )
            self._lock = False
        elif state == OperationState.TROLLEY_TO_SOURCE:
            self._current_hoist = self._source_approach_hoist_target_m()
            self._lock = False
        elif state == OperationState.HOIST_TO_PICK:
            pick_target = self._pick_hoist_target_m()
            progress = min(1.0, max(0.0, elapsed / HOIST_TRAVEL_SECONDS))
            approach_height = self._source_approach_hoist_target_m()
            self._current_hoist = approach_height + (pick_target - approach_height) * progress
            # Lock only after the spreader has reached the tier-appropriate
            # contact height and the twist-lock has had time to settle.
            self._lock = elapsed >= HOIST_TRAVEL_SECONDS + LOCK_SETTLE_SECONDS
            if self._lock:
                # A locked task reports the bottom face of the picked box,
                # not the empty spreader's contact plane.  The receiver adds
                # the physical box height back only for rendering, so there
                # is no visual jump at lock.
                self._current_hoist = self._pick_load_bottom_hoist_m()
        elif state == OperationState.LOCK:
            self._lock = True
            self._current_hoist = self._pick_load_bottom_hoist_m()
        elif state == OperationState.HOIST_UP:
            self._lock = True
            progress = min(1.0, max(0.0, elapsed / HOIST_TRAVEL_SECONDS))
            pick_height = self._pick_load_bottom_hoist_m()
            travel_height = self._transfer_hoist_target_m()
            self._current_hoist = pick_height + (travel_height - pick_height) * progress
        elif state == OperationState.TROLLEY_TO_TARGET:
            self._lock = True
            self._current_hoist = self._transfer_hoist_target_m()
        elif state == OperationState.HOIST_TO_PLACE:
            progress = min(1.0, max(0.0, elapsed / HOIST_TRAVEL_SECONDS))
            # Keep the box locked for the complete lowering motion.  The
            # carried PLC value is the box bottom, so interpolate to its
            # resting bottom height; the receiver supplies the matching
            # spreader/rope compensation while the twist-lock remains closed.
            self._lock = True
            travel_height = self._transfer_hoist_target_m()
            place_load_bottom = self._place_load_bottom_hoist_m()
            self._current_hoist = travel_height + (
                place_load_bottom - travel_height
            ) * progress
        elif state == OperationState.UNLOCK:
            self._lock = elapsed < UNLOCK_SETTLE_SECONDS
            self._current_hoist = (
                self._place_load_bottom_hoist_m()
                if self._lock
                else self._place_hoist_target_m()
            )
        elif state in (OperationState.HOIST_SAFE, OperationState.TRUCK_EXITING, OperationState.COMPLETE):
            self._lock = False
            self._current_hoist = self._ramp(
                self._current_hoist,
                self._safe_hoist_target_m(),
                elapsed,
            )
        else:
            self._lock = False
            # VALIDATE_TASK preserves the current waiting pose until the
            # first commanded motion phase starts.

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
            return (
                TRUCK_PLACE_HOIST_M
                - PHYSICAL_TIER_HEIGHT_M
                + operation.container_height_m
            )
        tier = operation.source.tier or 1
        return (
            operation.container_height_m
            + (max(1, tier) - 1) * PHYSICAL_TIER_HEIGHT_M
        )

    def _place_hoist_target_m(self) -> float:
        """Return the engineering height for the destination contact plane."""

        assert self._task is not None
        operation = self._task.operation
        if operation.work_type == WORK_TYPE_UNLOAD:
            return (
                TRUCK_PLACE_HOIST_M
                - PHYSICAL_TIER_HEIGHT_M
                + operation.container_height_m
            )
        tier = operation.target.tier or 1
        return (
            operation.container_height_m
            + (max(1, tier) - 1) * PHYSICAL_TIER_HEIGHT_M
        )

    def _pick_load_bottom_hoist_m(self) -> float:
        """Return the locked source container's bottom-face height."""

        assert self._task is not None
        return max(
            0.0,
            self._pick_hoist_target_m() - self._task.operation.container_height_m,
        )

    def _place_load_bottom_hoist_m(self) -> float:
        """Return the locked destination container's bottom-face height."""

        assert self._task is not None
        return max(
            0.0,
            self._place_hoist_target_m() - self._task.operation.container_height_m,
        )

    def _safe_hoist_target_m(self) -> float:
        """Return the empty-spreader parking height used after task completion."""

        return WAITING_HOIST_M

    def _source_approach_hoist_target_m(self) -> float:
        """Keep the empty spreader clear while it travels to the source row."""

        assert self._machine is not None
        return min(
            self._machine.MAX_HOIST_M,
            max(6.0, self._machine.source_approach_hoist_m),
        )

    def _transfer_hoist_target_m(self) -> float:
        """Raise to the bay-map clearance height before any trolley travel."""

        assert self._machine is not None
        return min(
            self._machine.MAX_HOIST_M,
            max(6.0, self._machine.transfer_hoist_m),
        )

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


def report_simulated_crossbeam_alignment() -> Optional[Dict[str, Any]]:
    """Report the live model centres used to calibrate a simulated 40 ft stop."""

    if _simulation is None or _simulation._driver is None:
        print("[RTG Simulation] no active simulation driver to measure")
        return None
    return _simulation._driver.report_crossbeam_alignment()


def publish_simulated_task(
    *,
    work_type: str,
    start_pos: Optional[str] = None,
    end_pos: Optional[str] = None,
    bay_map: str = "123456",
    container_size: int = 20,
    container_height: Optional[float] = None,
    container_type: Optional[str] = None,
    task_id: Optional[str] = None,
    container_no: Optional[str] = None,
    vision_required: bool = False,
    wait_for_previous: bool = True,
) -> Dict[str, Any]:
    """Publish or queue one simulated LOAD/UNLOAD/SWITCH task.

    By default, start this task only after the active task reaches ``COMPLETE``
    and its empty spreader has reached high wait. Set
    ``wait_for_previous=False`` only to deliberately replace an active task.
    """
    simulator = start_simulation()
    return simulator.submit(
        {
            "work_type": work_type,
            "start_pos": start_pos,
            "end_pos": end_pos,
            "bay_map": bay_map,
            "container_size": container_size,
            "container_height": container_height,
            "container_type": container_type,
            "task_id": task_id,
            "container_no": container_no,
            "vision_required": vision_required,
        },
        wait_for_previous=wait_for_previous,
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
        "queuedTaskCount": len(_simulation._pending_tasks),
    }


if __name__ == "__main__":
    print("Use start_simulation() and publish_simulated_task(...) in Kit Script Editor.")
