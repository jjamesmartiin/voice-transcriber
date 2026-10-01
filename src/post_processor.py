"""Backward-compatible shim for voice_transcriber.post_processor."""
import sys
import voice_transcriber.post_processor as _mod

sys.modules[__name__] = _mod
