# Voice Transcriber Windows Launcher
#
# Usage:  run.bat [flags] [mode] [mode args...]
#
# Modes:
#   (default)        Launch the app. Creates .venv and installs dependencies if
#                    they are missing, then runs. The model downloads on first
#                    use if no complete local copy is present.
#   verify           Report Python / dependency / model status. Installs and
#                    downloads nothing. Exit code 0 = ready, 1 = not ready.
#   fetch            Download + verify the model (~2.8 GB), then exit.
#   test [pytest..]  Run a test tier (pytest installed on demand unless
#                    --no-dev). A leading tier name maps to its directory:
#                    all | shared | windows | platform | e2e | model.
#   build            Build the standalone Windows EXE (PyInstaller installed on
#                    demand unless --no-dev).
#   clean [--models] [--yes]
#                    Remove .venv, build/, dist/ and caches. --models also
#                    deletes the downloaded weights. Prompts unless --yes.
#   help             Show this help and exit.
#
# Flags (place them before the mode):
#   --no-install
#                    Do not create .venv or run any pip install. Use whatever is
#                    already present (falls back to the host Python) and try to
#                    run anyway. Handy when an install is slow.
#   --no-dev         Do not install pytest / PyInstaller on demand.
#   --no-model
#                    Do not auto-download the model (VT_AUTO_DOWNLOAD_MODEL=0).
#   --model-dir <path>
#                    Load weights from <path> (sets VT_MODEL_DIR) - e.g. a backup
#                    copy, or a different version you want to try.
#   --venv <path>    Use the virtual environment at <path> instead of
#                    <repo>\.venv.
#   -h, --help       Show this help and exit.
#
# Everything after the mode is forwarded: `run.bat test shared -k tui -v`.
$ErrorActionPreference = "Stop"

function Show-Usage {
    Write-Host ""
    Write-Host "Voice Transcriber Windows launcher" -ForegroundColor Cyan
    Write-Host "Usage: run.bat [flags] [mode] [mode args...]" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Modes:"
    Write-Host "  (default)        Launch the app (installs deps + model if needed)"
    Write-Host "  doctor           Run system diagnostics (audio devices, permissions, GPU, model)"
    Write-Host "  verify           Check Python / deps / model; install nothing"
    Write-Host "  fetch            Download + verify the model, then exit"
    Write-Host "  test [pytest..]  Run a test tier: all|shared|windows|e2e"
    Write-Host "  build            Build the standalone EXE"
    Write-Host "  clean            Remove .venv / build / dist / caches"
    Write-Host "                   (clean --models --yes also deletes the weights)"
    Write-Host "  help             Show this help"
    Write-Host "  <verb>           Run a control API verb (start, stop, toggle, status, doctor, etc.)"
    Write-Host ""
    Write-Host "Flags (before the mode):"
    Write-Host "  --no-install     Skip venv creation and all pip installs"
    Write-Host "  --no-dev         Do not install pytest / PyInstaller on demand"
    Write-Host "  --no-model       Do not auto-download the model"
    Write-Host "  --model-dir PATH Use weights from PATH (sets VT_MODEL_DIR)"
    Write-Host "  --venv PATH      Use the virtual environment at PATH"
    Write-Host "  -h, --help       Show this help"
    Write-Host ""
}

# --- Parse arguments: flags first, then the mode, then forwarded args --------
$Mode = "run"
$ModeArgs = @()
$NoInstall = $false
$NoDev = $false
$NoModel = $false
$ModelDir = $null
$VenvOverride = $null
$ShowHelp = $false

$i = 0
$parsingFlags = $true
while ($i -lt $args.Count -and $parsingFlags) {
    $a = "$($args[$i])"
    if ($a -eq "--no-install") {
        $NoInstall = $true; $i++
    } elseif ($a -eq "--no-dev") {
        $NoDev = $true; $i++
    } elseif ($a -eq "--no-model") {
        $NoModel = $true; $i++
    } elseif ($a -eq "--model-dir") {
        if ($i + 1 -ge $args.Count) {
            Write-Host "[ERROR] --model-dir requires a path" -ForegroundColor Red
            exit 2
        }
        $ModelDir = "$($args[$i + 1])"; $i += 2
    } elseif ($a -eq "--venv") {
        if ($i + 1 -ge $args.Count) {
            Write-Host "[ERROR] --venv requires a path" -ForegroundColor Red
            exit 2
        }
        $VenvOverride = "$($args[$i + 1])"; $i += 2
    } elseif ($a -eq "-h" -or $a -eq "--help") {
        $ShowHelp = $true; $i++; $parsingFlags = $false
    } else {
        $parsingFlags = $false
    }
}

