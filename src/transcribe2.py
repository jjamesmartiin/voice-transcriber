"""Backward-compatible shim for voice_transcriber.transcribe2."""
import sys
import voice_transcriber.transcribe2 as _mod

sys.modules[__name__] = _mod
