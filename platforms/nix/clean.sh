#!/usr/bin/env bash
set -euo pipefail
# Nix - clean verb. Same target list as every other platform.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
cd "$REPO"
# Tell tools/vt_dev.py to use the flake interpreter, not a stale .venv.
export VT_TOOLCHAIN=nix
exec nix develop --command python tools/vt_dev.py clean "$@"
