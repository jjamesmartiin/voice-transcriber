"""Backward-compatible shim for voice_transcriber.tui_ratatui."""
import sys
import voice_transcriber.tui_ratatui as _mod

sys.modules[__name__] = _mod
