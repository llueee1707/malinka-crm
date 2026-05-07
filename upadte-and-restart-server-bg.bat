@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

where git >nul 2>nul
if errorlevel 1 (
    echo Git is not installed or is not available in PATH.
    pause
    exit /b 1
)

if not exist "restart_server_bg.bat" (
    echo restart_server_bg.bat was not found in %SCRIPT_DIR%.
    pause
    exit /b 1
)

echo Updating project from origin/main...
git fetch origin main
if errorlevel 1 goto fail

git checkout main
if errorlevel 1 goto fail

git pull --ff-only origin main
if errorlevel 1 goto fail

if exist ".venv\Scripts\python.exe" (
    if exist "requirements.txt" (
        echo Installing updated requirements...
        ".venv\Scripts\python.exe" -m pip install -r requirements.txt
        if errorlevel 1 goto fail
    )
) else (
    echo .venv was not found. Skipping requirements install.
)

echo.
echo Update complete. Restarting server in background...
call "%SCRIPT_DIR%restart_server_bg.bat"
exit /b %ERRORLEVEL%

:fail
echo.
echo Update failed. Server was not restarted.
pause
exit /b 1
