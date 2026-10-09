#!/usr/bin/env python3
"""Meeting-pipeline tests: turn slicing, per-turn ASR, concurrency (milestone D4).

All of it is **model-free and device-free**: the diarizer, the ASR and the
post-processor are injection seams on :class:`MeetingPipeline`, so turn count and
ordering, the per-turn post-processor rule, the single-speaker fallback, the
dictation-priority behaviour and cancellation are all pinned without the ~2.8 GB
Cohere weights or the ~47 MB sherpa-onnx graphs
(``docs/plan-diarization.md`` sec 9).

Hermeticity: ``tests/shared/conftest.py`` blocks non-loopback connects, and the
synthetic capture below never opens a PortAudio stream.
"""
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voice_transcriber import meeting  # noqa: E402
from voice_transcriber import meeting_pipeline as mp  # noqa: E402
from voice_transcriber.diarize import Turn  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------
def _audio(seconds: float, sample_rate: int = 16000, value: float = 0.1):
    return np.full(int(seconds * sample_rate), value, dtype=np.float32)


def _scripted(turns):
    """A diarizer callable returning a fixed turn list and reporting progress."""
    def diarizer(audio, sample_rate, *, progress=None):
        if progress is not None:
            for index in range(len(turns)):
                progress(index + 1, len(turns))
        return list(turns)
    return diarizer


def _identity_post(text):
    return text


def _make_pipeline(tmp_path, *, turns, texts=None, post=_identity_post, **kwargs):
    """A pipeline with a scripted diarizer and a token-per-call transcriber."""
    calls: list[float] = []
    counter = {"n": 0}

    def transcriber(segment):
        calls.append(round(len(segment) / 16000.0, 4))
        if texts is not None:
            value = texts[counter["n"]]
        else:
            value = f"turn-{counter['n']}"
        counter["n"] += 1
        return value

    pipeline = mp.MeetingPipeline(
        diarizer=_scripted(turns),
        transcriber=transcriber,
        post_processor=post,
        output_dir=str(tmp_path),
        **kwargs,
    )
    return pipeline, calls


class _SyntheticCapture:
    """Feeds canned chunks, then waits for the session's stop event."""

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.ready = threading.Event()

    def __call__(self, on_chunk, stop, sample_rate, device):  # noqa: ARG002
        try:
            for chunk in self.chunks:
                if stop.is_set():
                    break
                on_chunk(chunk)
        finally:
            self.ready.set()
        stop.wait(5.0)


# ---------------------------------------------------------------------------
# The inference lock (transcribe2) -- the shared-model guard
# ---------------------------------------------------------------------------
def test_inference_lock_is_exposed():
    import transcribe2

    assert hasattr(transcribe2, "inference_lock")
    # It is a re-entrant lock, so the pipeline can hold it around a call into
    # ``transcribe_audio`` (which acquires it again).
    assert transcribe2.inference_lock.acquire(timeout=1.0)
    transcribe2.inference_lock.release()


def test_transcribe_audio_serializes_on_the_inference_lock(monkeypatch):
    """Two producers must never be inside one shared model at once."""
    import transcribe2

    state = {"concurrent": 0, "peak": 0}
    guard = threading.Lock()

    class _FakeBackend:
        @staticmethod
        def transcribe_audio(**_kwargs):
            with guard:
                state["concurrent"] += 1
                state["peak"] = max(state["peak"], state["concurrent"])
            time.sleep(0.05)
            with guard:
                state["concurrent"] -= 1
            return "ok"

    monkeypatch.setattr(transcribe2, "get_backend", lambda name=None: _FakeBackend)

    threads = [
        threading.Thread(
            target=transcribe2.transcribe_audio, kwargs={"audio_data": [0.1]}
        )
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5.0)

    assert state["peak"] == 1, "transcribe_audio let two calls overlap"


