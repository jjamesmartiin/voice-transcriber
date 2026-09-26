# Voice Transcriber - Windows Environment Setup Script
# Run directly in PowerShell or by double-clicking setup.bat:
# .\platforms\windows\setup.ps1

$ErrorActionPreference = "Stop"

function Write-Step { param([string]$m) Write-Host "`n[SETUP] $m" -ForegroundColor Cyan }
function Write-Success { param([string]$m) Write-Host "[OK] $m" -ForegroundColor Green }
function Write-Warn { param([string]$m) Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Write-Err { param([string]$m) Write-Host "[ERROR] $m" -ForegroundColor Red }

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

# Step 1: Detect Python
Write-Step "1. Checking host Python installation..."
$hostPythonCmd = $null
$hostPythonArgs = @()

if (Get-Command python -ErrorAction SilentlyContinue) {
    try {
        $v = & python --version 2>&1
        if ($v -match "Python (\d+)\.(\d+)" -and ([int]$matches[1] -gt 3 -or ([int]$matches[1] -eq 3 -and [int]$matches[2] -ge 10))) {
            $hostPythonCmd = "python"
            Write-Success "Found Python in PATH: $v"
        }
    } catch {}
}

if (-not $hostPythonCmd -and (Get-Command py -ErrorAction SilentlyContinue)) {
    try {
        $v = & py -3 --version 2>&1
        if ($v -match "Python (\d+)\.(\d+)" -and ([int]$matches[1] -gt 3 -or ([int]$matches[1] -eq 3 -and [int]$matches[2] -ge 10))) {
            $hostPythonCmd = "py"
            $hostPythonArgs = @("-3")
            Write-Success "Found Python via py launcher: $v"
        }
    } catch {}
}

if (-not $hostPythonCmd) {
    Write-Err "Python 3.10+ was not found on your system."
    Write-Host "Please install Python 3.10+ from https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "IMPORTANT: Check 'Add python.exe to PATH' during installation!" -ForegroundColor Yellow
    exit 1
}

# Step 2: Virtual Environment
Write-Step "2. Setting up virtual environment (.venv)..."
$pythonExe = Join-Path $VenvDir "Scripts\python.exe"

if (-not (Test-Path $VenvDir) -or -not (Test-Path $pythonExe)) {
    Write-Host "Creating virtual environment at $VenvDir..."
    & $hostPythonCmd @hostPythonArgs -m venv $VenvDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $pythonExe)) {
        Write-Err "Failed to create virtual environment."
        exit 1
    }
    Write-Success "Virtual environment created."
} else {
    Write-Success "Virtual environment already exists at $VenvDir."
}

# Step 3: Upgrade pip and tooling
Write-Step "3. Ensuring pip and setup tools are up to date..."
& $pythonExe -m ensurepip --upgrade 2>$null
& $pythonExe -m pip install --upgrade pip setuptools wheel --quiet
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
    exit $LASTEXITCODE
}
Write-Success "Dependency installation complete."

# Step 5: Verify Critical Modules
Write-Step "5. Verifying installed packages..."
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
  - Double-click:  run.bat
  - PowerShell:    .\platforms\windows\run.ps1
  - Python venv:   .\.venv\Scripts\python.exe src\main.py
"@ -ForegroundColor Green
