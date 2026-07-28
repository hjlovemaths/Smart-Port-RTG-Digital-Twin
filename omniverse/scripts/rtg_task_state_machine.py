"""PLC-driven RTG operation state machines.

The state machine is deliberately independent from Omniverse/pxr.  PLC frames
remain the source of truth for motion and lock status; this module only decides
which operation phase is currently safe to show or execute next.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


WORK_TYPE_LOAD = "LOAD"       # truck -> yard
WORK_TYPE_UNLOAD = "UNLOAD"   # yard -> truck
WORK_TYPE_SWITCH = "SWITCH"   # yard -> yard

TRUCK_TROLLEY_M = 0.67
ROW_TROLLEY_M = {
    1: 18.06,
    2: 15.19,
    3: 12.35,
    4: 9.50,
    5: 6.66,
    6: 3.71,
}


class OperationState(str, Enum):
    IDLE = "IDLE"
    VALIDATE_TASK = "VALIDATE_TASK"
    GANTRY_TO_SOURCE = "GANTRY_TO_SOURCE"
    TRUCK_APPROACH = "TRUCK_APPROACH"
    TRUCK_WAITING = "TRUCK_WAITING"
    TROLLEY_TO_SOURCE = "TROLLEY_TO_SOURCE"
    HOIST_TO_PICK = "HOIST_TO_PICK"
    VISION_CONFIRM = "VISION_CONFIRM"
    LOCK = "LOCK"
    HOIST_UP = "HOIST_UP"
    TROLLEY_TO_TARGET = "TROLLEY_TO_TARGET"
    HOIST_TO_PLACE = "HOIST_TO_PLACE"
    UNLOCK = "UNLOCK"
    HOIST_SAFE = "HOIST_SAFE"
    TRUCK_EXITING = "TRUCK_EXITING"
    COMPLETE = "COMPLETE"
    FAULT = "FAULT"


@dataclass(frozen=True)
class TaskSlot:
    field: str
    bay: int
    row: Optional[int] = None
    tier: Optional[int] = None


@dataclass(frozen=True)
class OperationTask:
    task_id: str
    work_type: str
    source: TaskSlot
    target: TaskSlot
    vision_required: bool = False


@dataclass(frozen=True)
class PlcOperationSnapshot:
    field: Optional[str]
    bay: Optional[int]
    trolley_m: Optional[float]
    hoist_m: Optional[float]
    locked: bool
    alarmed: bool = False
    vision_confirmed: bool = True


@dataclass(frozen=True)
class StateTransition:
    previous: OperationState
    current: OperationState
    reason: str


class RtgOperationStateMachine:
    """Shared state machine for LOAD, UNLOAD and SWITCH tasks."""

    POSITION_TOLERANCE_M = 0.35
    SAFE_HOIST_M = 4.0
    SAFE_HOIST_CLEARANCE_M = 2.50
    MAX_HOIST_M = 15.0

    def __init__(
        self,
        task: OperationTask,
        *,
        truck_approach_seconds: float = 5.0,
        truck_settle_seconds: float = 1.5,
        truck_exit_seconds: float = 4.0,
        state_timeout_seconds: float = 120.0,
    ) -> None:
        if task.work_type not in (WORK_TYPE_LOAD, WORK_TYPE_UNLOAD, WORK_TYPE_SWITCH):
            raise ValueError(f"Unsupported RTG work type: {task.work_type!r}")
        self.task = task
        self.state = OperationState.VALIDATE_TASK
        self.entered_at = 0.0
        self.truck_approach_seconds = truck_approach_seconds
        self.truck_settle_seconds = truck_settle_seconds
        self.truck_exit_seconds = truck_exit_seconds
        self.state_timeout_seconds = state_timeout_seconds

    @property
    def uses_truck(self) -> bool:
        return self.task.work_type in (WORK_TYPE_LOAD, WORK_TYPE_UNLOAD)

    @property
    def source_trolley_m(self) -> float:
        if self.task.work_type == WORK_TYPE_LOAD:
            return TRUCK_TROLLEY_M
        return self._slot_trolley(self.task.source)

    @property
    def target_trolley_m(self) -> float:
        if self.task.work_type == WORK_TYPE_UNLOAD:
            return TRUCK_TROLLEY_M
        return self._slot_trolley(self.task.target)

    @property
    def safe_hoist_m(self) -> float:
        """Return a clearance height that keeps the spreader above its target."""

        if self.task.work_type not in (WORK_TYPE_LOAD, WORK_TYPE_SWITCH):
            return self.SAFE_HOIST_M
        tier = self.task.target.tier or 1
        target_contact_m = max(1, tier) * self.SAFE_HOIST_CLEARANCE_M
        return min(
            self.MAX_HOIST_M,
            max(self.SAFE_HOIST_M, target_contact_m + self.SAFE_HOIST_CLEARANCE_M),
        )

    def update(self, plc: PlcOperationSnapshot, now: float) -> Optional[StateTransition]:
        """Advance at most one state, based solely on the latest PLC snapshot."""

        if self.entered_at == 0.0:
            self.entered_at = now
        if self.state in (OperationState.COMPLETE, OperationState.FAULT):
            return None
        if plc.alarmed:
            return self._transition(OperationState.FAULT, now, "PLC alarm active")
        if now - self.entered_at > self.state_timeout_seconds:
            return self._transition(OperationState.FAULT, now, "state timeout")

        next_state: Optional[tuple[OperationState, str]] = None
        if self.state == OperationState.VALIDATE_TASK:
            next_state = (OperationState.GANTRY_TO_SOURCE, "task validated")
        elif self.state == OperationState.GANTRY_TO_SOURCE:
            if self._at_slot(plc, self.task.source):
                next_state = (
                    OperationState.TRUCK_APPROACH
                    if self.uses_truck
                    else OperationState.TROLLEY_TO_SOURCE,
                    "gantry reached source bay",
                )
        elif self.state == OperationState.TRUCK_APPROACH:
            if now - self.entered_at >= self.truck_approach_seconds:
                next_state = (OperationState.TRUCK_WAITING, "truck reached bay")
        elif self.state == OperationState.TRUCK_WAITING:
            if now - self.entered_at >= self.truck_settle_seconds:
                next_state = (OperationState.TROLLEY_TO_SOURCE, "truck settled")
        elif self.state == OperationState.TROLLEY_TO_SOURCE:
            if self._at_trolley(plc, self.source_trolley_m):
                next_state = (OperationState.HOIST_TO_PICK, "trolley reached source")
        elif self.state == OperationState.HOIST_TO_PICK:
            # Height is displayed from PLC but its safe numeric direction varies
            # between RTG PLCs; a confirmed lock is the portable pick-complete
            # signal.
            if plc.locked:
                next_state = (
                    OperationState.VISION_CONFIRM
                    if self.task.vision_required
                    else OperationState.LOCK,
                    "PLC lock detected",
                )
        elif self.state == OperationState.VISION_CONFIRM:
            if plc.vision_confirmed:
                next_state = (OperationState.LOCK, "vision confirmed container")
        elif self.state == OperationState.LOCK:
            if plc.locked:
                next_state = (OperationState.HOIST_UP, "lock confirmed")
        elif self.state == OperationState.HOIST_UP:
            # Lock remaining true prevents a trolley transfer with a released box.
            if plc.locked:
                next_state = (OperationState.TROLLEY_TO_TARGET, "load lifted")
        elif self.state == OperationState.TROLLEY_TO_TARGET:
            if self._at_trolley(plc, self.target_trolley_m):
                next_state = (OperationState.HOIST_TO_PLACE, "trolley reached target")
        elif self.state == OperationState.HOIST_TO_PLACE:
            if plc.locked:
                next_state = (OperationState.UNLOCK, "at placement height")
        elif self.state == OperationState.UNLOCK:
            if not plc.locked:
                next_state = (OperationState.HOIST_SAFE, "PLC unlock confirmed")
        elif self.state == OperationState.HOIST_SAFE:
            if plc.hoist_m is None or plc.hoist_m >= self.safe_hoist_m:
                next_state = (
                    OperationState.TRUCK_EXITING
                    if self.uses_truck
                    else OperationState.COMPLETE,
                    "spreader at safe height",
                )
        elif self.state == OperationState.TRUCK_EXITING:
            if now - self.entered_at >= self.truck_exit_seconds:
                next_state = (OperationState.COMPLETE, "truck exited yard")

        if next_state is None:
            return None
        return self._transition(next_state[0], now, next_state[1])

    def visual_phase(self) -> str:
        """Map detailed execution states onto the existing scene visual phases."""

        if self.state == OperationState.GANTRY_TO_SOURCE:
            return "GANTRY_TO_SOURCE"
        if self.state == OperationState.TRUCK_APPROACH:
            return "TRUCK_APPROACH"
        if self.state == OperationState.TRUCK_WAITING:
            return "TRUCK_WAITING"
        if self.state in (
            OperationState.VALIDATE_TASK,
            OperationState.TROLLEY_TO_SOURCE,
            OperationState.HOIST_TO_PICK,
            OperationState.VISION_CONFIRM,
        ):
            return "READY_TO_PICK"
        if self.state in (
            # PLC lock is the authoritative pick-complete event.  Showing the
            # carried visual only after a later HOIST_UP frame made SWITCH
            # tasks appear to grab successfully with an empty spreader.
            OperationState.LOCK,
            OperationState.HOIST_UP,
            OperationState.TROLLEY_TO_TARGET,
            OperationState.HOIST_TO_PLACE,
            OperationState.UNLOCK,
        ):
            return "CARRYING"
        if self.state == OperationState.HOIST_SAFE:
            return "PLACED"
        if self.state == OperationState.TRUCK_EXITING:
            return "TRUCK_EXITING"
        if self.state == OperationState.COMPLETE:
            return "COMPLETE"
        if self.state == OperationState.FAULT:
            return "FAULT"
        return "IDLE"

    def _transition(
        self, state: OperationState, now: float, reason: str
    ) -> StateTransition:
        transition = StateTransition(self.state, state, reason)
        self.state = state
        self.entered_at = now
        return transition

    @classmethod
    def _slot_trolley(cls, slot: TaskSlot) -> float:
        if slot.row not in ROW_TROLLEY_M:
            raise ValueError(f"Task slot needs a valid row: {slot!r}")
        return ROW_TROLLEY_M[slot.row]

    @classmethod
    def _at_trolley(cls, plc: PlcOperationSnapshot, target_m: float) -> bool:
        return (
            plc.trolley_m is not None
            and abs(float(plc.trolley_m) - target_m) <= cls.POSITION_TOLERANCE_M
        )

    @staticmethod
    def _at_slot(plc: PlcOperationSnapshot, slot: TaskSlot) -> bool:
        # ``currentField`` identifies the RTG working block in some PLC feeds,
        # while task positions use the yard slot block. They can legitimately
        # differ (for example device 3C serving task slot 4C0762). The actual
        # gantry arrival signal shared by both feeds is the bay number.
        return plc.bay == slot.bay
