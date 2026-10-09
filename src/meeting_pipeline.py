"""Backward-compatible shim for voice_transcriber.meeting_pipeline."""
import sys
import voice_transcriber.meeting_pipeline as _mod

sys.modules[__name__] = _mod