if ($ShowHelp) {
    Show-Usage
    exit 0
}

if ($i -lt $args.Count) {
    $Mode = "$($args[$i])"
    $i++
}
if ($i -lt $args.Count) {
    $ModeArgs = @($args[$i..($args.Count - 1)])
}

$specialModes = @("run", "verify", "fetch", "test", "build", "clean", "help")
if ($Mode -like "-*") {
    Write-Host "[ERROR] Unknown flag: $Mode" -ForegroundColor Red
    Show-Usage
    exit 2
}
if ($Mode -eq "help" -and $ModeArgs.Count -eq 0) {
    Show-Usage
    exit 0
}

# --- Repository / environment layout ----------------------------------------
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
if ($VenvOverride) { $VenvDir = $VenvOverride }
$SrcDir = Join-Path $RepoRoot "src"
$ReqFile = Join-Path $ScriptDir "requirements.txt"
if (-not (Test-Path $ReqFile)) { $ReqFile = Join-Path $RepoRoot "requirements.txt" }
$DevReqFile = Join-Path $ScriptDir "requirements-dev.txt"
if (-not (Test-Path $DevReqFile)) { $DevReqFile = Join-Path $RepoRoot "requirements-dev.txt" }

# Only these modes may create a venv or run pip. `verify`/`fetch` are explicitly
# side-effect-free (verify installs nothing; fetch only downloads the model),
# and `clean` must not recreate what it is about to delete.
$InstallAllowed = (-not $NoInstall) -and ($Mode -eq "run" -or $Mode -eq "test" -or $Mode -eq "build")

# Relocation knobs. `--model-dir` mirrors the VT_MODEL_DIR the model installer
# already honours, so a hand-placed/backed-up weights directory is accepted
# as-is once it is complete.
if ($ModelDir) { $env:VT_MODEL_DIR = $ModelDir }
if ($NoModel) { $env:VT_AUTO_DOWNLOAD_MODEL = "0" }

