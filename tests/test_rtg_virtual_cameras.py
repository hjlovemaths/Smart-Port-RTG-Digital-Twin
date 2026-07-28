from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_ROOT / "omniverse" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import rtg_virtual_cameras as virtual_cameras  # noqa: E402


class VirtualCameraMarkerTests(unittest.TestCase):
    def test_marker_number_accepts_rtg_camera_marker_names(self) -> None:
        self.assertEqual(virtual_cameras._marker_number("C1"), 1)
        self.assertEqual(virtual_cameras._marker_number("C33"), 33)
        self.assertEqual(virtual_cameras._marker_number("Camera_C4"), 4)
        self.assertEqual(virtual_cameras._marker_number("CameraMarker_C7"), 7)
        self.assertEqual(virtual_cameras._marker_number("CAM_C12_mesh"), 12)

    def test_marker_number_rejects_non_marker_names(self) -> None:
        self.assertIsNone(virtual_cameras._marker_number("Container_C12"))
        self.assertIsNone(virtual_cameras._marker_number("RTG_VIRTUAL_CAMERAS"))
        self.assertIsNone(virtual_cameras._marker_number("Beam"))

    def test_virtual_camera_path_uses_independent_root(self) -> None:
        self.assertEqual(
            virtual_cameras._virtual_camera_path(1),
            "/World/RTGVirtualCameras/C01",
        )

    def test_generated_camera_paths_are_excluded_from_marker_scan(self) -> None:
        self.assertTrue(
            virtual_cameras._is_generated_virtual_camera_path(
                "/World/RTG/C1/VirtualCamera"
            )
        )
        self.assertFalse(
            virtual_cameras._is_generated_virtual_camera_path("/World/RTG/C1")
        )
        self.assertTrue(
            virtual_cameras._is_generated_virtual_camera_path(
                "/World/RTGVirtualCameras/C01"
            )
        )


if __name__ == "__main__":
    unittest.main()
