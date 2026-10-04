"""Backward-compatible shim for voice_transcriber.dictionary."""
import os
import sys

_src_dir = os.path.dirname(os.path.abspath(__file__))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

import voice_transcriber.dictionary as _mod

sys.modules[__name__] = _mod
sys.modules["dictionary"] = _mod
sys.modules["voice_transcriber.dictionary"] = _mod

if __name__ == "__main__":
    _mod.main()