# --- clean mode: remove generated state, never tracked source ----------------
if ($Mode -eq "clean") {
    $cleanModels = $ModeArgs -contains "--models"
    $assumeYes = ($ModeArgs -contains "--yes") -or ($ModeArgs -contains "-y")

    $targets = @($VenvDir, (Join-Path $RepoRoot "build"), (Join-Path $RepoRoot "dist"))
    if ($cleanModels) { $targets += (Join-Path $RepoRoot "models") }
    $existing = @($targets | Where-Object { Test-Path -LiteralPath $_ })

    $pyRoots = @("src", "tests", "platforms", "scripts", "tools", "eval") |
        ForEach-Object { Join-Path $RepoRoot $_ } | Where-Object { Test-Path -LiteralPath $_ }
    $caches = @()
    foreach ($root in $pyRoots) {
        $caches += @(Get-ChildItem -LiteralPath $root -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue)
    }
    $pytestCache = Join-Path $RepoRoot "tests\.pytest_cache"

    if ($existing.Count -eq 0 -and $caches.Count -eq 0 -and -not (Test-Path -LiteralPath $pytestCache)) {
        Write-Host "Nothing to clean." -ForegroundColor Green
        exit 0
    }

    Write-Host "Will remove:" -ForegroundColor Yellow
    foreach ($t in $existing) { Write-Host "  $t" }
    if ($caches.Count -gt 0) { Write-Host "  $($caches.Count) __pycache__ directories" }
    if (Test-Path -LiteralPath $pytestCache) { Write-Host "  $pytestCache" }

    if (-not $assumeYes) {
        $answer = Read-Host "Proceed? [y/N]"
        if ($answer -notmatch '^(y|yes)$') {
            Write-Host "Aborted." -ForegroundColor Yellow
            exit 1
        }
    }

    foreach ($t in $existing) {
        try {
            Remove-Item -LiteralPath $t -Recurse -Force -ErrorAction Stop
            Write-Host "  removed $t" -ForegroundColor Green
        } catch {
            Write-Host "  [WARN] could not remove $t : $_" -ForegroundColor Yellow
        }
    }
    foreach ($c in $caches) {
        Remove-Item -LiteralPath $c.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $pytestCache) {
        Remove-Item -LiteralPath $pytestCache -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Host "Clean complete." -ForegroundColor Green
    exit 0
}

function Ensure-LongPathsSupport {
    $current = (Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -ErrorAction SilentlyContinue).LongPathsEnabled
    if ($current -eq 1) { return $true }
    try {
        Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -Value 1 -Type DWord -ErrorAction Stop
        return $true
    } catch {}
    try {
        $cmd = "Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name 'LongPathsEnabled' -Value 1 -Type DWord -Force"
        $proc = Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -Command `"$cmd`"" -PassThru -Wait -WindowStyle Hidden
        $current = (Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -ErrorAction SilentlyContinue).LongPathsEnabled
        if ($current -eq 1) { return $true }
    } catch {}
    return $false
}
# Long-path support only matters when we might install into a deep tree, and
# it can raise a UAC prompt - so skip it for the read-only / destructive modes.
if ($Mode -ne "verify" -and $Mode -ne "clean") {
    Ensure-LongPathsSupport | Out-Null
}

$env:PIP_DISABLE_PIP_VERSION_WARNING = "1"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
# Windows' locale code page (cp1252/cp437) cannot encode the ✓/✕ that
# model_download.py prints, so a redirected/piped `run.bat verify` died with
# UnicodeEncodeError instead of reporting. Scoped to the report/download modes:
# the app's own console output keeps Windows' console-API handling.
if ($Mode -eq "verify" -or $Mode -eq "fetch") { $env:PYTHONIOENCODING = "utf-8" }
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

# Absolute path to the host interpreter, for `--no-install` and `verify`.
$hostPythonExe = $null
if ($hostPythonCmd) {
    try {
        $hostPythonExe = (& $hostPythonCmd @hostPythonArgs -c "import sys; print(sys.executable)").Trim()
    } catch {}
}

$venvPython = Join-Path $VenvDir "Scripts\python.exe"

# Create the venv unless we were told not to install anything.
if (-not (Test-Path $venvPython) -and $InstallAllowed) {
    if (-not $hostPythonCmd) {
        Write-Host "`n[ERROR] Python 3.10+ was not found on your system." -ForegroundColor Red
        Write-Host "Please install Python 3.10+ from https://www.python.org/downloads/" -ForegroundColor Yellow
        Write-Host "Make sure to check 'Add python.exe to PATH' during installation.`n" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "Creating virtual environment at $VenvDir..." -ForegroundColor Cyan
    & $hostPythonCmd @hostPythonArgs -m venv $VenvDir
}

if (Test-Path $venvPython) {
    $pythonExe = $venvPython
} elseif ((-not $InstallAllowed) -and $hostPythonExe) {
    if ($NoInstall) {
        Write-Host "[WARN] --no-install: no virtual environment found; using host Python at $hostPythonExe" -ForegroundColor Yellow
    } else {
        Write-Host "No virtual environment; using host Python at $hostPythonExe (this mode installs nothing)." -ForegroundColor Cyan
    }
    $pythonExe = $hostPythonExe
} else {
    if (-not $hostPythonExe) {
        Write-Host "[ERROR] No usable Python found (no virtual environment, and no Python 3.10+ on PATH)." -ForegroundColor Red
        Write-Host "Install Python 3.10+, or run setup.bat (it can install it via winget)." -ForegroundColor Yellow
    } else {
        Write-Host "[ERROR] Virtual environment Python executable not found at $venvPython" -ForegroundColor Red
        Write-Host "Run without --no-install to create it, or pass --venv <path>." -ForegroundColor Yellow
    }
    exit 1
}

# 2. Check and install dependencies if missing (install modes only)
if ($InstallAllowed) {
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
            Write-Host "Please review the pip errors above, or run setup.bat (or: powershell -ExecutionPolicy Bypass -File .\platforms\windows\setup.ps1)" -ForegroundColor Yellow
            Write-Host "If you encountered [WinError 206] (path too long), move the folder to a shorter path (e.g. C:\voice-transcriber) or enable LongPaths in Windows." -ForegroundColor Yellow
            exit $LASTEXITCODE
        }
    }
}

