"""Backward-compatible shim for voice_transcriber.hal."""
import sys
import voice_transcriber.hal as _mod

sys.modules[__name__] = _mod
