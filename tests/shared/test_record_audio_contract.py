#!/usr/bin/env python3
"""Contract test for ``t2.record_audio_stream``.

The Windows/native fast path returns a raw *list* of frames while the WSL and
fallback paths return a concatenated NumPy array. Downstream code
(``process_recording`` and the micro-batcher) must accept both. This pins the
list contract using a fake ``sounddevice`` so no microphone is required.
"""
import sys
import threading
from pathlib import Path

import numpy as np

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import t2  # noqa: E402

BLOCK = 1024
N_BLOCKS = 3


class _FakeStream:
    def __init__(self, callback=None, **_kwargs):
        self._callback = callback

    def __enter__(self):
        for _ in range(N_BLOCKS):
            self._callback(
                np.zeros((BLOCK, 1), dtype=np.float32), BLOCK, None, None
            )
        # End the capture loop as soon as the fake blocks have been queued.
        t2.stop_recording.set()
        return self

    def __exit__(self, *exc):
        return False


class _FakeSD:
    def InputStream(self, **kwargs):
        return _FakeStream(**kwargs)

    def query_devices(self, _idx=None):
        return {
            "name": "Fake Mic",
            "max_input_channels": 1,
            "default_samplerate": 16000,
        }


class TestRecordAudioStreamContract:
    def test_fast_path_returns_consumable_frame_list(self, monkeypatch):
        monkeypatch.setattr(t2, "sd", _FakeSD())
        monkeypatch.setattr(t2, "_is_wsl", lambda: False)
        monkeypatch.setattr(t2, "INPUT_DEVICE_INDEX", 0)
        monkeypatch.setattr(t2, "stop_recording", threading.Event())

        frames = t2.record_audio_stream()

        # Fast path returns a Python list (this is what broke the .size check).
        assert isinstance(frames, list)
        assert len(frames) == N_BLOCKS
        for frame in frames:
            assert isinstance(frame, np.ndarray)
            assert frame.dtype == np.float32
            assert frame.ndim == 1

        # Must be consumable exactly like the numpy path downstream.
        joined = np.concatenate(frames)
        assert joined.shape == (N_BLOCKS * BLOCK,)
        assert len(frames) > 0  # works with the process_recording guard
