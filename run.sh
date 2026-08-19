#!/usr/bin/env bash
# Voice Transcriber runner for NixOS WSL
# Zero-setup on Windows: runs entirely inside NixOS WSL

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ==============================================================================
# CONFIGURATION OPTIONS (Easily customize here or pass via environment)
# ==============================================================================
# Model Backend: 'cohere' (default, high accuracy) or 'whisper' (fast offline)
export VT_MODEL_BACKEND="${VT_MODEL_BACKEND:-cohere}"

# Sound Effect Theme:
#   • 'proximity'  - Default modern soft Windows chime (Windows Proximity Notification.wav)
#   • 'speech'     - Cortana speech tones (Speech On.wav / Speech Off.wav)
#   • 'notify'     - Classic ding (Windows Notify.wav)
#   • 'navigation' - Subtle tap (Windows Navigation Start.wav)
#   • 'classic'    - Retro chimes & tada (chimes.wav / tada.wav)
#   • 'silent'     - Muted (no audio cues)
#   • Or pass any full path to a Windows .wav file (e.g. 'C:\Windows\Media\tada.wav')
export VT_SOUND_THEME="${VT_SOUND_THEME:-proximity}"

# Micro-Batching Engine: 'auto' (default hybrid), 'always', or 'disabled'
export VT_MICRO_BATCHING="${VT_MICRO_BATCHING:-auto}"

echo "🎙️ Starting Voice Transcriber in NixOS WSL..."
echo "ℹ️  Model Backend:   ${VT_MODEL_BACKEND}"
echo "ℹ️  Sound Theme:     ${VT_SOUND_THEME}"
echo "ℹ️  Micro-Batching:  ${VT_MICRO_BATCHING}"
echo "ℹ️  Microphone:      WSLg PulseAudio (Windows default mic)"
echo "ℹ️  Global Hotkey:   Hold Alt+Shift anywhere in Windows to speak"

exec nix run . "$@"
