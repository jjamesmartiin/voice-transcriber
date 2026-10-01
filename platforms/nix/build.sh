#!/usr/bin/env bash
set -euo pipefail
# Nix - build verb. Packages the app with Nix (Linux AppImage via --bundle).
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
cd "$REPO"
if [ "${1:-}" = "--bundle" ]; then
    shift
    exec nix bundle --bundler github:ralismark/nix-appimage \
        --extra-experimental-features "nix-command flakes" .#default "$@"
fi
exec nix build . "$@"
