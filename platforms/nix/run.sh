#!/usr/bin/env bash
set -euo pipefail
# Nix - run verb. `nix run .` builds (cached) and launches the app.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
cd "$REPO"
exec nix run . -- "$@"
