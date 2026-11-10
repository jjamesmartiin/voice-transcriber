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

    def test_record_audio_stream_honours_independent_stop_event(self, monkeypatch):
        """A cycle-specific stop_event must not be tricked if global stop_recording is cleared."""
        monkeypatch.setattr(t2, "sd", _FakeSD())
        monkeypatch.setattr(t2, "_is_wsl", lambda: False)
        monkeypatch.setattr(t2, "INPUT_DEVICE_INDEX", 0)

        t2.stop_recording.clear()
        cycle_stop = threading.Event()
        cycle_stop.set()

        frames = t2.record_audio_stream(stop_event=cycle_stop)
        assert isinstance(frames, list)



# ---------------------------------------------------------------------------
# Resampling: a 48 kHz-only device must not come out ringing or drifting
# ---------------------------------------------------------------------------
class TestStreamingResampler:
    """`resample_poly` per block is wrong twice over.

    Each call zero-pads both ends (a filter transient at every ~21 ms boundary)
    and returns ``ceil(len * up / down)`` samples, so a 48 kHz stream arrives a
    little short of 16 kHz and the shortfall accumulates across an utterance.
    Measured against a whole-signal resample on a two-tone signal: -3 dB SNR
    before, +18 dB after.
    """

    def _tone(self, rate, seconds=2.0):
        t = np.arange(int(rate * seconds)) / rate
        return (0.5 * np.sin(2 * np.pi * 440 * t)
                + 0.3 * np.sin(2 * np.pi * 2100 * t)).astype(np.float32)

    def _stream(self, resampler, block, size):
        return np.concatenate([resampler.process(block[i:i + 1024])
                               for i in range(0, size, 1024)])

    def test_the_stream_lands_at_exactly_the_target_rate(self):
        for rate in (48000, 44100, 32000):
            block = self._tone(rate)
            out = self._stream(t2.make_resampler(rate), block, len(block))
            expected = round(len(block) * 16000 / rate)
            assert abs(len(out) - expected) <= 1, (rate, len(out), expected)

    def test_odd_block_sizes_do_not_drift(self):
        """PortAudio does not promise our block size."""
        rate = 48000
        block = self._tone(rate)
        resampler = t2.make_resampler(rate)
        chunks = [resampler.process(block[i:i + 1470]) for i in range(0, len(block), 1470)]
        assert abs(sum(len(c) for c in chunks) - round(len(block) / 3)) <= 1

    def test_it_matches_a_whole_signal_resample(self):
        import scipy.signal as ss

        rate = 48000
        block = self._tone(rate)
        reference = ss.resample_poly(block, 16000, rate)
        out = self._stream(t2.make_resampler(rate), block, len(block))

        n = min(len(reference), len(out))
        snr = 10 * np.log10(np.sum(reference[:n] ** 2)
                            / max(np.sum((reference[:n] - out[:n]) ** 2), 1e-20))
        assert snr > 12.0, f"streamed resample is only {snr:.1f} dB from the reference"

    def test_it_stays_float32_and_finite(self):
        out = self._stream(t2.make_resampler(48000), self._tone(48000), 48000)
        assert out.dtype == np.float32
        assert np.isfinite(out).all()

    def test_a_native_16k_device_is_passed_through_untouched(self):
        block = np.arange(10, dtype=np.float32)
        assert t2.make_resampler(16000).process(block).tolist() == block.tolist()
        # An unknown/unset rate must not raise either.
        assert t2.make_resampler(None).process(block).size == 10
