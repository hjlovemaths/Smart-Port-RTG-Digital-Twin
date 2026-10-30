@echo off
setlocal
python "%~dp0omniverse\scripts\launch_kit_webrtc.py" %*
if errorlevel 1 pause
endlocal
