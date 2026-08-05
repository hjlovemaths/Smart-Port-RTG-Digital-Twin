from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_ROOT / "omniverse" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import rtg_container_info_popup as popup  # noqa: E402


class Attr:
    def __init__(self, value):
        self._value = value

    def Get(self):
        return self._value


class Prim:
    def __init__(self, attrs=None):
        self._attrs = attrs or {}

    def GetAttribute(self, name):
        if name not in self._attrs:
            return None
        return Attr(self._attrs[name])


class Stage:
    def __init__(self, prims):
        self._prims = prims

    def GetPrimAtPath(self, path):
        return self._prims.get(path)


class ContainerInfoPopupTests(unittest.TestCase):
    def test_selection_path_normalizes_hoisted_container_children(self) -> None:
        self.assertEqual(
            popup._container_root_path_from_selection(
                "/World/LiveContainers/LiveTwinHoistedContainer/Body/Rib_01"
            ),
            "/World/LiveContainers/LiveTwinHoistedContainer",
        )

    def test_selection_path_normalizes_yard_container_details(self) -> None:
        self.assertEqual(
            popup._container_root_path_from_selection(
                "/World/LiveContainers/YardInventory/Bay_1C_021/"
                "R05_T03_Container/WeatheredTop"
            ),
            "/World/LiveContainers/YardInventory/Bay_1C_021/R05_T03_Container",
        )

    def test_selection_path_ignores_collection_roots(self) -> None:
        self.assertIsNone(
            popup._container_root_path_from_selection("/World/LiveContainers")
        )

    def test_container_panel_uses_scene_hud_without_omni_window_chrome(self) -> None:
        self.assertEqual(
            popup.CONTAINER_SCENE_PANEL_ROOT_PATH,
            "/World/LiveHUD/RTG_CONTAINER_INFO_PANEL",
        )
        self.assertEqual(
            popup.CONTAINER_SCENE_PANEL_ROTATE_XYZ,
            (0.0, 0.0, 180.0),
        )

    def test_container_panel_world_z_caps_at_six_tier_stack_height(self) -> None:
        self.assertEqual(
            popup._container_panel_world_z(2.0),
            2.0 + popup.CONTAINER_SCENE_PANEL_Z_OFFSET_M,
        )
        self.assertEqual(
            popup._container_panel_world_z(30.0),
            popup.CONTAINER_SCENE_PANEL_MAX_WORLD_Z,
        )

    def test_stop_previous_builtin_service_clears_legacy_window_service(self) -> None:
        class Previous:
            def __init__(self):
                self.stopped = False

            def stop(self):
                self.stopped = True

        previous = Previous()
        old_value = getattr(popup.builtins, "_rtg_container_info_popup_service", None)
        popup.builtins._rtg_container_info_popup_service = previous
        try:
            popup._stop_previous_builtin_service()

            self.assertTrue(previous.stopped)
            self.assertIsNone(popup.builtins._rtg_container_info_popup_service)
        finally:
            popup.builtins._rtg_container_info_popup_service = old_value

    def test_collect_info_merges_status_and_container_attrs(self) -> None:
        root = "/World/LiveContainers/LiveTwinHoistedContainer"
        stage = Stage(
            {
                popup.STATUS_PRIM_PATH: Prim(
                    {
                        "rtg:containerNo": "MSCU1234567",
                        "rtg:containerType": "20HC",
                        "rtg:containerSize": 20,
                        "rtg:taskId": "TASK-001",
                        "rtg:workType": "UNLOAD",
                        "rtg:operationState": "CARRYING",
                        "rtg:startPos": "3C6324",
                        "rtg:endPos": "3C6300",
                        "rtg:bayMap": "441000",
                        "rtg:spreaderLocked": True,
                    }
                ),
                root: Prim({"live:source": "rtg_twin_hoisted_copy"}),
                f"{root}/Body": Prim(
                    {
                        "live:bayId": "3C/063",
                        "live:row": 2,
                        "live:tier": 4,
                        "live:tiers": 4,
                        "live:containerHeightMeters": 2.896,
                    }
                ),
            }
        )

        values = {row.label: row.value for row in popup._collect_info_rows(stage, root)}

        self.assertEqual(values["Container"], "MSCU1234567")
        self.assertEqual(values["Type"], "20HC")
        self.assertEqual(values["Height"], "2.896 m")
        self.assertEqual(values["Task ID"], "TASK-001")
        self.assertEqual(values["State"], "CARRYING")
        self.assertEqual(values["Bay"], "3C/063")
        self.assertEqual(values["Row / Tier"], "R02 / T04 of 4")
        self.assertEqual(values["Locked"], "YES")

    def test_collect_info_uses_task_truck_context_for_loaded_box(self) -> None:
        root = "/World/LiveContainers/LiveTaskTruck/LoadedContainer"
        stage = Stage(
            {
                popup.STATUS_PRIM_PATH: Prim(
                    {
                        "rtg:containerNo": "TRUCK-BOX",
                        "rtg:containerType": "40GP",
                        "rtg:operationState": "TRUCK_WAITING",
                    }
                ),
                "/World/LiveContainers/LiveTaskTruck": Prim(
                    {
                        "live:taskId": "task-truck-001",
                        "live:workType": "LOAD",
                        "live:containerSizeFt": 40,
                        "live:containerHeightMeters": 2.896,
                    }
                ),
                root: Prim(
                    {
                        "live:bayId": "3C/063",
                        "live:row": 0,
                        "live:tier": 0,
                    }
                ),
            }
        )

        values = {row.label: row.value for row in popup._collect_info_rows(stage, root)}

        self.assertEqual(values["Container"], "TRUCK-BOX")
        self.assertEqual(values["Task ID"], "task-truck-001")
        self.assertEqual(values["Work Type"], "LOAD")
        self.assertEqual(values["Size"], "40 ft")
        self.assertEqual(values["Height"], "2.896 m")

    def test_container_panel_bitmap_renderer_draws_transparent_blue_panel(self) -> None:
        font_path = popup._find_cjk_font_path()
        if font_path is None:
            self.skipTest("CJK font unavailable")
        path = Path(tempfile.gettempdir()) / "smart_port_rtg_container_panel_test.png"

        rendered = popup._render_container_panel_bitmap(
            (
                popup.InfoRow("Container", "FFAU8892415"),
                popup.InfoRow("Size", "40 ft"),
                popup.InfoRow("Type", "GP"),
                popup.InfoRow("Height", "2.90 m"),
                popup.InfoRow("Bay", "4C/030"),
                popup.InfoRow("Row / Tier", "R05 / T02"),
                popup.InfoRow("Source", "task-bay-map"),
                popup.InfoRow("Task ID", "27130485"),
                popup.InfoRow("Locked", "NO"),
            ),
            path,
        )

        self.assertTrue(rendered)
        self.assertTrue(path.is_file())
        from PIL import Image  # noqa: PLC0415

        with Image.open(path) as image:
            self.assertEqual(
                image.size,
                (popup.CONTAINER_PANEL_WIDTH, popup.CONTAINER_PANEL_HEIGHT),
            )
            self.assertIsNotNone(image.getchannel("A").getbbox())


if __name__ == "__main__":
    unittest.main()
