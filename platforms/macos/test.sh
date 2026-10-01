#!/usr/bin/env bash
set -euo pipefail
# macOS (Darwin) - test verb. Homebrew + venv are the native tooling.
# The verb itself lives in tools/vt_dev.py (so one fix lands on every platform);
# this script only locates the repo/Python and hands off.
VT_TAG=TEST
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../common/common.sh
. "$HERE/../common/common.sh"
REPO="$(vt_repo_root "$HERE")"
vt_require_venv "$REPO" || exit 1
vt_run_dev "$REPO" test "$@"