# ---------------------------------------------------------------------------
# Turn count and ordering (D4's stated exit criterion)
# ---------------------------------------------------------------------------
def test_two_voice_turns_are_sliced_and_transcribed_in_order(tmp_path):
    turns = [Turn(0.0, 2.0, 0), Turn(2.0, 4.0, 1), Turn(4.0, 6.0, 0), Turn(6.0, 8.0, 1)]
    pipeline, calls = _make_pipeline(
        tmp_path, turns=turns, texts=["alpha", "bravo", "charlie", "delta"]
    )

    result = pipeline.process(_audio(8.0), 16000)

    assert result.error is None
    assert [turn.speaker for turn in result.turns] == [0, 1, 0, 1]
    assert [turn.text for turn in result.turns] == ["alpha", "bravo", "charlie", "delta"]
    # Turn boundaries *are* the ASR inputs: each call is exactly one turn long.
    assert calls == [2.0, 2.0, 2.0, 2.0]
    assert "[Speaker 1] alpha" in result.text
    assert "[Speaker 2] bravo" in result.text


def test_a_turn_longer_than_the_cap_is_split_not_truncated(tmp_path):
    calls: list[float] = []

    def transcriber(segment):
        calls.append(round(len(segment) / 16000.0, 4))
        return "x"

    pipeline = mp.MeetingPipeline(
        diarizer=_scripted([Turn(0.0, 3.0, 0)]),
        transcriber=transcriber,
        post_processor=_identity_post,
        output_dir=str(tmp_path),
        max_turn_s=1.0,
    )
    result = pipeline.process(_audio(3.0), 16000)

    # Three 1 s calls, all the same speaker -- the tail is not dropped.
    assert calls == [1.0, 1.0, 1.0]
    assert [turn.speaker for turn in result.turns] == [0, 0, 0]
    assert result.turns[-1].end == pytest.approx(3.0)


def test_post_processor_is_called_once_per_turn_with_that_turns_text(tmp_path):
    turns = [Turn(0.0, 1.0, 0), Turn(1.0, 3.0, 1), Turn(3.0, 4.0, 0)]
    seen: list[str] = []

    def post(text):
        seen.append(text)
        return text.upper()

    pipeline, _ = _make_pipeline(
        tmp_path, turns=turns, texts=["one", "two", "three"], post=post
    )
    result = pipeline.process(_audio(4.0), 16000)

    # Exactly once per turn, with the turn's own text -- never the whole meeting.
    assert seen == ["one", "two", "three"]
    assert [turn.text for turn in result.turns] == ["ONE", "TWO", "THREE"]


# ---------------------------------------------------------------------------
# Diarization is an enhancement, not a prerequisite
# ---------------------------------------------------------------------------
def test_unavailable_diarization_still_produces_a_full_transcript(tmp_path):
    # diarize() returning [] is the documented fail-safe.
    pipeline, calls = _make_pipeline(tmp_path, turns=[])

    result = pipeline.process(_audio(5.0), 16000)

    assert result.error is None
    assert result.turns, "a meeting with no diarization must still be transcribed"
    assert len(result.turns) == 1
    assert result.turns[0].start == 0.0
    assert result.turns[0].end == pytest.approx(5.0)
    assert result.turns[0].text == "turn-0"
    assert calls == [5.0]
    assert result.diarized is False


def test_labels_are_suppressed_for_a_single_speaker(tmp_path):
    turns = [Turn(0.0, 2.0, 0), Turn(2.0, 4.0, 0)]
    pipeline, _ = _make_pipeline(tmp_path, turns=turns)

    result = pipeline.process(_audio(4.0), 16000)

    assert result.labelled is False
    assert "[Speaker" not in result.text
    assert result.speaker_count == 1


def test_labels_are_present_for_multiple_speakers(tmp_path):
    turns = [Turn(0.0, 2.0, 0), Turn(2.0, 4.0, 1)]
    pipeline, _ = _make_pipeline(tmp_path, turns=turns)

    result = pipeline.process(_audio(4.0), 16000)

    assert result.labelled is True
    assert result.speaker_count == 2
    assert "[Speaker 1]" in result.text and "[Speaker 2]" in result.text


