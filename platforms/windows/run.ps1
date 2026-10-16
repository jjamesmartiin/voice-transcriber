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
. (Join-Path $PSScriptRoot "..\common\common.ps1")

$RepoRoot = Get-RepoRoot -StartDir $PSScriptRoot
$VenvDir = Join-Path $RepoRoot ".venv"
$venvPython = Get-VenvPython -VenvDir $VenvDir

# 1. Prefer virtual environment if present
if (Test-Path $venvPython) {
    & $venvPython (Join-Path $RepoRoot "tools\vt_dev.py") run @args
    exit $LASTEXITCODE
}

# 2. Prefer prebuilt standalone executable if present (no Python required)
$prebuiltExe = Find-PrebuiltExe -RepoRoot $RepoRoot
if ($prebuiltExe) {
    $forwardArgs = @()
    $i = 0
    while ($i -lt $args.Count) {
        $a = $args[$i]
        if ($a -eq "--model-dir") {
            $i++
            if ($i -lt $args.Count) { $env:VT_MODEL_DIR = $args[$i] }
        } elseif ($a -eq "--no-model") {
            $env:VT_AUTO_DOWNLOAD_MODEL = "0"
        } else {
            $forwardArgs += $a
        }
        $i++
    }
    & $prebuiltExe @forwardArgs
    exit $LASTEXITCODE
}

$pythonExe = Get-LaunchPython -VenvDir $VenvDir
if (-not $pythonExe) {
    Write-Err "No environment found (.venv missing, no prebuilt VoiceTranscriber.exe, and no Python 3.10+ on PATH)."
    Write-Host "Run setup.bat first (can download prebuilt standalone without Python)." -ForegroundColor Yellow
    exit 1
}

# Preserve the app's exit code: PowerShell would otherwise report 0 for a crash.
& $pythonExe (Join-Path $RepoRoot "tools\vt_dev.py") run @args
exit $LASTEXITCODE
