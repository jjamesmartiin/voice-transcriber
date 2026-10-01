# Voice Transcriber - Windows build: the standalone EXE (PyInstaller).
#
#   build.bat                 self-contained bundle (weights included)
#   build.bat --no-model      model-free bundle (~1 GB; weights install on first run)
#
# Bootstraps only (find a Python); PyInstaller install-on-demand and the build
# script invocation live in tools/vt_dev.py.
$ErrorActionPreference = "Stop"
$VtTag = "BUILD"
. (Join-Path $PSScriptRoot "..\common\common.ps1")

$RepoRoot = Get-RepoRoot -StartDir $PSScriptRoot
$VenvDir = Join-Path $RepoRoot ".venv"

$pythonExe = Get-LaunchPython -VenvDir $VenvDir
if (-not $pythonExe) {
    Write-Err "No environment found. Run setup.bat first."
    exit 1
}

& $pythonExe (Join-Path $RepoRoot "tools\vt_dev.py") build @args
exit $LASTEXITCODE
