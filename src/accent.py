"""Backward-compatible shim for voice_transcriber.accent."""
import sys
import voice_transcriber.accent as _mod

sys.modules[__name__] = _mod
sys.modules["accent"] = _mod
sys.modules["voice_transcriber.accent"] = _mod
