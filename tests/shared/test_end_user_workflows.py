#!/usr/bin/env python3
"""Model-free end-user workflow simulation.

Drives the *real* recording -> micro-batcher -> process_recording -> output
sink path with a stubbed ASR, so the shape of the pipeline is verified on
Linux, Windows, and WSL without downloading or loading the model.
"""
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


class TestMicroBatcherPipeline:
    def test_batcher_produces_text_with_fake_asr(self, fake_asr):
        from micro_batcher import StreamingMicroBatcher

        fake_asr["text"] = "hello world"
        mb = StreamingMicroBatcher(sample_rate=16000)
        mb.start()
        speech = np.full(16000, 0.05, dtype=np.float32)
        for i in range(0, len(speech), 1024):
            mb.feed_audio(speech[i : i + 1024])

        text = mb.finish_and_get_text(skip_slm=True).lower()

        assert "hello" in text and "world" in text
        assert fake_asr["calls"], "ASR stub was never invoked"


def _make_app(monkeypatch, output_mode="clipboard"):
    from main import SimpleVoiceTranscriber
    import t2 as t2mod

    monkeypatch.setattr(t2mod, "ENABLE_SLM", False)
    monkeypatch.setattr(t2mod, "OUTPUT_MODE", output_mode)
    monkeypatch.setattr(t2mod, "AUTO_TYPE_TRAILING_SPACE", True)
    monkeypatch.setattr(t2mod, "AUTO_TYPE_AUTO_PUNCTUATE", True)
    monkeypatch.setattr(t2mod, "IS_MUTED", True)

    app = SimpleVoiceTranscriber.__new__(SimpleVoiceTranscriber)
    app.recording = False
    app.copy_to_clipboard = False
    app.start_time = 0.0
    app.release_time = 1.0
    app.last_finish_time = 0.0
    app.audio_frames = []
    app.last_transcription = ""
    app.model_load_error = None
    app._model_ready_event = MagicMock()
    app._model_ready_event.is_set.return_value = True
    app.tui = MagicMock()
    app.visual_notification = MagicMock()
    app.audio_cues = MagicMock()
    app.hotkey_system = MagicMock()
    app.hotkey_system.are_modifiers_pressed.return_value = False
    app.hotkey_system.type_text.return_value = True
    app.clipboard_sink = MagicMock()

    done = threading.Event()

    def _fake_copy(text, sink=None):
        app.copied_text = text
        done.set()
        return True

    monkeypatch.setattr("main.copy_to_clipboard_crossplatform", _fake_copy)
    app._copy_done = done
    return app


class TestFullDictationSimulation:
    def test_push_to_talk_records_and_copies(self, monkeypatch):
        import micro_batcher

        app = _make_app(monkeypatch)

        class FakeBatcher:
            def __init__(self, sample_rate=16000, tui=None):
                self.fed = []

            def start(self):
                pass

            def feed_audio(self, chunk):
                self.fed.append(chunk)

            def finish_and_get_text(self, skip_slm=True):
                return "dictated sentence"

        monkeypatch.setattr(micro_batcher, "StreamingMicroBatcher", FakeBatcher)

        def fake_record_audio_stream(stream_callback=None, **kwargs):
            frames = [np.full(1024, 0.05, dtype=np.float32) for _ in range(4)]
            for f in frames:
                if stream_callback:
                    stream_callback(f)
            return frames

        monkeypatch.setattr("main.record_audio_stream", fake_record_audio_stream)

        app.start_recording()
        assert app.recording is True

        app.stop_recording()
        app.process_thread.join(timeout=5)
        app._copy_done.wait(3)

        assert getattr(app, "copied_text", "") == "dictated sentence"

    def test_start_while_recording_is_ignored(self, monkeypatch):
        app = _make_app(monkeypatch)
        app.recording = True
        app.start_recording()  # must early-return
        assert app.recording is True

    def test_stop_when_not_recording_is_noop(self, monkeypatch):
        app = _make_app(monkeypatch)
        app.recording = False
        app.stop_recording()  # must not raise / spawn processing
        assert not hasattr(app, "process_thread")


class TestMeetingLiveTranscriptWiring:
    """The engine owns a display-only batcher for the duration of a capture.

    Meeting mode shows text while it records. That view is fed by a second
    micro-batcher, and it is *not* the transcript: the diarized batch pipeline
    still owns the written artifact, so this one must be cheap to throw away and
    must never be able to fail a capture.
    """

    def _app(self, monkeypatch):
        app = _make_app(monkeypatch)
        app.meeting_live_text = ""
        app._meeting_batcher = None
        app.meeting = MagicMock()
        app.meeting.sample_rate = 16000
        return app

    def test_the_batcher_is_started_and_released_around_a_capture(self, monkeypatch):
        app = self._app(monkeypatch)

        app._start_meeting_live_batcher()
        assert app._meeting_batcher is not None
        assert app._meeting_batcher.running is True
        try:
            # A captured chunk is routed to it; no audio is fed here, so the ASR
            # stub is never reached.
            app._on_meeting_chunk(np.zeros(64, dtype=np.float32))
            app._on_meeting_live_text("hello there")
            assert app.meeting_live_text == "hello there"
            app.tui.set_meeting_live_text.assert_called_once_with("hello there")
        finally:
            app._stop_meeting_live_batcher()

        assert app._meeting_batcher is None
        app._stop_meeting_live_batcher()  # idempotent
        app._on_meeting_chunk(np.zeros(64, dtype=np.float32))  # no batcher: no-op

    def test_a_front_end_without_a_live_view_still_gets_the_text_on_status(self, monkeypatch):
        """The Rich TUI has no live-text block, so publishing must not raise."""
        app = self._app(monkeypatch)
        app.tui = object()  # no set_meeting_live_text attribute at all

        app._on_meeting_live_text("hello")

        assert app.meeting_live_text == "hello"

    def test_a_batcher_that_cannot_start_leaves_the_capture_alone(self, monkeypatch):
        app = self._app(monkeypatch)

        def _boom(*_args, **_kwargs):
            raise RuntimeError("no live view today")

        monkeypatch.setattr("micro_batcher.StreamingMicroBatcher", _boom)

        app._start_meeting_live_batcher()  # must not raise

        assert app._meeting_batcher is None

    def test_the_live_view_yields_to_a_dictation(self, monkeypatch):
        """A dictation mid-meeting keeps the ASR lock; the live view can wait.

        Same reason the batch pipeline yields between turns: a live preview must
        never be the thing that makes the user's dictation slow.
        """
        app = self._app(monkeypatch)
        app._start_meeting_live_batcher()
        try:
            app._meeting_batcher = MagicMock()
            app.recording = False
            app._on_meeting_chunk(np.zeros(8, dtype=np.float32))
            assert app._meeting_batcher.feed_audio.call_count == 1

            app.recording = True
            app._on_meeting_chunk(np.zeros(8, dtype=np.float32))
            assert app._meeting_batcher.feed_audio.call_count == 1, (
                "a dictation must not compete with the live preview"
            )
        finally:
            app._stop_meeting_live_batcher()
