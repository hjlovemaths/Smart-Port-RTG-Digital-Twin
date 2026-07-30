from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "omniverse" / "scripts" / "rtg_gamepad_manual_control.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("rtg_gamepad_manual_control_test", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class GamepadInputMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manual = _load_module()

    def test_axis_deadzone_removes_small_stick_drift(self) -> None:
        self.assertEqual(self.manual.normalized_axis(0), 0.0)
        self.assertEqual(self.manual.normalized_axis(5000), 0.0)

    def test_axis_rescales_range_after_deadzone(self) -> None:
        self.assertGreater(self.manual.normalized_axis(32767), 0.99)
        self.assertLess(self.manual.normalized_axis(-32767), -0.99)
        self.assertGreater(self.manual.normalized_axis(20000), 0.0)

    def test_kit_directional_axis_removes_small_stick_drift(self) -> None:
        self.assertEqual(self.manual.normalized_directional_axis(0.08, 0.0), 0.0)
        self.assertEqual(self.manual.normalized_directional_axis(0.0, 0.08), 0.0)
        self.assertGreater(self.manual.normalized_directional_axis(1.0, 0.0), 0.99)
        self.assertLess(self.manual.normalized_directional_axis(0.0, 1.0), -0.99)

    def test_keyboard_state_exposes_safe_motion_and_actions(self) -> None:
        state = self.manual.KeyboardState(
            gantry=1.0,
            trolley=-1.0,
            hoist=0.0,
            deadman=True,
            just_pressed=frozenset({"G", "ONE"}),
        )
        self.assertTrue(state.deadman)
        self.assertEqual((state.gantry, state.trolley, state.hoist), (1.0, -1.0, 0.0))
        self.assertIn("G", state.just_pressed)

    def test_button_state_uses_bit_mask(self) -> None:
        state = self.manual.GamepadState(
            buttons=self.manual.XINPUT_GAMEPAD_A | self.manual.XINPUT_GAMEPAD_RIGHT_SHOULDER,
            left_x=0.0,
            left_y=0.0,
            right_y=0.0,
        )
        self.assertTrue(state.pressed(self.manual.XINPUT_GAMEPAD_A))
        self.assertFalse(state.pressed(self.manual.XINPUT_GAMEPAD_B))

    def test_nearest_bay_uses_current_gantry_engineering_position(self) -> None:
        from yard_coordinate_mapping import bay_center_m

        controller = object.__new__(self.manual.RtgGamepadManualController)
        controller._gantry_m = bay_center_m("3C", 63)
        controller._gantry_bay_reference = None
        controller._rtg_controller = None
        self.assertEqual(controller._nearest_bay(), ("3C", 63))

    def test_nearest_bay_preserves_business_bay_at_manual_takeover(self) -> None:
        from yard_coordinate_mapping import bay_center_m

        controller = object.__new__(self.manual.RtgGamepadManualController)
        controller._gantry_m = 12.34
        controller._gantry_bay_reference = ("1C", 19, controller._gantry_m)
        controller._rtg_controller = None
        self.assertEqual(controller._nearest_bay(), ("1C", 19))

        controller._gantry_m += bay_center_m("1C", 21) - bay_center_m("1C", 19)
        self.assertEqual(controller._nearest_bay(), ("1C", 21))

    def test_nearest_bay_respects_twenty_foot_odd_bay_numbering(self) -> None:
        from yard_coordinate_mapping import bay_center_m

        class TwentyFootSpreader:
            @staticmethod
            def current_spreader_size_ft() -> float:
                return 20.0

        controller = object.__new__(self.manual.RtgGamepadManualController)
        controller._gantry_m = bay_center_m("1C", 18) + 0.20
        controller._gantry_bay_reference = None
        controller._rtg_controller = TwentyFootSpreader()
        self.assertEqual(controller._nearest_bay(), ("1C", 19))

    def test_nearest_bay_respects_forty_foot_even_bay_numbering(self) -> None:
        from yard_coordinate_mapping import bay_center_m

        class FortyFootSpreader:
            @staticmethod
            def current_spreader_size_ft() -> float:
                return 40.0

            @staticmethod
            def get_gantry_bay_center_m() -> float:
                return bay_center_m("1C", 18)

        controller = object.__new__(self.manual.RtgGamepadManualController)
        controller._gantry_m = bay_center_m("1C", 18)
        controller._gantry_bay_reference = None
        controller._rtg_controller = FortyFootSpreader()
        self.assertEqual(controller._nearest_bay(), ("1C", 18))

    def test_takeover_bay_uses_calibrated_gantry_center_not_stale_status(self) -> None:
        class TwentyFootGantry:
            @staticmethod
            def current_spreader_size_ft() -> float:
                return 20.0

            @staticmethod
            def get_gantry_bay_center_m() -> float:
                return 25.1875

        controller = object.__new__(self.manual.RtgGamepadManualController)
        controller._gantry_m = 23.55
        controller._gantry_bay_reference = None
        controller._rtg_controller = TwentyFootGantry()

        self.assertEqual(controller._nearest_bay(), ("1C", 19))

    def test_takeover_bay_calibrates_when_live_control_module_was_cached(self) -> None:
        class CachedTwentyFootGantry:
            @staticmethod
            def current_spreader_size_ft() -> float:
                return 20.0

        controller = object.__new__(self.manual.RtgGamepadManualController)
        controller._gantry_m = 23.55
        controller._gantry_bay_reference = None
        controller._rtg_controller = CachedTwentyFootGantry()

        self.assertEqual(controller._nearest_bay(), ("1C", 19))

    def test_manual_status_reports_only_the_resolved_payload(self) -> None:
        original = self.manual._manual_controller
        try:
            controller = object.__new__(self.manual.RtgGamepadManualController)
            controller._gamepad = None
            controller._emergency_stopped = False
            controller._carried_source_path = "/World/LiveContainers/YardInventory/Bay_3C_055/R06_T02_Container"
            self.manual._manual_controller = controller

            status = self.manual.get_gamepad_manual_status()

            self.assertEqual(status["carriedContainer"], controller._carried_source_path)
            self.assertNotIn("pickTarget", status)
        finally:
            self.manual._manual_controller = original

    def test_lock_foot_contact_uses_rendered_world_bounds(self) -> None:
        # The test deliberately uses plain tuples: contact must not depend on
        # a prim's local scale or its inventory/bay metadata.
        feet = ((-1.05, -2.05, 3.00), (1.05, 2.05, 3.02))
        count, _error, gap = self.manual.RtgGamepadManualController._container_lock_foot_contact(
            feet,
            (-1.0, -2.0, 0.0),
            (1.0, 2.0, 3.0),
        )
        self.assertEqual(count, 2)
        self.assertLess(gap, 0.05)

    def test_lock_foot_contact_reports_gap_for_other_bays(self) -> None:
        count, _error, gap = self.manual.RtgGamepadManualController._container_lock_foot_contact(
            ((0.0, 0.0, 3.0),),
            (100.0, 100.0, 0.0),
            (102.0, 102.0, 3.0),
        )
        self.assertEqual(count, 0)
        self.assertGreater(gap, 100.0)

    def test_lock_foot_contact_can_ignore_y_after_bay_is_constrained(self) -> None:
        count, _error, gap = self.manual.RtgGamepadManualController._container_lock_foot_contact(
            ((0.0, 100.0, 3.0),),
            (-1.0, -2.0, 0.0),
            (1.0, 2.0, 3.0),
            ignore_y=True,
        )
        self.assertEqual(count, 1)
        self.assertEqual(gap, 0.0)

    def test_lock_foot_contact_can_ignore_lateral_mesh_offset_after_row_is_constrained(self) -> None:
        count, _error, gap = self.manual.RtgGamepadManualController._container_lock_foot_contact(
            ((3.10, 100.0, 3.02), (3.20, 101.0, 3.01)),
            (-1.0, -2.0, 0.0),
            (1.0, 2.0, 3.0),
            ignore_x=True,
            ignore_y=True,
        )
        self.assertEqual(count, 2)
        self.assertLess(gap, 0.05)

    def test_lock_row_resolution_uses_trolley_centre(self) -> None:
        row_trolley = self.manual.TROLLEY_ROW_CENTERS_M
        near_row_six = row_trolley[6] + 0.19
        near_row_five = row_trolley[5] - 0.25

        self.assertEqual(
            self.manual.RtgGamepadManualController._nearest_row_for_trolley_m(
                near_row_six
            ),
            6,
        )
        self.assertEqual(
            self.manual.RtgGamepadManualController._nearest_row_for_trolley_m(
                near_row_five
            ),
            5,
        )

    def test_lock_row_resolution_rejects_truck_lane(self) -> None:
        self.assertIsNone(
            self.manual.RtgGamepadManualController._nearest_row_for_trolley_m(0.67)
        )

    def test_trolley_row_delta_uses_row_centre(self) -> None:
        controller = object.__new__(self.manual.RtgGamepadManualController)
        controller._trolley_m = 3.90

        self.assertAlmostEqual(controller._trolley_row_delta_m(6), -0.19)

    def test_carried_box_x_moves_with_visible_trolley_direction(self) -> None:
        row_two_x = self.manual.RtgGamepadManualController._carried_box_world_x(15.19)
        row_four_x = self.manual.RtgGamepadManualController._carried_box_world_x(9.50)
        row_six_x = self.manual.RtgGamepadManualController._carried_box_world_x(3.71)

        self.assertGreater(row_two_x, row_four_x)
        self.assertGreater(row_four_x, row_six_x)

    def test_carried_box_x_interpolates_between_row_centres(self) -> None:
        self.assertAlmostEqual(
            self.manual.RtgGamepadManualController._carried_box_world_x(5.185),
            (-1.0981016666666639 + 0.4081853333333361) * 0.5,
        )

    def test_release_support_defaults_to_ground(self) -> None:
        support_z, support_path = (
            self.manual.RtgGamepadManualController._release_support_from_bounds(
                (-1.0, -1.0, 4.0),
                (1.0, 1.0, 5.0),
                [],
            )
        )

        self.assertAlmostEqual(support_z, self.manual.RELEASE_GROUND_SUPPORT_Z_M)
        self.assertEqual(support_path, "ground")

    def test_release_support_uses_highest_overlapping_container_top(self) -> None:
        supports = [
            ("/World/Lower", (-1.0, -1.0, 0.0), (1.0, 1.0, 2.0)),
            ("/World/Upper", (-1.0, -1.0, 2.0), (1.0, 1.0, 3.0)),
            ("/World/Side", (5.0, 5.0, 0.0), (7.0, 7.0, 4.0)),
        ]

        support_z, support_path = (
            self.manual.RtgGamepadManualController._release_support_from_bounds(
                (-1.0, -1.0, 4.0),
                (1.0, 1.0, 5.0),
                supports,
            )
        )

        self.assertEqual(support_z, 3.0)
        self.assertEqual(support_path, "/World/Upper")

    def test_release_support_ignores_containers_above_payload(self) -> None:
        support_z, support_path = (
            self.manual.RtgGamepadManualController._release_support_from_bounds(
                (-1.0, -1.0, 1.0),
                (1.0, 1.0, 2.0),
                [("/World/Above", (-1.0, -1.0, 2.2), (1.0, 1.0, 3.0))],
            )
        )

        self.assertAlmostEqual(support_z, self.manual.RELEASE_GROUND_SUPPORT_Z_M)
        self.assertEqual(support_path, "ground")

    def test_release_topple_plan_is_none_when_center_is_supported(self) -> None:
        plan = self.manual.RtgGamepadManualController._release_topple_plan(
            (-1.0, -1.0, 3.0),
            (1.0, 1.0, 4.0),
            (-1.2, -1.2, 0.0),
            (1.2, 1.2, 3.0),
        )

        self.assertIsNone(plan)

    def test_release_topple_plan_uses_support_edge_when_center_overhangs(self) -> None:
        plan = self.manual.RtgGamepadManualController._release_topple_plan(
            (1.2, -1.0, 3.0),
            (3.2, 1.0, 4.0),
            (-1.0, -1.0, 0.0),
            (1.0, 1.0, 3.0),
        )

        self.assertEqual(plan, ("Y", 1, 1.0))
        self.assertEqual(
            self.manual.RtgGamepadManualController._topple_angle_degrees("Y", 1),
            90.0,
        )

    def test_rotate_point_around_y_axis_can_topple_center_downward(self) -> None:
        rotated = self.manual.RtgGamepadManualController._rotate_point_around_axis(
            (2.0, 0.0, 1.0),
            (1.0, 0.0, 0.0),
            "Y",
            3.141592653589793 / 2.0,
        )

        self.assertAlmostEqual(rotated[0], 2.0)
        self.assertAlmostEqual(rotated[2], -1.0)

    def test_collision_nudge_is_zero_without_overlap(self) -> None:
        delta = self.manual.RtgGamepadManualController._collision_nudge_delta(
            (-1.0, -1.0, 0.0),
            (1.0, 1.0, 1.0),
            (5.0, 5.0, 0.0),
            (6.0, 6.0, 1.0),
        )

        self.assertEqual(delta, (0.0, 0.0))

    def test_collision_nudge_is_zero_without_horizontal_motion(self) -> None:
        delta = self.manual.RtgGamepadManualController._collision_nudge_delta(
            (-1.0, -1.0, 0.0),
            (1.0, 1.0, 1.0),
            (0.8, -0.5, 0.0),
            (2.0, 0.5, 1.0),
            motion_xy=(0.0, 0.0),
        )

        self.assertEqual(delta, (0.0, 0.0))

    def test_collision_nudge_follows_manual_motion_direction(self) -> None:
        delta = self.manual.RtgGamepadManualController._collision_nudge_delta(
            (-1.0, -1.0, 0.0),
            (1.0, 1.0, 1.0),
            (0.8, -0.5, 0.0),
            (2.0, 0.5, 1.0),
            motion_xy=(0.2, 0.0),
        )

        self.assertGreater(delta[0], 0.0)
        self.assertEqual(delta[1], 0.0)
        self.assertLessEqual(
            delta[0], self.manual.SPREADER_COLLISION_MAX_NUDGE_M
        )

    def test_released_location_stacks_on_support_container_metadata(self) -> None:
        released = (
            self.manual.RtgGamepadManualController._released_location_from_support(
                current_bay_id="1C/019",
                current_row=5,
                support_location=("1C/019", 6, 2),
            )
        )

        self.assertEqual(released, ("1C/019", 6, 3))

    def test_released_location_uses_current_row_on_ground(self) -> None:
        released = (
            self.manual.RtgGamepadManualController._released_location_from_support(
                current_bay_id="1C/019",
                current_row=6,
                support_location=None,
            )
        )

        self.assertEqual(released, ("1C/019", 6, 1))

    def test_live_container_location_normalizes_detail_prim(self) -> None:
        class Attr:
            def __init__(self, value: object) -> None:
                self._value = value

            def Get(self) -> object:
                return self._value

        class Prim:
            def __init__(
                self,
                name: str,
                attrs: dict[str, object] | None = None,
                parent: object = None,
            ) -> None:
                self._name = name
                self._attrs = attrs or {}
                self._parent = parent

            def GetName(self) -> str:
                return self._name

            def GetParent(self) -> object:
                return self._parent

            def GetAttribute(self, name: str) -> object:
                return Attr(self._attrs[name]) if name in self._attrs else None

        class Parent:
            def __init__(self) -> None:
                self.root = Prim(
                    "R06_T02_Container",
                    {"live:bayId": "1C/019", "live:row": 6, "live:tier": 2},
                    self,
                )

            def GetChild(self, name: str) -> object:
                return self.root if name == "R06_T02_Container" else None

        parent = Parent()
        detail = Prim("R06_T02_Container_WeatheredTop", parent=parent)

        self.assertEqual(
            self.manual.RtgGamepadManualController._live_container_location(detail),
            ("1C/019", 6, 2),
        )

    def test_live_container_row_reads_inventory_row_attribute(self) -> None:
        class Attr:
            def __init__(self, value: object) -> None:
                self._value = value

            def Get(self) -> object:
                return self._value

        class Prim:
            def __init__(self, value: object) -> None:
                self._value = value

            def GetAttribute(self, name: str) -> object:
                return Attr(self._value) if name == "live:row" else None

        self.assertEqual(
            self.manual.RtgGamepadManualController._live_container_row(Prim(6)),
            6,
        )
        self.assertIsNone(
            self.manual.RtgGamepadManualController._live_container_row(Prim(7))
        )
