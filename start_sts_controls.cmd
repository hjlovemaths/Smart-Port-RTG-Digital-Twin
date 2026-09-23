@echo off
setlocal
set "STS_PROJECT_ROOT=%~dp0"
if not exist "D:\isaacsim\python.bat" (
    echo Isaac Sim Python was not found at D:\isaacsim\python.bat
    pause
    exit /b 1
)
call "D:\isaacsim\python.bat" "%STS_PROJECT_ROOT%omniverse\scripts\launch_isaac_sim_project.py" --enable-sts-controls %*
if errorlevel 1 pause
endlocal
