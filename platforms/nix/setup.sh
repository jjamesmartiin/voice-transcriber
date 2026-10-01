#!/usr/bin/env bash
set -euo pipefail
# Nix (NixOS, nix on Linux/macOS, NixOS-WSL guest) - setup verb.
# The flake provides the interpreter and every dependency, so setup only has to
# acquire the model. --no-deps tells the shared runner to skip venv/pip.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
cd "$REPO"
# Tell tools/vt_dev.py to use the flake interpreter, not a stale .venv.
export VT_TOOLCHAIN=nix
exec nix develop --command python tools/vt_dev.py setup --no-deps "$@"
