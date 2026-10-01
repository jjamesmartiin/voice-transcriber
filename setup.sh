#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Voice Transcriber - root entry point: SETUP
#
# ./setup.sh [--no-dev] [--no-model]   prepare venv + deps + model
#
# One verb = one script. This dispatcher picks the platform toolchain and execs
# platforms/<toolchain>/setup.sh, which uses that OS's native tooling (apt/brew/
# venv, or Nix). The verb logic lives in tools/vt_dev.py (native) or the flake.
#
# VT_TOOLCHAIN=nix|linux|macos overrides auto-detection.
# VT_USE_VENV=1 forces the native venv path over Nix.
# ==============================================================================

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=platforms/common/common.sh
. "$HERE/platforms/common/common.sh"

case "${1:-}" in
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
esac

TOOLCHAIN="$(vt_detect_toolchain)"
exec "$HERE/platforms/$TOOLCHAIN/setup.sh" "$@"
