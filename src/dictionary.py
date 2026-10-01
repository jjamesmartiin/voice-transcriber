"""Backward-compatible shim for voice_transcriber.dictionary."""
import sys
import voice_transcriber.dictionary as _mod

sys.modules[__name__] = _mod
