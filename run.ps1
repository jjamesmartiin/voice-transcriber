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
/\_/\  /\_/\  /\_/\  /\_/\  /\_/\  /\_/\  /\_/\  /\_/\  /\_/\  /\_/\  /\_/\ 
( o.o )( o.o )( o.o )( o.o )( o.o )( o.o )( o.o )( o.o )( o.o )( o.o )( o.o )
 > ^ <  > ^ <  > ^ <  > ^ <  > ^ <  > ^ <  > ^ <  > ^ <  > ^ <  > ^ <  > ^ < 
 /\_/\                                                                 /\_/\ 
( o.o )    __     __    _                                             ( o.o )
 > ^ <     \ \   / /__ (_) ___ ___                                     > ^ < 
 /\_/\      \ \ / / _ \| |/ __/ _ \                                    /\_/\ 
( o.o )      \ V / (_) | | (_|  __/                                   ( o.o )
 > ^ <      __\_/ \___/|_|\___\___|            _ _                     > ^ < 
 /\_/\     |_   _| __ __ _ _ __  ___  ___ _ __(_) |__   ___ _ __       /\_/\ 
( o.o )      | || '__/ _` | '_ \/ __|/ __| '__| | '_ \ / _ \ '__|     ( o.o )
 > ^ <       | || | | (_| | | | \__ \ (__| |  | | |_) |  __/ |         > ^ < 
 /\_/\       |_||_|  \__,_|_| |_|___/\___|_|  |_|_.__/ \___|_|         /\_/\ 
( o.o )                                                               ( o.o )
 > ^ <                                                                 > ^ < 
 /\_/\  /\_/\  /\_/\  /\_/\  /\_/\  /\_/\  /\_/\  /\_/\  /\_/\  /\_/\  /\_/\ 
( o.o )( o.o )( o.o )( o.o )( o.o )( o.o )( o.o )( o.o )( o.o )( o.o )( o.o )
 > ^ <  > ^ <  > ^ <  > ^ <  > ^ <  > ^ <  > ^ <  > ^ <  > ^ <  > ^ <  > ^ < 
"@ -ForegroundColor Magenta

# Suppress pip warnings
$env:PIP_DISABLE_PIP_VERSION_WARNING = "1"
$env:PIP_ROOT_USER_ACTION = "ignore"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"

# Set default to Whisper (works offline, no token needed)
# User can override by setting $env:VT_MODEL_BACKEND before running
if (-not $env:VT_MODEL_BACKEND) {
    $env:VT_MODEL_BACKEND = "whisper"
    # $env:VT_MODEL_BACKEND = "cohere" # Uncomment to use Cohere's model (requires HF_TOKEN)
}

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

$pipExe = Join-Path $VenvDir "Scripts\pip.exe"
$reqFile = Join-Path $ProjectRoot "requirements.txt"

# Check if dependencies need installing
$depsInstalled = $false
try {
    $pkgs = & $pipExe list --format=json 2>&1 | ConvertFrom-Json
    $hasSounddevice = $pkgs | Where-Object { $_.name -eq "sounddevice" }
    $hasPynput = $pkgs | Where-Object { $_.name -eq "pynput" }
    if ($hasSounddevice -and $hasPynput) {
        $depsInstalled = $true
    }
} catch {}

if (-not $depsInstalled) {
    Write-Step "Installing dependencies from requirements.txt..."
    & $pipExe install -r $reqFile --quiet
    Write-Success "Dependencies installed"
}

# Check HF_TOKEN
$tokenFile = Join-Path $ProjectRoot "HF_TOKEN"
if (-not (Test-Path $tokenFile)) {
    Write-Host "`n[NOTE] Create 'HF_TOKEN' file in project root if using Cohere model (optional)" -ForegroundColor Yellow
}

# Check for test mode
$python = Join-Path $VenvDir "Scripts\python.exe"
$env:PYTHONPATH = $SrcDir

if ($args -and $args[0] -eq "test") {
    Write-Host "[TEST] Running transcription tests..." -ForegroundColor Cyan
    & $python "tests/test_transcribe.py"
    exit $LASTEXITCODE
}

# Run
Write-Step "Starting Voice Transcriber..."
Write-Host "  VT_MODEL_BACKEND: $env:VT_MODEL_BACKEND" -ForegroundColor Cyan

$env:VT_MODEL_BACKEND = $env:VT_MODEL_BACKEND

& $python (Join-Path $SrcDir "main_windows.py")
