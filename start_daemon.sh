#!/bin/bash
# Launcher script for the voice transcriber global shortcut daemon

echo "Starting Voice Transcriber Global Shortcut Daemon..."
echo "This will register Ctrl+Alt+V as a global shortcut for voice transcription."
echo ""

# Make sure we're in the right directory
cd "$(dirname "$0")"

# Check if we're in a nix-shell
if [ -z "$IN_NIX_SHELL" ]; then
    echo "Starting nix-shell environment..."
    nix-shell --run "python3 global_shortcut.py"
else
    echo "Running in existing nix-shell..."
    python3 global_shortcut.py
fi 