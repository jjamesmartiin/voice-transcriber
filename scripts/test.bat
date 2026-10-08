@echo off
setlocal
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\platforms\windows\test.ps1" %*
if errorlevel 1 (
    echo.
    echo [!] Tests reported a failure - see the messages above.
    pause
)
