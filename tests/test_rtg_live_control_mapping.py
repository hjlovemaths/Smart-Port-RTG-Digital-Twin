from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_ROOT / "omniverse" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


def _load_live_control_mapping_module() -> types.ModuleType:
    """Load coordinate helpers with the minimal USD stubs needed in unit tests."""

    fake_pxr = types.ModuleType("pxr")
    fake_pxr.Gf = types.SimpleNamespace(Vec3d=lambda *values: tuple(values))
    fake_pxr.Usd = types.SimpleNamespace()
    fake_pxr.UsdGeom = types.SimpleNamespace()
    fake_pxr.UsdPhysics = types.SimpleNamespace()
    module_path = SCRIPT_DIR / "rtg_live_control.py"
    module = types.ModuleType("rtg_live_control_mapping_test")
    module.__file__ = str(module_path)
    with patch.dict(sys.modules, {"pxr": fake_pxr}):
        exec(compile(module_path.read_text(encoding="utf-8"), str(module_path), "exec"), module.__dict__)
    return module


class GantryBayMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.live_control = _load_live_control_mapping_module()

    def test_40ft_even_bay_applies_the_lower_crossbeam_fine_offset(self) -> None:
        mapping = self.live_control
        short_bay_y = mapping.gantry_bay_to_usd("3C", 66, container_size_ft=20)
        long_bay_y = mapping.gantry_bay_to_usd("3C", 66, container_size_ft=40)

        self.assertAlmostEqual(
            long_bay_y - short_bay_y,
            mapping.RTG_40FT_BAY_CENTERLINE_FINE_OFFSET_Y_M,
        )

    def test_40ft_lower_crossbeam_line_lands_on_the_even_bay_center(self) -> None:
        mapping = self.live_control
        controller_y = mapping.gantry_bay_to_usd(
            "3C", 66, container_size_ft=40
        )

        self.assertAlmostEqual(
            controller_y + mapping.RTG_LOWER_PORTAL_CROSSBEAM_CENTER_LOCAL_Y_M,
            mapping.bay_scene_y_m("3C", 66),
        )
