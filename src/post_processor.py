"""Backward-compatible shim for voice_transcriber.post_processor."""
import sys
import voice_transcriber.post_processor as _mod

sys.modules[__name__] = _mod
sys.modules["post_processor"] = _mod
sys.modules["voice_transcriber.post_processor"] = _mod
