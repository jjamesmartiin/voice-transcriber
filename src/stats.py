"""Backward-compatible shim for voice_transcriber.stats."""
import sys
import voice_transcriber.stats as _mod

sys.modules[__name__] = _mod
sys.modules["stats"] = _mod
sys.modules["voice_transcriber.stats"] = _mod
