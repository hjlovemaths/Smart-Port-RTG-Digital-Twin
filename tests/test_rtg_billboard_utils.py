from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_ROOT / "omniverse" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import rtg_billboard_utils as billboard  # noqa: E402


class BillboardUtilsTests(unittest.TestCase):
    def test_z_rotation_faces_camera_from_y_axis_sides(self) -> None:
        self.assertAlmostEqual(
            billboard.billboard_z_rotation_degrees((0.0, 0.0, 0.0), (0.0, 10.0, 3.0)),
            0.0,
        )
        self.assertAlmostEqual(
            billboard.billboard_z_rotation_degrees((0.0, 0.0, 0.0), (0.0, -10.0, 3.0)),
            180.0,
        )

    def test_z_rotation_faces_camera_from_x_axis_sides(self) -> None:
        self.assertAlmostEqual(
            billboard.billboard_z_rotation_degrees((0.0, 0.0, 0.0), (10.0, 0.0, 3.0)),
            270.0,
        )
        self.assertAlmostEqual(
            billboard.billboard_z_rotation_degrees((0.0, 0.0, 0.0), (-10.0, 0.0, 3.0)),
            90.0,
        )

    def test_z_rotation_uses_fallback_when_positions_overlap(self) -> None:
        self.assertAlmostEqual(
            billboard.billboard_z_rotation_degrees(
                (2.0, 3.0, 1.0),
                (2.0, 3.0, 9.0),
                fallback_degrees=180.0,
            ),
            180.0,
        )

    def test_active_camera_position_prefers_realtime_viewport_transform(self) -> None:
        class Matrix:
            def ExtractTranslation(self):
                return (12.0, -5.0, 9.0)

        class Viewport:
            transform = Matrix()
            camera_path = "/World/Camera"

        class Stage:
            def GetPrimAtPath(self, _path):
                raise AssertionError("camera_path fallback should not be used")

        with (
            patch.object(billboard, "_active_viewport", return_value=Viewport()),
            patch.object(billboard, "_active_viewport_window_api", return_value=None),
        ):
            self.assertEqual(
                billboard.active_viewport_camera_position(Stage()),
                (12.0, -5.0, 9.0),
            )

    def test_active_camera_position_can_use_inverse_view_matrix(self) -> None:
        class WorldMatrix:
            def ExtractTranslation(self):
                return (3.0, 4.0, 5.0)

        class ViewMatrix:
            def GetInverse(self):
                return WorldMatrix()

        class Viewport:
            transform = None
            view = ViewMatrix()

        with (
            patch.object(billboard, "_active_viewport", return_value=Viewport()),
            patch.object(billboard, "_active_viewport_window_api", return_value=None),
        ):
            self.assertEqual(
                billboard.active_viewport_camera_position(None),
                (3.0, 4.0, 5.0),
            )

    def test_cached_camera_position_can_drive_multiple_panels(self) -> None:
        self.assertEqual(
            billboard.camera_facing_rotate_xyz_from_camera(
                (0.0, 0.0, 0.0),
                (-10.0, 0.0, 5.0),
                (0.0, 0.0, 180.0),
            ),
            (0.0, 0.0, 90.0),
        )


if __name__ == "__main__":
    unittest.main()
