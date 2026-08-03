from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_ROOT / "omniverse" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import rtg_camera_popup as popup  # noqa: E402


class CameraPopupTests(unittest.TestCase):
    def test_spreader_grid_matches_reference_video_orientation(self) -> None:
        self.assertEqual(popup.SPREADER_CAMERA_GRID_LAYOUT, ((1, 2), (4, 3)))

    def test_marker_number_accepts_common_usd_names(self) -> None:
        self.assertEqual(popup._marker_number("/World/RTG/C1"), 1)
        self.assertEqual(popup._marker_number("/World/RTG/C1/geometry"), 1)
        self.assertEqual(popup._marker_number("/World/RTG/Camera_C4"), 4)
        self.assertEqual(popup._marker_number("/World/RTG/C12_mesh"), 12)
        self.assertIsNone(popup._marker_number("/World/Yard/Container_C12"))
        self.assertIsNone(popup._marker_number("/World/RTG/Beam"))
        self.assertEqual(popup._selected_marker_number("/World/RTG/CameraMarker_C1/mesh"), 1)

    def test_config_builds_axis_rtsp_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Cam_RTG_220.json"
            path.write_text(
                json.dumps(
                    {
                        "CameraList": [
                            {
                                "Seq": 1,
                                "IP": "10.41.36.48",
                                "Port": 8000,
                                "RtspPort": 8000,
                                "UserName": "admin",
                                "Password": "p@ss",
                                "AttachedLine": "1",
                                "Brand": "Axis",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            cameras = popup._read_camera_config(path)

        self.assertEqual(cameras[1].attached_line, "1")
        self.assertEqual(
            cameras[1].rtsp_url,
            "rtsp://admin:p%40ss@10.41.36.48:8000/axis-media/media.amp?camera=1",
        )

    def test_config_builds_haikang_rtsp_url(self) -> None:
        camera = popup.CameraDefinition(
            seq=1,
            ip="192.168.2.64",
            port=8000,
            username="admin",
            password="secret",
            attached_line="1",
            brand="HaiKang",
        )
        self.assertEqual(
            camera.rtsp_url,
            "rtsp://admin:secret@192.168.2.64:554/Streaming/Channels/101",
        )


if __name__ == "__main__":
    unittest.main()
