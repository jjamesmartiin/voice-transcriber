"""Backward-compatible shim for voice_transcriber.meeting."""
import sys
import voice_transcriber.meeting as _mod

sys.modules[__name__] = _mod
