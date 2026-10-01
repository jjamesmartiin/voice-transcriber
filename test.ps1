# Run the voice-transcriber test tiers on Windows.
#
# Thin shim: the real logic lives in platforms/windows/test.ps1 (and the shared
# core in platforms/common/common.ps1), so there is a single place to change.
# Kept so the documented `.\test.ps1 [tier] [pytest args]` entry point keeps
# working from the repo root. Prefer test.bat (same thing, no exec-policy fuss).
#
#   .\test.ps1              # shared + Windows suite
#   .\test.ps1 shared       # cross-platform, model-free
#   .\test.ps1 windows      # Windows-specific
#   .\test.ps1 e2e          # model/audio end-to-end (local only; needs the model)
#   .\test.ps1 verify       # environment check only
#   .\test.ps1 shared -k tui -v
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "platforms\windows\test.ps1") @args
exit $LASTEXITCODE
