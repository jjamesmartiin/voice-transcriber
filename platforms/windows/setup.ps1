# Voice Transcriber - Windows Environment Setup Script
# Recommended: Double-click setup.bat in the repository root (bypasses execution policy)
# Or run in PowerShell with execution policy bypass:
# powershell -ExecutionPolicy Bypass -File .\platforms\windows\setup.ps1

$ErrorActionPreference = "Stop"

function Write-Step { param([string]$m) Write-Host "`n[SETUP] $m" -ForegroundColor Cyan }
function Write-Success { param([string]$m) Write-Host "[OK] $m" -ForegroundColor Green }
function Write-Warn { param([string]$m) Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Write-Err { param([string]$m) Write-Host "[ERROR] $m" -ForegroundColor Red }

# Flags:
#   --no-dev   Skip installing the dev/test tooling (pytest, PyInstaller).
#   -h/--help  Show help and exit.
$NoDev = $false
foreach ($a in $args) {
    if ($a -eq "--no-dev") {
        $NoDev = $true
    } elseif ($a -eq "-h" -or $a -eq "--help") {
        Write-Host @"
Voice Transcriber - Windows setup

Usage: setup.bat [--no-dev]

  --no-dev   Skip installing pytest / PyInstaller (the dev/test tooling).
             The app itself is still fully installed. Add the tools later
             with:  run.bat test   (or)   run.bat build
"@
        exit 0
    }
}

Write-Host @"
======================================================
       Voice Transcriber - Windows Setup
======================================================
"@ -ForegroundColor Magenta

$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = Get-Location }

if (Test-Path (Join-Path $ScriptDir "..\..\src")) {
    $RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
} elseif (Test-Path (Join-Path $ScriptDir "src")) {
    $RepoRoot = (Resolve-Path $ScriptDir).Path
} else {
    $RepoRoot = (Get-Location).Path
}

$VenvDir = Join-Path $RepoRoot ".venv"
$ReqFile = Join-Path $ScriptDir "requirements.txt"
if (-not (Test-Path $ReqFile)) { $ReqFile = Join-Path $RepoRoot "requirements.txt" }

function Ensure-LongPathsSupport {
    $current = (Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -ErrorAction SilentlyContinue).LongPathsEnabled
    if ($current -eq 1) {
        return $true
    }

    # Attempt to set directly if already running with admin privileges
    try {
        Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -Value 1 -Type DWord -ErrorAction Stop
        Write-Success "Windows long path support enabled."
        return $true
    } catch {}

    # Prompt user via UAC to enable LongPathsEnabled so no manual steps are needed
    Write-Host "`n[SETUP] Windows 260-character path limit (MAX_PATH) is currently active." -ForegroundColor Cyan
    Write-Host "Enabling long path support prevents '[WinError 206] filename too long' errors in deep folders." -ForegroundColor Cyan
    Write-Host "Requesting permission to enable long paths (a Windows prompt will appear)..." -ForegroundColor Yellow

    try {
        $cmd = "Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name 'LongPathsEnabled' -Value 1 -Type DWord -Force"
        $proc = Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -Command `"$cmd`"" -PassThru -Wait -WindowStyle Hidden
        $current = (Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -ErrorAction SilentlyContinue).LongPathsEnabled
        if ($current -eq 1) {
            Write-Success "Windows long path support successfully enabled!"
            return $true
        }
    } catch {
        Write-Warn "Administrator permission was not granted to enable long paths."
    }

    return $false
}

$longPathsEnabled = Ensure-LongPathsSupport

# Resolve a Python 3.10+ interpreter. Sets $script:hostPythonCmd / Args.
function Resolve-HostPython {
    $script:hostPythonCmd = $null
    $script:hostPythonArgs = @()
    $candidates = @(
        [pscustomobject]@{ Cmd = "python"; Args = @() },
        [pscustomobject]@{ Cmd = "py";     Args = @("-3") }
    )
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.Cmd -ErrorAction SilentlyContinue)) { continue }
        try {
            $cargs = @($c.Args)
            $v = & $c.Cmd @cargs --version 2>&1
            if ($v -match "Python (\d+)\.(\d+)" -and ([int]$matches[1] -gt 3 -or ([int]$matches[1] -eq 3 -and [int]$matches[2] -ge 10))) {
                $script:hostPythonCmd = $c.Cmd
                $script:hostPythonArgs = $cargs
                $script:hostPythonVersion = "$v"
                return $true
            }
        } catch {}
    }
    return $false
}

# winget updates the *registry* PATH; re-read it so the new Python is visible.
function Update-SessionPath {
    try {
        $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
        $user = [Environment]::GetEnvironmentVariable("Path", "User")
        if ($machine -or $user) { $env:Path = "$machine;$user" }
    } catch {}
}

# Step 1: Detect Python
Write-Step "1. Checking host Python installation..."
$hostPythonVersion = $null
if (Resolve-HostPython) {
    Write-Success "Found Python: $hostPythonVersion"
} else {
    Write-Warn "Python 3.10+ was not found on your system."
    $ready = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "Python 3.13 can be installed automatically with winget." -ForegroundColor Cyan
        $answer = Read-Host "Install Python 3.13 now? [y/N]"
        if ($answer -match '^(y|yes)$') {
            & winget install -e --id Python.Python.3.13 --accept-package-agreements --accept-source-agreements
            Update-SessionPath
            $ready = Resolve-HostPython
        }
    } else {
        Write-Host "winget is unavailable, so Python cannot be installed automatically." -ForegroundColor Yellow
    }

    if (-not $ready) {
        Write-Err "Python 3.10+ is required."
        Write-Host "Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH')," -ForegroundColor Yellow
        Write-Host "or run: winget install -e --id Python.Python.3.13" -ForegroundColor Yellow
        exit 1
    }
    Write-Success "Python installed via winget: $hostPythonVersion"
}

