@echo off
setlocal
cd /d "%~dp0"
if not exist ".cache\local-speech\venv\Scripts\python.exe" (
  echo Run setup_local_speech.cmd first.
  exit /b 1
)
".cache\local-speech\venv\Scripts\python.exe" "services\local_speech\manage.py" start
