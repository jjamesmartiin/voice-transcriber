#!/usr/bin/env python3
"""Unit tests for the meeting-mode capture lifecycle (milestone D2).

Everything here is model-free and device-free: the new
``voice_transcriber.meeting`` module is exercised through its injected capture
and audio-consumer seams, so the state machine, the spill buffer and the
progress arithmetic are all proven without a microphone and without the
~50 MB of diarization weights (``docs/plan-diarization.md`` sec 9).

Hermeticity: ``tests/shared/conftest.py`` blocks real non-loopback connects, and
the synthetic capture below never opens a PortAudio stream.
"""
import os
import subprocess
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
class SyntheticCapture:
    """A capture source that feeds canned chunks and then waits to be stopped."""

    def __init__(self, chunks, *, fail=None, ready=None):
        self.chunks = list(chunks)
        self.fail = fail
        self.ready = ready or threading.Event()
        self.device = "UNSET"

    def __call__(self, on_chunk, stop, sample_rate, device):
        self.device = device
        try:
            for chunk in self.chunks:
                if stop.is_set():
                    break
                on_chunk(chunk)
        finally:
            self.ready.set()
        if self.fail is not None:
            raise self.fail
        stop.wait(5.0)


def _audio_seconds(seconds, sample_rate=16000, value=0.1):
    return np.full(int(seconds * sample_rate), value, dtype=np.float32)


def _start_and_wait_ready(session, capture, **kwargs):
    status = session.start(**kwargs)
    assert status.state == "recording"
    assert capture.ready.wait(5.0), "capture never started"
    return status


