#!/bin/bash
# Launcher script for the GUI voice transcriber

echo "Starting Voice Transcriber GUI..."
echo "This will open a GUI window with F12 global shortcut support."
echo ""

# Make sure we're in the right directory
cd "$(dirname "$0")"

# Check if we're in a nix-shell
if [ -z "$IN_NIX_SHELL" ]; then
    echo "Starting nix-shell environment..."
    nix-shell --run "python3 voice_gui.py"
else
    echo "Running in existing nix-shell..."
    python3 voice_gui.py
fi 