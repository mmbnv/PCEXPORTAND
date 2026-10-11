@echo off
REM Start PortAgent from the bundle (Windows).
REM   START.bat          -> interactive console menu
REM   START.bat --gui    -> desktop GUI window (Tkinter)
setlocal
set "BUNDLE_DIR=%~dp0"

if not exist "%BUNDLE_DIR%env.bat" (
    echo env.bat missing -- running install.bat first ...
    call "%BUNDLE_DIR%install.bat" || exit /b 1
)

call "%BUNDLE_DIR%env.bat"

where python >nul 2>nul
if errorlevel 1 (
    echo python not found. Install Python 3.10+ and re-run. 1>&2
    exit /b 1
)

cd /d "%BUNDLE_DIR%"
python "%BUNDLE_DIR%run.py" %*
endlocal
