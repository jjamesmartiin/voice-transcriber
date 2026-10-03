"""Backward-compatible shim for voice_transcriber.console_text."""
import sys
import voice_transcriber.console_text as _mod

sys.modules[__name__] = _mod
sys.modules["console_text"] = _mod
sys.modules["voice_transcriber.console_text"] = _mod