$env:PYTHONPATH = $SrcDir

# Install dev tooling (pytest / PyInstaller) on demand for the test/build modes.
# `--no-dev` turns this into a presence check that never installs.
function Ensure-DevTooling {
    param([string]$Module, [string]$Label)
    # find_spec() returns None instead of raising, so this probe writes nothing
    # to stderr (a native-command stderr write would trip $ErrorActionPreference).
    & $pythonExe -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('$Module') else 1)"
    if ($LASTEXITCODE -eq 0) { return $true }

    if ($NoDev) {
        Write-Host "[ERROR] $Label is not installed and --no-dev was passed." -ForegroundColor Red
        Write-Host "Install it with: $pythonExe -m pip install -r $DevReqFile" -ForegroundColor Yellow
        return $false
    }
    if (-not (Test-Path $DevReqFile)) {
        Write-Host "[ERROR] $Label is missing and $DevReqFile was not found." -ForegroundColor Red
        Write-Host "Install it with: $pythonExe -m pip install $Label" -ForegroundColor Yellow
        return $false
    }
    Write-Host "Installing dev tooling ($Label) from $DevReqFile..." -ForegroundColor Cyan
    & $pythonExe -m pip install -r $DevReqFile
    if ($LASTEXITCODE -eq 0) { return $true }
    Write-Host "[ERROR] Failed to install $Label." -ForegroundColor Red
    return $false
}

# 3. Verify mode: report only, install/download nothing.
if ($Mode -eq "verify") {
    Write-Host ""
    Write-Host "Voice Transcriber - environment check" -ForegroundColor Cyan
    Write-Host "=====================================" -ForegroundColor Cyan
    Write-Host "Python:  $pythonExe"
    & $pythonExe --version

    if (Test-Path $venvPython) {
        Write-Host "Venv:    $VenvDir (found)"
    } else {
        Write-Host "Venv:    $VenvDir (not found - will be created on the next real run)" -ForegroundColor Yellow
    }
    if ($env:VT_MODEL_DIR) {
        Write-Host "Model:   $env:VT_MODEL_DIR (from --model-dir / VT_MODEL_DIR)"
    }
    $logPath = $null
    try { $logPath = (& $pythonExe -c "import logging_setup; print(logging_setup.default_log_path())" 2>$null) } catch {}
    if ($logPath) { Write-Host "Log:     $logPath" }
    Write-Host ""

    $checkScript = @'
import importlib.util as u
import sys

runtime = [
    "numpy", "scipy", "sounddevice", "soundfile", "yaml", "psutil",
    "pyperclip", "rich", "pynput", "keyboard", "torch", "transformers",
    "tokenizers", "onnxruntime",
]
dev = ["pytest", "PyInstaller"]


def report(title, names):
    print(title)
    missing = []
    for n in names:
        ok = u.find_spec(n) is not None
        if not ok:
            missing.append(n)
        print("   %s %s" % ("OK  " if ok else "MISS", n))
    return missing


missing_runtime = report("Runtime dependencies:", runtime)
report("Dev tooling (only needed for `test` / `build`):", dev)

if u.find_spec("torch") is not None:
    try:
        import torch
        print("torch device: %s" % ("cuda" if torch.cuda.is_available() else "cpu"))
        if torch.cuda.is_available():
            print("  gpu: %s" % torch.cuda.get_device_name(0))
    except Exception as exc:
        print("torch device: unavailable (%s)" % exc)

if u.find_spec("sounddevice") is not None:
    try:
        import sounddevice as sd
        inputs = [d for d in sd.query_devices() if d.get("max_input_channels", 0) > 0]
        print("input devices: %d" % len(inputs))
    except Exception as exc:
        print("input devices: unavailable (%s)" % exc)

sys.exit(1 if missing_runtime else 0)
'@
    # Windows PowerShell 5.1 (what run.bat invokes) strips the embedded double
    # quotes when a multi-line here-string is passed to a native command, so
    # `-c $checkScript` arrived as invalid source ("'(' was never closed") and
    # verify reported missing dependencies on every Windows machine. A file
    # round-trips unchanged; a UTF-8 BOM is legal in a Python source file.
    $checkFile = Join-Path ([System.IO.Path]::GetTempPath()) "vt-verify-$PID.py"
    Set-Content -LiteralPath $checkFile -Value $checkScript -Encoding UTF8
    & $pythonExe $checkFile
    $depsReady = ($LASTEXITCODE -eq 0)
    Remove-Item -LiteralPath $checkFile -Force -ErrorAction SilentlyContinue

    Write-Host ""
    $modelVerify = Join-Path $SrcDir "model_download.py"
    & $pythonExe $modelVerify --verify-only
    $modelReady = ($LASTEXITCODE -eq 0)

    Write-Host ""
    if ($depsReady -and $modelReady) {
        Write-Host "READY: dependencies and model are in place." -ForegroundColor Green
        exit 0
    }
    if (-not $depsReady) {
        Write-Host "NOT READY: some runtime dependencies are missing. Run without --no-install to fetch them." -ForegroundColor Yellow
    }
    if (-not $modelReady) {
        Write-Host "NOT READY: model weights are missing or incomplete (they download on first run unless --no-model)." -ForegroundColor Yellow
        Write-Host "Place a complete model under <repo>\models\cohere, or point at one with --model-dir <path>." -ForegroundColor Yellow
    }
    exit 1
}

