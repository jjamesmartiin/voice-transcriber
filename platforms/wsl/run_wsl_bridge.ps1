# Voice Transcriber - Full WSL bridge launcher.
# Starts the in-guest backend and connects the Windows global-hotkey listener.
#
#   run_wsl_bridge.ps1                 auto-picks NixOS if registered
#   run_wsl_bridge.ps1 -Distro Ubuntu  use a specific WSL distribution
#
# The guest half uses whatever the distro provides: `nix develop` on NixOS
# (or any distro with Nix), else the repo venv from ./setup.sh, else python3.
param(
    [string]$Distro
)
$ErrorActionPreference = "Stop"
$VtTag = "BRIDGE"
. (Join-Path $PSScriptRoot "..\common\common.ps1")

$ProjectRoot = Get-RepoRoot -StartDir $PSScriptRoot

Write-Host @"
======================================================
  Voice Transcriber (VT) - Hybrid WSL + Windows Bridge
======================================================
"@ -ForegroundColor Magenta

if (-not $Distro) {
    $distros = @(wsl --list --quiet 2>&1 |
        ForEach-Object { "$_".Trim() } |
        Where-Object { $_ -and $_ -notmatch "^\x00+$" })
    if ($distros -contains "NixOS") { $Distro = "NixOS" }
    elseif ($distros.Count -ge 1) { $Distro = $distros[0] }
    else { Write-Err "No WSL distribution found. Run setup_wsl.bat first."; exit 1 }
}
Write-Success "Using WSL distribution: $Distro"

$wslPath = (wsl -d $Distro wslpath -u ($ProjectRoot.Replace('\', '/')) 2>&1).Trim()
if (-not $wslPath -or $wslPath -match "error") {
    Write-Err "Could not resolve the project path inside $Distro : $wslPath"
    exit 1
}

# Guest backend: prefer Nix, then the repo venv, then a bare python3.
$guestCmd = @'
export PULSE_SERVER=unix:/mnt/wslg/runtime-dir/pulse/native
if command -v nix >/dev/null 2>&1; then
    exec nix develop --command python src/wsl_bridge.py
elif [ -x .venv/bin/python ]; then
    exec .venv/bin/python src/wsl_bridge.py
else
    exec python3 src/wsl_bridge.py
fi
'@

Write-Step "1. Launching the in-guest transcription backend..."
$wslJob = Start-Job -ScriptBlock {
    param($Distro, $Path, $GuestCmd)
    wsl -d $Distro -- bash -lc "cd '$Path' && $GuestCmd"
} -ArgumentList $Distro, $wslPath, $guestCmd

Start-Sleep -Seconds 2
Write-Success "Guest backend started (Job ID: $($wslJob.Id))"

Write-Step "2. Starting the Windows global-hotkey listener (Alt+Shift)..."
$python = "python"
if (Test-Path "$ProjectRoot\.venv\Scripts\python.exe") {
    $python = "$ProjectRoot\.venv\Scripts\python.exe"
}

try {
    & $python "$ProjectRoot\src\wsl_bridge_host.py"
} finally {
    Write-Step "Stopping the background guest job..."
    Stop-Job $wslJob -ErrorAction SilentlyContinue
    Remove-Job $wslJob -ErrorAction SilentlyContinue
    Write-Success "Cleaned up."
}
