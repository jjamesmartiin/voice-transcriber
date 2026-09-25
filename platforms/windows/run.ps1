# Voice Transcriber Windows Launcher
$ErrorActionPreference = "Stop"

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
$SrcDir = Join-Path $RepoRoot "src"
$ReqFile = Join-Path $ScriptDir "requirements.txt"
if (-not (Test-Path $ReqFile)) { $ReqFile = Join-Path $RepoRoot "requirements.txt" }

$env:PIP_DISABLE_PIP_VERSION_WARNING = "1"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
$env:VT_PLATFORM = "windows"

if (-not $env:VT_MODEL_BACKEND) {
    $env:VT_MODEL_BACKEND = "cohere"
}

# Verify Python
try {
    $v = python --version 2>&1
    if ($v -notmatch "Python (\d+)\.(\d+)" -or [int]$matches[1] -lt 3 -or [int]$matches[2] -lt 10) {
        Write-Error "Python 3.10+ required. Found: $v"
        exit 1
    }
} catch {
    Write-Error "Python not found. Please install Python 3.10+ from python.org"
    exit 1
}

# Ensure virtual environment
if (-not (Test-Path $VenvDir)) {
    Write-Host "Creating virtual environment at $VenvDir..."
    python -m venv $VenvDir
}

$pipExe = Join-Path $VenvDir "Scripts\pip.exe"
$pythonExe = Join-Path $VenvDir "Scripts\python.exe"

# Install dependencies if sounddevice or pynput is missing
$needsInstall = $true
try {
    $check = & $pythonExe -c "import sounddevice, pynput, keyboard, pyperclip; print('OK')" 2>$null
    if ($check -match "OK") { $needsInstall = $false }
} catch {}

if ($needsInstall -and (Test-Path $ReqFile)) {
    Write-Host "Installing Windows dependencies..."
    & $pipExe install -r $ReqFile --quiet
}

$env:PYTHONPATH = $SrcDir

# Test mode
if ($args -and $args[0] -eq "test") {
    if ($args.Count -gt 1) {
        $testArgs = $args[1..($args.Count - 1)]
        & $pythonExe -m pytest @testArgs
    } else {
        & $pythonExe -m pytest (Join-Path $RepoRoot "tests\test_platform_hal.py") `
                               (Join-Path $RepoRoot "tests\test_dictionary.py") `
                               (Join-Path $RepoRoot "tests\test_config_sync.py") `
                               (Join-Path $RepoRoot "tests\test_post_processor.py") `
                               (Join-Path $RepoRoot "tests\test_tui.py") `
                               (Join-Path $RepoRoot "tests\test_user_workflows.py") `
                               (Join-Path $RepoRoot "tests\test_wsl.py") `
                               (Join-Path $RepoRoot "tests\test_end_to_end_crossplatform.py") -v
    }
    exit $LASTEXITCODE
}

# Build mode
if ($args -and $args[0] -eq "build") {
    $buildScript = Join-Path $ScriptDir "build_offline.py"
    & $pythonExe $buildScript
    exit $LASTEXITCODE
}

# Run application
Write-Host "Starting Voice Transcriber (Backend: $env:VT_MODEL_BACKEND)..."
& $pythonExe (Join-Path $SrcDir "main.py")
