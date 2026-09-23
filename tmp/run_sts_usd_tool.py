import os
import sys
import runpy
from pathlib import Path
runtime = Path(r'D:\software\kit-app-template\_build\windows-x86_64\release\extscache\omni.usd.libs-1.0.3+698af100.wx64.r.cp312')
dll = os.add_dll_directory(str(runtime / 'bin'))
sys.path.insert(0, str(runtime))
sys.path.insert(0, str(Path('omniverse/scripts').resolve()))
script = sys.argv.pop(1)
runpy.run_path(script, run_name='__main__')
