#!/usr/bin/env bash
# Voice Transcriber runner for NixOS WSL
# Zero-setup on Windows: runs entirely inside NixOS WSL

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export VT_MODEL_BACKEND="${VT_MODEL_BACKEND:-cohere}"

echo "🎙️ Starting Voice Transcriber in NixOS WSL..."
echo "ℹ️  Backend: Cohere (Default)"
echo "ℹ️  Microphone: WSLg PulseAudio (Windows default mic)"
echo "ℹ️  Global Hotkey: Hold Alt+Shift anywhere in Windows to speak"

exec nix run . "$@"
