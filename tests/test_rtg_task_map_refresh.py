from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "omniverse/scripts"))
import rtg_twin_state_receiver as receiver
from yard_inventory import YardInventory


@pytest.fixture
def driver(tmp_path):
    class Driver(receiver.OmniverseRtgTwinDriver):
        def _render_inventory_bay(self, stage, key):
            stack = self._inventory.stacks.get(key)
            self.rendered.append((key, self._inventory.stack_pattern(stack) if stack else None))

    instance = Driver(tmp_path / "yard.live.json", inventory_autosave=False)
    instance.rendered = []
    return instance


@pytest.fixture
def stage():
    return SimpleNamespace(GetPrimAtPath=lambda path: None)


def load_task(**changes):
    return dict({"taskId": "load-fourth-tier", "workType": "LOAD", "endPos": "4C2844",
                 "containerSize": 40, "containerHeight": 2.6, "bayMap": "011300"}, **changes)


def test_new_task_map_overrides_stale_local_inventory(driver, stage):
    driver._inventory.reconcile_task_snapshot(load_task(bayMap="011500"))
    prepared = driver._prepare_inventory_task(stage, load_task())
    assert prepared["bayMap"] == "011300"
    assert driver._inventory.stack_pattern(driver._inventory.stack("4C", 28, 40)) == "011300"
    machine = driver._build_operation_state_machine(prepared, driver._task_vehicle_key(prepared))
    assert machine.placement_load_bottom_hoist_m == pytest.approx(10.4)


def test_same_task_map_correction_is_not_overwritten_by_its_cached_first_frame(driver, stage):
    driver._prepare_inventory_task(stage, load_task(bayMap="011500"))
    corrected = driver._prepare_inventory_task(stage, load_task())
    assert corrected["bayMap"] == "011300"
    assert corrected["inventorySyncState"] == receiver.INVENTORY_SYNC_READY
    assert "4C/028" in driver._inventory_dirty_bays


def test_missing_map_and_cross_bay_target_map_can_arrive_later_without_reset(driver, stage):
    pending = driver._prepare_inventory_task(stage, load_task(bayMap=None))
    assert pending["inventorySyncState"] == receiver.INVENTORY_SYNC_PENDING
    assert driver._prepare_inventory_task(stage, load_task())["inventorySyncState"] == receiver.INVENTORY_SYNC_READY
    switch = {"taskId": "cross-bay", "workType": "SWITCH", "startPos": "2C1111",
              "endPos": "2C3551", "containerSize": 20, "bayMap": "100000"}
    assert driver._prepare_inventory_task(stage, switch)["inventorySyncState"] == receiver.INVENTORY_SYNC_PENDING
    assert driver._prepare_inventory_task(stage, dict(switch, targetBayMap="000000"))["inventorySyncState"] == receiver.INVENTORY_SYNC_READY


def test_unchanged_task_frames_do_not_reconcile_save_or_render_again(driver, stage):
    reconcile = Mock(wraps=driver._inventory.reconcile_task_snapshot)
    driver._inventory.reconcile_task_snapshot = reconcile
    driver._inventory.save = Mock()
    first = driver._prepare_inventory_task(stage, load_task())
    for age in range(10):
        prepared = driver._prepare_inventory_task(stage, load_task(taskAgeSeconds=age))
        assert prepared["inventoryRevision"] == first["inventoryRevision"]
    assert reconcile.call_count == 1
    assert driver.rendered == []
    driver._inventory.save.assert_not_called()


def test_same_map_with_corrected_slot_heights_refreshes_fourth_tier_contact(driver, stage):
    driver._prepare_inventory_task(stage, load_task())
    corrected = driver._prepare_inventory_task(stage, load_task(baySlotHeights={
        "R04_T01": 2.9, "R04T02": {"height": 2.9}, "4-3": 2.9,
    }))
    assert corrected["baySlotHeights"][3] == [2.9, 2.9, 2.9]
    machine = driver._build_operation_state_machine(corrected, driver._task_vehicle_key(corrected))
    assert machine.placement_load_bottom_hoist_m == pytest.approx(11.3)


