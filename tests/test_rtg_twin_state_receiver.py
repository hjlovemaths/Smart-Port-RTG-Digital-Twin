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

    def test_task_route_endpoints_use_truck_for_load_and_unload(self) -> None:
        self.assertEqual(
            receiver._task_route_endpoints(
                {
                    "workType": "LOAD",
                    "endPos": "4C3012",
                    "bayMap": "144332",
                }
            ),
            (("truck", 0, 0), ("slot", 1, 2)),
        )
        self.assertEqual(
            receiver._task_route_endpoints(
                {
                    "workType": "UNLOAD",
                    "startPos": "4C3052",
                    "bayMap": "144332",
                }
            ),
            (("slot", 5, 2), ("truck", 0, 0)),
        )
        self.assertEqual(
            receiver._task_route_endpoints(
                {
                    "workType": "SWITCH",
                    "startPos": "4C3052",
                    "endPos": "4C3012",
                    "bayMap": "144332",
                }
            ),
            (("slot", 5, 2), ("slot", 1, 2)),
        )

    def test_container_selection_normalizes_detail_prim_to_box_root(self) -> None:
        self.assertEqual(
            receiver._container_root_path_from_selection(
                "/World/LiveContainers/YardInventory/Bay_4C_018/"
                "R02_T03_Container/WeatheredTop"
            ),
            "/World/LiveContainers/YardInventory/Bay_4C_018/R02_T03_Container",
        )

    def test_container_selection_normalizes_imported_model_to_box_root(self) -> None:
        self.assertEqual(
            receiver._container_root_path_from_selection(
                "/World/LiveContainers/YardInventory/Bay_4C_018/"
                "R02_T03_Container_ImportedModel/scene/Mesh"
            ),
            "/World/LiveContainers/YardInventory/Bay_4C_018/R02_T03_Container",
        )
        self.assertEqual(
            receiver._container_root_path_from_selection(
                "/World/LiveContainers/LiveTwinHoistedContainer_ImportedModel/"
                "scene/Mesh"
            ),
            "/World/LiveContainers/LiveTwinHoistedContainer",
        )

    def test_rtg_selection_is_not_treated_as_container_selection(self) -> None:
        self.assertIsNone(
            receiver._container_root_path_from_selection(
                "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/Body"
            )
        )

    def test_truck_selection_normalizes_visual_child_to_live_truck_root(self) -> None:
        self.assertEqual(
            receiver._truck_root_path_from_selection(
                f"{receiver.LIVE_TASK_TRUCK_VISUAL_PATH}/scene/Meshes/Cab"
            ),
            receiver.LIVE_TASK_TRUCK_PATH,
        )
        self.assertEqual(
            receiver._truck_root_path_from_selection(
                f"{receiver.LIVE_TASK_TRUCK_PATH}/TaskTruckCabWhite"
            ),
            receiver.LIVE_TASK_TRUCK_PATH,
        )
        self.assertEqual(
            receiver._truck_root_path_from_selection(
                "/World/PortAndRTG/LIVE_internal_yard_truck_unified_40ft/"
                "LIVE_internal_lane_truck_white_cab_body"
            ),
            "/World/PortAndRTG/LIVE_internal_yard_truck_unified_40ft",
        )
        self.assertEqual(
            receiver._truck_root_path_from_selection(receiver.LIVE_TASK_TRUCK_PATH),
            receiver.LIVE_TASK_TRUCK_PATH,
        )
        self.assertIsNone(
            receiver._truck_root_path_from_selection(
                f"{receiver.LIVE_TASK_TRUCK_PATH}/LoadedContainer"
            )
        )

    def test_truck_runinfo_requires_successful_validation(self) -> None:
        self.assertIsNone(
            receiver._truck_runinfo_from_payload(
                "引导识别到ITK/AIV车号:H822,侧面车号: 时间:2026-08-14 07:58:46.999"
            )
        )

    def test_truck_runinfo_prefers_complete_candidate_over_truncated_side_number(self) -> None:
        info = receiver._truck_runinfo_from_payload(
            "校验内集卡车号通过，算法检测车号:H327侧面车号H"
        )
        self.assertEqual(info, receiver.TruckRunInfo("内集卡", "H327"))

    def test_truck_runinfo_parses_external_truck_plate_from_task_number(self) -> None:
        info = receiver._truck_runinfo_from_payload(
            "校验外集卡车号通过，算法检测车号:,侧面车号:沪EG8905"
            "任务车号:沪EG8905,排队车号: 时间:2026-08-14 10:14:03.565"
        )

        self.assertEqual(info, receiver.TruckRunInfo("外集卡", "沪EG8905"))

    def test_truck_runinfo_parses_internal_truck_side_number_without_colon(self) -> None:
        info = receiver._truck_runinfo_from_payload(
            "校验内集卡车号通过，算法检测车号:H360侧面车号H360 "
            "时间:2026-08-14 09:19:36.862"
        )

        self.assertEqual(info, receiver.TruckRunInfo("内集卡", "H360"))

    def test_truck_runinfo_parses_aiv_after_guidance_and_validation(self) -> None:
        info = receiver._truck_runinfo_from_payload(
            "引导识别到ITK/AIV车号:H822,侧面车号: 时间:2026-08-14 07:58:46.999，"
            "校验AIV车号通过，且有粗停，开始进入精停引导流程 "
            "时间:2026-08-14 07:58:47.000"
        )

        self.assertEqual(info, receiver.TruckRunInfo("AIV", "H822"))

    def test_truck_runinfo_parses_inbound_container_truck_validation(self) -> None:
        info = receiver._truck_runinfo_from_payload(
            "进箱集卡检测箱号和任务箱号一致,认为集卡校验通过车号:H370,"
            "箱号:SZLU2048135 时间:2026-08-14 16:41:04.741"
        )

        self.assertEqual(info, receiver.TruckRunInfo("进箱集卡", "H370"))

    def test_current_truck_runinfo_falls_back_to_status_prim(self) -> None:
        class Attr:
            def __init__(self, value):
                self.value = value

            def Get(self):
                return self.value

        class Prim:
            def GetAttribute(self, name):
                if name == "rtg:runinfo":
                    return Attr(
                        "进箱集卡检测箱号和任务箱号一致,"
                        "认为集卡校验通过车号:H370,箱号:SZLU2048135 "
                        "时间:2026-08-14 16:41:04.741"
                    )
                return None

        stage = types.SimpleNamespace(
            GetPrimAtPath=lambda path: Prim()
            if path == receiver.STATUS_PRIM_PATH
            else None
        )
        self.driver._last_runinfo_payload = {}
        self.driver._latest_state = None

        self.assertEqual(
            self.driver._current_truck_runinfo(stage),
            receiver.TruckRunInfo("进箱集卡", "H370"),
        )

    def test_validated_truck_runinfo_survives_later_guidance_message(self) -> None:
        task = {
            "taskId": "task-load-001",
            "workType": "LOAD",
        }
        self.driver._update_validated_truck_runinfo_cache(
            "校验内集卡车号通过，算法检测车号:H421侧面车号H421 "
            "时间:2026-08-17 15:18:37.494",
            task,
        )
        self.driver._update_validated_truck_runinfo_cache(
            "内集卡到位，开始精停引导",
            task,
        )

        stage = types.SimpleNamespace(GetPrimAtPath=lambda _path: None)
        self.assertEqual(
            self.driver._current_truck_runinfo(stage),
            receiver.TruckRunInfo("内集卡", "H421"),
        )

    def test_validated_truck_runinfo_is_cleared_when_task_ends(self) -> None:
        task = {"taskId": "task-unload-001", "workType": "UNLOAD"}
        self.driver._update_validated_truck_runinfo_cache(
            "校验外集卡车号通过，算法检测车号:沪A12345",
            task,
        )
        self.driver._update_validated_truck_runinfo_cache(
            None,
            {"taskId": "task-unload-001", "workType": "UNLOAD", "operationState": "COMPLETED"},
        )

        self.assertIsNone(self.driver._validated_truck_runinfo)
        self.assertIsNone(self.driver._validated_truck_task_id)

    def test_container_selection_preserves_visible_device_panel(self) -> None:
        self.driver._device_panel_selected = True
        with patch.object(
            receiver.OmniverseRtgTwinDriver,
            "_viewport_selection_paths",
            return_value=[
                "/World/LiveContainers/YardInventory/Bay_4C_018/"
                "R02_T03_Container/WeatheredTop"
            ],
        ):
            self.assertTrue(self.driver._refresh_device_panel_selection("test"))
        self.assertTrue(self.driver._device_panel_selected)
        self.assertEqual(
            self.driver._container_info_selected_root_path,
            "/World/LiveContainers/YardInventory/Bay_4C_018/R02_T03_Container",
        )

    def test_truck_selection_preserves_visible_device_panel(self) -> None:
        self.driver._device_panel_selected = True
        with patch.object(
            receiver.OmniverseRtgTwinDriver,
            "_viewport_selection_paths",
            return_value=[
                f"{receiver.LIVE_TASK_TRUCK_VISUAL_PATH}/scene/Meshes/Cab"
            ],
        ):
            self.assertTrue(self.driver._refresh_device_panel_selection("test"))
        self.assertTrue(self.driver._device_panel_selected)
        self.assertEqual(
            self.driver._truck_info_selected_root_path,
            receiver.LIVE_TASK_TRUCK_PATH,
        )
        self.assertIsNone(self.driver._container_info_selected_root_path)

    def test_camera_marker_selection_does_not_open_device_panel(self) -> None:
        self.driver._device_panel_selected = True
        with patch.object(
            receiver.OmniverseRtgTwinDriver,
            "_viewport_selection_paths",
            return_value=[
                f"{receiver.PRIMARY_RTG_SELECTION_ROOT_PATH}/"
                "RTG_CAMERA_MARKERS_ROOT/C10/C10_green_sphere_mesh"
            ],
        ):
            self.assertTrue(self.driver._refresh_device_panel_selection("test"))
        self.assertFalse(self.driver._device_panel_selected)
        self.assertIsNone(self.driver._container_info_selected_root_path)
        self.assertIsNone(self.driver._truck_info_selected_root_path)

    def test_task_panel_bay_map_draws_r6_to_r1_left_to_right(self) -> None:
        self.assertEqual(
            receiver.TASK_PANEL_BAY_MAP_ROWS_LEFT_TO_RIGHT,
            (6, 5, 4, 3, 2, 1),
        )

    def test_task_panel_content_key_ignores_live_operation_phase(self) -> None:
        task = {
            "taskId": "27255556",
            "taskState": "WORKING",
            "workType": "UNLOAD",
            "startPos": "4C2752",
            "endPos": "",
            "containerNo": "TGBU3831240",
            "containerType": "GP",
            "containerHeight": "2.6",
            "spreaderPos": "H301",
            "carType": "ITK",
            "carNo": "H301",
            "bayMap": "033122",
            "operationState": "TROLLEY_TO_SOURCE",
            "visualPhase": "APPROACH",
        }
        changed_phase = dict(
            task,
            operationState="HOIST_TO_PICK",
            visualPhase="PICKING",
        )

        self.assertEqual(
            receiver._task_panel_content_key(task),
            receiver._task_panel_content_key(changed_phase),
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

    def test_runinfo_bitmap_renderer_draws_chinese_text(self) -> None:
        path = Path(tempfile.gettempdir()) / "smart_port_rtg_runinfo_test.png"

        rendered = receiver._render_runinfo_bitmap("作业完成ED6632", 220, 32, path)

        self.assertTrue(rendered)
        self.assertTrue(path.is_file())
        from PIL import Image  # noqa: PLC0415

        with Image.open(path) as image:
            alpha = image.getchannel("A")
            self.assertGreater(alpha.getbbox()[2], 0)

    def test_floating_runinfo_bitmap_renderer_draws_panel(self) -> None:
        path = Path(tempfile.gettempdir()) / "smart_port_rtg_runinfo_float_test.png"

        rendered = receiver._render_floating_runinfo_bitmap(
            "作业完成ED6632",
            path,
            "2026-08-04 15:30:45",
        )

        self.assertTrue(rendered)
        self.assertTrue(path.is_file())
        from PIL import Image  # noqa: PLC0415

        with Image.open(path) as image:
            self.assertEqual(
                image.size,
                (
                    receiver.RUNINFO_FLOATING_IMAGE_WIDTH,
                    receiver.RUNINFO_FLOATING_IMAGE_HEIGHT,
                ),
            )
            alpha = image.getchannel("A")
            self.assertIsNotNone(alpha.getbbox())
            self.assertGreaterEqual(
                image.getpixel(
                    (
                        receiver.RUNINFO_FLOATING_IMAGE_WIDTH // 2,
                        receiver.RUNINFO_FLOATING_IMAGE_HEIGHT - 52,
                    )
                )[3],
                240,
            )

    def test_floating_runinfo_bitmap_renderer_draws_default_text(self) -> None:
        path = Path(tempfile.gettempdir()) / "smart_port_rtg_runinfo_float_default.png"

        rendered = receiver._render_floating_runinfo_bitmap(
            receiver.RUNINFO_FLOATING_DEFAULT_TEXT,
            path,
        )

        self.assertTrue(rendered)
        self.assertTrue(path.is_file())

    def test_runinfo_run_state_selects_three_panel_variants(self) -> None:
        self.assertEqual(
            receiver._runinfo_level_key({"runState": 1}),
            receiver.RUNINFO_LEVEL_PROMPT,
        )
        self.assertEqual(
            receiver._runinfo_level_key({"run_state": "2"}),
            receiver.RUNINFO_LEVEL_ALERT,
        )
        self.assertEqual(
            receiver._runinfo_level_key({"runState": 3}),
            receiver.RUNINFO_LEVEL_EXCEPTION,
        )
        self.assertEqual(
            receiver._runinfo_level_key({"run_state": "3"}),
            receiver.RUNINFO_LEVEL_EXCEPTION,
        )

    def test_floating_runinfo_renderer_supports_three_visual_variants(self) -> None:
        for level_key in (
            receiver.RUNINFO_LEVEL_PROMPT,
            receiver.RUNINFO_LEVEL_ALERT,
            receiver.RUNINFO_LEVEL_EXCEPTION,
        ):
            path = (
                Path(tempfile.gettempdir())
                / f"smart_port_rtg_runinfo_{level_key.lower()}_test.png"
            )
            self.assertTrue(
                receiver._render_floating_runinfo_bitmap(
                    "设备状态需要关注",
                    path,
                    "2026-08-06 12:00:00",
                    level_key,
                )
            )
            self.assertTrue(path.is_file())

    def test_task_info_panel_bitmap_renderer_draws_chinese_panel(self) -> None:
        font_path = receiver._find_cjk_font_path()
        if font_path is None:
            self.skipTest("CJK font unavailable")
        path = Path(tempfile.gettempdir()) / "smart_port_rtg_task_panel_test.png"

        rendered = receiver._render_task_info_panel_bitmap(
            {
                "taskId": "27130485",
                "workType": "SWITCH",
                "startPos": "4C3053",
                "endPos": "4C3012",
                "containerNo": "FFAU8892415",
                "containerType": "GP",
                "containerHeight": "2.9",
                "spreaderPos": "5",
                "carType": "内集卡",
                "bayMap": "144332",
            },
            path,
        )

        self.assertTrue(rendered)
        self.assertTrue(path.is_file())
        from PIL import Image  # noqa: PLC0415

        with Image.open(path) as image:
            self.assertEqual(
                image.size,
                (
                    receiver.TASK_PANEL_IMAGE_WIDTH,
                    receiver.TASK_PANEL_IMAGE_HEIGHT,
                ),
            )
            self.assertIsNotNone(image.getchannel("A").getbbox())
            self.assertGreaterEqual(image.getpixel((80, 150))[3], 240)
            self.assertGreaterEqual(image.getpixel((90, 50))[3], 240)

    def test_task_panel_route_indicator_supports_three_work_types(self) -> None:
        common = {
            "taskId": "27130485",
            "bayMap": "144332",
            "containerNo": "FFAU8892415",
        }
        cases = (
            (receiver.WORK_TYPE_LOAD, "", "4C3051"),
            (receiver.WORK_TYPE_UNLOAD, "4C3051", ""),
            (receiver.WORK_TYPE_SWITCH, "4C3051", "4C3011"),
        )
        for work_type, start_pos, end_pos in cases:
            task = {
                **common,
                "workType": work_type,
                "startPos": start_pos,
                "endPos": end_pos,
            }
            route = receiver._task_panel_route_map_points(
                task,
                map_left=568,
                bottom=692,
                cell_w=54,
                cell_h=24,
                gap=16,
                lane_left=574,
                lane_w=42,
            )
            self.assertIsNotNone(route)
            self.assertEqual(route[2], work_type)

    def test_task_panel_bay_map_keeps_right_margin_after_left_shift(self) -> None:
        cell_w = 54
        gap = 16
        lane_left = receiver.TASK_PANEL_BAY_MAP_LEFT_PX + 6
        lane_w = 42
        stack_left = lane_left + lane_w + gap
        right_edge = stack_left + 5 * (cell_w + gap) + cell_w

        self.assertEqual(receiver.TASK_PANEL_BAY_MAP_LEFT_PX, 500)
        self.assertLessEqual(right_edge, receiver.TASK_PANEL_IMAGE_WIDTH - 24)

    def test_task_panel_load_route_can_target_empty_bay_slot(self) -> None:
        route = receiver._task_panel_route_map_points(
            {
                "workType": receiver.WORK_TYPE_LOAD,
                "startPos": "4C0421",
                "endPos": "4C0421",
                "bayMap": "401332",
            },
            map_left=568,
            bottom=692,
            cell_w=54,
            cell_h=24,
            gap=16,
            lane_left=574,
            lane_w=42,
        )
        self.assertIsNotNone(route)
        self.assertEqual(route[2], receiver.WORK_TYPE_LOAD)

    def test_task_panel_uses_dashed_load_target_and_solid_unload_source(self) -> None:
        load_source, load_target = receiver._task_panel_slot_markers(
            {
                "workType": receiver.WORK_TYPE_LOAD,
                "endPos": "4C3054",
                "bayMap": "144332",
            }
        )
        unload_source, unload_target = receiver._task_panel_slot_markers(
            {
                "workType": receiver.WORK_TYPE_UNLOAD,
                "startPos": "4C3052",
                "bayMap": "144332",
            }
        )

        self.assertIsNone(load_source)
        self.assertEqual(load_target, ("slot", 5, 4))
        self.assertEqual(unload_source, ("slot", 5, 3))
        self.assertIsNone(unload_target)

    def test_task_panel_truck_route_anchor_is_above_cab_icon_for_both_directions(self) -> None:
        bottom = 692
        expected_y = bottom - receiver.TASK_PANEL_TRUCK_ROUTE_ANCHOR_FROM_BOTTOM_PX
        common = {
            "bayMap": "144332",
            "map_left": 568,
            "bottom": bottom,
            "cell_w": 54,
            "cell_h": 24,
            "gap": 16,
            "lane_left": 574,
            "lane_w": 42,
        }
        load_route = receiver._task_panel_route_map_points(
            {
                "workType": receiver.WORK_TYPE_LOAD,
                "endPos": "4C3051",
                "bayMap": common["bayMap"],
            },
            **{key: value for key, value in common.items() if key != "bayMap"},
        )
        unload_route = receiver._task_panel_route_map_points(
            {
                "workType": receiver.WORK_TYPE_UNLOAD,
                "startPos": "4C3051",
                "bayMap": common["bayMap"],
            },
            **{key: value for key, value in common.items() if key != "bayMap"},
        )

        self.assertIsNotNone(load_route)
        self.assertIsNotNone(unload_route)
        self.assertEqual(load_route[0][1], expected_y)
        self.assertEqual(unload_route[1][1], expected_y)
        self.assertLess(expected_y, bottom - 39.0)

    def test_task_panel_route_polyline_uses_horizontal_and_vertical_segments(self) -> None:
        route = ((100.0, 640.0), (940.0, 520.0), receiver.WORK_TYPE_SWITCH)
        points = receiver._task_panel_route_polyline(route)
        self.assertEqual(points[0], route[0])
        self.assertEqual(points[-1], route[1])
        for start, end in zip(points, points[1:]):
            self.assertTrue(start[0] == end[0] or start[1] == end[1])

    def test_container_routes_lift_before_horizontal_transfer_for_all_work_types(self) -> None:
        common = {
            "bayMap": "144332",
            "startPos": "4C3051",
            "endPos": "4C3011",
        }
        cases = (
            (receiver.WORK_TYPE_LOAD, "", "4C3011"),
            (receiver.WORK_TYPE_UNLOAD, "4C3051", ""),
            (receiver.WORK_TYPE_SWITCH, "4C3051", "4C3011"),
        )
        for work_type, start_pos, end_pos in cases:
            route = receiver._task_panel_route_map_points(
                {
                    **common,
                    "workType": work_type,
                    "startPos": start_pos,
                    "endPos": end_pos,
                },
                map_left=568,
                bottom=692,
                cell_w=54,
                cell_h=24,
                gap=16,
                lane_left=574,
                lane_w=42,
            )
            self.assertIsNotNone(route)
            points = receiver._task_panel_route_polyline(route)
            self.assertEqual(len(points), 4)
            self.assertEqual(points[0][0], points[1][0])
            self.assertEqual(points[1][1], points[2][1])
            self.assertEqual(points[2][0], points[3][0])
            self.assertLess(points[1][1], points[0][1])

    def test_task_panel_route_indicator_draws_static_dashed_arrow(self) -> None:
        font_path = receiver._find_cjk_font_path()
        if font_path is None:
            self.skipTest("CJK font unavailable")
        task = {
            "taskId": "27130485",
            "workType": receiver.WORK_TYPE_SWITCH,
            "startPos": "4C3051",
            "endPos": "4C3011",
            "bayMap": "144332",
        }
        path = Path(tempfile.gettempdir()) / "smart_port_task_route_static.png"
        self.assertTrue(
            receiver._render_task_info_panel_bitmap(
                task,
                path,
                route_phase=0.0,
            )
        )
        from PIL import Image  # noqa: PLC0415

        with Image.open(path) as image:
            self.assertIsNotNone(image.getchannel("A").getbbox())

    def test_floating_runinfo_wraps_long_text_to_three_lines(self) -> None:
        font_path = receiver._find_cjk_font_path()
        if font_path is None:
            self.skipTest("CJK font unavailable")
        from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

        draw = ImageDraw.Draw(Image.new("RGBA", (500, 300), (0, 0, 0, 0)))
        font = ImageFont.truetype(
            str(font_path),
            receiver.RUNINFO_FLOATING_TEXT_FONT_SIZE,
        )
        lines = receiver._wrap_bitmap_text(
            draw,
            "任务开始箱号FFAU8892415目标位置4C3012请尽快执行",
            font,
            360,
            receiver.RUNINFO_FLOATING_TEXT_MAX_LINES,
        )

        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[-1].endswith("..."))

    def test_runinfo_visual_panels_use_solid_3d_variants(self) -> None:
        self.assertFalse(receiver.RUNINFO_OVERLAY_ENABLED)
        self.assertTrue(receiver.RUNINFO_FLOATING_ENABLED)
        self.assertGreaterEqual(receiver.RUNINFO_FLOATING_LOCAL_TRANSLATE[2], 13.0)
        self.assertEqual(receiver.RUNINFO_FLOATING_PANEL_WIDTH_M, 12.0)
        self.assertEqual(receiver.RUNINFO_FLOATING_PANEL_HEIGHT_M, 3.5)
        self.assertEqual(receiver.RUNINFO_FLOATING_IMAGE_HEIGHT, 448)
        self.assertEqual(receiver.RUNINFO_FLOATING_RIGHT_X_OFFSET_M, 1.2)
        self.assertEqual(receiver.RUNINFO_FLOATING_BACK_Y_OFFSET_M, 7.0)
        self.assertEqual(receiver.RUNINFO_FLOATING_UP_Z_OFFSET_M, 1.0)
        self.assertEqual(receiver.RUNINFO_FLOATING_TEXT_FONT_SIZE, 60)
        self.assertEqual(receiver.RUNINFO_FLOATING_CLOCK_FONT_SIZE, 44)
        self.assertEqual(receiver.RUNINFO_FLOATING_TIME_OFFSET_HOURS, 0)
        self.assertEqual(
            set(receiver.RUNINFO_LEVEL_STYLES),
            {
                receiver.RUNINFO_LEVEL_PROMPT,
                receiver.RUNINFO_LEVEL_ALERT,
                receiver.RUNINFO_LEVEL_EXCEPTION,
            },
        )

    def test_runinfo_time_text_uses_runinfo_timespan(self) -> None:
        self.assertEqual(
            receiver._runinfo_time_text({"timespan": "2026-08-04T15:30:45Z"}),
            "2026-08-04 15:30:45",
        )

    def test_runinfo_time_text_formats_unix_milliseconds(self) -> None:
        self.assertRegex(
            receiver._runinfo_time_text({"timespan": 1785857445000}),
            r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$",
        )

    def test_runinfo_message_persists_until_explicit_clear(self) -> None:
        self.assertEqual(
            self.driver._effective_runinfo_payload({"message": "作业完成"}),
            {"message": "作业完成"},
        )
        self.assertEqual(
            self.driver._effective_runinfo_payload({}),
            {"message": "作业完成"},
        )
        self.assertEqual(
            self.driver._effective_runinfo_payload(None),
            {"message": "作业完成"},
        )
        self.assertEqual(
            self.driver._effective_runinfo_payload({"message": ""}),
            {},
        )

    def test_runinfo_is_visible_only_for_active_unfinished_task(self) -> None:
        active = {"taskId": "active-1", "operationState": "WORKING"}
        self.assertTrue(receiver._runinfo_should_be_visible(active, {"message": "吊具下降"}))
        self.assertFalse(receiver._runinfo_should_be_visible({}, {"message": "等待任务"}))
        self.assertFalse(
            receiver._runinfo_should_be_visible(
                {"taskId": "done-1", "operationState": "COMPLETED"},
                {"message": "任务完成"},
            )
        )
        self.assertFalse(
            receiver._runinfo_should_be_visible(
                active,
                {"message": "本关作业完成"},
            )
        )

    def test_runinfo_cache_is_cleared_after_completion_or_no_task(self) -> None:
        active = {"taskId": "active-2", "operationState": "WORKING"}
        self.assertEqual(
            self.driver._runinfo_payload_for_task(
                {"message": "正在执行"},
                active,
            ),
            {"message": "正在执行"},
        )
        self.assertEqual(
            self.driver._runinfo_payload_for_task(
                {"message": "任务完成"},
                active,
            ),
            {},
        )
        self.assertEqual(self.driver._last_runinfo_payload, {})
        self.assertEqual(
            self.driver._runinfo_payload_for_task(None, {}),
            {},
        )

    def test_runinfo_panel_root_is_hidden_when_visibility_is_disabled(self) -> None:
        class Prim:
            def __init__(self, path: str) -> None:
                self.path = path

            def GetPath(self) -> str:
                return self.path

        class EditContext:
            def __init__(self, *_args) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> bool:
                return False

        current = Prim(receiver.RUNINFO_FLOATING_ROOT_PATH)
        legacy = Prim(receiver.LEGACY_RUNINFO_FLOATING_ROOT_PATH)
        hidden: list[str] = []
        removed: list[str] = []
        stage = types.SimpleNamespace(
            GetSessionLayer=lambda: object(),
            GetPrimAtPath=lambda path: {
                receiver.RUNINFO_FLOATING_ROOT_PATH: current,
                receiver.LEGACY_RUNINFO_FLOATING_ROOT_PATH: legacy,
            }.get(path),
            RemovePrim=lambda path: removed.append(str(path)),
        )
        fake_pxr = types.ModuleType("pxr")
        fake_pxr.Gf = types.SimpleNamespace()
        fake_pxr.Sdf = types.SimpleNamespace()
        fake_pxr.Usd = types.SimpleNamespace(EditContext=EditContext)
        fake_pxr.UsdGeom = types.SimpleNamespace(
            Imageable=lambda prim: types.SimpleNamespace(
                MakeInvisible=lambda: hidden.append(prim.GetPath())
            )
        )
        fake_pxr.UsdShade = types.SimpleNamespace()
        self.driver._floating_runinfo_text = "旧提示"

        with patch.dict(sys.modules, {"pxr": fake_pxr}):
            self.driver._sync_floating_runinfo_billboard(
                stage,
                {},
                visible=False,
            )

        self.assertEqual(hidden, [receiver.RUNINFO_FLOATING_ROOT_PATH])
        self.assertEqual(removed, [receiver.LEGACY_RUNINFO_FLOATING_ROOT_PATH])
        self.assertIsNone(self.driver._floating_runinfo_text)

    def test_floating_runinfo_panel_is_rotated_180_degrees(self) -> None:
        self.assertEqual(receiver.RUNINFO_FLOATING_ROTATE_XYZ, (0.0, 0.0, 180.0))
        self.assertEqual(
            receiver.RUNINFO_FLOATING_FACE_VERTEX_INDICES,
            (0, 1, 2, 3),
        )
        self.assertEqual(
            receiver.RUNINFO_FLOATING_UV_COORDS[0],
            (0.0, 0.0),
        )
        self.assertEqual(
            receiver.RUNINFO_FLOATING_UV_COORDS,
            (
                (0.0, 0.0),
                (1.0, 0.0),
                (1.0, 1.0),
                (0.0, 1.0),
            ),
        )

    def test_floating_runinfo_is_not_parented_to_rtg(self) -> None:
        self.assertEqual(
            receiver.RUNINFO_FLOATING_ROOT_PATH,
            "/World/LiveHUD/RTG_RUNINFO_FLOATING_BILLBOARD",
        )
        self.assertFalse(
            receiver.RUNINFO_FLOATING_ROOT_PATH.startswith(
                "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/"
            )
        )
        self.assertTrue(
            receiver.LEGACY_RUNINFO_FLOATING_ROOT_PATH.startswith(
                "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/"
            )
        )

    def test_stale_runinfo_billboards_are_removed_from_stage(self) -> None:
        class Prim:
            def __init__(self, path: str) -> None:
                self.path = path

            def GetPath(self) -> str:
                return self.path

            def GetName(self) -> str:
                return self.path.rsplit("/", 1)[-1]

        class EditContext:
            def __init__(self, *_args) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> bool:
                return False

        live = Prim(receiver.RUNINFO_FLOATING_ROOT_PATH)
        live_panel = Prim(f"{receiver.RUNINFO_FLOATING_ROOT_PATH}/Panel")
        live_material = Prim(receiver.RUNINFO_FLOATING_MATERIAL_PATH)
        legacy = Prim(receiver.LEGACY_RUNINFO_FLOATING_ROOT_PATH)
        copied = Prim("/World/PortAndRTG/RTG_RUNINFO_FLOATING_BILLBOARD_001")
        old_named = Prim("/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/RunInfoPanel")
        other = Prim("/World/LiveHUD/RTG_TASK_INFO_PANEL")
        prims = {
            prim.path: prim
            for prim in (
                live,
                live_panel,
                live_material,
                legacy,
                copied,
                old_named,
                other,
            )
        }
        removed: list[str] = []
        stage = types.SimpleNamespace(
            GetSessionLayer=lambda: object(),
            GetPrimAtPath=lambda path: prims.get(path),
            Traverse=lambda: list(prims.values()),
            RemovePrim=lambda path: removed.append(str(path)),
        )
        fake_pxr = types.ModuleType("pxr")
        fake_pxr.Usd = types.SimpleNamespace(EditContext=EditContext)

        with patch.dict(sys.modules, {"pxr": fake_pxr}):
            count = receiver._remove_stale_runinfo_billboards(stage)

        self.assertEqual(count, 3)
        self.assertEqual(
            set(removed),
            {
                "/World/PortAndRTG/RTG_RUNINFO_FLOATING_BILLBOARD_001",
                "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/RunInfoPanel",
                receiver.LEGACY_RUNINFO_FLOATING_ROOT_PATH,
            },
        )

    def test_all_runinfo_billboards_can_be_removed_from_stage(self) -> None:
        class Prim:
            def __init__(self, path: str) -> None:
                self.path = path

            def GetPath(self) -> str:
                return self.path

            def GetName(self) -> str:
                return self.path.rsplit("/", 1)[-1]

        class EditContext:
            def __init__(self, *_args) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> bool:
                return False

        live = Prim(receiver.RUNINFO_FLOATING_ROOT_PATH)
        live_panel = Prim(f"{receiver.RUNINFO_FLOATING_ROOT_PATH}/Panel")
        live_material = Prim(receiver.RUNINFO_FLOATING_MATERIAL_PATH)
        live_material_shader = Prim(f"{receiver.RUNINFO_FLOATING_MATERIAL_PATH}/Texture")
        legacy = Prim(receiver.LEGACY_RUNINFO_FLOATING_ROOT_PATH)
        prims = {
            prim.path: prim
            for prim in (
                live,
                live_panel,
                live_material,
                live_material_shader,
                legacy,
            )
        }
        removed: list[str] = []
        stage = types.SimpleNamespace(
            GetSessionLayer=lambda: object(),
            GetPrimAtPath=lambda path: prims.get(path),
            Traverse=lambda: list(prims.values()),
            RemovePrim=lambda path: removed.append(str(path)),
        )
        fake_pxr = types.ModuleType("pxr")
        fake_pxr.Usd = types.SimpleNamespace(EditContext=EditContext)

        with patch.dict(sys.modules, {"pxr": fake_pxr}):
            count = receiver._remove_runinfo_billboards(stage, keep_current=False)

        self.assertEqual(count, 3)
        self.assertEqual(
            set(removed),
            {
                receiver.RUNINFO_FLOATING_ROOT_PATH,
                receiver.RUNINFO_FLOATING_MATERIAL_PATH,
                receiver.LEGACY_RUNINFO_FLOATING_ROOT_PATH,
            },
        )

    def test_driver_stop_unsubscribes_update_callback(self) -> None:
        class Subscription:
            def __init__(self) -> None:
                self.unsubscribed = False

            def unsubscribe(self) -> None:
                self.unsubscribed = True

        driver = receiver.OmniverseRtgTwinDriver(
            inventory_mode=receiver.INVENTORY_MODE_SIMULATION,
            inventory_autosave=False,
        )
        subscription = Subscription()
        driver._subscription = subscription
        driver._current_stage = lambda: None

        driver.stop()

        self.assertTrue(subscription.unsubscribed)
        self.assertIsNone(driver._subscription)

    def test_task_info_panel_is_live_hud_not_rtg_child(self) -> None:
        self.assertEqual(
            receiver.TASK_PANEL_ROOT_PATH,
            "/World/LiveHUD/RTG_TASK_INFO_PANEL",
        )
        self.assertFalse(
            receiver.TASK_PANEL_ROOT_PATH.startswith(
                "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/"
            )
        )
        self.assertEqual(receiver.TASK_PANEL_ROTATE_XYZ, (0.0, 0.0, 180.0))
        self.assertEqual(
            receiver.TASK_PANEL_UV_COORDS,
            (
                (0.0, 0.0),
                (1.0, 0.0),
                (1.0, 1.0),
                (0.0, 1.0),
            ),
        )
        self.assertEqual(
            receiver.TASK_PANEL_UV_COORDS,
            receiver.RUNINFO_FLOATING_UV_COORDS,
        )
        self.assertEqual(
            receiver.TASK_PANEL_ONE_TIER_VISUAL_HEIGHT_M,
            receiver.REFERENCE_CONTAINER_VISUAL_HEIGHT_Z_M,
        )
        self.assertAlmostEqual(
            receiver.TASK_PANEL_BASE_WORLD_Z - receiver.TASK_PANEL_HEIGHT_M * 0.5,
            receiver.TASK_PANEL_ONE_TIER_VISUAL_HEIGHT_M,
        )
        self.assertAlmostEqual(
            receiver.TASK_PANEL_WORLD_X,
            12.0
            + receiver.TASK_PANEL_WIDTH_M * 0.25
            + receiver.TASK_PANEL_CONTAINER_WIDTH_X_M,
        )

    def test_panel_positions_put_device_left_and_swap_task_container(self) -> None:
        self.assertEqual(
            receiver.DEVICE_LOCATION_PANEL_ROOT_PATH,
            "/World/LiveHUD/RTG_DEVICE_LOCATION_PANEL",
        )
        self.assertLess(receiver.DEVICE_LOCATION_PANEL_WORLD_X, 12.0)
        self.assertAlmostEqual(
            receiver.DEVICE_LOCATION_PANEL_WORLD_X,
            12.0
            - receiver.TASK_PANEL_WIDTH_M * 2.25
            - receiver.TASK_PANEL_CONTAINER_WIDTH_X_M,
        )
        self.assertAlmostEqual(
            receiver.CONTAINER_INFO_PANEL_WORLD_X,
            receiver.TASK_PANEL_WORLD_X
            + (
                receiver.CONTAINER_INFO_PANEL_WIDTH_M
                - receiver.TASK_PANEL_WIDTH_M
            )
            * 0.5,
        )
        self.assertAlmostEqual(
            receiver.CONTAINER_INFO_PANEL_WORLD_Z,
            receiver.TASK_PANEL_BASE_WORLD_Z - 0.8,
        )
        self.assertAlmostEqual(
            receiver.DEVICE_LOCATION_PANEL_WORLD_Z - receiver.TASK_PANEL_WORLD_Z,
            receiver.TASK_PANEL_WIDTH_M / 4.0,
        )
        self.assertEqual(
            receiver.DEVICE_LOCATION_PANEL_Y_OFFSET_M,
            receiver.TASK_PANEL_Y_OFFSET_M,
        )
        self.assertEqual(
            receiver.DEVICE_LOCATION_PANEL_ROTATE_XYZ,
            receiver.TASK_PANEL_ROTATE_XYZ,
        )

    def test_device_location_panel_renderer_draws_live_device_values(self) -> None:
        font_path = receiver._find_cjk_font_path()
        if font_path is None:
            self.skipTest("CJK font unavailable")
        path = Path(tempfile.gettempdir()) / "smart_port_rtg_device_location_test.png"
        rendered = receiver._render_device_location_panel_bitmap(
            {
                "Device": "RTG_220",
                "Field": "4C",
                "Bay": "031",
                "WorkMode": "远程手动",
                "Gantry": "165.23 m",
                "Trolley": "12.35 m",
                "Hoist": "8.00 m",
            },
            path,
        )
        self.assertTrue(rendered)
        from PIL import Image  # noqa: PLC0415

        with Image.open(path) as image:
            self.assertEqual(
                image.size,
                (
                    receiver.DEVICE_LOCATION_PANEL_IMAGE_WIDTH,
                    receiver.DEVICE_LOCATION_PANEL_IMAGE_HEIGHT,
                ),
            )
            self.assertIsNotNone(image.getchannel("A").getbbox())

    def test_device_location_values_overlay_contains_visible_pixels(self) -> None:
        font_path = receiver._find_cjk_font_path()
        if font_path is None:
            self.skipTest("CJK font unavailable")
        path = Path(tempfile.gettempdir()) / "smart_port_rtg_device_values_overlay_test.png"
        rendered = receiver._render_device_location_values_bitmap(
            {
                "Device": "RTG_220",
                "Field": "4C",
                "Bay": "021",
                "WorkMode": "自动模式",
                "Gantry": "76.04 m",
                "Trolley": "3.00 m",
                "Hoist": "14.14 m",
            },
            path,
        )
        self.assertTrue(rendered)
        from PIL import Image  # noqa: PLC0415

        with Image.open(path) as image:
            self.assertEqual(
                image.size,
                (
                    receiver.DEVICE_LOCATION_PANEL_IMAGE_WIDTH,
                    receiver.DEVICE_LOCATION_PANEL_IMAGE_HEIGHT,
                ),
            )
            self.assertIsNotNone(image.getchannel("A").getbbox())

    def test_device_location_refresh_key_ignores_motion_positions(self) -> None:
        first = {
            "Device": "RTG_220",
            "Field": "4C",
            "Bay": "021",
            "Gantry": "10.00 m",
            "Trolley": "2.00 m",
            "Hoist": "3.00 m",
        }
        second = dict(first)
        second.update(
            {
                "Gantry": "388.80 m",
                "Trolley": "10.38 m",
                "Hoist": "15.03 m",
            }
        )
        self.assertEqual(
            receiver._device_location_panel_value_key(first),
            receiver._device_location_panel_value_key(second),
        )
        self.assertEqual(
            set(receiver.DEVICE_LOCATION_PANEL_VALUE_Y),
            {"Device", "Field", "Bay", "WorkMode"},
        )

    def test_device_location_panel_values_read_live_status(self) -> None:
        class Attr:
            def __init__(self, value):
                self.value = value

            def Get(self):
                return self.value

        class Prim:
            def __init__(self, values):
                self.values = values

            def GetAttribute(self, name):
                return Attr(self.values[name]) if name in self.values else None

        status = Prim(
            {
                "rtg:rtgName": "RTG_220",
                "rtg:currentField": "4C",
                "rtg:currentBay": 31,
                "rtg:workMode": 3,
                "rtg:gantryMeter": 165.234,
                "rtg:trolleyMeter": 12.35,
                "rtg:hoistMeter": 8.0,
            }
        )
        stage = types.SimpleNamespace(
            GetPrimAtPath=lambda path: status
            if path == receiver.STATUS_PRIM_PATH
            else None
        )
        self.assertEqual(
            receiver._device_location_panel_values(stage),
            {
                "Device": "RTG_220",
                "Field": "4C",
                "Bay": "31",
                "WorkMode": "远程手动",
                "Gantry": "165.23 m",
                "Trolley": "12.35 m",
                "Hoist": "8.00 m",
            },
        )

    def test_work_mode_labels_cover_heartbeat_codes(self) -> None:
        expected = {
            0: "未知",
            1: "自动模式",
            2: "辅助模式",
            3: "远程手动",
            4: "本地模式",
            5: "维修模式",
        }
        for code, label in expected.items():
            self.assertEqual(receiver._work_mode_text(code), label)
        self.assertEqual(receiver._work_mode_text(99), "未知")

    def test_user_view_camera_is_rtg_side_view_entry(self) -> None:
        self.assertEqual(
            receiver.USER_SIDE_VIEW_CAMERA_PATH,
            "/World/PortAndRTG/Omniverse_User_View/Omniverse_User_View_data",
        )
        self.assertGreater(receiver.USER_SIDE_VIEW_CAMERA_TRANSLATE[0], 80.0)
        self.assertLess(receiver.USER_SIDE_VIEW_CAMERA_TRANSLATE[2], 0.0)
        self.assertEqual(
            receiver.USER_SIDE_VIEW_CAMERA_ROTATE_YXZ,
            (-181.93245, 520.10876, -543.2226),
        )


