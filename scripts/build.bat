@echo off
setlocal
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\platforms\windows\build.ps1" %*
if errorlevel 1 (
    echo.
    echo [!] Build failed - see the messages above.
    pause
)
