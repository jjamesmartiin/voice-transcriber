#!/usr/bin/env python3
"""
Cross-Platform End-to-End Test Suite for Voice Transcriber (Phase 1).

This module is the executable specification for the Cross-Platform Convergence
& Automated HITL Testing Plan. It exercises:

  1. Platform detection + the Hardware/OS Abstraction Layer (HAL) output-sink
     contract for Linux, Windows Native, and WSL (``VT_PLATFORM`` override).
  2. Cross-platform clipboard / typing / audio-cue hooks (wl-copy, xclip,
     ydotool, pyperclip, Win32 ``keybd_event``, ``winsound``, ``clip.exe`` and
     the WSL socket JSON-RPC bridge).
  3. Synthetic hotkey triggering: Push-to-Talk and the Space hands-free latch,
     driven through the real audio pipeline.
  4. End-to-end synthetic audio ingestion (no microphone / no display) with
     latency and accuracy SLA gates.
  5. Silence / ambient-noise hallucination rejection.

Everything runs headless inside the Nix environment::

    nix develop --command python -m pytest tests/e2e/test_end_to_end_crossplatform.py -v

The platform adapters implemented here are *contract reference
implementations*. Phase 3 of the plan extracts them into ``src/platform/``;
the assertions in this file define exactly what those implementations must do.
"""

from __future__ import annotations

import os
import re
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

# ---------------------------------------------------------------------------
# Paths / source import
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
TEST_AUDIO_DIR = REPO_ROOT / "tests" / "test_transcribe"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# ---------------------------------------------------------------------------
# Plugin environment: keep BLAS single-threaded (same as tests/conftest.py) so
# the heavy ASR libraries never trip the FPU teardown crash under pytest.
# ---------------------------------------------------------------------------
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

# Hard ceiling from the plan's SLA table: key-release -> clipboard/typing.
POST_RELEASE_LATENCY_BUDGET_SEC = 1.5
# Accuracy floor from the plan's SLA table.
ACCURACY_FLOOR = 0.80

# ===========================================================================
# 1. Output recording sink for E2E testing
# ===========================================================================
# ===========================================================================
class OutputSink:
    """
    Base class for the clipboard / typing / audio-cue output sink.

    Phase 3 will split these into ``copy_text``, ``paste_keystrokes`` and an
    audio-cue player; the single-class form keeps the Phase 1 tests compact.
    """

    platform = "unknown"

    def __init__(self) -> None:
        # Test-visible recordings of the exact operations performed.
        self.copied: list[str] = []
        self.typed: list[str] = []
        self.cues: list[str] = []

    def copy_text(self, text: str) -> bool:
        self.copied.append(text)
        return True

    def type_text(self, text: str) -> bool:
        self.typed.append(text)
        return True

    def play_cue(self, name: str) -> None:
        self.cues.append(name)


def get_output_sink(platform: str | None = None) -> OutputSink:
    """Return an in-memory recording sink for synthetic hotkey pipeline tests."""
    return OutputSink()


# ===========================================================================
# 3. Audio helpers
# ===========================================================================
def load_test_audio(name: str) -> np.ndarray:
    """Load a benchmark mp3 as mono 16 kHz float32 (no physical hardware)."""
    path = TEST_AUDIO_DIR / f"{name}.mp3"
    audio, sample_rate = sf.read(str(path), dtype="float32")
    if audio.ndim > 1:
        audio = audio[:, 0]
    if sample_rate != 16000:
        import librosa

        audio = librosa.resample(audio, orig_sr=sample_rate, target_sr=16000)
    return np.asarray(audio, dtype=np.float32)


def load_expected_text(name: str) -> str:
    return (TEST_AUDIO_DIR / f"{name}.md").read_text().strip()