# ---------------------------------------------------------------------------
# The import-free rule (mirrors test_diarize.py)
# ---------------------------------------------------------------------------
def test_importing_meeting_does_not_import_sounddevice_or_t2():
    code = (
        "import sys; sys.path.insert(0, %r); "
        "from voice_transcriber import meeting; "
        "assert 'sounddevice' not in sys.modules, 'sounddevice leaked into import'; "
        "assert 't2' not in sys.modules, 't2 leaked into import'; "
        "assert 'main' not in sys.modules, 'main leaked into import'; "
        "print('OK')" % str(SRC_DIR)
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# Spill threshold arithmetic / normalisation
# ---------------------------------------------------------------------------
def test_default_spill_threshold_is_ten_minutes():
    """600 s x 16 000 frames/s x 4 bytes = ~38.4 MB of in-memory tail."""
    assert meeting.DEFAULT_SPILL_MINUTES == 10
    assert meeting.MIN_SPILL_MINUTES == 1
    assert meeting.MAX_SPILL_MINUTES == 240
    # The shipped value is one the settings modal can actually reach.
    assert meeting.DEFAULT_SPILL_MINUTES in meeting.SPILL_MINUTE_CHOICES


@pytest.mark.parametrize("value,expected", [
    (None, 10),
    (5, 5),
    ("20", 20),
    (" 30 ", 30),
    (0, 10),
    (-5, 10),
    (999, 10),
    (True, 10),          # a bool is not a minute count
    ("ten", 10),
    (10.9, 10),
])
def test_normalize_spill_minutes(value, expected):
    assert meeting.normalize_spill_minutes(value) == expected


# ---------------------------------------------------------------------------
# Progress arithmetic
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("processed,total,expected", [
    (0, 0, 0),
    (0, 10, 0),
    (1, 3, 33),
    (2, 3, 67),
    (3, 3, 100),
    (5, 3, 100),          # clamped
    (-1, 10, 0),
    ("x", 10, 0),
    (None, 10, 0),
    (1, None, 0),
    (1, -10, 0),
    ("2", "4", 50),
])
def test_progress_percent(processed, total, expected):
    assert meeting.progress_percent(processed, total) == expected


def test_set_progress_updates_the_status():
    session = meeting.MeetingSession()
    assert session.set_progress(1, 4) == 25
    status = session.status()
    assert status.progress_pct == 25
    assert status.processed == 1
    assert status.total == 4


# ---------------------------------------------------------------------------
# AudioSpillBuffer: retention, order, and the temp file
# ---------------------------------------------------------------------------
def test_buffer_below_the_threshold_never_creates_a_temp_file(tmp_path):
    buffer = meeting.AudioSpillBuffer(
        sample_rate=1000, spill_after_s=1, directory=str(tmp_path)
    )
    buffer.append(np.ones(500, dtype=np.float32))
    assert buffer.spilled is False
    assert buffer.temp_path is None

    audio = buffer.read_all()
    assert audio.shape == (500,)
    assert buffer.temp_path is None  # still no file, even after read-back
    buffer.close()


def test_buffer_spills_past_the_threshold_and_frees_memory(tmp_path):
    buffer = meeting.AudioSpillBuffer(
        sample_rate=1000, spill_after_s=1, directory=str(tmp_path)
    )
    for _ in range(3):
        buffer.append(np.zeros(500, dtype=np.float32))

    assert buffer.spilled is True
    assert buffer.total_frames == 1500
    # The tail is what is left in memory; the first 1000 frames went to disk.
    assert buffer.memory_frames == 500
    assert buffer.memory_frames <= buffer.spill_after_frames
    path = buffer.temp_path
    assert path is not None and os.path.exists(path)

    buffer.close()
    assert not os.path.exists(path)


def test_spilled_read_back_preserves_order_and_values(tmp_path):
    buffer = meeting.AudioSpillBuffer(
        sample_rate=1000, spill_after_s=1, directory=str(tmp_path)
    )
    buffer.append(np.full(1000, 1.0, dtype=np.float32))
    buffer.append(np.full(700, 2.0, dtype=np.float32))

    audio = buffer.read_all()
    assert audio.shape == (1700,)
    assert np.allclose(audio[:1000], 1.0)
    assert np.allclose(audio[1000:], 2.0)
    assert buffer.closed is False
    buffer.close()


def test_read_back_reports_progress_per_block(tmp_path):
    buffer = meeting.AudioSpillBuffer(
        sample_rate=100, spill_after_s=1, directory=str(tmp_path)
    )
    buffer.append(np.zeros(250, dtype=np.float32))
    seen = []
    buffer.read_all(progress=lambda done, total: seen.append((done, total)))
    assert seen
    assert seen[-1] == (250, 250)
    assert all(done <= total for done, total in seen)
    buffer.close()


def test_buffer_close_is_idempotent_and_removes_the_file(tmp_path):
    buffer = meeting.AudioSpillBuffer(
        sample_rate=1000, spill_after_s=1, directory=str(tmp_path)
    )
    buffer.append(np.zeros(2000, dtype=np.float32))
    path = buffer.temp_path
    assert path is not None
    buffer.close()
    buffer.close()  # must not raise or double-unlink
    assert not os.path.exists(path)
    with pytest.raises(meeting.MeetingStateError):
        buffer.append(np.zeros(1, dtype=np.float32))


def test_long_capture_does_not_grow_memory_without_bound(tmp_path):
    """The whole reason the spill exists: capture memory is capped, always."""
    buffer = meeting.AudioSpillBuffer(
        sample_rate=1000, spill_after_s=1, directory=str(tmp_path)
    )
    chunk = np.zeros(250, dtype=np.float32)
    peak = 0
    for _ in range(400):  # 100 000 frames == 100 s at 1 kHz
        buffer.append(chunk)
        peak = max(peak, buffer.memory_frames)

    assert buffer.total_frames == 100_000
    assert peak <= buffer.spill_after_frames + int(chunk.size)
    buffer.close()


# ---------------------------------------------------------------------------
# State machine transitions
# ---------------------------------------------------------------------------
def test_a_capture_runs_to_completion(tmp_path):
    capture = SyntheticCapture([_audio_seconds(0.1, sample_rate=1000)])
    session = meeting.MeetingSession(
        sample_rate=1000, capture=capture, directory=str(tmp_path)
    )
    _start_and_wait_ready(session, capture, spill_minutes=1)
    assert session.state == "recording"

    session.stop()
    assert session.wait(5.0) is True
    assert session.state == "idle"
    status = session.status()
    assert status.duration_s == pytest.approx(0.1, abs=1e-6)
    assert status.progress_pct == 100


def test_stopping_when_not_started_is_an_error():
    session = meeting.MeetingSession()
    with pytest.raises(meeting.MeetingStateError, match="not recording"):
        session.stop()


def test_starting_twice_is_an_error(tmp_path):
    capture = SyntheticCapture([_audio_seconds(0.05, sample_rate=1000)])
    session = meeting.MeetingSession(
        sample_rate=1000, capture=capture, directory=str(tmp_path)
    )
    _start_and_wait_ready(session, capture, spill_minutes=1)
    try:
        with pytest.raises(meeting.MeetingStateError, match="already recording"):
            session.start(spill_minutes=1)
    finally:
        session.stop()
        session.wait(5.0)


def test_state_enum_values_are_the_reported_strings():
    assert meeting.MeetingState.IDLE == "idle"
    assert meeting.MeetingState.RECORDING == "recording"
    assert meeting.MeetingState.PROCESSING == "processing"


# ---------------------------------------------------------------------------
# Temp-file cleanup on the failure paths
# ---------------------------------------------------------------------------
def test_temp_file_is_deleted_when_capture_fails(tmp_path):
    capture = SyntheticCapture(
        [_audio_seconds(0.05, sample_rate=1000)] * 4,
        fail=RuntimeError("microphone died"),
    )
    session = meeting.MeetingSession(
        sample_rate=1000, capture=capture, directory=str(tmp_path)
    )
    _start_and_wait_ready(session, capture, spill_minutes=1)

    assert session.wait(5.0) is True
    status = session.status()
    assert status.state == "idle"
    assert status.error and "microphone died" in status.error
    assert session.temp_path is None
    assert list(tmp_path.iterdir()) == []  # nothing left behind


def test_temp_file_is_deleted_when_the_consumer_fails(tmp_path):
    def explode(_audio, _rate):
        raise RuntimeError("turn slicer exploded")

    capture = SyntheticCapture([_audio_seconds(0.05, sample_rate=1000)])
    session = meeting.MeetingSession(
        sample_rate=1000, capture=capture, directory=str(tmp_path), on_audio=explode
    )
    _start_and_wait_ready(session, capture, spill_minutes=1)
    session.stop()
    assert session.wait(5.0) is True
    assert session.state == "idle"
    assert list(tmp_path.iterdir()) == []


def test_close_deletes_the_temp_file_while_recording(tmp_path):
    capture = SyntheticCapture([_audio_seconds(0.05, sample_rate=1000)])
    session = meeting.MeetingSession(
        sample_rate=1000, capture=capture, directory=str(tmp_path)
    )
    # 10 frames per frame? sample_rate=10 -> 1 min == 600 frames; spill quickly.
    session.start(spill_minutes=1, sample_rate=10)
    capture.ready.wait(5.0)
    # Append enough through the buffer to force a spill, then close mid-capture.
    for _ in range(10):
        session._append_chunk(np.zeros(100, dtype=np.float32))
    assert session.spilled is True

    session.close()
    assert session.state == "idle"
    assert session.temp_path is None
    assert list(tmp_path.iterdir()) == []


def test_audio_consumer_receives_the_read_back_audio(tmp_path):
    received = {}

    def consume(audio, rate):
        received["audio"] = np.array(audio)
        received["rate"] = rate

    chunks = [_audio_seconds(0.05, sample_rate=1000, value=0.25)]
    capture = SyntheticCapture(chunks)
    session = meeting.MeetingSession(
        sample_rate=1000, capture=capture, directory=str(tmp_path), on_audio=consume
    )
    _start_and_wait_ready(session, capture, spill_minutes=1)
    session.stop()
    assert session.wait(5.0) is True
    assert received["rate"] == 1000
    assert received["audio"].shape == (50,)
    assert np.allclose(received["audio"], 0.25)


# ---------------------------------------------------------------------------
# Status callback / TUI wiring seam
# ---------------------------------------------------------------------------
def test_on_change_is_notified_of_transitions(tmp_path):
    seen = []
    capture = SyntheticCapture([_audio_seconds(0.05, sample_rate=1000)])
    session = meeting.MeetingSession(
        sample_rate=1000,
        capture=capture,
        directory=str(tmp_path),
        on_change=lambda status: seen.append(status.state),
    )
    _start_and_wait_ready(session, capture, spill_minutes=1)
    session.stop()
    session.wait(5.0)
    assert "recording" in seen
    assert seen[-1] == "idle"


def test_elapsed_is_frozen_once_processing_begins(tmp_path):
    capture = SyntheticCapture([_audio_seconds(0.05, sample_rate=1000)])
    session = meeting.MeetingSession(
        sample_rate=1000, capture=capture, directory=str(tmp_path)
    )
    _start_and_wait_ready(session, capture, spill_minutes=1)
    time.sleep(0.05)
    session.stop()
    session.wait(5.0)
    first = session.status().elapsed_s
    time.sleep(0.05)
    assert session.status().elapsed_s == first


# ---------------------------------------------------------------------------
# Engine + control-API wiring
# ---------------------------------------------------------------------------
def _make_engine(tmp_path, monkeypatch):
    """A real ``SimpleVoiceTranscriber`` with no TUI, no audio, no model."""
    from unittest.mock import MagicMock

    import main
    import t2

    monkeypatch.setattr(t2, "CONFIG_FILE", str(tmp_path / "config.yaml"))
    # Tests must not depend on whatever a previous test left in the globals.
    t2.set_meeting("off")
    t2.set_meeting_spill_minutes(10)
    engine = main.SimpleVoiceTranscriber.__new__(main.SimpleVoiceTranscriber)
    engine.tui = MagicMock()
    engine.tui.state = "READY"
    engine.recording = False
    engine.hotkey_system = None
    engine.last_transcription = ""
    return engine


def test_meeting_start_refuses_while_mode_is_off(tmp_path, monkeypatch):
    import t2

    engine = _make_engine(tmp_path, monkeypatch)
    engine.meeting = meeting.MeetingSession(sample_rate=1000)
    t2.set_meeting("off")
    try:
        with pytest.raises(ValueError, match="meeting mode is off"):
            engine.handle_control("meeting-start", {})
        assert engine.meeting.state == "idle"
        assert engine.meeting.temp_path is None
    finally:
        t2.set_meeting("off")


def test_meeting_control_lifecycle(tmp_path, monkeypatch):
    import t2

    engine = _make_engine(tmp_path, monkeypatch)
    t2.set_meeting("on")
    capture = SyntheticCapture([_audio_seconds(0.05, sample_rate=1000)])
    engine.meeting = meeting.MeetingSession(
        sample_rate=1000, capture=capture, directory=str(tmp_path / "meetings")
    )
    (tmp_path / "meetings").mkdir()
    try:
        reply = engine.handle_control("meeting-start", {})
        assert reply["ok"] is True
        assert reply["meeting_state"] == "recording"
        assert reply["meeting_progress"] == 0
        assert capture.ready.wait(5.0)

        # Starting twice is an error, not a second capture.
        with pytest.raises(ValueError, match="already recording"):
            engine.handle_control("meeting-start", {})

        engine.handle_control("meeting-stop", {})
        assert engine.meeting.wait(5.0)
        status = engine.handle_control("status", {})
        assert status["meeting_state"] == "idle"
        assert status["meeting_progress"] == 100
        # The read-back freed the temp file on the success path too.
        assert list((tmp_path / "meetings").iterdir()) == []
    finally:
        t2.set_meeting("off")


def test_meeting_stop_when_idle_is_an_error(tmp_path, monkeypatch):
    engine = _make_engine(tmp_path, monkeypatch)
    engine.meeting = meeting.MeetingSession(sample_rate=1000)
    with pytest.raises(ValueError, match="not recording"):
        engine.handle_control("meeting-stop", {})


def test_meeting_setting_and_spill_verbs(tmp_path, monkeypatch):
    import t2

    engine = _make_engine(tmp_path, monkeypatch)
    try:
        reply = engine.handle_control("meeting", {"value": "on"})
        assert reply["ok"] is True
        assert reply["meeting"] == "on"
        assert reply["meeting_setting"] == "on"

        # Omitting the value flips the toggle, per the verb's "toggles" spec.
        reply = engine.handle_control("meeting", {})
        assert reply["meeting"] == "off"
        engine.handle_control("meeting", {})
        assert t2.get_meeting() == "on"

        reply = engine.handle_control("meeting-spill", {"value": "20"})
        assert reply["meeting_spill_minutes"] == 20

        with pytest.raises(ValueError, match="expects minutes"):
            engine.handle_control("meeting-spill", {"value": "soon"})
    finally:
        t2.set_meeting("off")
        t2.set_meeting_spill_minutes(10)


def test_status_reports_meeting_while_off(tmp_path, monkeypatch):
    engine = _make_engine(tmp_path, monkeypatch)
    engine.meeting = meeting.MeetingSession(sample_rate=1000)
    status = engine.handle_control("status", {})
    assert status["meeting"] == "off"
    assert status["meeting_setting"] == "off"
    assert status["meeting_state"] == "idle"
    assert status["meeting_progress"] == 0
    assert status["meeting_elapsed_s"] == 0.0
    assert status["meeting_spill_minutes"] == 10


# ---------------------------------------------------------------------------
# The off gate: with meeting mode off the dictation path is unchanged
# ---------------------------------------------------------------------------
def test_meeting_off_does_not_touch_the_dictation_path(tmp_path, monkeypatch):
    """Regression pin for the plan's "off is byte-identical" requirement.

    A full dictation cycle is driven with meeting mode off, and the produced
    text must be exactly the established golden value while the meeting session
    is never started and leaves nothing on disk.
    """
    from unittest.mock import MagicMock

    import main
    import t2

    monkeypatch.setattr(t2, "CONFIG_FILE", str(tmp_path / "config.yaml"))
    meetings_dir = tmp_path / "meetings"
    meetings_dir.mkdir()
    t2.set_meeting("off")
    monkeypatch.setattr(t2, "OUTPUT_MODE", "type_fast")
    monkeypatch.setattr(t2, "AUTO_TYPE_TRAILING_SPACE", False)
    monkeypatch.setattr(t2, "AUTO_TYPE_AUTO_PUNCTUATE", False)

    try:
        engine = main.SimpleVoiceTranscriber.__new__(main.SimpleVoiceTranscriber)
        engine.recording = False
        engine.copy_to_clipboard = False
        engine._model_ready_event = MagicMock()
        engine._model_ready_event.is_set.return_value = True
        engine.model_load_error = None
        engine.tui = MagicMock()
        engine.visual_notification = MagicMock()
        engine.clipboard_sink = MagicMock()
        engine.start_time = 0.0
        engine.release_time = 1.0
        engine.last_finish_time = 0.0
        engine.audio_frames = np.ones(16000, dtype=np.float32) * 0.05
        engine.last_transcription = ""
        engine.hotkey_system = MagicMock()
        engine.hotkey_system.are_modifiers_pressed.return_value = False
        typed = []
        engine.hotkey_system.type_text.side_effect = (
            lambda text, fast=False: typed.append(text) or True
        )

        # A spy session: starting it here would be the bug this test catches.
        session = meeting.MeetingSession(sample_rate=1000, directory=str(meetings_dir))
        start_calls = []
        monkeypatch.setattr(
            session, "start", lambda **kwargs: start_calls.append(kwargs)
        )
        engine.meeting = session

        monkeypatch.setattr(
            "main.process_audio_stream", lambda frames: ("hello world", 0.1)
        )
        monkeypatch.setattr(
            "main.copy_to_clipboard_crossplatform", lambda text, sink=None: True
        )

        engine.process_recording()

        assert typed == ["hello world"]  # unchanged dictation output
        assert start_calls == []          # meeting capture never started
        assert session.state == "idle"
        assert list(meetings_dir.iterdir()) == []
    finally:
        t2.set_meeting("off")

