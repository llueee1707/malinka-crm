@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%restart_server.ps1" -Reload %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Server restart failed with exit code %EXIT_CODE%.
    pause
)

exit /b %EXIT_CODE%
