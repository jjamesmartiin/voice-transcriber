# Run the voice-transcriber test tiers on Windows.
#
# Thin shim: the real logic (pytest install-on-demand, tier -> directory
# mapping, flags) lives in platforms/windows/run.ps1, so there is a single
# place to change. Kept so the documented `.\test.ps1 [tier] [pytest args]`
# entry point keeps working from the repo root.
#
#   .\test.ps1              # shared + Windows suite
#   .\test.ps1 shared       # cross-platform, model-free
#   .\test.ps1 windows      # Windows-specific
#   .\test.ps1 e2e          # model/audio end-to-end (local only; needs the model)
#   .\test.ps1 shared -k tui -v
#
# For flags (e.g. --no-install) call the launcher directly: run.bat test ...
$ErrorActionPreference = "Stop"
$launcher = Join-Path $PSScriptRoot "platforms\windows\run.ps1"
& $launcher test @args
exit $LASTEXITCODE
