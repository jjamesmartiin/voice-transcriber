# Voice Transcriber - WSL launcher.
#
# Works with any WSL distro: the guest runs its own platform dispatcher
# (./run.sh, ./test.sh, ...), which picks Nix or the native apt/venv path
# exactly like bare Linux. So a NixOS-WSL or an Ubuntu-WSL guest both work.
#
#   run_wsl.bat                    launch (auto-picks NixOS if registered)
#   run_wsl.bat -Distro Ubuntu      use a specific WSL distribution
#   run_wsl.bat status              forward a control verb to the app
#   run_wsl.bat test [args]         run the guest test tier
param(
    [string]$Distro
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\common\common.ps1")

$ProjectRoot = Get-RepoRoot -StartDir $PSScriptRoot

Write-Host @"
======================================================
  Voice Transcriber (VT) - WSL Launcher
======================================================
"@ -ForegroundColor Magenta

# 1. WSL installed?
try {
    wsl --version 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Err "WSL is not fully initialized. Run setup_wsl.ps1 in an Administrator prompt."
        exit 1
    }
    Write-Success "WSL is installed"
} catch {
    Write-Err "The 'wsl' command was not found. Install WSL first."
    exit 1
}

# 2. Pick a distribution: -Distro, else NixOS if registered, else the first one.
if (-not $Distro) {
    $distros = @(wsl --list --quiet 2>&1 |
        ForEach-Object { "$_".Trim() } |
        Where-Object { $_ -and $_ -notmatch "^\x00+$" })
    if ($distros -contains "NixOS") {
        $Distro = "NixOS"
    } elseif ($distros.Count -ge 1) {
        $Distro = $distros[0]
    } else {
        Write-Err "No WSL distribution found. Run setup_wsl.bat first."
        exit 1
    }
}
Write-Success "Using WSL distribution: $Distro"

# 3. Map the repo path into the guest.
$wslPath = (wsl -d $Distro wslpath -u ($ProjectRoot.Replace('\', '/')) 2>&1).Trim()
if (-not $wslPath -or $wslPath -match "error") {
    Write-Err "Could not resolve the project path inside $Distro : $wslPath"
    exit 1
}
Write-Host "Project WSL path: $wslPath" -ForegroundColor Gray

# 4. Map a leading verb onto the guest's own entry point (run/test/setup/...).
#    Anything else is forwarded to `./run.sh` as a control verb / app argument.
$verbs = @("setup", "run", "test", "build", "clean")
$guestVerb = "run"
$guestArgs = @()
if ($args.Count -gt 0 -and $verbs -contains "$($args[0])") {
    $guestVerb = "$($args[0])"
    if ($args.Count -gt 1) { $guestArgs = @($args[1..($args.Count - 1)]) }
} else {
    $guestArgs = @($args)
}

$quoted = (($guestArgs | ForEach-Object {
    "'" + ("$_" -replace "'", "'\''") + "'"
}) -join ' ')

$pulse = "export PULSE_SERVER=unix:/mnt/wslg/runtime-dir/pulse/native"
$inner = "$pulse; cd '$wslPath' && bash ./$guestVerb.sh $quoted"

Write-Step "Running '$guestVerb' inside $Distro..."
if ($guestVerb -eq "run" -and $guestArgs.Count -eq 0) {
    Write-Host "  WSLg PulseAudio: /mnt/wslg/runtime-dir/pulse/native" -ForegroundColor Cyan
    Write-Host "  The guest picks Nix or its native venv automatically." -ForegroundColor Cyan
}

# -l so a Nix profile / PATH set in ~/.bashrc is visible inside the guest.
wsl -d $Distro -- bash -lc "$inner"
exit $LASTEXITCODE