class RemoteModeSignalLinkTests(unittest.TestCase):
    def test_non_remote_mode_hides_signal_without_building_geometry(self) -> None:
        driver = receiver.OmniverseRtgTwinDriver()
        driver._automatic_signal_work_mode = 1
        visibility = []
        driver._set_automatic_signal_visible = (
            lambda _stage, visible: visibility.append(visible)
        )

        driver._sync_automatic_mode_signal(object(), 10.0)

        self.assertEqual(visibility, [False])

    def test_work_mode_prefers_pose_and_accepts_top_level_fallback(self) -> None:
        self.assertEqual(
            receiver._state_work_mode(
                {"workMode": 3, "pose": {"workMode": 1}}
            ),
            1,
        )
        self.assertEqual(receiver._state_work_mode({"work_mode": "4"}), 4)
        self.assertEqual(receiver._state_work_mode({"pose": {}}), 0)

    def test_signal_arc_starts_at_antenna_and_ends_at_rtg(self) -> None:
        start = (135.6, 470.0, 20.0)
        end = (12.0, 95.0, 15.0)

        points, control = receiver._automatic_signal_arc(start, end, 21)

        self.assertEqual(points[0], start)
        self.assertEqual(points[-1], end)
        self.assertEqual(len(points), 21)
        self.assertGreater(control[2], max(start[2], end[2]))
        self.assertGreater(points[len(points) // 2][2], max(start[2], end[2]))

    def test_signal_target_uses_portal_centerline_above_rtg(self) -> None:
        target = receiver.AUTOMATIC_SIGNAL_RTG_TARGET_LOCAL_POINT

        self.assertAlmostEqual(target[0], (-6.399999 + 4.805813) * 0.5)
        self.assertAlmostEqual(target[1], 5.0)
        self.assertGreater(target[2], 8.36)

    def test_signal_data_rails_converge_at_both_endpoints(self) -> None:
        start = (0.0, 0.0, 5.0)
        end = (20.0, 40.0, 12.0)
        points, _control = receiver._automatic_signal_arc(start, end, 9)

        left = receiver._automatic_signal_rail_points(points, start, end, 0.24)
        right = receiver._automatic_signal_rail_points(points, start, end, -0.24)

        self.assertEqual(left[0], points[0])
        self.assertEqual(left[-1], points[-1])
        self.assertEqual(right[0], points[0])
        self.assertAlmostEqual(right[-1][0], points[-1][0])
        self.assertAlmostEqual(right[-1][1], points[-1][1])
        self.assertNotEqual(left[len(left) // 2], right[len(right) // 2])

    def test_packet_sampling_clamps_progress_to_arc(self) -> None:
        start = (0.0, 0.0, 0.0)
        control = (5.0, 5.0, 10.0)
        end = (10.0, 0.0, 0.0)

        self.assertEqual(
            receiver._quadratic_signal_point(start, control, end, -1.0), start
        )
        self.assertEqual(
            receiver._quadratic_signal_point(start, control, end, 2.0), end
        )


class LivePoseValidationTests(unittest.TestCase):
    def test_non_yard_plc_field_or_bay_is_rejected(self) -> None:
        self.assertFalse(receiver._is_supported_yard_bay("CR", 1))
        self.assertFalse(receiver._is_supported_yard_bay("4C", 91))
        self.assertTrue(receiver._is_supported_yard_bay("4C", 41))

    def test_invalid_plc_field_and_bay_fall_back_to_continuous_gantry_distance(self) -> None:
        class Controller:
            @staticmethod
            def current_spreader_size_ft():
                return 40.0

            @staticmethod
            def set_gantry_safe(value):
                return value

            @staticmethod
            def set_trolley_safe(value):
                return value

            @staticmethod
            def set_hoist_safe(value, carried_load=False):
                return value

        driver = receiver.OmniverseRtgTwinDriver()
        driver._current_stage = lambda: object()
        driver._disable_demo_artifacts_for_stage = lambda _stage: None
        driver._sync_inventory_scene = lambda _stage: None
        driver._controller_for_stage = lambda _stage: Controller()
        driver._sync_spreader_size_for_task = lambda _controller, _task: (40.0, None)
        driver._sync_task_stack = lambda _stage, _task: False
        driver._remove_task_route_indicator_once = lambda _stage: None
        driver._sync_task_vehicle = lambda *_args: None
        driver._sync_task_switch = lambda *_args: None
        driver._write_status = lambda *_args: None
        driver._sync_status_overlay = lambda *_args: None

        applied = driver.apply_state(
            {
                "pose": {
                    "currentField": "CR",
                    "currentBay": 91,
                    "bigcarElecDistance": 12.5,
                    "trolleyMeter": 1.0,
                    "hoistMeter": 2.0,
                },
                "task": {},
            }
        )

        self.assertEqual(applied["gantryMeter"], 12.5)
        self.assertEqual(applied["gantrySource"], "bigcarElecDistance")

    def test_valid_field_and_bay_override_continuous_gantry_distance(self) -> None:
        class Controller:
            @staticmethod
            def current_spreader_size_ft():
                return 40.0

            @staticmethod
            def set_gantry_bay(field, bay, **_kwargs):
                assert field == "3C"
                assert bay == 90
                return 225.0

            @staticmethod
            def set_gantry_safe(value):
                return value

            @staticmethod
            def set_trolley_safe(value):
                return value

            @staticmethod
            def set_hoist_safe(value, carried_load=False):
                return value

        driver = receiver.OmniverseRtgTwinDriver()
        driver._current_stage = lambda: object()
        driver._disable_demo_artifacts_for_stage = lambda _stage: None
        driver._sync_inventory_scene = lambda _stage: None
        driver._controller_for_stage = lambda _stage: Controller()
        driver._sync_spreader_size_for_task = lambda _controller, _task: (40.0, None)
        driver._sync_task_stack = lambda _stage, _task: False
        driver._remove_task_route_indicator_once = lambda _stage: None
        driver._sync_task_vehicle = lambda *_args: None
        driver._sync_task_switch = lambda *_args: None
        driver._write_status = lambda *_args: None
        driver._sync_status_overlay = lambda *_args: None

        applied = driver.apply_state(
            {
                "pose": {
                    "currentField": "3C",
                    "currentBay": 90,
                    "bigcarElecDistance": 298.68,
                    "trolleyMeter": 1.0,
                    "hoistMeter": 2.0,
                },
                "task": {},
            }
        )

        self.assertEqual(applied["gantryMeter"], 225.0)
        self.assertEqual(applied["gantrySource"], "3C/090")

    def test_baychange_is_not_a_container_work_task(self) -> None:
        self.assertFalse(
            receiver._is_container_work_task(
                {"taskId": "27167922", "workType": "BAYCHANGE"}
            )
        )


class GantryCrossbeamAlignmentTests(unittest.TestCase):
    def test_alignment_delta_moves_crossbeam_to_rendered_box_center(self) -> None:
        self.assertAlmostEqual(
            receiver._gantry_crossbeam_alignment_delta_y(18.45, 16.8125),
            1.6375,
        )


class TaskVisualPolicyTests(unittest.TestCase):
    def test_removed_3d_route_indicator_is_cleaned_once_per_stage(self) -> None:
        driver = receiver.OmniverseRtgTwinDriver()
        removed = []
        driver._remove_task_route_indicator = lambda stage: removed.append(stage)
        first_stage = object()
        second_stage = object()

        driver._remove_task_route_indicator_once(first_stage)
        driver._remove_task_route_indicator_once(first_stage)
        driver._remove_task_route_indicator_once(second_stage)

        self.assertEqual(removed, [first_stage, second_stage])

    def test_live_task_truck_uses_blender_lane_truck_asset(self) -> None:
        self.assertTrue(receiver.LIVE_TASK_TRUCK_VISUAL_ASSET_PATH.is_file())
        self.assertEqual(
            receiver.LIVE_TASK_TRUCK_VISUAL_ASSET_PATH.name,
            "RTG_Model_Lane_Truck.usdc",
        )
        self.assertEqual(
            receiver.LIVE_TASK_TRUCK_VISUAL_SCALE,
            (1.0, 1.22, 1.0),
        )
        self.assertEqual(receiver.LIVE_TASK_TRUCK_ASSET_GROUND_Z_M, 0.14)

    def test_imported_truck_cab_aligns_with_task_layout(self) -> None:
        target_cab_y = -7.30
        translated_asset_cab_y = (
            receiver.LIVE_TASK_TRUCK_ASSET_CAB_CENTER_Y_M
            + receiver._task_truck_imported_visual_translate_y(target_cab_y)
        )
        self.assertAlmostEqual(translated_asset_cab_y, target_cab_y)

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

    def test_task_stack_lifecycle_has_no_task_scoped_weather_effects(self) -> None:
        source = (
            PROJECT_ROOT / "omniverse" / "scripts" / "rtg_twin_state_receiver.py"
        ).read_text(encoding="utf-8")
        assert "_refresh_task_bay_weather_effects" not in source
        assert "refresh_task_bay_roof_rain" not in source
        assert "update_rain_fps_quality" not in source

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

    def test_load_task_starts_truck_immediately_when_already_at_task_bay(self) -> None:
        driver = receiver.OmniverseRtgTwinDriver()
        driver._set_operation_state_attr = lambda *_args: None
        driver._set_task_flow_attr = lambda *_args: None
        task = {
            "taskId": "27155095",
            "workType": "LOAD",
            "startPos": "3C0551",
            "endPos": "3C0551",
            "containerSize": 0,
            "bayMap": "244402",
        }
        key = driver._task_vehicle_key(task)

        with patch.object(receiver.time, "time", return_value=200.0):
            state = driver._advance_operation_flow(
                object(),
                key,
                task,
                {
                    "currentField": "3C",
                    "currentBay": 5,
                    "trolleyMeter": 0.453,
                    "hoistMeter": 15.026,
                    "lockState": 0,
                    "unlockState": 1,
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


class LiveInfoPopupFacingSyncTests(unittest.TestCase):
    def test_runinfo_pose_refresh_anchors_above_rtg_and_faces_camera(self) -> None:
        root_prim = types.SimpleNamespace(path=receiver.RUNINFO_FLOATING_ROOT_PATH)
        rtg_prim = types.SimpleNamespace(path=receiver.RUNINFO_FLOATING_ABOVE_RTG_PATH)

        def get_prim_at_path(path: str) -> object | None:
            if path == receiver.RUNINFO_FLOATING_ABOVE_RTG_PATH:
                return rtg_prim
            return None

        class FakeBox:
            @staticmethod
            def GetMin() -> tuple[float, float, float]:
                return (2.0, 4.0, 6.0)

            @staticmethod
            def GetMax() -> tuple[float, float, float]:
                return (10.0, 14.0, 16.0)

        class FakeWorldBound:
            @staticmethod
            def ComputeAlignedBox() -> FakeBox:
                return FakeBox()

        class FakeBBoxCache:
            def __init__(self, *_args: object) -> None:
                pass

            @staticmethod
            def ComputeWorldBound(_prim: object) -> FakeWorldBound:
                return FakeWorldBound()

        stage = types.SimpleNamespace(GetPrimAtPath=get_prim_at_path)
        fake_pxr = types.ModuleType("pxr")
        fake_pxr.Gf = types.SimpleNamespace(
            Vec3d=lambda *values: tuple(values),
            Vec3f=lambda *values: tuple(values),
        )
        fake_pxr.Usd = types.SimpleNamespace(
            TimeCode=types.SimpleNamespace(Default=lambda: None)
        )
        fake_pxr.UsdGeom = types.SimpleNamespace(
            BBoxCache=FakeBBoxCache,
            Tokens=types.SimpleNamespace(default_="default"),
        )
        translations = []
        rotations = []

        driver = receiver.OmniverseRtgTwinDriver.__new__(
            receiver.OmniverseRtgTwinDriver
        )
        driver._set_xform_translate = lambda prim, translate: translations.append(
            (prim.path, translate)
        )
        driver._set_xform_rotate_xyz = lambda prim, rotate: rotations.append(
            (prim.path, rotate)
        )

        with patch.dict(sys.modules, {"pxr": fake_pxr}):
            with patch.object(
                receiver,
                "camera_facing_rotate_xyz",
                return_value=(0.0, 0.0, 123.0),
            ) as facing:
                driver._sync_floating_runinfo_pose(stage, root_prim)

        expected_translate = (
            6.0 + receiver.RUNINFO_FLOATING_RIGHT_X_OFFSET_M,
            9.0 + receiver.RUNINFO_FLOATING_BACK_Y_OFFSET_M,
            16.0
            + receiver.RUNINFO_FLOATING_ABOVE_GAP_M
            + receiver.RUNINFO_FLOATING_PANEL_HEIGHT_M * 0.5
            + receiver.RUNINFO_FLOATING_UP_Z_OFFSET_M,
        )
        self.assertEqual(
            translations,
            [(receiver.RUNINFO_FLOATING_ROOT_PATH, expected_translate)],
        )
        facing.assert_called_once_with(
            stage,
            expected_translate,
            receiver.RUNINFO_FLOATING_ROTATE_XYZ,
        )
        self.assertEqual(
            rotations,
            [(receiver.RUNINFO_FLOATING_ROOT_PATH, (0.0, 0.0, 123.0))],
        )

    def test_sync_visible_live_info_panels_rotates_all_usd_panel_roots(self) -> None:
        class FakePrim:
            def __init__(self, path: str) -> None:
                self.path = path

        prims = {
            path: FakePrim(path)
            for path in receiver.LIVE_INFO_CAMERA_FACING_PANEL_ROOT_PATHS
        }
        stage = types.SimpleNamespace(GetPrimAtPath=lambda path: prims.get(path))
        fake_pxr = types.ModuleType("pxr")
        fake_pxr.Gf = types.SimpleNamespace(Vec3f=lambda *values: tuple(values))
        rotations = []

        driver = receiver.OmniverseRtgTwinDriver.__new__(
            receiver.OmniverseRtgTwinDriver
        )
        driver._current_stage = lambda: stage
        driver._get_xform_translate = lambda _prim: (1.0, 2.0, 3.0)
        driver._set_xform_rotate_xyz = lambda prim, rotate: rotations.append(
            (prim.path, rotate)
        )

        with patch.dict(sys.modules, {"pxr": fake_pxr}):
            facing_calls = []

            def fake_facing(stage_arg, translate_arg, rotate_arg):
                facing_calls.append((stage_arg, translate_arg, rotate_arg))
                return (0.0, 0.0, 123.0)

            with patch.object(
                receiver,
                "camera_facing_rotate_xyz",
                side_effect=fake_facing,
            ):
                driver._sync_visible_live_info_panels_facing_camera()

        self.assertEqual(
            [call[2] for call in facing_calls],
            [
                receiver.LIVE_INFO_CAMERA_FACING_PANEL_ROTATE_XYZ[path]
                for path in receiver.LIVE_INFO_CAMERA_FACING_PANEL_ROOT_PATHS
            ],
        )
        self.assertEqual(
            rotations,
            [
                (path, (0.0, 0.0, 123.0))
                for path in receiver.LIVE_INFO_CAMERA_FACING_PANEL_ROOT_PATHS
            ],
        )

    def test_panel_facing_update_reads_camera_once_and_is_throttled(self) -> None:
        stage = object()
        camera_position = (12.0, -8.0, 16.0)
        calls = []
        driver = receiver.OmniverseRtgTwinDriver.__new__(
            receiver.OmniverseRtgTwinDriver
        )
        driver._last_panel_facing_update_at = 0.0
        driver._current_stage = lambda: stage
        driver._sync_visible_task_panel_facing_camera = (
            lambda stage_arg, camera_arg: calls.append(
                ("task", stage_arg, camera_arg)
            )
        )
        driver._sync_visible_live_info_panels_facing_camera = (
            lambda stage_arg, camera_arg: calls.append(
                ("live", stage_arg, camera_arg)
            )
        )

        with (
            patch.object(receiver.time, "monotonic", side_effect=(10.0, 10.01)),
            patch.object(
                receiver,
                "active_viewport_camera_position",
                return_value=camera_position,
            ) as camera_read,
        ):
            driver._sync_visible_panels_facing_camera_if_due()
            driver._sync_visible_panels_facing_camera_if_due()

        camera_read.assert_called_once_with(stage)
        self.assertEqual(
            calls,
            [
                ("task", stage, camera_position),
                ("live", stage, camera_position),
            ],
        )


class LiveWeatherControlTests(unittest.TestCase):
    def test_live_update_applies_rtg_control_weather_and_keeps_rain_moving(self) -> None:
        class FakeAttribute:
            def __init__(self, value):
                self.value = value

            def Get(self):
                return self.value

        class FakeEnvironment:
            def __init__(self):
                self.values = {
                    "mode": "rain",
                    "rain": "light",
                    "fog": "medium",
                    "wind": 3.0,
                }

            def IsValid(self):
                return True

            def GetAttribute(self, name):
                return FakeAttribute(self.values[name])

        environment = FakeEnvironment()
        stage = types.SimpleNamespace(
            GetPrimAtPath=lambda path: environment if path == "/Environment" else None
        )
        applied = []
        animated = []
        weather = types.ModuleType("smart_port_environment")
        weather.ENVIRONMENT_ROOT_PATH = "/Environment"
        weather.RAIN_AMOUNT_ATTRIBUTE = "rain"
        weather.FOG_AMOUNT_ATTRIBUTE = "fog"
        weather.WIND_SPEED_ATTRIBUTE = "wind"
        weather.DEFAULT_RAIN_AMOUNT = "medium"
        weather.DEFAULT_FOG_AMOUNT = "medium"
        weather.DEFAULT_WIND_SPEED_MPS = 4.5
        weather.current_environment_mode = lambda _stage: environment.values["mode"]
        weather.normalize_rain_amount = lambda value: value
        weather.normalize_fog_amount = lambda value: value
        weather.apply_environment_mode = lambda _stage, mode: applied.append(mode)
        weather.update_weather_animation = (
            lambda _stage, elapsed: animated.append(elapsed) or True
        )

        driver = receiver.OmniverseRtgTwinDriver.__new__(
            receiver.OmniverseRtgTwinDriver
        )
        driver._weather_signature = None
        # This test represents an already initialized live stage.  Startup
        # itself intentionally resets a stale session-layer night mode to day.
        driver._weather_stage_id = id(stage)
        driver._last_error_at = 0.0
        driver._log = lambda _message: None

        with patch.dict(sys.modules, {"smart_port_environment": weather}):
            driver._sync_weather_environment(stage)
            environment.values.update(rain="torrential", fog="heavy", wind=15.0)
            driver._last_weather_attribute_sync_at = 0.0
            driver._sync_weather_environment(stage)

        self.assertEqual(applied, ["rain", "rain"])
        self.assertEqual(len(animated), 2)
        self.assertEqual(
            driver._weather_signature,
            (id(stage), "rain", "torrential", "heavy", 15.0),
        )


if __name__ == "__main__":
    unittest.main()
