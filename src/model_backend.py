"""Backward-compatible shim for voice_transcriber.model_backend."""
import sys
import voice_transcriber.model_backend as _mod

sys.modules[__name__] = _mod
