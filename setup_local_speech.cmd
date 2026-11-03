@echo off
setlocal
cd /d "%~dp0"
if not exist ".cache\local-speech\venv\Scripts\python.exe" (
  py -3.11 -m venv ".cache\local-speech\venv"
  if errorlevel 1 exit /b 1
)
".cache\local-speech\venv\Scripts\python.exe" -m pip install -r "services\local_speech\requirements.txt"
if errorlevel 1 exit /b 1
".cache\local-speech\venv\Scripts\python.exe" "services\local_speech\download_models.py"
if errorlevel 1 exit /b 1
".cache\local-speech\venv\Scripts\python.exe" "services\local_speech\manage.py" start
