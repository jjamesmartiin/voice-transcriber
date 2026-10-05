"""Backward-compatible shim for voice_transcriber.audio_state."""
import sys
import voice_transcriber.audio_state as _mod

sys.modules[__name__] = _mod
