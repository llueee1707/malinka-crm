@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%restart_server.ps1" -ListenHost 0.0.0.0 -Port 8000 -Reload
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Radmin start failed with exit code %EXIT_CODE%.
    pause
)

exit /b %EXIT_CODE%