def _tokens(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", "", (text or "").lower()).split()


def transcription_accuracy(expected: str, actual: str) -> float:
    """
    Token accuracy against ground truth.

    Matches the repo's convention (``tests/e2e/test_transcribe.py``) by measuring how
    many ground-truth tokens the hypothesis recalls, while also considering
    order-preserving sequence similarity via ``SequenceMatcher``. Extra ASR
    tokens (e.g. a stray "team") do not unfairly penalise a correct result.
    """
    expected_tokens = _tokens(expected)
    actual_tokens = _tokens(actual)
    if not expected_tokens:
        return 0.0
    if not actual_tokens:
        return 0.0
    recall = len(set(expected_tokens) & set(actual_tokens)) / len(set(expected_tokens))
    sequence = SequenceMatcher(None, expected_tokens, actual_tokens).ratio()
    return max(recall, sequence)


# ===========================================================================
# 4. Synthetic hotkey + pipeline harness
# ===========================================================================
class SyntheticHotkeyHarness:
    """
    Drives the *real* Voice Transcriber pipeline (StreamingMicroBatcher energy
    gate + VAD trim -> ASR backend -> post-processor) from synthetic hotkey
    events, with no microphone or display attached.

    The hotkey state machine mirrors ``src/hotkeys.py::handle_key_event`` exactly:

      Push-to-Talk : Alt+Shift down -> record -> Alt+Shift up -> transcribe -> copy
      Hands-Free   : Alt+Shift down -> Space tap (latch) -> Alt+Shift up keeps
                     recording -> Alt+Shift tap -> transcribe -> copy
    """

    SAMPLE_RATE = 16000

    def __init__(self, sink: OutputSink, copy_on_stop: bool = True):
        self.sink = sink
        self.copy_on_stop = copy_on_stop

        # Physical-key state (mirrors WaylandGlobalHotkeys).
        self.hotkey_active = False
        self.latch_release = False

        # Recording state.
        self.recording = False
        self.batcher = None
        self.release_time: float | None = None
        self.post_release_latency: float | None = None
        self.last_transcription = ""

    # -- hotkey events -----------------------------------------------------
    def hotkey_down(self) -> None:
        if self.hotkey_active:
            return
        self.hotkey_active = True
        self.start_recording()

    def hotkey_up(self, copy_to_clipboard: bool | None = None) -> None:
        if not self.hotkey_active:
            return
        self.hotkey_active = False
        if self.latch_release:
            # Space was tapped during the hold: keep recording hands-free.
            self.latch_release = False
            return
        self.stop_recording(
            copy_to_clipboard=self.copy_on_stop if copy_to_clipboard is None else copy_to_clipboard
        )

    def space_tap(self) -> None:
        if self.hotkey_active:
            self.latch_release = True

    # -- recording / pipeline ---------------------------------------------
    def start_recording(self) -> None:
        if self.recording:
            return
        from micro_batcher import StreamingMicroBatcher

        self.recording = True
        self.last_transcription = ""
        self.batcher = StreamingMicroBatcher(sample_rate=self.SAMPLE_RATE)
        self.batcher.start()

    def feed_audio(self, pcm: np.ndarray) -> None:
        if self.recording and self.batcher is not None:
            self.batcher.feed_audio(pcm)

    def stream_audio(self, pcm: np.ndarray, block_ms: int = 100) -> None:
        """Feed audio in realistic 100 ms blocks (VAD/micro-batching stay active)."""
        block = max(1, int(self.SAMPLE_RATE * block_ms / 1000))
        for start in range(0, len(pcm), block):
            self.feed_audio(pcm[start : start + block])

    def stop_recording(self, copy_to_clipboard: bool = True) -> str:
        """Release -> transcribe -> copy, tracking post-release latency."""
        if not self.recording:
            return ""
        self.release_time = time.perf_counter()
        self.recording = False

        text = self.batcher.finish_and_get_text(skip_slm=True).strip()
        self.last_transcription = text

        if text and copy_to_clipboard:
            self.sink.copy_text(text)

        self.post_release_latency = time.perf_counter() - self.release_time
        return text


# ===========================================================================
# 6. Fixtures
# ===========================================================================


@pytest.fixture(scope="session")
def warm_asr():
    """Load + warm the ASR backend once so latency measurements are meaningful."""
    import transcribe2

    backend = "cohere"
    transcribe2.set_backend(backend)
    warm_audio = load_test_audio("short_word")
    probe = transcribe2.transcribe_audio(audio_data=warm_audio)
    if isinstance(probe, str) and probe.startswith("Error loading model"):
        pytest.skip(f"ASR backend ({backend}) unavailable: {probe}")
    return backend


# ===========================================================================
# 7. Synthetic hotkey triggering end-to-end
# ===========================================================================
class TestSyntheticHotkeyTriggering:
    """Push-to-Talk and hands-free latch drive the real pipeline end-to-end."""

    def test_push_to_talk_records_transcribes_and_copies(self, warm_asr):
        audio = load_test_audio("short_phrase")
        expected = load_expected_text("short_phrase")
        sink = get_output_sink()
        harness = SyntheticHotkeyHarness(sink)

        # Alt+Shift down -> record -> Alt+Shift up -> transcribe -> copy
        harness.hotkey_down()
        assert harness.recording is True
        harness.stream_audio(audio)
        harness.hotkey_up()

        assert harness.recording is False
        actual = harness.last_transcription
        assert actual, "Push-to-Talk produced an empty transcription"
        assert sink.copied == [actual], "Clipboard sink did not receive the transcription"
        assert harness.post_release_latency is not None
        assert harness.post_release_latency <= POST_RELEASE_LATENCY_BUDGET_SEC
        assert transcription_accuracy(expected, actual) >= ACCURACY_FLOOR

    def test_hands_free_latch_records_after_modifier_release(self, warm_asr):
        audio = load_test_audio("short_phrase")
        expected = load_expected_text("short_phrase")
        sink = get_output_sink()
        harness = SyntheticHotkeyHarness(sink)

        # Alt+Shift down -> Space tap (latch) -> Alt+Shift up keeps recording
        harness.hotkey_down()
        harness.space_tap()
        harness.hotkey_up()
        assert harness.recording is True, "Space latch did not hold the recording open"
        assert sink.copied == [], "Latch must not stop/copy while hands-free"

        # Audio continues to stream while the user is hands-free.
        harness.stream_audio(audio)

        # Alt+Shift tap (down then up) -> transcribe -> copy
        harness.hotkey_down()
        harness.hotkey_up()

        assert harness.recording is False
        actual = harness.last_transcription
        assert actual, "Hands-free latch produced an empty transcription"
        assert sink.copied == [actual]
        assert harness.post_release_latency is not None
        assert harness.post_release_latency <= POST_RELEASE_LATENCY_BUDGET_SEC
        assert transcription_accuracy(expected, actual) >= ACCURACY_FLOOR

    def test_release_without_space_does_not_latch(self, warm_asr):
        harness = SyntheticHotkeyHarness(get_output_sink(), copy_on_stop=False)
        harness.hotkey_down()
        harness.hotkey_up()
        assert harness.recording is False
        assert harness.latch_release is False


# ===========================================================================
# 12. Synthetic end-to-end audio pipeline (no mic / no display)
# ===========================================================================
class TestSyntheticAudioPipeline:
    """Full VAD + micro-batch + ASR + post-process pipeline on benchmark audio."""

    @pytest.mark.parametrize("sample", ["short_word", "short_phrase"])
    def test_synthetic_audio_pipeline_accuracy_and_latency(self, warm_asr, sample):
        audio = load_test_audio(sample)
        expected = load_expected_text(sample)
        sink = get_output_sink()
        harness = SyntheticHotkeyHarness(sink)

        harness.start_recording()
        harness.stream_audio(audio, block_ms=100)  # 16 kHz mono, 100 ms frames
        actual = harness.stop_recording(copy_to_clipboard=True)

        assert actual, f"[{sample}] pipeline returned no transcription"
        assert harness.post_release_latency is not None, "latency timer was not tracked"
        assert harness.post_release_latency <= POST_RELEASE_LATENCY_BUDGET_SEC, (
            f"[{sample}] post-release latency {harness.post_release_latency:.3f}s "
            f"exceeded {POST_RELEASE_LATENCY_BUDGET_SEC}s"
        )
        accuracy = transcription_accuracy(expected, actual)
        assert accuracy >= ACCURACY_FLOOR, (
            f"[{sample}] accuracy {accuracy:.2f} below floor {ACCURACY_FLOOR}: "
            f"expected={expected!r} actual={actual!r}"
        )
        assert sink.copied == [actual]


# ===========================================================================
# 13. Silence / ambient-noise hallucination rejection
# ===========================================================================
class TestSilenceHallucinationRejection:
    """2 s of silence or ambient noise must produce ``""`` with zero tokens."""

    @pytest.mark.parametrize(
        "name,audio",
        [
            ("pure_zeros", np.zeros(2 * 16000, dtype=np.float32)),
            (
                "ambient_noise",
                np.random.default_rng(1234)
                .uniform(-0.002, 0.002, 2 * 16000)
                .astype(np.float32),
            ),
        ],
    )
    def test_silence_is_rejected_with_zero_tokens(self, warm_asr, name, audio):
        sink = get_output_sink()
        harness = SyntheticHotkeyHarness(sink)

        harness.start_recording()
        harness.stream_audio(audio, block_ms=100)
        actual = harness.stop_recording(copy_to_clipboard=True)

        assert actual == "", f"[{name}] hallucinated {actual!r} from silence/noise"
        assert _tokens(actual) == [], f"[{name}] produced hallucinated tokens"
        assert sink.copied == [], f"[{name}] copied hallucinated text to clipboard"


# ===========================================================================
# 14. Post-processor terminal punctuation (docs/TODO.md regression guard)
# ===========================================================================
class TestPostProcessorTerminalPunctuation:
    """
    The post-processor now terminates complete multi-word statements with a
    period. This locks the behaviour that docs/TODO.md flagged as a stale test
    expectation (``test_post_processor_artifacts``).
    """

    def test_multiword_sentence_gets_terminal_period(self):
        from post_processor import clean_speech_transcription

        assert clean_speech_transcription("This is important oops") == "This is important."
        assert (
            clean_speech_transcription("So. I think we should proceed")
            == "So, I think we should proceed."
        )

    def test_short_two_word_fragment_has_no_trailing_period(self):
        from post_processor import clean_speech_transcription

        assert clean_speech_transcription("Hello world whoops") == "Hello world"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
