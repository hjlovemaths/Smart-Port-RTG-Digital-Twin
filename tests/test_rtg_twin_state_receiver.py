from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_ROOT / "omniverse" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import rtg_twin_state_receiver as receiver  # noqa: E402


class TaskPositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.driver = receiver.OmniverseRtgTwinDriver()

    def test_four_c_position_uses_two_digit_bay_then_row_and_tier(self) -> None:
        self.assertEqual(
            self.driver._parse_task_position_details("4C0762"),
            ("4C", 7, 6, 2),
        )

    def test_live_outbound_task_uses_start_bay_row_and_tier(self) -> None:
        task = {
            "taskId": "26885357",
            "workType": "UNLOAD",
            "startPos": "3C7741",
            "endPos": None,
            "containerSize": 0,
            "bayMap": "413122",
        }

        self.assertEqual(
            self.driver._task_stack_key(task),
            ("3C", 77, "413122", 20),
        )
        self.assertEqual(
            self.driver._task_source_container_path(task),
            f"{receiver.LIVE_TASK_STACK_PATH}/R04_T01_Container",
        )

    def test_outbound_prefers_source_over_destination_position(self) -> None:
        task = {
            "workType": "UNLOAD",
            "startPos": "1C02033",
            "endPos": "1C001",
            "containerSize": 40,
            "bayMap": "103020",
        }

        self.assertEqual(
            self.driver._task_stack_key(task),
            ("1C", 20, "103020", 40),
        )
        self.assertEqual(
            self.driver._task_source_container_path(task),
            f"{receiver.LIVE_TASK_STACK_PATH}/R03_T03_Container",
        )

    def test_reported_second_row_first_tier_task_selects_r02_t01(self) -> None:
        task = {
            "taskId": "26885371",
            "workType": "UNLOAD",
            "startPos": "3C7721",
            "containerSize": 0,
            "bayMap": "413012",
        }

        self.assertEqual(
            self.driver._task_source_container_path(task),
            f"{receiver.LIVE_TASK_STACK_PATH}/R02_T01_Container",
        )

    def test_source_outside_bay_map_is_rejected(self) -> None:
        task = {
            "workType": "UNLOAD",
            "startPos": "3C7749",
            "bayMap": "413122",
        }

        self.assertIsNone(self.driver._task_source_container_path(task))

    def test_rehandle_uses_source_bay_row_and_tier(self) -> None:
        task = {
            "taskId": "26945501",
            "workType": "SWITCH",
            "startPos": "3C6323",
            "endPos": "3C6351",
            "containerSize": 20,
            "bayMap": "133456",
        }

        self.assertEqual(
            self.driver._task_stack_key(task),
            ("3C", 63, "133456", 20),
        )
        self.assertEqual(
            self.driver._task_source_container_path(task),
            f"{receiver.LIVE_TASK_STACK_PATH}/R02_T03_Container",
        )

    def test_operation_state_machine_receives_current_bay_heights(self) -> None:
        machine = self.driver._build_operation_state_machine(
            {
                "workType": "UNLOAD",
                "startPos": "3C6323",
                "bayMap": "233400",
            },
            ("UNLOAD", "3C", 63, "233400", 20, "clearance-live"),
        )

        self.assertIsNotNone(machine)
        self.assertEqual(machine.task.bay_heights, (2, 3, 3, 4, 0, 0))
        self.assertEqual(machine.transfer_hoist_m, 11.4)


class GantryCrossbeamAlignmentTests(unittest.TestCase):
    def test_alignment_delta_moves_crossbeam_to_rendered_box_center(self) -> None:
        self.assertAlmostEqual(
            receiver._gantry_crossbeam_alignment_delta_y(18.45, 16.8125),
            1.6375,
        )


