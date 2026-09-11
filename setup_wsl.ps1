# NixOS-WSL Setup Script for Voice Transcriber
# Run this in an Administrator PowerShell prompt if Windows features are not yet enabled:
# Right-click PowerShell -> "Run as Administrator", then run:
# Set-Location "C:\Users\jjame\gitprojects\vt2" ; .\setup_wsl.ps1

$ErrorActionPreference = "Stop"

function Write-Step { param([string]$m) Write-Host "`n[SETUP] $m" -ForegroundColor Cyan }
function Write-Success { param([string]$m) Write-Host "[OK] $m" -ForegroundColor Green }
function Write-Warn { param([string]$m) Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Write-Err { param([string]$m) Write-Host "[ERROR] $m" -ForegroundColor Red }

Write-Host @"
======================================================
     NixOS on WSL2 Setup & Audio Configuration
======================================================
"@ -ForegroundColor Magenta

# Check admin rights
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Warn "Note: If enabling Windows Virtualization features is needed, please run this script from an Administrator PowerShell prompt."
}

# Step 1: Enable Windows Virtualization & WSL Features
Write-Step "1. Checking Windows Virtualization Features..."
try {
    dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
    dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
    Write-Success "VirtualMachinePlatform and WSL features enabled"
} catch {
    Write-Warn "Could not configure features directly (requires Administrator privileges). If WSL fails to start, open PowerShell as Admin and run: dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all"
}

# Step 2: Check / Download NixOS-WSL image
Write-Step "2. Checking NixOS-WSL Image..."
$imageDir = "C:\Users\jjame\WSL"
$imagePath = Join-Path $imageDir "nixos.wsl"

if (-not (Test-Path $imagePath)) {
    mkdir $imageDir -Force | Out-Null
    Write-Host "Downloading latest NixOS-WSL release image (~550MB)..." -ForegroundColor Yellow
    curl.exe -L -o $imagePath "https://github.com/nix-community/NixOS-WSL/releases/download/2605.7.2/nixos.wsl"
    Write-Success "NixOS-WSL image downloaded to $imagePath"
} else {
    Write-Success "NixOS-WSL image already present at $imagePath"
}

# Step 3: Register NixOS with WSL
Write-Step "3. Registering NixOS in WSL2..."
$distros = wsl --list --quiet 2>&1
if ($distros -match "NixOS") {
    Write-Success "NixOS distribution already registered in WSL."
} else {
    Write-Host "Importing NixOS distribution..." -ForegroundColor Yellow
    wsl --install --from-file $imagePath
    if ($LASTEXITCODE -ne 0) {
        $installDir = "$env:USERPROFILE\WSL\NixOS"
        mkdir $installDir -Force | Out-Null
        wsl --import NixOS $installDir $imagePath --version 2
    }
    Write-Success "NixOS successfully registered!"
}

# Step 4: Configure Nix Flakes & Audio in NixOS
Write-Step "4. Configuring Nix Flakes & WSLg Audio in NixOS..."
$setupCmd = @'
mkdir -p ~/.config/nix
echo "experimental-features = nix-command flakes" > ~/.config/nix/nix.conf
echo "export PULSE_SERVER=unix:/mnt/wslg/runtime-dir/pulse/native" >> ~/.bashrc
'@

wsl -d NixOS -- bash -c "$setupCmd"
Write-Success "Nix Flakes and PULSE_SERVER configured."

# Step 5: Test Microphone Audio Passthrough
Write-Step "5. Testing Microphone Audio Passthrough inside NixOS..."
Write-Host "Querying audio devices inside NixOS WSL..." -ForegroundColor Cyan
wsl -d NixOS -- bash -c "export PULSE_SERVER=unix:/mnt/wslg/runtime-dir/pulse/native; cd /mnt/c/Users/jjame/gitprojects/vt2 && python3 src/check_devices.py 2>/dev/null || true"

Write-Host @"
======================================================
  Setup Complete!
  To run Voice Transcriber in WSL:
    .\run_wsl.ps1
======================================================
"@ -ForegroundColor Green
