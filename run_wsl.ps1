# Voice Transcriber - NixOS WSL Launcher
# Run: .\run_wsl.ps1

$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
if (-not $ProjectRoot) { $ProjectRoot = Get-Location }

function Write-Step { param([string]$m) Write-Host "`n[WSL] $m" -ForegroundColor Cyan }
function Write-Success { param([string]$m) Write-Host "[OK] $m" -ForegroundColor Green }
function Write-Warn { param([string]$m) Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Write-Err { param([string]$m) Write-Host "[ERROR] $m" -ForegroundColor Red }

Write-Host @"
======================================================
  Voice Transcriber (VT) - NixOS on WSL Launcher
======================================================
"@ -ForegroundColor Magenta

# 1. Check WSL installation
try {
    $wslVer = wsl --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Err "WSL is not fully initialized. Please run .\setup_wsl.ps1 in an Administrator PowerShell prompt."
        exit 1
    }
    Write-Success "WSL is installed"
} catch {
    Write-Err "WSL command not found. Please install WSL."
    exit 1
}

# 2. Check if NixOS distribution exists in WSL
$distros = wsl --list --quiet 2>&1
$hasNixOS = $distros -match "NixOS"

if (-not $hasNixOS) {
    Write-Warn "NixOS distribution not found in WSL."
    Write-Step "Checking for downloaded nixos.wsl image..."
    $imagePath = "C:\Users\jjame\WSL\nixos.wsl"
    if (Test-Path $imagePath) {
        Write-Step "Importing NixOS distribution from $imagePath ..."
        wsl --install --from-file $imagePath
        if ($LASTEXITCODE -ne 0) {
            # Fallback to import command
            mkdir "$env:USERPROFILE\WSL\NixOS" -Force | Out-Null
            wsl --import NixOS "$env:USERPROFILE\WSL\NixOS" $imagePath --version 2
        }
        Write-Success "NixOS distribution registered successfully!"
    } else {
        Write-Err "Could not find $imagePath. Please run .\setup_wsl.ps1"
        exit 1
    }
} else {
    Write-Success "NixOS distribution found"
}

# 3. Convert Windows path to WSL path
$wslPath = wsl -d NixOS wslpath -u ($ProjectRoot.Replace('\', '/')) 2>&1
$wslPath = $wslPath.Trim()

Write-Host "Project WSL Path: $wslPath" -ForegroundColor Gray

# 4. Check HF_TOKEN
$tokenFile = Join-Path $ProjectRoot "HF_TOKEN"
if (-not (Test-Path $tokenFile)) {
    Write-Warn "Optional: Create 'HF_TOKEN' file in project root if using Cohere models."
}

# 5. Handle command arguments (test / check-audio / run)
if ($args -and $args[0] -eq "check-audio") {
    Write-Step "Running audio device diagnostics inside NixOS WSL..."
    wsl -d NixOS -- bash -c "export PULSE_SERVER=unix:/mnt/wslg/runtime-dir/pulse/native; cd '$wslPath' && python3 src/check_devices.py"
    exit $LASTEXITCODE
}

if ($args -and $args[0] -eq "test") {
    Write-Step "Running transcription tests inside NixOS WSL..."
    wsl -d NixOS -- bash -c "export PULSE_SERVER=unix:/mnt/wslg/runtime-dir/pulse/native; cd '$wslPath' && nix run .#test"
    exit $LASTEXITCODE
}

# 6. Run Voice Transcriber via Nix Flake inside NixOS WSL
Write-Step "Starting Voice Transcriber inside NixOS WSL..."
Write-Host "  WSLg PulseAudio Socket: /mnt/wslg/runtime-dir/pulse/native" -ForegroundColor Cyan
Write-Host "  Using Nix Flake for dependency management" -ForegroundColor Cyan

wsl -d NixOS -- bash -c "export PULSE_SERVER=unix:/mnt/wslg/runtime-dir/pulse/native; cd '$wslPath' && nix run ."
