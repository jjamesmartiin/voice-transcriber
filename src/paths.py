"""Backward-compatible shim for voice_transcriber.paths."""
import sys
import voice_transcriber.paths as _mod

sys.modules[__name__] = _mod
sys.modules["paths"] = _mod
sys.modules["voice_transcriber.paths"] = _mod
