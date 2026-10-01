"""Backward-compatible shim for voice_transcriber.doctor."""
import sys
import voice_transcriber.doctor as _mod

sys.modules[__name__] = _mod
