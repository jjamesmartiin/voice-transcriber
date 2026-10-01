"""Backward-compatible shim for voice_transcriber.notifications."""
import sys
import voice_transcriber.notifications as _mod

sys.modules[__name__] = _mod
