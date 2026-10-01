"""Backward-compatible shim for voice_transcriber.check_devices."""
import sys
import voice_transcriber.check_devices as _mod

sys.modules[__name__] = _mod
