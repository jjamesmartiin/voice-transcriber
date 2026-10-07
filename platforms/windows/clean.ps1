# Voice Transcriber - Windows clean: remove generated state, never source.
#
#   clean.bat                  .venv, build\, dist\, caches
#   clean.bat --models         also delete the downloaded weights (models\)
#   clean.bat --yes            no prompt
#
# Bootstraps only (find a Python); the target list + prompt live in
# tools/vt_dev.py, shared with macOS/Linux.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\common\common.ps1")

$RepoRoot = Get-RepoRoot -StartDir $PSScriptRoot
$VenvDir = Join-Path $RepoRoot ".venv"

$pythonExe = Get-LaunchPython -VenvDir $VenvDir
if (-not $pythonExe) {
    Write-Err "No Python 3.10+ found. Run setup.bat first."
    exit 1
}

& $pythonExe (Join-Path $RepoRoot "tools\vt_dev.py") clean @args
exit $LASTEXITCODE
