#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

# Ensure user has input device access
if [[ -e /dev/input/event0 ]] && ! [[ -r /dev/input/event0 ]]; then
    if ! groups | grep -q '\binput\b'; then
        echo "Warning: Current user is not in the 'input' group."
        echo "Run: sudo usermod -a -G input $USER (then re-login)"
    fi
fi

if command -v nix >/dev/null 2>&1; then
    exec nix run . "$@"
else
    export PYTHONPATH="$REPO_ROOT/src:${PYTHONPATH:-}"
    exec python3 src/main.py "$@"
fi
