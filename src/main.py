#!/usr/bin/env python3
"""Backward-compatible entry point for Voice Transcriber."""
import os
import sys

_src_dir = os.path.dirname(os.path.abspath(__file__))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

import voice_transcriber.main as _vt_main

# When imported as a module, replace self with voice_transcriber.main
# so monkeypatching main.<attr> patches voice_transcriber.main.<attr>.
if __name__ != "__main__":
    sys.modules[__name__] = _vt_main
else:
    _vt_main.cli()
