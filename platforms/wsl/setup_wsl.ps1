# Voice Transcriber - WSL host setup.
#
# Registers/selects a WSL distribution and configures WSLg audio + (when the
# guest has Nix) flakes. It does NOT install the app's Python env or the model -
# after this, use `run_wsl.bat setup` which runs the guest's own ./setup.sh and
# picks Nix or the native apt/venv path.
#
#   setup_wsl.ps1                          register/import NixOS (recommended)
#   setup_wsl.ps1 -Distro Ubuntu           configure an existing Ubuntu guest
#
# Run from an Administrator PowerShell if Windows features are not yet enabled.
param(
    [string]$Distro
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\common\common.ps1")

Write-Host @"
======================================================
   Voice Transcriber (VT) - WSL Setup
======================================================
"@ -ForegroundColor Magenta

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Warn "Enabling Windows virtualization features needs an Administrator prompt."
}

# 1. Enable the WSL Windows features (no-op if already enabled).
Write-Step "1. Checking Windows virtualization features"
try {
    dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart | Out-Null
    dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart | Out-Null
    Write-Success "VirtualMachinePlatform and WSL features enabled"
} catch {
    Write-Warn "Could not configure features (needs Administrator). If WSL fails, run:"
    Write-Host "  dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all" -ForegroundColor Yellow
}

function Get-WslDistros {
    @(wsl --list --quiet 2>&1 |
        ForEach-Object { "$_".Trim() } |
        Where-Object { $_ -and $_ -notmatch "^\x00+$" })
}

# 2. Select or install a distribution.
Write-Step "2. Selecting a WSL distribution"
$distros = Get-WslDistros
if ($Distro) {
    if ($distros -notcontains $Distro) {
        Write-Err "Distribution '$Distro' is not registered."
        Write-Host "Install it first, e.g.: wsl --install -d $Distro" -ForegroundColor Yellow
        exit 1
    }
    Write-Success "Using existing distribution: $Distro"
} elseif ($distros -contains "NixOS") {
    $Distro = "NixOS"
    Write-Success "Using existing distribution: NixOS"
} elseif ($distros.Count -ge 1) {
    $Distro = $distros[0]
    Write-Success "Using existing distribution: $Distro"
} else {
    # No distro at all: import NixOS-WSL (the recommended guest).
    $Distro = "NixOS"
    $imageDir = Join-Path $env:USERPROFILE "WSL"
    $imagePath = Join-Path $imageDir "nixos.wsl"
    if (-not (Test-Path $imagePath)) {
        New-Item -ItemType Directory -Force -Path $imageDir | Out-Null
        Write-Host "Downloading the NixOS-WSL image (~550 MB)..." -ForegroundColor Yellow
        curl.exe -L -o $imagePath "https://github.com/nix-community/NixOS-WSL/releases/download/2605.7.2/nixos.wsl"
    }
    if (-not (Test-Path $imagePath)) {
        Write-Err "Could not download $imagePath"
        exit 1
    }
    Write-Host "Importing NixOS..." -ForegroundColor Yellow
    wsl --install --from-file $imagePath
    if ($LASTEXITCODE -ne 0) {
        $installDir = "$env:USERPROFILE\WSL\NixOS"
        New-Item -ItemType Directory -Force -Path $installDir | Out-Null
        wsl --import NixOS $installDir $imagePath --version 2
    }
    Write-Success "NixOS registered."
}

# 3. Configure the guest: WSLg audio, and flakes when Nix is present.
Write-Step "3. Configuring the guest (audio + Nix flakes if available)"
$guestConfig = @'
set -e
grep -q PULSE_SERVER ~/.bashrc 2>/dev/null || \
    echo "export PULSE_SERVER=unix:/mnt/wslg/runtime-dir/pulse/native" >> ~/.bashrc
export PULSE_SERVER=unix:/mnt/wslg/runtime-dir/pulse/native
if command -v nix >/dev/null 2>&1; then
    mkdir -p ~/.config/nix
    echo "experimental-features = nix-command flakes" > ~/.config/nix/nix.conf
    echo "Nix detected: flakes enabled."
else
    echo "No Nix in this guest - the native apt/venv path will be used."
fi
'@
wsl -d $Distro -- bash -lc $guestConfig
if ($LASTEXITCODE -ne 0) {
    Write-Warn "Guest configuration reported an error (see above)."
}

Write-Host @"

======================================================
  WSL Setup Complete ($Distro)

  Next, install the app + model inside the guest:
    run_wsl.bat setup

  Then launch:
    run_wsl.bat
======================================================
"@ -ForegroundColor Green
