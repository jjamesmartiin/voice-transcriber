"""Backward-compatible shim for voice_transcriber.diarize."""
import sys
import voice_transcriber.diarize as _mod

sys.modules[__name__] = _mod
