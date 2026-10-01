# Voice Transcriber - shared Windows helpers (native Windows + WSL host side).
#
# Dot-source this; do not execute it. It only bootstraps: find the repo root and
# a Python, then the platform scripts dispatch to the one verb implementation,
# tools/vt_dev.py. All venv/dependency/model logic lives there.
#
#     . (Join-Path $PSScriptRoot "..\common\common.ps1")
#
# Set $VtTag before calling Write-Step to label output (e.g. $VtTag = "SETUP").

function Write-Step { param([string]$m) Write-Host "`n[$($VtTag)] $m" -ForegroundColor Cyan }
function Write-Success { param([string]$m) Write-Host "[OK] $m" -ForegroundColor Green }
function Write-Warn { param([string]$m) Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Write-Err { param([string]$m) Write-Host "[ERROR] $m" -ForegroundColor Red }

# Repo root given the calling script's directory. Mirrors the bash helper.
function Get-RepoRoot {
    param([string]$StartDir)
    if (-not $StartDir) { $StartDir = (Get-Location).Path }
    if (Test-Path (Join-Path $StartDir "..\..\src")) {
        return (Resolve-Path (Join-Path $StartDir "..\..")).Path
    } elseif (Test-Path (Join-Path $StartDir "src")) {
        return (Resolve-Path $StartDir).Path
    }
    return (Get-Location).Path
}

# Find a Python >= 3.10. Returns $null, or an object with Cmd/Args/Exe/Version.
function Resolve-HostPython {
    $candidates = @(
        [pscustomobject]@{ Cmd = "python"; Args = @() },
        [pscustomobject]@{ Cmd = "py";     Args = @("-3") }
    )
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.Cmd -ErrorAction SilentlyContinue)) { continue }
        try {
            $cargs = @($c.Args)
            $v = & $c.Cmd @cargs --version 2>&1
            if ("$v" -match "Python (\d+)\.(\d+)" -and
                ([int]$matches[1] -gt 3 -or ([int]$matches[1] -eq 3 -and [int]$matches[2] -ge 10))) {
                $exe = $null
                try { $exe = (& $c.Cmd @cargs -c "import sys; print(sys.executable)").Trim() } catch {}
                return [pscustomobject]@{
                    Cmd = $c.Cmd; Args = $cargs; Exe = $exe; Version = "$v"
                }
            }
        } catch {}
    }
    return $null
}

# Re-read the registry PATH so a just-installed Python (winget) becomes visible.
function Update-SessionPath {
    try {
        $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
        $user = [Environment]::GetEnvironmentVariable("Path", "User")
        if ($machine -or $user) { $env:Path = "$machine;$user" }
    } catch {}
}

function Get-VenvPython { param([string]$VenvDir) Join-Path $VenvDir "Scripts\python.exe" }

# The interpreter a platform script should use: the repo venv if present, else
# the host Python. Launchers never create anything - that is setup's job.
function Get-LaunchPython {
    param([string]$VenvDir, [switch]$RequireVenv)
    $venvPython = Get-VenvPython -VenvDir $VenvDir
    if (Test-Path $venvPython) { return $venvPython }
    if ($RequireVenv) { return $null }
    $hostPy = Resolve-HostPython
    if ($hostPy) { return $hostPy.Exe }
    return $null
}
