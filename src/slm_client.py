"""Backward-compatible shim for voice_transcriber.slm_client."""
import sys
import voice_transcriber.slm_client as _mod

sys.modules[__name__] = _mod
sys.modules["slm_client"] = _mod
sys.modules["voice_transcriber.slm_client"] = _mod
