"""Backward-compatible shim for voice_transcriber.model_download."""
import sys
import voice_transcriber.model_download as _mod

sys.modules[__name__] = _mod