def test_diarization_off_skips_the_pass(tmp_path):
    """D6: diarization off never calls the backend and yields the one-speaker doc.

    This is the byte-identity pin: with the setting off the pipeline behaves
    exactly like a build without the pass, and the same fixture labels speakers
    once it is on.
    """
    calls = {"n": 0}

    def diarizer(audio, sample_rate, *, progress=None):
        calls["n"] += 1
        return [Turn(0.0, 2.0, 0), Turn(2.0, 4.0, 1)]

    off_pipeline = mp.MeetingPipeline(
        diarizer=diarizer,
        transcriber=lambda segment: "hello",
        post_processor=_identity_post,
        output_dir=str(tmp_path / "off"),
        diarization_gate=lambda: False,
    )
    off_transcript = off_pipeline.process(_audio(4.0), 16000)

    assert calls["n"] == 0, "the backend must not be loaded or called when off"
    assert off_transcript.speaker_count == 1
    assert "[Speaker" not in off_transcript.text

    on_pipeline = mp.MeetingPipeline(
        diarizer=diarizer,
        transcriber=lambda segment: "hello",
        post_processor=_identity_post,
        output_dir=str(tmp_path / "on"),
        diarization_gate=lambda: True,
    )
    on_transcript = on_pipeline.process(_audio(4.0), 16000)

    assert calls["n"] == 1
    assert on_transcript.speaker_count == 2
    assert "[Speaker 1]" in on_transcript.text


def test_speakers_hint_is_passed_through(tmp_path):
    """D6: an exact speaker count is a hint, auto means let the backend decide."""
    seen = {}

    def diarizer(audio, sample_rate, *, progress=None, num_speakers=None):
        seen["num_speakers"] = num_speakers
        return [Turn(0.0, 1.0, 0)]

    pipeline = mp.MeetingPipeline(
        diarizer=diarizer,
        transcriber=lambda segment: "x",
        post_processor=_identity_post,
        output_dir=str(tmp_path),
        diarization_gate=lambda: True,
        speakers_hint=lambda: 3,
    )
    pipeline.process(_audio(1.0), 16000)
    assert seen["num_speakers"] == 3

    # "auto" is passed through as no hint at all (the backend's num_clusters=-1).
    seen.clear()
    auto_pipeline = mp.MeetingPipeline(
        diarizer=diarizer,
        transcriber=lambda segment: "x",
        post_processor=_identity_post,
        output_dir=str(tmp_path),
        diarization_gate=lambda: True,
        speakers_hint=lambda: "auto",
    )
    auto_pipeline.process(_audio(1.0), 16000)
    assert seen["num_speakers"] is None


def test_audio_is_resampled_to_16k_mono(tmp_path):
    seen = {}

    def diarizer(audio, sample_rate, *, progress=None):
        seen["frames"] = len(audio)
        seen["rate"] = sample_rate
        return [Turn(0.0, 2.0, 0)]

    pipeline = mp.MeetingPipeline(
        diarizer=diarizer,
        transcriber=lambda segment: "x",
        post_processor=_identity_post,
        output_dir=str(tmp_path),
    )
    pipeline.process(_audio(4.0, sample_rate=8000), 8000)

    assert seen["rate"] == 16000
    assert seen["frames"] == 4 * 16000


# ---------------------------------------------------------------------------
# Dictation priority and the shared lock
# ---------------------------------------------------------------------------
def test_pipeline_waits_while_a_dictation_is_live(tmp_path):
    turns = [Turn(0.0, 1.0, 0), Turn(1.0, 2.0, 1)]
    dictating = threading.Event()
    dictating.set()
    calls: list[float] = []

    def transcriber(segment):
        calls.append(len(segment) / 16000.0)
        return "x"

    pipeline = mp.MeetingPipeline(
        is_dictating=dictating.is_set,
        diarizer=_scripted(turns),
        transcriber=transcriber,
        post_processor=_identity_post,
        output_dir=str(tmp_path),
        poll_s=0.01,
    )
    worker = threading.Thread(target=pipeline.process, args=(_audio(2.0), 16000))
    worker.start()
    try:
        # The diarizer has run, but no ASR may start while dictation is live.
        time.sleep(0.3)
        assert calls == [], "pipeline did not yield to a live dictation"
    finally:
        dictating.clear()
        worker.join(5.0)

    assert not worker.is_alive()
    assert len(calls) == 2


