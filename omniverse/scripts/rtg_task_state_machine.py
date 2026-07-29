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


def bay_heights_from_pattern(value: object) -> tuple[int, ...]:
    """Parse the current six-row bay map into physical stack heights.

    The operation feeds use ``bayMap`` in R01..R06 order, such as ``441000``.
    Returning an empty tuple for an unavailable pattern keeps the state machine
    backward compatible with PLC payloads that do not provide a bay map.
    """

    pattern = str(value or "").strip()
    if len(pattern) != 6 or not pattern.isdigit():
        return ()
    return tuple(int(height) for height in pattern)


class OperationState(str, Enum):
    IDLE = "IDLE"
    VALIDATE_TASK = "VALIDATE_TASK"
    GANTRY_TO_SOURCE = "GANTRY_TO_SOURCE"
    TRUCK_APPROACH = "TRUCK_APPROACH"
    TRUCK_WAITING = "TRUCK_WAITING"
    HOIST_TO_SOURCE_CLEARANCE = "HOIST_TO_SOURCE_CLEARANCE"
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
    bay_heights: tuple[int, ...] = ()


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
    HEIGHT_TOLERANCE_M = 0.01
    SAFE_HOIST_M = 4.0
    CONTAINER_TIER_HEIGHT_M = 2.60
    TRANSFER_CLEARANCE_M = 1.0
    MAX_HOIST_M = 18.0

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
        self._source_approach_required_m: Optional[float] = None

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
        """Return a clearance height above the box being cleared after release."""

        if self.task.work_type == WORK_TYPE_UNLOAD:
            # The box comes from the yard, so clear its source tier before
            # travelling back toward the truck.  A fixed 6 m height is below
            # the third tier and made the loaded spreader visibly descend.
            tier = self.task.source.tier or 1
        elif self.task.work_type in (WORK_TYPE_LOAD, WORK_TYPE_SWITCH):
            tier = self.task.target.tier or 1
        else:
            return self.SAFE_HOIST_M
        target_contact_m = max(1, tier) * self.CONTAINER_TIER_HEIGHT_M
        return min(
            self.MAX_HOIST_M,
            max(
                self.SAFE_HOIST_M,
                target_contact_m + self.CONTAINER_TIER_HEIGHT_M,
            ),
        )

    @property
    def transfer_rows(self) -> tuple[int, ...]:
        """Return the yard rows crossed while carrying the container."""

        return self._rows_between(self.source_trolley_m, self.target_trolley_m)

    @property
    def source_approach_hoist_m(self) -> float:
        """Empty-spreader height needed before travelling to the source box."""

        if self._source_approach_required_m is not None:
            return self._source_approach_required_m
        return self._source_approach_hoist_for(TRUCK_TROLLEY_M)

    @property
    def source_approach_clearance_possible(self) -> bool:
        return self.source_approach_hoist_m <= self.MAX_HOIST_M

    @property
    def transfer_obstacle_tier(self) -> int:
        """Tallest stack tier in the current bay while carrying a box."""

        if len(self.task.bay_heights) != len(ROW_TROLLEY_M):
            return 0
        # Lift above the highest originally occupied stack in this bay before
        # trolley travel.  Even when the picked box was the top tier of that
        # row, treating it as already removed could start horizontal travel at
        # a lower height than the pick/raise visual and scrape an adjacent row.
        return max(self.task.bay_heights)

    @property
    def transfer_hoist_m(self) -> float:
        """Minimum PLC hoist height that clears every crossed stack.

        Once a box is locked, ``hoistMeter`` reports the height of that box's
        bottom face.  The RTG task protocol therefore requires one metre from
        the tallest stack top to the reported value; the carried box height is
        intentionally not added a second time.
        """

        obstacle_clearance_m = (
            self.transfer_obstacle_tier * self.CONTAINER_TIER_HEIGHT_M
            + self.TRANSFER_CLEARANCE_M
        )
        return round(max(self.safe_hoist_m, obstacle_clearance_m), 3)

    @property
    def transfer_clearance_possible(self) -> bool:
        """Whether the RTG can carry this box over the current bay map."""

        return self.transfer_hoist_m <= self.MAX_HOIST_M

    @property
    def transfer_clearance_fault_reason(self) -> str:
        rows = ",".join(f"R{row:02d}" for row in self.transfer_rows) or "none"
        return (
            "transfer clearance blocked: "
            f"travelRows={rows} bayTallestTier={self.transfer_obstacle_tier} "
            f"requires={self.transfer_hoist_m:.1f}m max={self.MAX_HOIST_M:.1f}m"
        )

    def source_approach_fault_reason(self, start_trolley_m: Optional[float]) -> str:
        rows = self._source_approach_rows(start_trolley_m)
        row_text = ",".join(f"R{row:02d}" for row in rows) or "none"
        return (
            "source approach clearance blocked: "
            f"rows={row_text} tallestTier={self._obstacle_tier(rows)} "
            f"requires={self.source_approach_hoist_m:.1f}m "
            f"max={self.MAX_HOIST_M:.1f}m"
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
            # Reject an impossible loaded transfer before travelling to and
            # locking the source box.  Otherwise the visual can leave a
            # locked box sitting on its stack while the eventual transfer
            # check faults, which looks like a spreader embedded in cargo.
            if not self.transfer_clearance_possible:
                next_state = (
                    OperationState.FAULT,
                    self.transfer_clearance_fault_reason,
                )
            else:
                next_state = (OperationState.GANTRY_TO_SOURCE, "task validated")
        elif self.state == OperationState.GANTRY_TO_SOURCE:
            if self._at_slot(plc, self.task.source):
                next_state = (
                    OperationState.TRUCK_APPROACH
                    if self.uses_truck
                    else OperationState.HOIST_TO_SOURCE_CLEARANCE,
                    "gantry reached source bay",
                )
        elif self.state == OperationState.TRUCK_APPROACH:
            if now - self.entered_at >= self.truck_approach_seconds:
                next_state = (OperationState.TRUCK_WAITING, "truck reached bay")
        elif self.state == OperationState.TRUCK_WAITING:
            if now - self.entered_at >= self.truck_settle_seconds:
                next_state = (
                    OperationState.HOIST_TO_SOURCE_CLEARANCE,
                    "truck settled",
                )
        elif self.state == OperationState.HOIST_TO_SOURCE_CLEARANCE:
            if not self.source_approach_clearance_possible:
                next_state = (
                    OperationState.FAULT,
                    self.source_approach_fault_reason(plc.trolley_m),
                )
            elif (
                plc.hoist_m is not None
                and plc.hoist_m >= self.source_approach_hoist_m
            ):
                next_state = (
                    OperationState.TROLLEY_TO_SOURCE,
                    f"source approach clear at {self.source_approach_hoist_m:.1f}m",
                )
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
            # Horizontal travel is only permitted after the suspended box
            # clears every stack represented by the current bay map.
            if not self.transfer_clearance_possible:
                next_state = (
                    OperationState.FAULT,
                    self.transfer_clearance_fault_reason,
                )
            elif plc.locked and (
                plc.hoist_m is not None
                and plc.hoist_m + self.HEIGHT_TOLERANCE_M >= self.transfer_hoist_m
            ):
                next_state = (
                    OperationState.TROLLEY_TO_TARGET,
                    f"load lifted clear at {self.transfer_hoist_m:.1f}m",
                )
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
        if next_state[0] == OperationState.HOIST_TO_SOURCE_CLEARANCE:
            self._source_approach_required_m = self._source_approach_hoist_for(
                plc.trolley_m
            )
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
            OperationState.HOIST_TO_SOURCE_CLEARANCE,
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
    def _rows_between(cls, start_trolley_m: float, end_trolley_m: float) -> tuple[int, ...]:
        lower = min(start_trolley_m, end_trolley_m)
        upper = max(start_trolley_m, end_trolley_m)
        return tuple(
            row
            for row, trolley_m in sorted(ROW_TROLLEY_M.items())
            if lower - cls.POSITION_TOLERANCE_M
            <= trolley_m
            <= upper + cls.POSITION_TOLERANCE_M
        )

    def _source_approach_rows(
        self, start_trolley_m: Optional[float]
    ) -> tuple[int, ...]:
        if start_trolley_m is None:
            # Without a trolley position, include every row rather than allow
            # an empty spreader to move through an unknown high stack.
            return tuple(sorted(ROW_TROLLEY_M))
        return self._rows_between(float(start_trolley_m), self.source_trolley_m)

    def _source_approach_hoist_for(self, start_trolley_m: Optional[float]) -> float:
        obstacle_clearance_m = (
            self._obstacle_tier(self._source_approach_rows(start_trolley_m))
            * self.CONTAINER_TIER_HEIGHT_M
            + self.TRANSFER_CLEARANCE_M
        )
        return round(max(self.SAFE_HOIST_M, obstacle_clearance_m), 3)

    def _obstacle_tier(self, rows: tuple[int, ...]) -> int:
        if len(self.task.bay_heights) != len(ROW_TROLLEY_M):
            return 0
        return max((self.task.bay_heights[row - 1] for row in rows), default=0)

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
