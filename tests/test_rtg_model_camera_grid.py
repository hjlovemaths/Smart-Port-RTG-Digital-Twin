from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_ROOT / "omniverse" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import rtg_model_camera_grid as model_grid  # noqa: E402


class ModelCameraGridTests(unittest.TestCase):
    def test_layout_matches_reference_video(self) -> None:
        self.assertEqual(model_grid.CAMERA_LAYOUT, ((1, 2), (4, 3)))

    def test_viewport_positions_cover_four_quadrants(self) -> None:
        self.assertEqual(
            [model_grid.CAMERA_POSITIONS[seq] for seq in (1, 2, 4, 3)],
            [(40, 80), (680, 80), (40, 450), (680, 450)],
        )


if __name__ == "__main__":
    unittest.main()
