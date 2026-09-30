@echo off
setlocal
cd /d "%~dp0..\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
if errorlevel 1 (
    echo.
    echo [!] The launcher exited with an error - see the messages above.
    echo     Log [if enabled]: %LOCALAPPDATA%\vt\vt.log
    pause
)
