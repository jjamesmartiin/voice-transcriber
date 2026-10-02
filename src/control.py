"""Backward-compatible shim for voice_transcriber.control."""
import sys
import voice_transcriber.control as _mod

sys.modules[__name__] = _mod
sys.modules["control"] = _mod
sys.modules["voice_transcriber.control"] = _mod
