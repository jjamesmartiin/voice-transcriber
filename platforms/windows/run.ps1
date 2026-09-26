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

# 1. Discover host Python (check 'python' then 'py -3')
$hostPythonCmd = $null
$hostPythonArgs = @()

if (Get-Command python -ErrorAction SilentlyContinue) {
    try {
        $v = & python --version 2>&1
        if ($v -match "Python (\d+)\.(\d+)" -and ([int]$matches[1] -gt 3 -or ([int]$matches[1] -eq 3 -and [int]$matches[2] -ge 10))) {
            $hostPythonCmd = "python"
        }
    } catch {}
}

if (-not $hostPythonCmd -and (Get-Command py -ErrorAction SilentlyContinue)) {
    try {
        $v = & py -3 --version 2>&1
        if ($v -match "Python (\d+)\.(\d+)" -and ([int]$matches[1] -gt 3 -or ([int]$matches[1] -eq 3 -and [int]$matches[2] -ge 10))) {
            $hostPythonCmd = "py"
            $hostPythonArgs = @("-3")
        }
    } catch {}
}

$pythonExe = Join-Path $VenvDir "Scripts\python.exe"

# If venv doesn't exist, verify host python before creating
if (-not (Test-Path $VenvDir) -or -not (Test-Path $pythonExe)) {
    if (-not $hostPythonCmd) {
        Write-Host "`n[ERROR] Python 3.10+ was not found on your system." -ForegroundColor Red
        Write-Host "Please install Python 3.10+ from https://www.python.org/downloads/" -ForegroundColor Yellow
        Write-Host "Make sure to check 'Add python.exe to PATH' during installation.`n" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "Creating virtual environment at $VenvDir..." -ForegroundColor Cyan
    & $hostPythonCmd @hostPythonArgs -m venv $VenvDir
}

if (-not (Test-Path $pythonExe)) {
    Write-Host "[ERROR] Virtual environment Python executable not found at $pythonExe" -ForegroundColor Red
    exit 1
}

# 2. Check and install dependencies if missing
$needsInstall = $true
try {
    $check = & $pythonExe -c "import sounddevice, pynput, keyboard, pyperclip, rich; print('OK')" 2>$null
    if ($check -match "OK") { $needsInstall = $false }
} catch {}

if ($needsInstall -and (Test-Path $ReqFile)) {
    Write-Host "Installing Windows dependencies via pip..." -ForegroundColor Cyan
    & $pythonExe -m pip install --upgrade pip --quiet
    & $pythonExe -m pip install -r $ReqFile
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Dependency installation from $ReqFile failed." -ForegroundColor Red
        Write-Host "Please review the pip errors above, or run: .\platforms\windows\setup.ps1" -ForegroundColor Yellow
        exit $LASTEXITCODE
    }
}

$env:PYTHONPATH = $SrcDir

# 3. Test mode
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

# 4. Build mode
if ($args -and $args[0] -eq "build") {
    $buildScript = Join-Path $ScriptDir "build_offline.py"
    & $pythonExe $buildScript
    exit $LASTEXITCODE
}

# 5. Run application
Write-Host "Starting Voice Transcriber (Backend: $env:VT_MODEL_BACKEND)..." -ForegroundColor Green
& $pythonExe (Join-Path $SrcDir "main.py")
