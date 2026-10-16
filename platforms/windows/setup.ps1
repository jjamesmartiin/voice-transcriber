# Voice Transcriber - Windows setup: prepare this machine, including the model.
#
# Entry point: setup.bat  (or  powershell -ExecutionPolicy Bypass -File .\platforms\windows\setup.ps1)
#
# This script bootstraps the runtime only (ensure Python, enable long paths);
# the venv / dependencies / model logic lives in tools/vt_dev.py so one fix
# lands on every platform. `run` only launches - it never installs.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\common\common.ps1")

$NoDev = $false
$NoModel = $false
$Prebuilt = $false
foreach ($a in $args) {
    switch ("$a") {
        "--no-dev"   { $NoDev = $true }
        "--no-model" { $NoModel = $true }
        "--prebuilt" { $Prebuilt = $true }
        "-h"         { $ShowHelp = $true }
        "--help"     { $ShowHelp = $true }
        default {
            Write-Err "Unknown option: $a"
            Write-Host "Run setup.bat --help for available options." -ForegroundColor Yellow
            exit 2
        }
    }
}

if ($ShowHelp) {
    Write-Host @"
Voice Transcriber - Windows setup

Usage: setup.bat [--prebuilt] [--no-dev] [--no-model]

  --prebuilt   Download and set up the standalone prebuilt bundle (no Python required).
  --no-dev     Skip the dev/test tooling (pytest, PyInstaller).
  --no-model   Do not acquire the model. Use when running fully offline with
               no weights yet, or when you will place them in models\ manually.

The model is resolved from models\ in this order:
  1. models\cohere\            an unpacked copy (used as-is)
  2. a split bundle in models\ (parts + *.SHA256SUMS, or a .zip/.tar)
                               assembled offline with no extra tools
  3. otherwise it is downloaded (~2.8 GB, one time)

Then launch with:  run.bat
"@
    exit 0
}

Write-Host @"
======================================================
       Voice Transcriber - Windows Setup
======================================================
"@ -ForegroundColor Magenta

$RepoRoot = Get-RepoRoot -StartDir $PSScriptRoot

# --- Windows-specific: long path support (MAX_PATH) --------------------------
function Ensure-LongPathsSupport {
    $current = (Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -ErrorAction SilentlyContinue).LongPathsEnabled
    if ($current -eq 1) { return $true }
    try {
        Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -Value 1 -Type DWord -ErrorAction Stop
        Write-Success "Windows long path support enabled."
        return $true
    } catch {}
    Write-Step "Windows MAX_PATH (260) is active; enabling long paths needs permission."
    try {
        $cmd = "Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name 'LongPathsEnabled' -Value 1 -Type DWord -Force"
        Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -Command `"$cmd`"" -PassThru -Wait -WindowStyle Hidden | Out-Null
        $current = (Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -ErrorAction SilentlyContinue).LongPathsEnabled
        if ($current -eq 1) { Write-Success "Windows long path support enabled."; return $true }
    } catch {
        Write-Warn "Administrator permission was not granted to enable long paths."
    }
    return $false
}
Ensure-LongPathsSupport | Out-Null

# --- Python 3.10+ or Prebuilt Standalone ------------------------------------
if ($Prebuilt) {
    Write-Step "1. Installing prebuilt standalone bundle"
    if (Install-PrebuiltRelease -RepoRoot $RepoRoot -NoModel:$NoModel) {
        Write-Host @"

======================================================
             Setup Complete (Prebuilt)
======================================================
Launch:      run.bat
Status:      run.bat status
Diagnostics: run.bat doctor

"@ -ForegroundColor Green
        exit 0
    }
    exit 1
}

Write-Step "1. Checking host Python installation"
$hostPy = Resolve-HostPython
if ($hostPy) {
    Write-Success "Found Python: $($hostPy.Version)"
} else {
    Write-Warn "Python 3.10+ was not found on your system."
    Write-Host "You can download the prebuilt standalone bundle (no Python required),"
    Write-Host "or install Python 3.13 via winget."
    $choice = "1"
    try {
        $choice = Read-Host "Choose: [1] Download prebuilt standalone (Recommended) | [2] Install Python 3.13 | [3] Exit [1]"
    } catch {}
    if (-not $choice -or $choice -eq "1") {
        if (Install-PrebuiltRelease -RepoRoot $RepoRoot -NoModel:$NoModel) {
            Write-Host @"

======================================================
             Setup Complete (Prebuilt)
======================================================
Launch:      run.bat
Status:      run.bat status
Diagnostics: run.bat doctor

"@ -ForegroundColor Green
            exit 0
        }
        exit 1
    } elseif ($choice -eq "2") {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            & winget install -e --id Python.Python.3.13 --accept-package-agreements --accept-source-agreements
            Update-SessionPath
            $hostPy = Resolve-HostPython
        }
    }
    if (-not $hostPy) {
        Write-Err "Python 3.10+ is required when not using prebuilt standalone."
        Write-Host "Install from https://www.python.org/downloads/ (tick 'Add python.exe to PATH')," -ForegroundColor Yellow
        Write-Host "or run: setup.bat --prebuilt" -ForegroundColor Yellow
        exit 1
    }
    Write-Success "Python installed via winget: $($hostPy.Version)"
}

# --- Hand off to the shared runner -------------------------------------------
$devArgs = @()
if ($NoDev)   { $devArgs += "--no-dev" }
if ($NoModel) { $devArgs += "--no-model" }

& $hostPy.Exe (Join-Path $RepoRoot "tools\vt_dev.py") setup @devArgs
$code = $LASTEXITCODE

if ($code -eq 0) {
    Write-Host @"

======================================================
             Setup Complete
======================================================
Launch:      run.bat
Run tests:   test.bat
Build EXE:   build.bat [--no-model]
Clean:       clean.bat [--models] [--yes]

Model location is models\cohere (drop an unpacked copy or a split bundle in
models\ and re-run setup.bat to go fully offline - see docs/offline_install.md).
"@ -ForegroundColor Green
}
exit $code
