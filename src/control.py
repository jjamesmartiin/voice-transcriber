"""Backward-compatible shim for voice_transcriber.control."""
import sys
import voice_transcriber.control as _mod

sys.modules[__name__] = _mod
