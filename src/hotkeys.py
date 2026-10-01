"""Backward-compatible shim for voice_transcriber.hotkeys."""
import sys
import voice_transcriber.hotkeys as _mod

sys.modules[__name__] = _mod
