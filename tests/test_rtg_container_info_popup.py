from __future__ import annotations

import sys
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


if __name__ == "__main__":
    unittest.main()