class TaskVisualPolicyTests(unittest.TestCase):
    def test_demo_sequence_artifacts_are_disabled_without_touching_live_task_truck(self) -> None:
        class Prim:
            def __init__(self, path: str, parent: object = None) -> None:
                self.path = path
                self.parent = parent
                self.children = []
                self.active = True
                self.expired = False
                if parent is not None:
                    parent.children.append(self)

            def GetName(self) -> str:
                if self.expired:
                    raise RuntimeError("Accessed invalid expired prim")
                return self.path.rsplit("/", 1)[-1]

            def GetPath(self) -> str:
                if self.expired:
                    raise RuntimeError("Accessed invalid expired prim")
                return self.path

            def SetActive(self, value: bool) -> None:
                self.active = value
                if not value:
                    for child in self.children:
                        child.expired = True

        class EditContext:
            def __init__(self, *_args) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> bool:
                return False

        demo_truck = Prim("/World/PortAndRTG/LIVE_internal_yard_truck_unified_40ft")
        demo_truck_child = Prim(
            "/World/PortAndRTG/LIVE_internal_yard_truck_unified_40ft/LIVE_internal_lane_truck_headlight_left",
            parent=demo_truck,
        )
        demo_box = Prim("/World/LiveContainers/Task01_TruckLoadedBox_To_R02_T03")
        live_task_truck = Prim(f"{receiver.LIVE_TASK_TRUCK_PATH}/LoadedContainer")
        prims = {
            prim.path: prim
            for prim in (demo_truck, demo_truck_child, demo_box, live_task_truck)
        }
        stage = types.SimpleNamespace(
            GetSessionLayer=lambda: object(),
            GetPrimAtPath=lambda path: prims.get(path),
            Traverse=lambda: list(prims.values()),
        )
        fake_pxr = types.ModuleType("pxr")
        fake_pxr.Usd = types.SimpleNamespace(EditContext=EditContext)
        fake_pxr.UsdGeom = types.SimpleNamespace(
            Imageable=lambda _prim: types.SimpleNamespace(MakeInvisible=lambda: None)
        )

        with patch.dict(sys.modules, {"pxr": fake_pxr}):
            disabled = receiver.disable_demo_sequence_artifacts(stage)

        self.assertEqual(disabled, 2)
        self.assertFalse(demo_truck.active)
        self.assertTrue(demo_truck_child.active)
        self.assertFalse(demo_box.active)
        self.assertTrue(live_task_truck.active)

    def test_inventory_can_restore_before_first_task_state(self) -> None:
        class RestoreDriver(receiver.OmniverseRtgTwinDriver):
            def __init__(self):
                super().__init__(
                    inventory_mode=receiver.INVENTORY_MODE_SIMULATION,
                    inventory_autosave=False,
                )
                self.stage = object()
                self.synced = []

            def _current_stage(self):
                return self.stage

            def _sync_inventory_scene(self, stage):
                self.synced.append(stage)

        driver = RestoreDriver()
        status = driver.restore_inventory_scene()

        self.assertEqual(driver.synced, [driver.stage])
        self.assertEqual(status["mode"], receiver.INVENTORY_MODE_SIMULATION)
        self.assertEqual(status["stats"]["containerCount"], 574)

    def test_live_and_simulation_modes_use_different_default_files(self) -> None:
        live = receiver.OmniverseRtgTwinDriver(
            inventory_mode=receiver.INVENTORY_MODE_LIVE
        )
        simulation = receiver.OmniverseRtgTwinDriver(
            inventory_mode=receiver.INVENTORY_MODE_SIMULATION
        )

        self.assertNotEqual(live._inventory_path, simulation._inventory_path)
        self.assertTrue(str(live._inventory_path).endswith("yard_inventory.live.json"))
        self.assertTrue(
            str(simulation._inventory_path).endswith("yard_inventory.sim.json")
        )
        self.assertFalse(live._inventory_full_sync_ready)
        self.assertTrue(simulation._inventory_full_sync_ready)

    def test_corrupt_live_cache_degrades_to_pending_empty_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "yard_inventory.live.json"
            path.write_text("{not valid json", encoding="utf-8")

            driver = receiver.OmniverseRtgTwinDriver(
                path,
                inventory_mode=receiver.INVENTORY_MODE_LIVE,
            )

        self.assertIsNotNone(driver._inventory_load_error)
        self.assertEqual(driver._inventory.to_dict()["stats"]["containerCount"], 0)
        self.assertFalse(driver._inventory_full_sync_ready)

    def test_live_task_without_bay_map_stays_pending_and_cannot_settle(self) -> None:
        class InventoryDriver(receiver.OmniverseRtgTwinDriver):
            def _render_inventory_bay(self, _stage, _stack_key):
                return None

        with tempfile.TemporaryDirectory() as temp_dir:
            driver = InventoryDriver(
                Path(temp_dir) / "yard_inventory.live.json",
                inventory_mode=receiver.INVENTORY_MODE_LIVE,
            )
            task = {
                "taskId": "offline-drift",
                "workType": "UNLOAD",
                "startPos": "3C6321",
                "containerSize": 20,
                "operationState": "COMPLETED",
            }
            prepared = driver._prepare_inventory_task(object(), task)

            self.assertEqual(
                prepared["inventorySyncState"],
                receiver.INVENTORY_SYNC_PENDING,
            )
            self.assertFalse(driver._settle_inventory_task(object(), prepared))
            self.assertNotIn(
                "offline-drift",
                driver._inventory.completed_tasks,
            )

    def test_live_task_bay_map_reconciles_offline_drift_before_settlement(self) -> None:
        class InventoryDriver(receiver.OmniverseRtgTwinDriver):
            def _render_inventory_bay(self, _stage, _stack_key):
                return None

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "yard_inventory.live.json"
            driver = InventoryDriver(
                path,
                inventory_mode=receiver.INVENTORY_MODE_LIVE,
            )
            task = {
                "taskId": "offline-reconciled",
                "workType": "UNLOAD",
                "startPos": "3C6321",
                "containerSize": 20,
                "containerNo": "REAL-BOX",
                "bayMap": "010000",
                "operationState": "COMPLETED",
            }
            prepared = driver._prepare_inventory_task(object(), task)

            self.assertEqual(
                prepared["inventorySyncState"],
                receiver.INVENTORY_SYNC_READY,
            )
            self.assertTrue(driver._settle_inventory_task(object(), prepared))
            self.assertEqual(
                driver._inventory.stack_pattern(
                    driver._inventory.stack("3C", 63, 20)
                ),
                "000000",
            )

    def test_live_cross_bay_switch_requires_target_bay_map(self) -> None:
        class InventoryDriver(receiver.OmniverseRtgTwinDriver):
            def _render_inventory_bay(self, _stage, _stack_key):
                return None

        with tempfile.TemporaryDirectory() as temp_dir:
            driver = InventoryDriver(
                Path(temp_dir) / "yard_inventory.live.json",
                inventory_mode=receiver.INVENTORY_MODE_LIVE,
            )
            task = {
                "taskId": "cross-bay-sync",
                "workType": "SWITCH",
                "startPos": "2C1111",
                "endPos": "2C3551",
                "containerSize": 20,
                "containerNo": "CROSS-BAY",
                "bayMap": "100000",
            }

            pending = driver._prepare_inventory_task(object(), task)
            self.assertEqual(
                pending["inventorySyncState"],
                receiver.INVENTORY_SYNC_PENDING,
            )

            driver._active_inventory_task_key = None
            ready = driver._prepare_inventory_task(
                object(),
                dict(task, targetBayMap="000000"),
            )
            self.assertEqual(
                ready["inventorySyncState"],
                receiver.INVENTORY_SYNC_READY,
            )
            self.assertTrue(driver._settle_inventory_task(object(), ready))

    def test_failed_inventory_save_rolls_back_in_memory_transaction(self) -> None:
        class InventoryDriver(receiver.OmniverseRtgTwinDriver):
            def _render_inventory_bay(self, _stage, _stack_key):
                return None

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "yard_inventory.live.json"
            driver = InventoryDriver(
                path,
                inventory_mode=receiver.INVENTORY_MODE_LIVE,
            )
            task = {
                "taskId": "save-failure",
                "workType": "LOAD",
                "endPos": "3C6321",
                "containerSize": 20,
                "containerNo": "SAVE-FAIL-BOX",
                "bayMap": "000000",
                "operationState": "COMPLETED",
            }
            prepared = driver._prepare_inventory_task(object(), task)
            driver._inventory.save = lambda _path: (_ for _ in ()).throw(
                OSError("disk full")
            )

            self.assertFalse(driver._settle_inventory_task(object(), prepared))
            self.assertNotIn("save-failure", driver._inventory.completed_tasks)
            self.assertEqual(
                driver._inventory.stack_pattern(
                    driver._inventory.stack("3C", 63, 20)
                ),
                "000000",
            )

    def test_receiver_commits_completed_task_to_persistent_inventory(self) -> None:
        class InventoryDriver(receiver.OmniverseRtgTwinDriver):
            def __init__(self, path):
                super().__init__(path, inventory_autosave=True)
                self.rendered = []

            def _render_inventory_bay(self, _stage, stack_key):
                self.rendered.append(stack_key)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "yard_inventory.json"
            driver = InventoryDriver(path)
            task = {
                "taskId": "receiver-load-1",
                "workType": "LOAD",
                "endPos": "3C6321",
                "containerSize": 20,
                "containerNo": "RECEIVER-BOX",
                "containerHeight": 2.591,
                "bayMap": "000000",
            }
            prepared = driver._prepare_inventory_task(object(), task)
            # Task preparation uses the temporary animated stack, so it must not
            # rebuild the persistent inventory bay before immediately hiding it.
            self.assertEqual(driver.rendered, [])
            completed = dict(prepared, operationState="COMPLETED")

            self.assertTrue(driver._settle_inventory_task(object(), completed))
            self.assertEqual(driver.rendered, ["3C/063"])
            self.assertTrue(path.is_file())
            self.assertEqual(
                driver._inventory.stack_pattern(
                    driver._inventory.stack("3C", 63, 20)
                ),
                "010000",
            )
            self.assertEqual(
                driver._inventory.stack("3C", 63, 20)["rows"][1][0][
                    "containerNo"
                ],
                "RECEIVER-BOX",
            )

            rendered_count = len(driver.rendered)
            self.assertTrue(driver._settle_inventory_task(object(), completed))
            self.assertEqual(len(driver.rendered), rendered_count)

    def test_explicit_container_height_is_independent_from_20_or_40ft_length(self) -> None:
        gp_40 = {"containerSize": 40, "containerHeight": "2.591", "containerType": "40GP"}
        hc_40 = {"containerSize": 40, "containerHeight": "2896mm", "containerType": "40HC"}

        self.assertEqual(receiver._task_container_height_m(gp_40), 2.591)
        self.assertEqual(receiver._task_container_height_m(hc_40), 2.896)

    def test_container_type_is_only_a_height_fallback(self) -> None:
        self.assertEqual(
            receiver._task_container_height_m(
                {"containerSize": 40, "containerType": "40HC"}
            ),
            2.896,
        )
        self.assertEqual(
            receiver._task_container_height_m(
                {
                    "containerSize": 40,
                    "containerHeight": "2.60",
                    "containerType": "40HC",
                }
            ),
            2.6,
        )

    def test_future_bay_inventory_overrides_each_slot_height(self) -> None:
        heights = receiver._task_bay_slot_heights_m(
            {
                "containerHeight": 2.896,
                "baySlotHeights": {
                    "R01_T01": 2.591,
                    "R01_T02": {"containerType": "40HC"},
                },
            },
            "210000",
            source_row=1,
            source_tier=2,
        )

        self.assertEqual(heights[0], (2.591, 2.896))
        self.assertEqual(heights[1], (2.6,))

    def test_task_completion_detection_accepts_simulator_and_terminal_states(self) -> None:
        self.assertTrue(
            receiver._task_is_complete(
                {
                    "stateSource": "SIMULATION",
                    "visualPhase": "COMPLETE",
                    "state": "ACTIVE",
                }
            )
        )
        self.assertTrue(receiver._task_is_complete({"operationState": "DONE"}))
        self.assertTrue(receiver._task_is_complete({"state": "COMPLETED"}))
        self.assertFalse(
            receiver._task_is_complete(
                {
                    "stateSource": "SIMULATION",
                    "visualPhase": "GANTRY_TO_SOURCE",
                    "state": "ACTIVE",
                }
            )
        )

    def test_task_stack_render_key_changes_for_each_task_identity(self) -> None:
        driver = receiver.OmniverseRtgTwinDriver()
        first = {
            "taskId": "switch-20",
            "workType": "SWITCH",
            "startPos": "3C4344",
            "endPos": "3C4356",
            "containerSize": 20,
            "bayMap": "123456",
        }
        second = dict(
            first,
            taskId="switch-40",
            startPos="3C6644",
            endPos="3C6656",
            containerSize=40,
        )

        self.assertNotEqual(
            driver._task_stack_render_key(first),
            driver._task_stack_render_key(second),
        )

    def test_spreader_only_transforms_when_a_new_task_needs_a_new_size(self) -> None:
        class Controller:
            def __init__(self) -> None:
                self.current_size = 20.0
                self.commands = []

            def current_spreader_size_ft(self):
                return self.current_size

            def set_spreader_size(self, size_ft, *, smooth):
                self.commands.append((size_ft, smooth))
                self.current_size = float(size_ft)
                return self.current_size

        driver = receiver.OmniverseRtgTwinDriver()
        controller = Controller()
        first_task = {
            "taskId": "task-40",
            "workType": "SWITCH",
            "startPos": "3C6321",
            "endPos": "3C6351",
            "containerSize": 40,
        }

        self.assertEqual(
            driver._sync_spreader_size_for_task(controller, first_task),
            (40.0, 40),
        )
        self.assertEqual(controller.commands, [(40, True)])
        self.assertEqual(
            driver._sync_spreader_size_for_task(controller, first_task),
            (40.0, 40),
        )
        self.assertEqual(controller.commands, [(40, True)])

        # A USD animation sample may reset the composed transform during an
        # active task.  The receiver repairs that mismatch, but leaves a
        # matching endpoint alone on the following frame.
        controller.current_size = 20.0
        self.assertEqual(
            driver._sync_spreader_size_for_task(controller, first_task),
            (40.0, 40),
        )
        self.assertEqual(controller.commands, [(40, True), (40, True)])
        self.assertEqual(
            driver._sync_spreader_size_for_task(controller, first_task),
            (40.0, 40),
        )
        self.assertEqual(controller.commands, [(40, True), (40, True)])

        matching_task = dict(first_task, taskId="task-40-repeat")
        self.assertEqual(
            driver._sync_spreader_size_for_task(controller, matching_task),
            (40.0, 40),
        )
        self.assertEqual(controller.commands, [(40, True), (40, True), (40, True)])

        self.assertEqual(
            driver._sync_spreader_size_for_task(controller, {}),
            (40.0, None),
        )
        self.assertEqual(controller.commands, [(40, True), (40, True), (40, True)])

        next_task = dict(first_task, taskId="task-20", containerSize=20)
        self.assertEqual(
            driver._sync_spreader_size_for_task(controller, next_task),
            (20.0, 20),
        )
        self.assertEqual(
            controller.commands,
            [(40, True), (40, True), (40, True), (20, True)],
        )

    def test_only_explicit_simulator_phase_bypasses_plc_state_machine(self) -> None:
        self.assertEqual(
            receiver._simulated_visual_phase(
                {
                    "stateSource": "SIMULATION",
                    "visualPhase": "CARRYING",
                }
            ),
            receiver.TASK_STATE_CARRYING,
        )
        self.assertIsNone(
            receiver._simulated_visual_phase(
                {
                    "stateSource": "PLC",
                    "visualPhase": "CARRYING",
                }
            )
        )
        self.assertIsNone(
            receiver._simulated_visual_phase(
                {
                    "stateSource": "SIMULATION",
                    "visualPhase": "UNKNOWN",
                }
            )
        )

    def test_only_simulated_locked_load_uses_bottom_face_hoist_compensation(self) -> None:
        self.assertTrue(
            receiver._hoist_uses_simulated_load_bottom(
                {"stateSource": "SIMULATION"}
            )
        )
        self.assertFalse(
            receiver._hoist_uses_simulated_load_bottom(
                {"stateSource": "PLC"}
            )
        )
        self.assertFalse(receiver._hoist_uses_simulated_load_bottom({}))

    def test_task_truck_box_top_matches_4_32m_plc_contact_height(self) -> None:
        container_height = 0.904353
        tier_gap = 0.035
        trailer_center_z, box_center_z = receiver._task_truck_vertical_layout(
            container_height,
            tier_gap,
        )
        box_bottom_z = box_center_z - container_height * 0.5
        expected_bottom_z = (
            receiver.PLC_TRUCK_CONTACT_HOIST_M
            - receiver.PLC_CONTAINER_TIER_HEIGHT_M
        ) * (container_height + tier_gap) / receiver.PLC_CONTAINER_TIER_HEIGHT_M

        self.assertAlmostEqual(box_bottom_z, expected_bottom_z)
        self.assertAlmostEqual(trailer_center_z + 0.09, box_bottom_z)

    def test_high_cube_changes_box_height_without_raising_trailer(self) -> None:
        standard = receiver._container_visual_height_z(2.591)
        high_cube = receiver._container_visual_height_z(2.896)
        standard_layout = receiver._task_truck_vertical_layout(standard, 0.035)
        high_cube_layout = receiver._task_truck_vertical_layout(high_cube, 0.035)

        self.assertAlmostEqual(standard_layout[0], high_cube_layout[0])
        self.assertGreater(high_cube_layout[1], standard_layout[1])

    def test_spreader_size_prefers_plc_limit_signal(self) -> None:
        self.assertEqual(
            receiver._requested_spreader_size_ft(
                {"tool20f": 1, "tool40f": 0},
                {"containerSize": 40},
            ),
            20,
        )
        self.assertEqual(
            receiver._requested_spreader_size_ft(
                {"tool20f": 0, "tool40f": 1},
                {"containerSize": 20},
            ),
            40,
        )

    def test_spreader_size_uses_task_while_between_limit_switches(self) -> None:
        self.assertEqual(
            receiver._requested_spreader_size_ft(
                {"tool20f": 0, "tool40f": 0},
                {"containerSize": 20},
            ),
            20,
        )
        self.assertEqual(
            receiver._requested_spreader_size_ft(
                {"tool20f": 0, "tool40f": 0},
                {"containerSize": 0},
                40,
            ),
            40,
        )

    def test_conflicting_spreader_limit_signals_hold_current_size(self) -> None:
        self.assertIsNone(
            receiver._requested_spreader_size_ft(
                {"tool20f": 1, "tool40f": 1},
                {"containerSize": 20},
            )
        )

    def test_spreader_endpoint_signal_snaps_to_exact_reported_size(self) -> None:
        self.assertTrue(
            receiver._spreader_size_limit_confirmed(
                {"tool20f": 1, "tool40f": 0},
                20,
            )
        )
        self.assertTrue(
            receiver._spreader_size_limit_confirmed(
                {"tool20f": 0, "tool40f": 1},
                40,
            )
        )
        self.assertFalse(
            receiver._spreader_size_limit_confirmed(
                {"tool20f": 0, "tool40f": 0},
                20,
            )
        )

    def test_dual_lock_signals_latch_through_switching_gap(self) -> None:
        driver = receiver.OmniverseRtgTwinDriver()

        self.assertFalse(
            driver._resolve_spreader_locked(
                {"lockState": 0, "unlockState": 1}
            )
        )
        self.assertTrue(
            driver._resolve_spreader_locked(
                {"lockState": 1, "unlockState": 0}
            )
        )
        self.assertTrue(
            driver._resolve_spreader_locked(
                {"lockState": 0, "unlockState": 0}
            )
        )
        self.assertFalse(
            driver._resolve_spreader_locked(
                {"lockState": 0, "unlockState": 1}
            )
        )

    def test_legacy_lock_only_signal_does_not_latch(self) -> None:
        driver = receiver.OmniverseRtgTwinDriver()

        self.assertTrue(driver._resolve_spreader_locked({"lockState": 1}))
        self.assertFalse(driver._resolve_spreader_locked({"lockState": 0}))

    def test_truck_center_has_no_visual_camera_offset(self) -> None:
        self.assertEqual(receiver.TASK_TRUCK_VISUAL_X_OFFSET_M, 0.0)
        self.assertEqual(
            receiver.TRUCK_OPERATING_LANE_CENTER_X_M
            + receiver.TASK_TRUCK_VISUAL_X_OFFSET_M,
            -3.75,
        )

    def test_task_trailer_deck_is_narrower_but_still_supports_the_box(self) -> None:
        self.assertEqual(receiver.TASK_TRUCK_TRAILER_WIDTH_X_M, 2.0)
        self.assertEqual(receiver.TASK_TRUCK_TRAILER_WHEEL_X_M, 0.88)
        self.assertGreater(receiver.TASK_TRUCK_TRAILER_WIDTH_X_M, 1.306287)

    def test_20ft_load_box_sits_at_the_front_of_a_40ft_chassis(self) -> None:
        container_length_y = 6.1
        trailer_length_y, trailer_center_y, cab_center_y = receiver._task_trailer_layout(
            container_length_y,
            20,
        )

        trailer_front_y = trailer_center_y - trailer_length_y * 0.5
        box_front_y = -container_length_y * 0.5
        self.assertGreater(trailer_length_y, container_length_y * 2.0)
        self.assertAlmostEqual(
            box_front_y - trailer_front_y,
            receiver.TASK_TRUCK_FRONT_CONTAINER_CLEARANCE_Y_M,
        )
        self.assertLess(cab_center_y, box_front_y)

    def test_40ft_load_truck_box_center_matches_the_target_bay_center(self) -> None:
        bay_centre_y = 24.75

        self.assertEqual(
            receiver._task_truck_stop_y_for_box_center(
                bay_centre_y,
                40,
                receiver.WORK_TYPE_LOAD,
            ),
            bay_centre_y,
        )

    def test_40ft_unload_uses_the_same_box_centre_stop_as_load(self) -> None:
        bay_centre_y = 24.75

        self.assertEqual(
            receiver._task_truck_stop_y_for_box_center(
                bay_centre_y,
                40,
                receiver.WORK_TYPE_UNLOAD,
            ),
            bay_centre_y,
        )

    def test_20ft_load_keeps_the_visible_hook_calibration(self) -> None:
        bay_centre_y = 24.75

        self.assertEqual(
            receiver._task_truck_stop_y_for_box_center(
                bay_centre_y,
                20,
                receiver.WORK_TYPE_LOAD,
            ),
            24.9,
        )

    def test_hoisted_box_top_meets_spreader_contact_plane(self) -> None:
        height = 0.904353
        center_z = receiver._hoisted_container_local_center_z(height)

        self.assertAlmostEqual(center_z, 2.0978235)
        self.assertAlmostEqual(
            center_z + height * 0.5,
            receiver.SPREADER_CONTAINER_CONTACT_LOCAL_Z,
        )

    def test_hoisted_box_is_world_scoped_to_avoid_rtg_x_scale(self) -> None:
        self.assertTrue(
            receiver.LIVE_HOISTED_LOAD_PATH.startswith("/World/LiveContainers/")
        )
        self.assertNotIn("RTG_PRIMARY_DYNAMIC", receiver.LIVE_HOISTED_LOAD_PATH)

    def test_carried_box_x_matches_truck_and_all_six_row_centres(self) -> None:
        expected = (
            (0.67, -3.75),
            (3.71, -1.0981016666666639),
            (6.66, 0.4081853333333361),
            (9.50, 1.9144723333333360),
            (12.35, 3.4207593333333360),
            (15.19, 4.9270463333333360),
            (18.06, 6.4333333333333360),
        )

        for trolley_m, center_x in expected:
            with self.subTest(trolley_m=trolley_m):
                self.assertAlmostEqual(
                    receiver._carried_box_world_x(trolley_m),
                    center_x,
                )

    def test_carried_box_moves_toward_truck_with_the_visible_trolley(self) -> None:
        source_x = receiver._carried_box_world_x(15.19)
        halfway_x = receiver._carried_box_world_x(9.50)
        truck_x = receiver._carried_box_world_x(0.67)

        self.assertGreater(source_x, halfway_x)
        self.assertGreater(halfway_x, truck_x)

    def test_only_the_20ft_spreader_receives_the_short_box_center_correction(self) -> None:
        self.assertEqual(receiver._spreader_center_offset_y(20), -0.30)
        self.assertEqual(receiver._spreader_center_offset_y(40), 0.0)
        self.assertEqual(receiver._spreader_center_offset_y(None), 0.0)

    def test_locked_source_uses_spreader_target_without_time_lag(self) -> None:
        class IdentityTransform:
            def Transform(self, value):
                return value

            def GetInverse(self):
                return self

        class SourcePrim:
            def GetParent(self):
                return object()

            def GetPath(self):
                return "/World/LiveContainers/Task/R02_T04_Container"

        fake_pxr = types.ModuleType("pxr")
        fake_pxr.Gf = types.SimpleNamespace(Vec3d=lambda *values: tuple(values))
        fake_pxr.UsdGeom = types.SimpleNamespace(
            XformCache=lambda: types.SimpleNamespace(
                GetLocalToWorldTransform=lambda _prim: IdentityTransform()
            )
        )

        driver = receiver.OmniverseRtgTwinDriver()
        driver._last_task_source_path = str(SourcePrim().GetPath())
        driver._get_xform_scale = lambda _prim: (2.4, 6.1, 2.6)
        driver._get_xform_translate = lambda _prim: (1.0, 5.0, 2.0)
        driver._hoisted_container_world_center = (
            lambda _stage, _height, trolley_m=None: (7.0, 99.0, 8.0)
        )
        targets = []
        driver._move_task_source_to_local_target = (
            lambda _stage, _source, _parent, target: targets.append(target)
        )
        stage = types.SimpleNamespace(
            GetPrimAtPath=lambda _path: SourcePrim()
        )

        with patch.dict(sys.modules, {"pxr": fake_pxr}):
            with patch.object(receiver.time, "time", return_value=0.01):
                driver._move_task_source_with_spreader(stage, trolley_m=15.19)

        self.assertEqual(targets, [(7.0, 5.0, 8.0)])

    def test_load_carried_box_keeps_the_task_truck_longitudinal_center(self) -> None:
        class SourceTransform:
            def Transform(self, _value):
                return (101.0, 6.85, 3.0)

        source = object()
        stage = types.SimpleNamespace(
            GetPrimAtPath=lambda _path: source,
        )
        fake_pxr = types.ModuleType("pxr")
        fake_pxr.Gf = types.SimpleNamespace(Vec3d=lambda *values: tuple(values))
        fake_pxr.UsdGeom = types.SimpleNamespace(
            XformCache=lambda: types.SimpleNamespace(
                GetLocalToWorldTransform=lambda prim: (
                    SourceTransform() if prim is source else None
                )
            )
        )

        with patch.dict(sys.modules, {"pxr": fake_pxr}):
            center = receiver.OmniverseRtgTwinDriver._load_carried_box_world_center(
                stage,
                (4.927, 27.0, 7.5),
            )

        self.assertEqual(center, (4.927, 6.85, 7.5))

    def test_load_target_stack_slot_uses_target_row_and_tier(self) -> None:
        driver = receiver.OmniverseRtgTwinDriver()

        self.assertEqual(
            driver._load_target_stack_slot({"endPos": "1C423"}),
            (2, 3),
        )
        self.assertIsNone(
            driver._load_target_stack_slot({"endPos": "1C4"})
        )
        self.assertIsNone(
            driver._load_target_stack_slot({"endPos": "1C427"})
        )

    def test_load_truck_arrives_loaded_and_leaves_empty(self) -> None:
        self.assertTrue(
            receiver._truck_box_visible(
                receiver.WORK_TYPE_LOAD, receiver.TASK_STATE_TRUCK_APPROACH
            )
        )
        self.assertFalse(
            receiver._truck_box_visible(
                receiver.WORK_TYPE_LOAD, receiver.TASK_STATE_PLACED
            )
        )

    def test_unload_truck_arrives_empty_and_leaves_loaded(self) -> None:
        self.assertFalse(
            receiver._truck_box_visible(
                receiver.WORK_TYPE_UNLOAD, receiver.TASK_STATE_READY_TO_PICK
            )
        )
        self.assertTrue(
            receiver._truck_box_visible(
                receiver.WORK_TYPE_UNLOAD, receiver.TASK_STATE_PLACED
            )
        )

    def test_unload_source_hides_after_lock(self) -> None:
        self.assertTrue(
            receiver._outbound_source_visible(
                receiver.WORK_TYPE_UNLOAD, receiver.TASK_STATE_READY_TO_PICK
            )
        )
        self.assertFalse(
            receiver._outbound_source_visible(
                receiver.WORK_TYPE_UNLOAD, receiver.TASK_STATE_CARRYING
            )
        )

    def test_rehandle_source_hides_while_carried(self) -> None:
        self.assertTrue(
            receiver._outbound_source_visible(
                receiver.WORK_TYPE_SWITCH, receiver.TASK_STATE_READY_TO_PICK
            )
        )
        self.assertFalse(
            receiver._outbound_source_visible(
                receiver.WORK_TYPE_SWITCH, receiver.TASK_STATE_CARRYING
            )
        )

    def test_source_visibility_includes_sibling_container_details(self) -> None:
        class Prim:
            def __init__(self, name: str) -> None:
                self.name = name
                self.parent = None
                self.children = []

            def __bool__(self) -> bool:
                return True

            def GetName(self) -> str:
                return self.name

            def GetParent(self):
                return self.parent

            def GetChildren(self):
                return self.children

        parent = Prim("LiveTaskStack")
        source = Prim("R04_T01_Container")
        rib = Prim("R04_T01_Container_Front_Rib_01")
        top = Prim("R04_T01_Container_WeatheredTop")
        other = Prim("R05_T01_Container")
        parent.children = [source, rib, top, other]
        for child in parent.children:
            child.parent = parent

        class Stage:
            @staticmethod
            def GetPrimAtPath(_path):
                return source

        calls = []
        driver = receiver.OmniverseRtgTwinDriver()
        driver._set_visibility = lambda prim, visible: calls.append(
            (prim.GetName(), visible)
        )
        driver._set_container_visual_visibility(Stage(), "/source", False)

        self.assertEqual(
            calls,
            [
                ("R04_T01_Container", False),
                ("R04_T01_Container_Front_Rib_01", False),
                ("R04_T01_Container_WeatheredTop", False),
            ],
        )


