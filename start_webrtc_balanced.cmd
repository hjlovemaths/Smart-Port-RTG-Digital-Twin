@echo off
setlocal
python "%~dp0omniverse\scripts\launch_kit_webrtc.py" --width 1920 --height 1080 --fps 30 %*
if errorlevel 1 pause
endlocal
