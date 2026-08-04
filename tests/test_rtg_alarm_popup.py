from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_ROOT / "omniverse" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import rtg_alarm_popup as popup  # noqa: E402


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


class AlarmPopupTests(unittest.TestCase):
    def tearDown(self) -> None:
        popup.clear_rtg_alarm()

    def test_collect_alarm_payload_uses_status_prim(self) -> None:
        stage = Stage(
            {
                popup.STATUS_PRIM_PATH: Prim(
                    {
                        "rtg:alarmStatus": 1,
                        "rtg:alarmMessage": "大车防撞报警！",
                        "rtg:alarmSeverity": "CRITICAL",
                    }
                )
            }
        )

        payload = popup._collect_alarm_payload(stage, now=100.0)

        self.assertTrue(payload.active)
        self.assertEqual(payload.message, "大车防撞报警！")
        self.assertEqual(payload.severity, "CRITICAL")

    def test_collect_alarm_payload_inactive_without_status(self) -> None:
        stage = Stage({popup.STATUS_PRIM_PATH: Prim({"rtg:alarmStatus": 0})})

        payload = popup._collect_alarm_payload(stage, now=100.0)

        self.assertFalse(payload.active)

    def test_manual_alarm_expires_after_duration(self) -> None:
        import builtins

        builtins.__dict__[popup.ALARM_MANUAL_PAYLOAD_KEY] = {
            "message": "大车防撞报警！",
            "severity": "ALARM",
            "startedAt": 100.0,
            "durationSeconds": 2.0,
        }

        self.assertIsNotNone(popup._manual_alarm_payload(now=101.0))
        self.assertIsNone(popup._manual_alarm_payload(now=103.1))

    def test_alarm_bitmap_renders_chinese_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alarm.png"

            ok = popup._render_alarm_bitmap("大车防撞报警！", path)

            self.assertTrue(ok)
            self.assertTrue(path.exists())
            self.assertGreater(path.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