class TaskFlowTests(unittest.TestCase):
    def test_truck_approach_timer_starts_when_gantry_reaches_task_bay(self) -> None:
        class Machine:
            state = receiver.OperationState.GANTRY_TO_SOURCE

            @staticmethod
            def update(_plc, _now):
                return None

            @staticmethod
            def visual_phase():
                return receiver.TASK_STATE_TRUCK_APPROACH

        driver = receiver.OmniverseRtgTwinDriver()
        key = ("UNLOAD", "4C", 7, "123456", 20, "26947440")
        driver._operation_task_key = key
        driver._operation_state_machine = Machine()
        driver._task_flow_state = receiver.TASK_STATE_GANTRY_TO_SOURCE
        driver._task_vehicle_started_at = 10.0
        driver._set_operation_state_attr = lambda *_args: None
        driver._set_task_flow_attr = lambda *_args: None

        with patch.object(receiver.time, "time", return_value=200.0):
            state = driver._advance_operation_flow(
                object(),
                key,
                {"visionConfirmed": True},
                {
                    "currentField": "3C",
                    "currentBay": 7,
                    "trolleyMeter": 3.70,
                    "hoistMeter": 14.70,
                    "lockState": 0,
                    "alarmStatus": 0,
                },
            )

        self.assertEqual(state, receiver.TASK_STATE_TRUCK_APPROACH)
        self.assertEqual(driver._task_vehicle_started_at, 200.0)

    def test_unload_visual_sync_does_not_override_plc_trolley_position(self) -> None:
        task = {
            "taskId": "26945521",
            "workType": "UNLOAD",
            "startPos": "3C7741",
            "containerSize": 20,
            "bayMap": "413122",
        }
        driver = receiver.OmniverseRtgTwinDriver()
        driver._last_task_vehicle_key = driver._task_vehicle_key(task)
        calls = []
        driver._advance_operation_flow = (
            lambda *_args: receiver.TASK_STATE_READY_TO_PICK
        )
        driver._update_task_vehicle_pose = (
            lambda *_args: calls.append("vehicle") or 1.0
        )
        driver._sync_task_source_visual = lambda *_args: calls.append("source")
        driver._sync_task_visuals = lambda *_args: calls.append("visuals")
        driver._align_spreader_to_task_source = (
            lambda *_args: calls.append("trolley_override")
        )

        driver._sync_task_vehicle(object(), task, {"trolleyPosition": 9.47})

        self.assertEqual(calls, ["vehicle", "source", "visuals"])

    def test_lock_and_unlock_advance_outbound_flow(self) -> None:
        driver = receiver.OmniverseRtgTwinDriver()
        driver._task_flow_state = receiver.TASK_STATE_TRUCK_APPROACH
        driver._task_flow_state_started_at = 100.0
        driver._task_vehicle_started_at = 100.0
        driver._set_task_flow_attr = lambda _stage, _state: None
        key = ("UNLOAD", "3C", 77, "413122", 20, "26885357")

        with patch.object(receiver.time, "time", return_value=105.0):
            self.assertEqual(
                driver._advance_task_flow(object(), key, {"lockState": 0}),
                receiver.TASK_STATE_TRUCK_WAITING,
            )
        with patch.object(receiver.time, "time", return_value=106.5):
            self.assertEqual(
                driver._advance_task_flow(object(), key, {"lockState": 0}),
                receiver.TASK_STATE_READY_TO_PICK,
            )
        with patch.object(receiver.time, "time", return_value=106.6):
            self.assertEqual(
                driver._advance_task_flow(object(), key, {"lockState": 1}),
                receiver.TASK_STATE_CARRYING,
            )
        with patch.object(receiver.time, "time", return_value=107.2):
            self.assertEqual(
                driver._advance_task_flow(object(), key, {"lockState": 0}),
                receiver.TASK_STATE_PLACED,
            )
        with patch.object(receiver.time, "time", return_value=108.2):
            self.assertEqual(
                driver._advance_task_flow(object(), key, {"lockState": 0}),
                receiver.TASK_STATE_TRUCK_EXITING,
            )

    def test_lock_and_unlock_advance_rehandle_flow(self) -> None:
        driver = receiver.OmniverseRtgTwinDriver()
        driver._task_flow_state = receiver.TASK_STATE_READY_TO_PICK
        driver._task_flow_state_started_at = 100.0
        driver._set_task_flow_attr = lambda _stage, _state: None
        key = ("SWITCH", "3C", 63, "133456", 20, "26945501")

        with patch.object(receiver.time, "time", return_value=100.1):
            self.assertEqual(
                driver._advance_switch_flow(object(), key, {"lockState": 1}),
                receiver.TASK_STATE_CARRYING,
            )
        with patch.object(receiver.time, "time", return_value=100.7):
            self.assertEqual(
                driver._advance_switch_flow(object(), key, {"lockState": 0}),
                receiver.TASK_STATE_PLACED,
            )


if __name__ == "__main__":
    unittest.main()
