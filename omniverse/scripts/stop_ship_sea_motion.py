"""Stop old/new sea controllers without opening or animating another one."""
import runpy
from pathlib import Path

module=runpy.run_path(str(Path(__file__).with_name('ship_sea_motion.py')))
module['stop_sea_motion']()
