#!/usr/bin/env python3
"""A slow model load must not poison the session.

The watcher joined the preload for ``MODEL_READY_TIMEOUT_S`` (30 s) and recorded
"model load timed out" as a terminal error that nothing ever cleared. The very
first run downloads ~2.8 GB, which is far longer than 30 s, so dictating during
the download dropped the audio *and* every later dictation in that session was
dropped the same way -- even after the model had arrived. Only a restart
recovered.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import main


class _StuckThread:
    """A preload that never finishes within the watcher's budget."""

    def __init__(self):
        self.started = threading.Event()

    def join(self, timeout=None):
        self.started.set()
        return None

    def is_alive(self):
        return True


def _engine(monkeypatch, *, loaded, preload_thread):
    app = main.SimpleVoiceTranscriber.__new__(main.SimpleVoiceTranscriber)
    app.tui = MagicMock()
    app.visual_notification = MagicMock()
    app.recording = False
    app.process_thread = None
    app.model_load_error = None
    app._model_ready_event = threading.Event()
    monkeypatch.setattr(app, "_preload_threads", lambda: [preload_thread])
    monkeypatch.setattr("main.MODEL_READY_TIMEOUT_S", 0.01)
    monkeypatch.setattr(
        "transcribe2.get_backend",
        lambda: type("B", (), {"_model": object() if loaded else None})(),
    )
    return app


def test_a_slow_load_is_not_an_error(monkeypatch):
    """The event is set, so dictation waits for the model instead of dropping."""
    app = _engine(monkeypatch, loaded=False, preload_thread=_StuckThread())

    app._watch_model_load()

    assert app._model_ready_event.is_set(), "recording waits on this event"
    assert app.model_load_error is None, app.model_load_error


def test_a_load_that_really_failed_is_reported(monkeypatch):
    """A finished preload that produced no model is a genuine failure."""
    class _Done:
        def join(self, timeout=None):
            return None

        def is_alive(self):
            return False

    app = _engine(monkeypatch, loaded=False, preload_thread=_Done())

    app._watch_model_load()

    assert app._model_ready_event.is_set()
    assert "failed to load" in (app.model_load_error or "")


def test_a_load_that_produced_a_model_clears_nothing_and_reports_ready(monkeypatch):
    app = _engine(monkeypatch, loaded=True, preload_thread=_StuckThread())

    app._watch_model_load()

    assert app._model_ready_event.is_set()
    assert app.model_load_error is None
