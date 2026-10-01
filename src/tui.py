"""Backward-compatible shim for voice_transcriber.tui."""
import sys
import voice_transcriber.tui as _mod

sys.modules[__name__] = _mod