# 3b. Fetch mode: download + verify the model, then exit.
if ($Mode -eq "fetch") {
    if ($NoModel) {
        Write-Host "[ERROR] --no-model contradicts the fetch mode." -ForegroundColor Red
        exit 2
    }
    Write-Host "Downloading and verifying the Cohere model (~2.8 GB, one time)..." -ForegroundColor Cyan
    & $pythonExe (Join-Path $SrcDir "model_download.py")
    exit $LASTEXITCODE
}

# 4. Test mode. A leading tier name maps to its directory; anything else is
# passed straight to pytest.
if ($Mode -eq "test") {
    if (-not (Ensure-DevTooling -Module "pytest" -Label "pytest")) { exit 1 }
    $tierDirs = @{
        "all"      = @("tests\shared", "tests\windows")
        "shared"   = @("tests\shared")
        "windows"  = @("tests\windows")
        "platform" = @("tests\windows")
        "e2e"      = @("tests\e2e")
        "model"    = @("tests\e2e")
    }
    $tierKey = ""
    if ($ModeArgs.Count -gt 0) { $tierKey = "$($ModeArgs[0])".ToLower() }

    if ($ModeArgs.Count -eq 0) {
        $pytestArgs = @($tierDirs["all"] | ForEach-Object { Join-Path $RepoRoot $_ }) + @("-v")
    } elseif ($tierDirs.ContainsKey($tierKey)) {
        $rest = @()
        if ($ModeArgs.Count -gt 1) { $rest = @($ModeArgs[1..($ModeArgs.Count - 1)]) }
        $pytestArgs = @($tierDirs[$tierKey] | ForEach-Object { Join-Path $RepoRoot $_ }) + $rest
    } else {
        $pytestArgs = $ModeArgs
    }
    & $pythonExe -m pytest @pytestArgs
    exit $LASTEXITCODE
}

# 5. Build mode
if ($Mode -eq "build") {
    if (-not (Ensure-DevTooling -Module "PyInstaller" -Label "PyInstaller")) { exit 1 }
    $buildScript = Join-Path $ScriptDir "build_offline.py"
    & $pythonExe $buildScript
    exit $LASTEXITCODE
}

# 6. Run application / command
$appArgs = @()
if ($Mode -ne "run") { $appArgs += $Mode }
if ($ModeArgs.Count -gt 0) { $appArgs += $ModeArgs }

if ($Mode -eq "run") {
    Write-Host "Starting Voice Transcriber (Backend: $env:VT_MODEL_BACKEND)..." -ForegroundColor Green
}
& $pythonExe (Join-Path $SrcDir "main.py") @appArgs
# Without this the script terminates "normally" and powershell.exe reports 0,
# so run.bat's `if errorlevel 1` could never see a crashed app (verified on
# Windows 11 / PowerShell 5.1: app exit 3 -> launcher exit 0).
exit $LASTEXITCODE
