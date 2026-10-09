"""Backward-compatible shim for voice_transcriber.formatter."""
import sys
import voice_transcriber.formatter as _mod

sys.modules[__name__] = _mod
