# Run the voice-transcriber test tiers on Windows.
#
# Usage (from the repo root):
#   .\test.ps1              # shared + Windows suite
#   .\test.ps1 shared       # cross-platform, model-free
#   .\test.ps1 windows      # Windows-specific
#   .\test.ps1 e2e          # model/audio end-to-end (local only; needs the model)
#   .\test.ps1 shared -k tui -v     # extra args are forwarded to pytest
param(
    [Parameter(Position = 0)][string]$Category = "all",
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$ExtraArgs
)

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot

$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

switch ($Category.ToLower()) {
    "all"      { $targets = @("tests\shared", "tests\windows") }
    "shared"   { $targets = @("tests\shared") }
    "windows"  { $targets = @("tests\windows") }
    "platform" { $targets = @("tests\windows") }
    "e2e"      { $targets = @("tests\e2e") }
    "model"    { $targets = @("tests\e2e") }
    default {
        Write-Host "Unknown category: $Category" -ForegroundColor Red
        Write-Host "Expected: all | shared | windows | e2e" -ForegroundColor Yellow
        exit 2
    }
}

$env:PYTHONPATH = Join-Path $RepoRoot "src"
Write-Host "▶ Running [$Category] tests on Windows: $($targets -join ', ')" -ForegroundColor Cyan
& $python -m pytest @targets @ExtraArgs
exit $LASTEXITCODE
