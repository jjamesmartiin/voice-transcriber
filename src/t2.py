"""Backward-compatible shim for voice_transcriber.t2."""
import sys
import voice_transcriber.t2 as _mod

sys.modules[__name__] = _mod
sys.modules["t2"] = _mod
sys.modules["voice_transcriber.t2"] = _mod
