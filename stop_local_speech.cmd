@echo off
setlocal
cd /d "%~dp0"
if not exist ".cache\local-speech\venv\Scripts\python.exe" exit /b 0
".cache\local-speech\venv\Scripts\python.exe" "services\local_speech\manage.py" stop
