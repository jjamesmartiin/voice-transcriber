# Voice Transcriber - Windows launcher. Launches the app and nothing else.
#
#   run.bat                       start the app
#   run.bat status                forward a control verb (start/stop/status/...)
#   run.bat --model-dir PATH      use weights from PATH (sets VT_MODEL_DIR)
#   run.bat --venv PATH           use the venv at PATH
#   run.bat --no-model            never auto-download the model
#
# Bootstraps only (find a Python); the launch logic lives in tools/vt_dev.py,
# shared with macOS/Linux. If the environment is not ready, run setup.bat first.
$ErrorActionPreference = "Stop"
$VtTag = "RUN"
. (Join-Path $PSScriptRoot "..\common\common.ps1")

$RepoRoot = Get-RepoRoot -StartDir $PSScriptRoot
$VenvDir = Join-Path $RepoRoot ".venv"

$pythonExe = Get-LaunchPython -VenvDir $VenvDir
if (-not $pythonExe) {
    Write-Err "No environment found (.venv missing and no Python 3.10+ on PATH)."
    Write-Host "Run setup.bat first." -ForegroundColor Yellow
    exit 1
}

# Preserve the app's exit code: PowerShell would otherwise report 0 for a crash.
& $pythonExe (Join-Path $RepoRoot "tools\vt_dev.py") run @args
exit $LASTEXITCODE