def test_corrected_hidden_inventory_is_rendered_before_the_next_task_reveals_it(driver, stage):
    driver._prepare_inventory_task(stage, load_task(bayMap="011500"))
    driver._prepare_inventory_task(stage, load_task())
    assert driver.rendered == []
    driver._prepare_inventory_task(stage, load_task(taskId="next-task", endPos="4C3211", bayMap="000000"))
    assert driver.rendered == [("4C/028", "011300")]
    assert "4C/028" not in driver._inventory_dirty_bays


def test_duplicate_completion_with_changed_metadata_does_not_restore_pre_task_map(driver, stage):
    task = load_task()
    prepared = driver._prepare_inventory_task(stage, task)
    assert driver._settle_inventory_task(stage, prepared)
    assert driver._inventory.stack_pattern(driver._inventory.stack("4C", 28, 40)) == "011400"
    driver._prepare_inventory_task(stage, dict(task, operationState="COMPLETED", containerType="40GP"))
    assert driver._inventory.stack_pattern(driver._inventory.stack("4C", 28, 40)) == "011400"
    assert len(driver._inventory.completed_tasks) == 1


def test_invalid_map_cannot_silently_fall_back_to_cached_rows(driver, stage):
    driver._prepare_inventory_task(stage, load_task())
    driver._inventory_full_sync_ready = True
    corrected = driver._prepare_inventory_task(stage, load_task(bayMap="011900"))
    assert corrected["inventorySyncState"] == receiver.INVENTORY_SYNC_PENDING
    assert corrected["bayMap"] == "011900"
    assert driver._task_stack_key(corrected) is None


def test_equal_maps_still_accept_new_slot_heights_and_repeated_metadata_is_a_noop():
    inventory = YardInventory.empty()
    task = load_task()
    inventory.reconcile_task_snapshot(task)
    revision = inventory.stack("4C", 28, 40)["revision"]
    assert inventory.reconcile_task_snapshot(task) == set()
    corrected = load_task(baySlotHeights=[[], [2.9], [2.9], [2.9, 2.9, 2.9], [], []])
    assert inventory.reconcile_task_snapshot(corrected) == {"4C/028"}
    assert inventory.stack("4C", 28, 40)["revision"] == revision + 1
    before = copy.deepcopy(inventory.stacks)
    assert inventory.reconcile_task_snapshot(corrected) == set()
    assert inventory.stacks == before


def test_corrected_map_and_heights_keep_the_inflight_operation_phase(driver, stage):
    driver._set_operation_state_attr = lambda *args: None
    driver._set_task_flow_attr = lambda *args: None
    prepared = driver._prepare_inventory_task(stage, load_task(bayMap="011500"))
    pose = {"currentField": "4C", "currentBay": 28, "trolleyMeter": 9.5,
            "hoistMeter": 15.0, "lockState": 1}
    driver._advance_operation_flow(stage, driver._task_vehicle_key(prepared), prepared, pose)
    original = driver._operation_state_machine
    original.state = receiver.OperationState.HOIST_TO_PLACE
    entered_at = original.entered_at
    corrected = driver._prepare_inventory_task(stage, load_task(baySlotHeights=[
        [], [2.6], [2.6], [2.9, 2.9, 2.9], [], [],
    ]))
    driver._advance_operation_flow(stage, driver._task_vehicle_key(corrected), corrected, pose)
    machine = driver._operation_state_machine
    assert machine is not original
    assert machine.state == receiver.OperationState.HOIST_TO_PLACE
    assert machine.entered_at == entered_at
    assert machine.task.bay_heights == (0, 1, 1, 3, 0, 0)
    assert machine.placement_load_bottom_hoist_m == pytest.approx(11.3)


def test_correcting_a_map_does_not_recreate_the_same_truck(driver, stage):
    old = load_task(bayMap="011500")
    task = load_task()
    driver._last_task_vehicle_key = driver._task_vehicle_key(old)
    driver._create_task_vehicle = Mock()
    driver._restore_task_source = Mock()
    driver._advance_operation_flow = lambda *args: receiver.TASK_STATE_CARRYING
    driver._update_task_vehicle_pose = lambda *args: 1.0
    driver._sync_task_source_visual = lambda *args: None
    driver._sync_task_visuals = lambda *args: None
    driver._sync_task_vehicle(stage, task, {})
    driver._create_task_vehicle.assert_not_called()
    driver._restore_task_source.assert_not_called()
    assert driver._last_task_vehicle_key == driver._task_vehicle_key(task)
