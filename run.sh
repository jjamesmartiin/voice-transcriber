#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Voice Transcriber (VT) - Universal Launcher (Linux / macOS)
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Warn if Linux user is missing input permissions (only if input event devices exist)
if [[ "$(uname -s)" == "Linux" ]]; then
    if [[ -e /dev/input/event0 ]] && ! [[ -r /dev/input/event0 ]]; then
        if command -v groups >/dev/null 2>&1 && ! groups | grep -qw 'input'; then
            echo "[WARN] Current user is not in the 'input' group." >&2
            echo "       Run: sudo usermod -a -G input $USER (then re-login)" >&2
        fi
    fi
fi

# 1. If .venv exists, launch with the virtual environment
if [[ -d "$SCRIPT_DIR/.venv" && -f "$SCRIPT_DIR/.venv/bin/python" ]]; then
    export PYTHONPATH="$SCRIPT_DIR/src:${PYTHONPATH:-}"
    exec "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/src/main.py" "$@"
fi

# 2. If Nix is installed and venv is not explicitly requested, dispatch via Nix Flake
if [[ "${VT_USE_VENV:-0}" != "1" ]] && command -v nix >/dev/null 2>&1; then
    exec nix run . -- "$@"
fi

# 3. Neither .venv nor Nix is available
echo "Voice Transcriber virtual environment (.venv) not found and Nix is not available."

if [[ -t 0 ]]; then
    read -r -p "Would you like to run ./setup.sh now? [Y/n] " response
    response="${response:-y}"
    if [[ "$response" =~ ^[Yy] ]]; then
        "$SCRIPT_DIR/setup.sh"
        if [[ -f "$SCRIPT_DIR/.venv/bin/python" ]]; then
            export PYTHONPATH="$SCRIPT_DIR/src:${PYTHONPATH:-}"
            exec "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/src/main.py" "$@"
        else
            echo "[ERROR] Setup completed but .venv/bin/python was not found." >&2
            exit 1
        fi
    else
        echo "Aborted. Run ./setup.sh to set up the environment."
        exit 1
    fi
else
    echo "Running in non-interactive environment. Running ./setup.sh..."
    "$SCRIPT_DIR/setup.sh"
    if [[ -f "$SCRIPT_DIR/.venv/bin/python" ]]; then
        export PYTHONPATH="$SCRIPT_DIR/src:${PYTHONPATH:-}"
        exec "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/src/main.py" "$@"
    else
        echo "[ERROR] Setup completed but .venv/bin/python was not found." >&2
        exit 1
    fi
fi
