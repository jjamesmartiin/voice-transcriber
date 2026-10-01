#!/usr/bin/env bash
# Voice Transcriber - shared POSIX helpers (Linux / macOS / WSL guest).
#
# Source this; do not execute it. Platform scripts
# (platforms/<os>/<verb>.sh) use it to find the repo, find a Python, and hand
# off to the one verb implementation, tools/vt_dev.py. The Nix platform does
# not source this - it calls the flake directly.
#
#     . "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/common/common.sh"
#
# Set VT_TAG before calling vt_step() to label output (e.g. VT_TAG=SETUP).

if [ -t 1 ]; then
    VT_CYAN='\033[0;36m'; VT_GREEN='\033[0;32m'; VT_YELLOW='\033[1;33m'
    VT_RED='\033[0;31m'; VT_BOLD='\033[1m'; VT_NC='\033[0m'
else
    VT_CYAN=''; VT_GREEN=''; VT_YELLOW=''; VT_RED=''; VT_BOLD=''; VT_NC=''
fi

vt_step() { printf '\n%s[%s]%s %s\n' "$VT_CYAN$VT_BOLD" "${VT_TAG:-VT}" "$VT_NC" "$1"; }
vt_ok()   { printf '%s[OK]%s %s\n'    "$VT_GREEN"  "$VT_NC" "$1"; }
vt_warn() { printf '%s[WARN]%s %s\n'  "$VT_YELLOW" "$VT_NC" "$1"; }
vt_err()  { printf '%s[ERROR]%s %s\n' "$VT_RED"    "$VT_NC" "$1" >&2; }

# Repo root given a starting directory (the script's own dir). Mirrors the
# PowerShell helper.
vt_repo_root() {
    local start="${1:-$(pwd)}"
    if [ -d "$start/../../src" ]; then (cd "$start/../.." && pwd); return 0; fi
    if [ -d "$start/src" ]; then (cd "$start" && pwd); return 0; fi
    pwd
}

# Print the path to a Python >= 3.10, or return 1.
vt_find_python() {
    local c
    for c in python3 python python3.13 python3.12 python3.11 python3.10; do
        if command -v "$c" >/dev/null 2>&1 \
           && "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
            command -v "$c"
            return 0
        fi
    done
    return 1
}

# Print the repo venv interpreter if it exists.
vt_venv_python() {
    local vp="$1/.venv/bin/python"
    if [ -x "$vp" ]; then printf '%s' "$vp"; return 0; fi
    return 1
}

# Which toolchain should a root dispatcher use? nix | linux | macos.
#   VT_TOOLCHAIN=nix|linux|macos  explicit override
#   VT_USE_VENV=1                 force the native (venv) path over Nix
vt_detect_toolchain() {
    if [ -n "${VT_TOOLCHAIN:-}" ]; then printf '%s' "$VT_TOOLCHAIN"; return 0; fi
    if [ "$(uname -s)" = "Darwin" ]; then printf 'macos'; return 0; fi
    if [ "${VT_USE_VENV:-0}" != "1" ] && command -v nix >/dev/null 2>&1; then
        printf 'nix'
    else
        printf 'linux'
    fi
}

# Interpreter for the native verb runner: the repo venv, else the host Python.
vt_dev_python() {
    local repo="$1" v
    if v="$(vt_venv_python "$repo")"; then printf '%s' "$v"; return 0; fi
    vt_find_python
}

# Dispatch a verb to the one implementation.
#   vt_run_dev REPO VERB [args...]
vt_run_dev() {
    local repo="$1"; shift
    local verb="$1"; shift
    local py
    if ! py="$(vt_dev_python "$repo")"; then
        vt_err "No Python 3.10+ found. Run ./setup.sh first."
        return 1
    fi
    exec "$py" "$repo/tools/vt_dev.py" "$verb" "$@"
}

# Require the repo venv. run/test never install - that is setup's job.
vt_require_venv() {
    local repo="$1"
    if vt_venv_python "$repo" >/dev/null; then return 0; fi
    vt_err "No .venv found. Run ./setup.sh first."
    return 1
}
