# Voice Transcriber - Full WSL Bridge Launcher
# Starts NixOS WSL backend and connects Windows Global Hotkeys
# Run: .\run_wsl_bridge.ps1

$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
if (-not $ProjectRoot) { $ProjectRoot = Get-Location }

function Write-Step { param([string]$m) Write-Host "`n[BRIDGE] $m" -ForegroundColor Cyan }
function Write-Success { param([string]$m) Write-Host "[OK] $m" -ForegroundColor Green }

Write-Host @"
======================================================
  Voice Transcriber (VT) - Hybrid WSL + Windows Bridge
======================================================
"@ -ForegroundColor Magenta

# 1. Convert Windows path to WSL path
$wslPath = wsl -d NixOS wslpath -u ($ProjectRoot.Replace('\', '/')) 2>&1
$wslPath = $wslPath.Trim()

# 2. Start WSL Daemon in background
Write-Step "1. Launching NixOS WSL Transcription Backend..."
$wslJob = Start-Job -ScriptBlock {
    param($path)
    wsl -d NixOS -- bash -c "export PULSE_SERVER=unix:/mnt/wslg/runtime-dir/pulse/native; cd '$path' && python3 src/wsl_bridge.py"
} -ArgumentList $wslPath

Start-Sleep -Seconds 2
Write-Success "WSL Backend started (Job ID: $($wslJob.Id))"

# 3. Start Windows Global Hotkey Listener
Write-Step "2. Starting Windows Global Hotkey Listener (Alt+Shift)..."
$python = "python"
if (Test-Path "$ProjectRoot\.venv\Scripts\python.exe") {
    $python = "$ProjectRoot\.venv\Scripts\python.exe"
}

try {
    & $python "$ProjectRoot\src\wsl_bridge_host.py"
} finally {
    Write-Step "Stopping background WSL job..."
    Stop-Job $wslJob -ErrorAction SilentlyContinue
    Remove-Job $wslJob -ErrorAction SilentlyContinue
    Write-Success "Cleaned up."
}