def test_inference_lock_is_released_between_turns(tmp_path):
    """The lock is per-turn, never held across the whole meeting."""
    import transcribe2

    turns = [Turn(0.0, 1.0, 0), Turn(1.0, 2.0, 1)]
    calls: list[int] = []
    first_done = threading.Event()
    release = threading.Event()

    def transcriber(segment):
        calls.append(1)
        first_done.set()
        return "x"

    def is_dictating():
        # Live only *after* the first turn, so the pipeline pauses between turns
        # and the test can probe the lock there.
        return len(calls) >= 1 and not release.is_set()

    pipeline = mp.MeetingPipeline(
        is_dictating=is_dictating,
        diarizer=_scripted(turns),
        transcriber=transcriber,
        post_processor=_identity_post,
        output_dir=str(tmp_path),
        poll_s=0.01,
    )
    worker = threading.Thread(target=pipeline.process, args=(_audio(2.0), 16000))
    worker.start()
    try:
        assert first_done.wait(5.0), "first turn never ran"
        # Between turns the pipeline is waiting on the dictation flag without
        # the lock, so another producer can take it.
        assert transcribe2.inference_lock.acquire(timeout=2.0)
        transcribe2.inference_lock.release()
    finally:
        release.set()
        worker.join(5.0)

    assert len(calls) == 2


# ---------------------------------------------------------------------------
# Cancellation and failure
# ---------------------------------------------------------------------------
def test_cancel_stops_at_the_next_turn_and_leaves_no_artifact(tmp_path):
    turns = [Turn(0.0, 1.0, 0), Turn(1.0, 2.0, 1), Turn(2.0, 3.0, 0)]
    started = threading.Event()
    proceed = threading.Event()
    calls: list[int] = []

    def transcriber(segment):
        calls.append(1)
        started.set()
        proceed.wait(2.0)  # hold the first turn so cancel lands mid-meeting
        return "x"

    pipeline = mp.MeetingPipeline(
        diarizer=_scripted(turns),
        transcriber=transcriber,
        post_processor=_identity_post,
        output_dir=str(tmp_path),
    )
    worker = threading.Thread(target=pipeline.process, args=(_audio(3.0), 16000))
    worker.start()
    assert started.wait(5.0)
    pipeline.cancel()
    proceed.set()
    worker.join(5.0)

    result = pipeline.last_transcript
    assert result is not None
    assert result.cancelled is True
    assert result.path is None
    assert len(calls) == 1, "cancel must take effect at the next turn boundary"
    assert list(tmp_path.iterdir()) == [], "a cancelled run must leave no artifact"


