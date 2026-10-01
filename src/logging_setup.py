"""Backward-compatible shim for voice_transcriber.logging_setup."""
import sys
import voice_transcriber.logging_setup as _mod

sys.modules[__name__] = _mod
