from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICES_DIR = PROJECT_ROOT / "services"
if str(SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICES_DIR))

from rtg_twin_service import TwinStateCache  # noqa: E402


class RunInfoTimespanTests(unittest.TestCase):
    def test_runinfo_timespan_is_forwarded_to_twin_state(self) -> None:
        cache = TwinStateCache("RTG_220", 3.0, 1800.0)

        cache.update_runinfo(
            {
                "msg_descption": "作业完成",
                "timespan": "2026-08-04T15:30:45Z",
            }
        )

        runinfo = cache.build_state()["runinfo"]

        self.assertEqual(runinfo["message"], "作业完成")
        self.assertEqual(runinfo["timespan"], "2026-08-04T15:30:45Z")


if __name__ == "__main__":
    unittest.main()
