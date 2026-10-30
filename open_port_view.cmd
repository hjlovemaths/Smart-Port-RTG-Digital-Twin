@echo off
setlocal
set "PORT_VIEW_EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%PORT_VIEW_EDGE%" set "PORT_VIEW_EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if exist "%PORT_VIEW_EDGE%" (
    start "" "%PORT_VIEW_EDGE%" --app="http://localhost:8011/" --start-fullscreen
) else (
    start "" "http://localhost:8011/"
)
endlocal
