@echo off
setlocal
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\platforms\wsl\run_wsl.ps1" %*
if errorlevel 1 (
    echo.
    echo [!] The WSL launcher exited with an error - see the messages above.
    pause
)
