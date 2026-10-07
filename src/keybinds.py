"""Backward-compatible shim for voice_transcriber.keybinds."""
import sys
import voice_transcriber.keybinds as _mod

sys.modules[__name__] = _mod
sys.modules["keybinds"] = _mod
sys.modules["voice_transcriber.keybinds"] = _mod
