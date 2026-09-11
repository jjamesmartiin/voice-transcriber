@echo off
:: Enable Virtual Machine Platform and WSL Features
echo ========================================================
echo   Enabling Windows Virtual Machine Platform for WSL2
echo ========================================================
echo.

:: Check for administrator privileges
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [!] Administrator privileges required.
    echo Please right-click this file and select "Run as administrator".
    echo.
    pause
    exit /b 1
)

echo [1/3] Enabling VirtualMachinePlatform...
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart

echo [2/3] Enabling Microsoft-Windows-Subsystem-Linux...
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart

echo.
echo [3/3] Importing NixOS distribution...
wsl.exe --install --from-file "C:\Users\jjame\WSL\nixos.wsl"

echo.
echo ========================================================
echo   Setup Complete! 
echo   If WSL requires a reboot, please restart your PC.
echo ========================================================
pause
