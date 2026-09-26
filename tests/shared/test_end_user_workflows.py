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
import pytest

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

        def fake_record_audio_stream(stream_callback=None):
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
