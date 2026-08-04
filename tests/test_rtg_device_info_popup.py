from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_ROOT / "omniverse" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import rtg_device_info_popup as popup  # noqa: E402


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


class DeviceInfoPopupTests(unittest.TestCase):
    def test_rtg_path_accepts_any_primary_rtg_child(self) -> None:
        self.assertEqual(
            popup._rtg_root_path_from_selection(
                "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/"
                "ANIM_CTRL_RTG_GANTRY_TRAVEL/lower_portal_crossbeam_x_5_8"
            ),
            "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC",
        )

    def test_rtg_path_rejects_non_rtg_scene_elements(self) -> None:
        self.assertIsNone(
            popup._rtg_root_path_from_selection(
                "/World/LiveContainers/YardInventory/Bay_1C_021/R05_T01_Container"
            )
        )
        self.assertIsNone(
            popup._rtg_root_path_from_selection(
                "/World/PortAndRTG/Omniverse_User_View/Omniverse_User_View_data"
            )
        )

    def test_collect_device_rows_mirrors_hud_device_values(self) -> None:
        selected = (
            "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/"
            "ANIM_CTRL_RTG_GANTRY_TRAVEL/beam"
        )
        stage = Stage(
            {
                popup.STATUS_PRIM_PATH: Prim(
                    {
                        "rtg:rtgName": "RTG_220",
                        "rtg:manualPayload": "/World/LiveContainers/Payload",
                        "rtg:currentField": "3C",
                        "rtg:currentBay": 63,
                        "rtg:currentBayId": "3C/063",
                        "rtg:controlMode": "REMOTE_MANUAL",
                        "rtg:gantryMeter": 165.234,
                        "rtg:trolleyMeter": 12.35,
                        "rtg:hoistMeter": 8.0,
                        "rtg:spreaderSizeFt": 20.0,
                        "rtg:spreaderLocked": True,
                    }
                )
            }
        )

        values = {
            row.label: row.value
            for row in popup._collect_device_info_rows(stage, selected)
        }

        self.assertEqual(values["Device"], "RTG_220")
        self.assertEqual(values["Payload"], "/World/LiveContainers/Payload")
        self.assertEqual(values["Bay ID"], "3C/063")
        self.assertEqual(values["Mode"], "REMOTE_MANUAL")
        self.assertEqual(values["Gantry"], "165.23 m")
        self.assertEqual(values["Trolley"], "12.35 m")
        self.assertEqual(values["Hoist"], "8.00 m")
        self.assertEqual(values["Tool Size"], "20 ft")
        self.assertEqual(values["Locked"], "YES")
        self.assertEqual(values["Selected"], selected)


if __name__ == "__main__":
    unittest.main()
