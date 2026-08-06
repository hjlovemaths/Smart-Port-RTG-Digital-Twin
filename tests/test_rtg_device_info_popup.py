from __future__ import annotations

import sys
import tempfile
import types
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

    def test_rtg_path_accepts_primary_rtg_under_alternate_parent(self) -> None:
        self.assertEqual(
            popup._rtg_root_path_from_selection(
                "/World/SomePayload/PortAndRTG/RTG_PRIMARY_DYNAMIC/Beam"
            ),
            "/World/SomePayload/PortAndRTG/RTG_PRIMARY_DYNAMIC",
        )

    def test_rtg_path_accepts_port_payload_root_selection(self) -> None:
        self.assertEqual(
            popup._rtg_root_path_from_selection("/World/PortAndRTG"),
            "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC",
        )
        self.assertEqual(
            popup._rtg_root_path_from_selection("/World/SomePayload/PortAndRTG"),
            "/World/SomePayload/PortAndRTG/RTG_PRIMARY_DYNAMIC",
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

    def test_device_panel_uses_scene_hud_without_omni_window_chrome(self) -> None:
        self.assertEqual(
            popup.DEVICE_SCENE_PANEL_ROOT_PATH,
            "/World/LiveHUD/RTG_DEVICE_LOCATION_PANEL",
        )
        self.assertFalse(
            popup.DEVICE_SCENE_PANEL_ROOT_PATH.startswith(
                "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/"
            )
        )
        self.assertEqual(
            popup.DEVICE_SCENE_PANEL_ROTATE_XYZ,
            (0.0, 0.0, 180.0),
        )
        self.assertAlmostEqual(popup.DEVICE_SCENE_PANEL_WIDTH_M, 6.24)
        self.assertAlmostEqual(popup.DEVICE_SCENE_PANEL_HEIGHT_M, 4.16)
        self.assertGreaterEqual(popup.DEVICE_SCENE_PANEL_WORLD_Z, 10.0)

    def test_device_hud_selection_maps_to_panel_root_for_dragging(self) -> None:
        self.assertTrue(
            popup._is_live_hud_selection_path(
                f"{popup.DEVICE_SCENE_PANEL_ROOT_PATH}/Panel"
            )
        )
        self.assertEqual(
            popup._device_hud_root_from_selection(
                f"{popup.DEVICE_SCENE_PANEL_ROOT_PATH}/Panel"
            ),
            popup.DEVICE_SCENE_PANEL_ROOT_PATH,
        )
        self.assertEqual(
            popup._device_hud_root_from_selection(
                f"{popup.DEVICE_SCENE_PANEL_RIGHT_ROOT_PATH}/Panel"
            ),
            popup.DEVICE_SCENE_PANEL_RIGHT_ROOT_PATH,
        )

    def test_stop_previous_builtin_service_clears_legacy_window_service(self) -> None:
        class Previous:
            def __init__(self):
                self.stopped = False

            def stop(self):
                self.stopped = True

        previous = Previous()
        legacy_location = Previous()
        old_value = getattr(popup.builtins, "_rtg_device_info_popup_service", None)
        old_location = getattr(
            popup.builtins,
            "_rtg_device_location_panel_service",
            None,
        )
        popup.builtins._rtg_device_info_popup_service = previous
        popup.builtins._rtg_device_location_panel_service = legacy_location
        try:
            popup._stop_previous_builtin_service()

            self.assertTrue(previous.stopped)
            self.assertTrue(legacy_location.stopped)
            self.assertIsNone(popup.builtins._rtg_device_info_popup_service)
            self.assertIsNone(popup.builtins._rtg_device_location_panel_service)
        finally:
            popup.builtins._rtg_device_info_popup_service = old_value
            popup.builtins._rtg_device_location_panel_service = old_location

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

    def test_device_panel_bitmap_renderer_draws_without_baked_template_values(self) -> None:
        font_path = popup._find_cjk_font_path()
        if font_path is None:
            self.skipTest("CJK font unavailable")
        path = Path(tempfile.gettempdir()) / "smart_port_rtg_device_panel_clean_test.png"

        rendered = popup._render_device_panel_bitmap(
            {
                "Device": "RTG_220",
                "Field": "4C",
                "Bay": "40",
                "Gantry": "436.95 m",
                "Trolley": "0.39 m",
                "Hoist": "4.69 m",
            },
            path,
        )

        self.assertTrue(rendered)
        self.assertTrue(path.is_file())
        from PIL import Image  # noqa: PLC0415

        with Image.open(path) as image:
            self.assertEqual(
                image.size,
                (popup.DEVICE_PANEL_WIDTH, popup.DEVICE_PANEL_HEIGHT),
            )
            self.assertIsNotNone(image.getchannel("A").getbbox())
            self.assertGreaterEqual(image.getpixel((80, 120))[3], 240)
            self.assertGreaterEqual(image.getpixel((80, 44))[3], 240)

    def test_update_poll_keeps_device_scene_panel_facing_camera(self) -> None:
        service = popup.RtgDeviceInfoPopupService.__new__(
            popup.RtgDeviceInfoPopupService
        )
        calls = []
        service._handle_selected_paths = (
            lambda paths, source="selection": calls.append(("selection", paths, source))
        )
        service._sync_visible_scene_panel_facing_camera = (
            lambda: calls.append(("facing",))
        )
        previous_selected_paths = popup._selected_paths
        popup._selected_paths = lambda: [popup.DEVICE_SCENE_PANEL_ROOT_PATH]
        try:
            service._on_update()
        finally:
            popup._selected_paths = previous_selected_paths

        self.assertEqual(
            calls,
            [
                ("selection", [popup.DEVICE_SCENE_PANEL_ROOT_PATH], "poll"),
                ("facing",),
            ],
        )

    def test_device_service_keeps_facing_control_while_live_driver_runs(self) -> None:
        service = popup.RtgDeviceInfoPopupService.__new__(
            popup.RtgDeviceInfoPopupService
        )
        service._last_update_poll_at = 0.0
        calls = []
        service._handle_selected_paths = (
            lambda paths, source="selection": calls.append((paths, source))
        )
        service._sync_visible_scene_panel_facing_camera = (
            lambda: calls.append(("facing",))
        )
        old_owner = getattr(
            popup.builtins,
            "_smart_port_rtg_panel_facing_driver",
            None,
        )
        popup.builtins._smart_port_rtg_panel_facing_driver = object()
        old_selected_paths = popup._selected_paths
        popup._selected_paths = lambda: [popup.DEVICE_SCENE_PANEL_ROOT_PATH]
        try:
            service._on_update()
        finally:
            popup._selected_paths = old_selected_paths
            popup.builtins._smart_port_rtg_panel_facing_driver = old_owner

        self.assertEqual(
            calls,
            [
                ([popup.DEVICE_SCENE_PANEL_ROOT_PATH], "poll"),
                ("facing",),
            ],
        )

    def test_visible_scene_panel_facing_updates_all_device_panel_roots(self) -> None:
        class FakeRoot:
            def __init__(self, path: str) -> None:
                self.path = path

            @staticmethod
            def AddRotateXYZOp():
                return None

            @staticmethod
            def SetXformOpOrder(_ops):
                pass

        class TranslateOp:
            @staticmethod
            def Get():
                return (1.0, 2.0, 3.0)

        class RotateOp:
            def __init__(self, path: str) -> None:
                self.path = path

            def Set(self, value):
                rotations.append((self.path, value))

        roots = {
            root_path: FakeRoot(root_path)
            for root_path in popup.DEVICE_SCENE_PANEL_ROOT_PATHS
        }
        rotations = []
        stage = types.SimpleNamespace(GetPrimAtPath=lambda path: roots.get(path))
        fake_pxr = types.ModuleType("pxr")
        fake_pxr.Gf = types.SimpleNamespace(Vec3f=lambda *values: tuple(values))
        fake_pxr.UsdGeom = types.SimpleNamespace(Xformable=lambda root: root)

        old_current_stage = popup._current_stage
        old_existing_xform_op = popup.existing_xform_op
        old_get_or_add_xform_op = popup.get_or_add_xform_op
        old_camera_facing_rotate_xyz = popup.camera_facing_rotate_xyz
        popup._current_stage = lambda: stage
        popup.existing_xform_op = lambda _xform, _name: TranslateOp()
        popup.get_or_add_xform_op = (
            lambda xform, _name, _add_op: RotateOp(xform.path)
        )
        popup.camera_facing_rotate_xyz = (
            lambda _stage, _translate, _fallback: (4.0, 5.0, 6.0)
        )
        previous_pxr = sys.modules.get("pxr")
        sys.modules["pxr"] = fake_pxr
        try:
            service = popup.RtgDeviceInfoPopupService.__new__(
                popup.RtgDeviceInfoPopupService
            )
            service._sync_visible_scene_panel_facing_camera()
        finally:
            popup._current_stage = old_current_stage
            popup.existing_xform_op = old_existing_xform_op
            popup.get_or_add_xform_op = old_get_or_add_xform_op
            popup.camera_facing_rotate_xyz = old_camera_facing_rotate_xyz
            if previous_pxr is None:
                sys.modules.pop("pxr", None)
            else:
                sys.modules["pxr"] = previous_pxr

        self.assertEqual(
            rotations,
            [
                (popup.DEVICE_SCENE_PANEL_ROOT_PATH, (4.0, 5.0, 6.0)),
                (popup.DEVICE_SCENE_PANEL_RIGHT_ROOT_PATH, (4.0, 5.0, 6.0)),
            ],
        )

    def test_clear_selection_gizmo_removes_default_selection_outline(self) -> None:
        selected_paths = [popup.PRIMARY_RTG_PATH]
        cleared = []

        class Selection:
            def clear_selected_prim_paths(self):
                selected_paths.clear()
                cleared.append(True)

            def set_selected_prim_paths(self, paths, _expand):
                selected_paths[:] = list(paths)

        omni_module = types.ModuleType("omni")
        usd_module = types.ModuleType("omni.usd")
        usd_module.get_context = lambda: types.SimpleNamespace(
            get_selection=lambda: Selection()
        )
        omni_module.usd = usd_module
        previous_omni = sys.modules.get("omni")
        previous_usd = sys.modules.get("omni.usd")
        sys.modules["omni"] = omni_module
        sys.modules["omni.usd"] = usd_module
        try:
            service = popup.RtgDeviceInfoPopupService.__new__(
                popup.RtgDeviceInfoPopupService
            )
            service._clearing_selection = False
            service._last_selection_signature = ()

            service._clear_selection_gizmo()

            self.assertEqual(selected_paths, [])
            self.assertEqual(cleared, [True])
            self.assertEqual(service._last_selection_signature, ())
            self.assertFalse(service._clearing_selection)
        finally:
            if previous_omni is None:
                sys.modules.pop("omni", None)
            else:
                sys.modules["omni"] = previous_omni
            if previous_usd is None:
                sys.modules.pop("omni.usd", None)
            else:
                sys.modules["omni.usd"] = previous_usd


if __name__ == "__main__":
    unittest.main()
