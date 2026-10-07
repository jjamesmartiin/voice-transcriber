# Voice Transcriber - Windows test runner (test == verify).
#
#   test.bat              shared + Windows suite
#   test.bat shared       cross-platform, model-free
#   test.bat windows      Windows-specific
#   test.bat e2e          model/audio end-to-end (local only)
#   test.bat verify       environment check only, no tests
#   test.bat shared -k tui -v
#
# Bootstraps only (find a Python); tier selection + pytest live in
# tools/vt_dev.py, shared with macOS/Linux.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\common\common.ps1")

$RepoRoot = Get-RepoRoot -StartDir $PSScriptRoot
$VenvDir = Join-Path $RepoRoot ".venv"

$pythonExe = Get-LaunchPython -VenvDir $VenvDir
if (-not $pythonExe) {
    Write-Err "No environment found (.venv missing and no Python 3.10+ on PATH)."
    Write-Host "Run setup.bat first." -ForegroundColor Yellow
    exit 1
}

# model_download.py prints U+2713/U+2715; Windows' legacy code page cannot
# encode them, so a piped verify raised UnicodeEncodeError instead of reporting.
$env:PYTHONIOENCODING = "utf-8"

& $pythonExe (Join-Path $RepoRoot "tools\vt_dev.py") test @args
exit $LASTEXITCODE
