"""Backward-compatible shim for voice_transcriber.micro_batcher."""
import sys
import voice_transcriber.micro_batcher as _mod

sys.modules[__name__] = _mod