# Step 2: Virtual Environment
Write-Step "2. Setting up virtual environment (.venv)..."
$pythonExe = Join-Path $VenvDir "Scripts\python.exe"

# If long paths could not be enabled and the repo path is long, use a directory junction to AppData to avoid MAX_PATH
if (-not $longPathsEnabled -and $RepoRoot.Length -gt 70 -and -not (Test-Path $VenvDir)) {
    $shortVenv = Join-Path $env:LOCALAPPDATA "VoiceTranscriber\venv"
    Write-Host "Using short path ($shortVenv) with directory junction to avoid Windows path limit..." -ForegroundColor Cyan
    try {
        $shortParent = Split-Path $shortVenv
        if (-not (Test-Path $shortParent)) { New-Item -ItemType Directory -Path $shortParent -Force | Out-Null }
        & $hostPythonCmd @hostPythonArgs -m venv $shortVenv
        if (Test-Path (Join-Path $shortVenv "Scripts\python.exe")) {
            New-Item -ItemType Junction -Path $VenvDir -Target $shortVenv | Out-Null
            Write-Success "Linked virtual environment to $VenvDir via directory junction."
        }
    } catch {
        Write-Warn "Junction fallback failed; falling back to standard venv creation: $_"
    }
}

if (-not (Test-Path $VenvDir) -or -not (Test-Path $pythonExe)) {
    Write-Host "Creating virtual environment at $VenvDir..."
    & $hostPythonCmd @hostPythonArgs -m venv $VenvDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $pythonExe)) {
        Write-Err "Failed to create virtual environment."
        exit 1
    }
    Write-Success "Virtual environment created."
} else {
    Write-Success "Virtual environment ready at $VenvDir."
}

# Step 3: Upgrade pip and tooling
Write-Step "3. Ensuring pip is up to date..."
& $pythonExe -m ensurepip --upgrade 2>$null
& $pythonExe -m pip install --upgrade pip --quiet
Write-Success "Pip is up to date."

# Step 4: Install Dependencies
Write-Step "4. Installing Voice Transcriber dependencies from requirements.txt..."
if (Test-Path $ReqFile) {
    Write-Host "Running: python -m pip install -r $ReqFile"
    & $pythonExe -m pip install -r $ReqFile
} else {
    Write-Err "Requirements file not found at $ReqFile."
    exit 1
}

if ($LASTEXITCODE -ne 0) {
    Write-Err "pip install returned exit code $LASTEXITCODE. Please check the error messages above."
    if (-not $longPathsEnabled) {
        Write-Host "`n[TIP] If you encountered [WinError 206] (path too long):" -ForegroundColor Yellow
        Write-Host "  Re-run setup.bat and click 'Yes' on the Windows permission prompt to enable long paths," -ForegroundColor Yellow
        Write-Host "  or move the project to a shorter folder path (e.g. C:\voice-transcriber)." -ForegroundColor Yellow
    }
    exit $LASTEXITCODE
}
Write-Success "Dependency installation complete."

# Step 5: Install dev/test tooling (pytest for .\test.ps1, PyInstaller for run.bat build)
if ($NoDev) {
    Write-Step "5. Skipping dev & test tooling (--no-dev)"
    Write-Host "pytest / PyInstaller were not installed. The app is ready to run." -ForegroundColor Yellow
    Write-Host "Add them later with:  run.bat test   (or)   run.bat build" -ForegroundColor Yellow
} else {
    Write-Step "5. Installing dev & test tooling (pytest, PyInstaller)..."
    $DevReqFile = Join-Path $ScriptDir "requirements-dev.txt"
    if (Test-Path $DevReqFile) {
        Write-Host "Running: python -m pip install -r $DevReqFile"
        & $pythonExe -m pip install -r $DevReqFile
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "Dev tooling install returned exit code $LASTEXITCODE."
            Write-Host "The app will still run, but .\test.ps1 (pytest) and run.bat build (PyInstaller) may not work until this succeeds." -ForegroundColor Yellow
        } else {
            Write-Success "pytest and PyInstaller installed - .\test.ps1 and run.bat build are ready."
        }
    } else {
        Write-Warn "requirements-dev.txt not found at $DevReqFile; skipping dev tooling."
        Write-Host "Install later with: python -m pip install pytest pyinstaller" -ForegroundColor Yellow
    }
}

# Step 6: Verify Critical Modules
Write-Step "6. Verifying installed packages..."
$verifyScript = "import sounddevice, soundfile, scipy, numpy, pynput, keyboard, pyperclip, rich, yaml, psutil; print('All core modules verified successfully!')"
$verifyOutput = & $pythonExe -c $verifyScript 2>&1

if ($verifyOutput -match "All core modules verified successfully") {
    Write-Success "Verification passed: all core native Windows modules are ready."
} else {
    Write-Warn "Verification output: $verifyOutput"
}

Write-Host @"

======================================================
             Setup Completed Successfully!
======================================================
You can now run Voice Transcriber using any of:
  - Batch launcher: run.bat (or double-click run.bat)
  - PowerShell:     powershell -ExecutionPolicy Bypass -File .\platforms\windows\run.ps1
  - Python venv:    .\.venv\Scripts\python.exe src\main.py

Run the tests with:  .\test.ps1
Build the EXE with:  run.bat build

First launch downloads the ~2.8 GB Cohere model into models\cohere (one time).
Make sure Windows Settings > Privacy & security > Microphone allows desktop apps.
"@ -ForegroundColor Green