def test_write_failure_keeps_the_transcript_in_memory(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    pipeline = mp.MeetingPipeline(
        diarizer=_scripted([Turn(0.0, 1.0, 0)]),
        transcriber=lambda segment: "hello",
        post_processor=_identity_post,
        output_dir=str(blocker),
    )

    result = pipeline.process(_audio(1.0), 16000)

    assert result.error is not None
    assert result.path is None
    assert "hello" in result.text
    # No stray temp file next to the (unwritable) target.
    assert list(tmp_path.iterdir()) == [blocker]


def test_invalid_audio_returns_an_error_and_never_raises(tmp_path):
    pipeline = mp.MeetingPipeline(
        diarizer=_scripted([Turn(0.0, 1.0, 0)]),
        transcriber=lambda segment: "x",
        post_processor=_identity_post,
        output_dir=str(tmp_path),
    )

    result = pipeline.process(_audio(1.0), -1)

    assert result.error is not None
    assert result.turns == []
    assert list(tmp_path.iterdir()) == []


def test_one_bad_turn_does_not_poison_the_transcript(tmp_path):
    turns = [Turn(0.0, 1.0, 0), Turn(1.0, 2.0, 1), Turn(2.0, 3.0, 0)]
    seen: list[str] = []

    def post(text):
        seen.append(text)
        if len(seen) == 2:
            raise RuntimeError("post exploded")
        return text.upper()

    pipeline, _ = _make_pipeline(
        tmp_path, turns=turns, texts=["one", "two", "three"], post=post
    )
    result = pipeline.process(_audio(3.0), 16000)

    assert result.error is None
    # The failed turn keeps its raw text; the rest are unaffected.
    assert [turn.text for turn in result.turns] == ["ONE", "two", "THREE"]


def test_pipeline_never_raises_when_the_diarizer_explodes(tmp_path):
    def boom(audio, sample_rate, *, progress=None):
        raise RuntimeError("diarizer exploded")

    pipeline = mp.MeetingPipeline(
        diarizer=boom,
        transcriber=lambda segment: "survived",
        post_processor=_identity_post,
        output_dir=str(tmp_path),
    )
    result = pipeline.process(_audio(2.0), 16000)

    # A broken diarizer degrades to one speaker; it never costs the meeting.
    assert result.error is None
    assert len(result.turns) == 1
    assert result.turns[0].text == "survived"


def test_cancel_releases_a_pipeline_stuck_waiting_for_dictation(tmp_path):
    """A quit must not block on a pipeline parked on a live dictation."""
    dictating = threading.Event()
    dictating.set()
    pipeline = mp.MeetingPipeline(
        is_dictating=dictating.is_set,
        diarizer=_scripted([Turn(0.0, 1.0, 0)]),
        transcriber=lambda segment: "x",
        post_processor=_identity_post,
        output_dir=str(tmp_path),
        poll_s=0.01,
    )

    worker = threading.Thread(target=pipeline.process, args=(_audio(1.0), 16000))
    worker.start()
    time.sleep(0.1)
    pipeline.cancel()
    worker.join(2.0)

    assert not worker.is_alive(), "cancel did not unblock a dictation wait"


# ---------------------------------------------------------------------------
# Progress and stages
# ---------------------------------------------------------------------------
def test_progress_is_monotonic_and_reaches_100(tmp_path):
    turns = [Turn(0.0, 1.0, 0), Turn(1.0, 2.0, 1), Turn(2.0, 3.0, 0)]
    stages: list[str] = []
    progress: list[tuple[int, int]] = []
    pipeline, _ = _make_pipeline(
        tmp_path,
        turns=turns,
        stage_callback=stages.append,
        progress=lambda processed, total: progress.append((processed, total)),
    )

    pipeline.process(_audio(3.0), 16000)

    assert "diarizing" in stages
    assert "transcribing" in stages
    assert "rendering" in stages
    percentages = [round(100.0 * p / t) for p, t in progress if t]
    assert percentages == sorted(percentages), "progress went backwards"
    assert percentages[-1] == 100


# ---------------------------------------------------------------------------
# The artifact and the status seam
# ---------------------------------------------------------------------------
def test_artifact_has_a_header_and_labelled_turns(tmp_path):
    turns = [Turn(0.0, 65.0, 0), Turn(65.0, 125.0, 1)]
    pipeline, _ = _make_pipeline(
        tmp_path, turns=turns, texts=["Hello there.", "General Kenobi."],
        max_turn_s=1000.0,
    )

    result = pipeline.process(_audio(125.0), 16000)

    assert result.path is not None
    content = Path(result.path).read_text(encoding="utf-8")
    assert "Meeting transcript" in content
    assert "Duration: 02:05" in content
    assert "Speakers: 2" in content
    assert "[Speaker 1] Hello there." in content
    assert "[Speaker 2] General Kenobi." in content


# ---------------------------------------------------------------------------
# End-to-end: session capture -> pipeline -> status, plus spill cleanup
# ---------------------------------------------------------------------------
def test_meeting_session_drives_the_pipeline_end_to_end(tmp_path):
    chunk = _audio(1.0, sample_rate=16000)
    capture = _SyntheticCapture([chunk, chunk])
    turns = [Turn(0.0, 1.0, 0), Turn(1.0, 2.0, 1)]
    pipeline, _ = _make_pipeline(tmp_path, turns=turns, texts=["hi", "there"])
    session = meeting.MeetingSession(
        sample_rate=16000, capture=capture, directory=str(tmp_path),
        on_audio=pipeline.process,
    )

    session.start(spill_minutes=1)
    assert capture.ready.wait(5.0)
    session.stop()
    assert session.wait(5.0) is True

    assert session.temp_path is None
    assert list(tmp_path.iterdir()) == [Path(pipeline.last_transcript.path)]
    assert pipeline.last_transcript.speaker_count == 2


def _make_engine(tmp_path, monkeypatch):
    """A real ``SimpleVoiceTranscriber`` with no TUI, no audio, no model."""
    from unittest.mock import MagicMock

    import main
    import t2

    monkeypatch.setattr(t2, "CONFIG_FILE", str(tmp_path / "config.yaml"))
    t2.set_meeting("off")
    t2.set_meeting_spill_minutes(10)
    engine = main.SimpleVoiceTranscriber.__new__(main.SimpleVoiceTranscriber)
    engine.tui = MagicMock()
    engine.tui.state = "READY"
    engine.recording = False
    engine.hotkey_system = None
    engine.last_transcription = ""
    return engine


def test_status_exposes_the_transcript_without_the_filesystem(tmp_path, monkeypatch):
    import t2

    engine = _make_engine(tmp_path, monkeypatch)
    t2.set_meeting("on")
    turns = [Turn(0.0, 1.0, 0), Turn(1.0, 2.0, 1)]
    pipeline, _ = _make_pipeline(tmp_path, turns=turns, texts=["alpha", "bravo"])
    engine.meeting_pipeline = pipeline
    capture = _SyntheticCapture([_audio(1.0), _audio(1.0)])
    engine.meeting = meeting.MeetingSession(
        sample_rate=16000, capture=capture, directory=str(tmp_path),
        on_audio=pipeline.process,
    )
    try:
        engine.handle_control("meeting-start", {})
        assert capture.ready.wait(5.0)
        engine.handle_control("meeting-stop", {})
        assert engine.meeting.wait(5.0)

        status = engine.handle_control("status", {})
        assert status["meeting_state"] == "idle"
        assert "alpha" in status["meeting_transcript"]
        assert "bravo" in status["meeting_transcript"]
        assert status["meeting_speakers"] == 2
        assert status["meeting_transcript_path"] is not None
        assert status["meeting_progress"] == 100
    finally:
        t2.set_meeting("off")


def test_cleanup_cancels_a_running_pipeline(tmp_path, monkeypatch):
    """``SimpleVoiceTranscriber.cleanup`` must not wait out a whole meeting."""
    import t2

    engine = _make_engine(tmp_path, monkeypatch)
    dictating = threading.Event()
    dictating.set()
    capture = _SyntheticCapture([_audio(1.0)])
    engine.meeting = meeting.MeetingSession(
        sample_rate=16000, capture=capture, directory=str(tmp_path),
    )
    # Mirror the engine wiring: the pipeline shares the session's cancel event.
    pipeline = mp.MeetingPipeline(
        is_dictating=dictating.is_set,
        progress=engine.meeting.set_progress,
        stage_callback=engine.meeting.set_stage,
        cancel_event=engine.meeting.cancel_event,
        diarizer=_scripted([Turn(0.0, 1.0, 0)]),
        transcriber=lambda segment: "x",
        post_processor=_identity_post,
        output_dir=str(tmp_path),
        poll_s=0.01,
    )
    engine.meeting_pipeline = pipeline
    engine.meeting.set_audio_consumer(pipeline.process)
    t2.set_meeting("on")
    try:
        engine.handle_control("meeting-start", {})
        assert capture.ready.wait(5.0)
        engine.handle_control("meeting-stop", {})
        # Wait until the pipeline is parked waiting for the (never-ending)
        # dictation, then clean up and assert it returns promptly.
        deadline = time.monotonic() + 5.0
        while pipeline.stage != "transcribing" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pipeline.stage == "transcribing", "pipeline never reached the ASR stage"
        started = time.monotonic()
        engine.cleanup()
        elapsed = time.monotonic() - started
        assert elapsed < 5.0, f"cleanup blocked for {elapsed:.1f}s"
        assert pipeline.cancel_event.is_set()
    finally:
        t2.set_meeting("off")


# ---------------------------------------------------------------------------
# Real weights (skipped when the diarization models are absent)
# ---------------------------------------------------------------------------
def _diarization_available() -> bool:
    try:
        from voice_transcriber.diarizers import sherpa_onnx

        return bool(sherpa_onnx.available())
    except Exception:
        return False


@pytest.mark.skipif(not _diarization_available(), reason="diarization weights not installed")
def test_real_diarizer_produces_a_transcript(tmp_path):
    sample_rate = 16000
    samples = np.linspace(0, 4.0, 4 * sample_rate, endpoint=False)
    audio = (0.2 * np.sin(2 * np.pi * 220 * samples)).astype(np.float32)
    pipeline = mp.MeetingPipeline(
        transcriber=lambda segment: "spoken",
        post_processor=_identity_post,
        output_dir=str(tmp_path),
    )

    result = pipeline.process(audio, sample_rate)

    assert result.error is None
    assert result.turns, "the real diarizer produced no turns at all"
    assert os.path.exists(result.path)
