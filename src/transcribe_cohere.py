"""Backward-compatible shim for voice_transcriber.transcribe_cohere."""
import sys
import voice_transcriber.transcribe_cohere as _mod

sys.modules[__name__] = _mod
