# Voice Transcriber Windows Launcher
# Run: .\run.ps1

$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
if (-not $ProjectRoot) { $ProjectRoot = Get-Location }

$VenvDir = Join-Path $ProjectRoot ".venv"
$SrcDir = Join-Path $ProjectRoot "src"

function Write-Step { param([string]$m) Write-Host "`n[SETUP] $m" -ForegroundColor Cyan }
function Write-Success { param([string]$m) Write-Host "[OK] $m" -ForegroundColor Green }
function Write-Err { param([string]$m) Write-Host "[ERROR] $m" -ForegroundColor Red }

Write-Host @"

  Voice Transcriber
  Windows Launcher
"@ -ForegroundColor Magenta

# Suppress pip warnings
$env:PIP_DISABLE_PIP_VERSION_WARNING = "1"
$env:PIP_ROOT_USER_ACTION = "ignore"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"

# Set default to Whisper (works offline, no token needed)
$env:VT_MODEL_BACKEND = "whisper"

# Check Python
try {
    $v = python --version 2>&1
    if ($v -match "Python (\d+)\.(\d+)") {
        if ([int]$matches[1] -eq 3 -and [int]$matches[2] -ge 10) {
            Write-Success "Python found: $v"
        } else {
            Write-Err "Python 3.10+ required. Found: $v"
            exit 1
        }
    }
} catch {
    Write-Err "Python not found. Install from python.org"
    exit 1
}

# Create venv if needed
if (-not (Test-Path $VenvDir)) {
    Write-Step "Creating virtual environment..."
    python -m venv $VenvDir
    Write-Success "Virtual environment created"
}

$pip = Join-Path $VenvDir "Scripts\pip.exe"

# Check if core dependencies are installed, install if not
$depsInstalled = $false
try {
    $pkgs = & $pip list --format=json 2>&1 | ConvertFrom-Json
    $hasSounddevice = $pkgs | Where-Object { $_.name -eq "sounddevice" }
    $hasPynput = $pkgs | Where-Object { $_.name -eq "pynput" }
    if ($hasSounddevice -and $hasPynput) {
        $depsInstalled = $true
    }
} catch {}

if (-not $depsInstalled) {
    Write-Step "Installing dependencies..."
    & $pip install --upgrade pip --quiet 2>&1 | Out-Null
    & $pip install sounddevice soundfile numpy scipy --quiet
    & $pip install torch transformers huggingface-hub faster-whisper sentencepiece protobuf accelerate librosa datasets --quiet
    & $pip install pynput pyperclip --quiet
    Write-Success "Dependencies installed"
}

# Check HF_TOKEN
$tokenFile = Join-Path $ProjectRoot "HF_TOKEN"
if (-not (Test-Path $tokenFile)) {
    Write-Host "`n[NOTE] Create 'HF_TOKEN' file in project root if using Cohere model (optional)" -ForegroundColor Yellow
}

# Run
Write-Step "Starting Voice Transcriber..."

$python = Join-Path $VenvDir "Scripts\python.exe"
$env:PYTHONPATH = $SrcDir

& $python (Join-Path $SrcDir "main_windows.py")
