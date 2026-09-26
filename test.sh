#!/usr/bin/env bash
# Run the voice-transcriber test tiers (Linux / WSL).
#
# Usage (from the repo root):
#   ./test.sh              # shared + this platform's suite (auto-detects Linux/WSL)
#   ./test.sh shared       # cross-platform, model-free
#   ./test.sh platform     # platform-specific (tests/linux or tests/wsl)
#   ./test.sh e2e          # model/audio end-to-end (local only; needs the model)
#   ./test.sh shared -k tui -v      # extra args are forwarded to pytest
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

# Detect WSL vs native Linux for the platform tier.
if [ -n "${WSL_DISTRO_NAME:-}" ] || [ -e /mnt/wslg ] || grep -qi microsoft /proc/version 2>/dev/null; then
    PLATFORM_TIER="tests/wsl"
    PLATFORM_NAME="WSL"
else
    PLATFORM_TIER="tests/linux"
    PLATFORM_NAME="Linux"
fi

CATEGORY="${1:-all}"
if [ "$#" -gt 0 ]; then
    shift
fi

case "$CATEGORY" in
    all)      TARGETS="tests/shared $PLATFORM_TIER" ;;
    shared)   TARGETS="tests/shared" ;;
    platform) TARGETS="$PLATFORM_TIER" ;;
    e2e|model) TARGETS="tests/e2e" ;;
    -h|--help)
        sed -n '2,10p' "$0"
        exit 0
        ;;
    *)
        echo "Unknown category: $CATEGORY" >&2
        echo "Expected: all | shared | platform | e2e" >&2
        exit 2
        ;;
esac

echo "▶ Running [$CATEGORY] tests on $PLATFORM_NAME: $TARGETS"
exec nix develop --command python -m pytest $TARGETS "$@"
