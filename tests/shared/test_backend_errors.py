#!/usr/bin/env python3
"""A backend failure must never become transcript text.

``transcribe_audio`` used to return ``f"Error loading model: {e}"`` on a load
failure. That string is indistinguishable from speech to everything upstream:
the micro-batcher stitches it into the utterance and the engine types it into
whatever window has focus (or writes it into a meeting transcript).
"""
from __future__ import annotations

import numpy as np
import pytest


@pytest.mark.parametrize("module_name", ["transcribe_cohere", "transcribe_parakeet"])
def test_a_backend_load_failure_is_not_returned_as_transcript_text(monkeypatch, module_name):
    import importlib

    # Import the package module directly: `transcribe_parakeet` has no compat shim.
    try:
        module = importlib.import_module(f"voice_transcriber.{module_name}")
    except ImportError as exc:  # noqa: PERF203 - one platform-specific import
        # `transcribe_cohere` imports torch at module level. The Windows and
        # macOS CI jobs install no torch on purpose, so this asserts the real
        # behaviour only where the real module exists.
        pytest.skip(f"{module_name} needs a runtime this job does not install ({exc})")

    def _boom(*args, **kwargs):
        raise RuntimeError("no local model")

    monkeypatch.setattr(module, "has_speech_activity", lambda data: True)
    monkeypatch.setattr(module, "get_model", _boom)

    text = module.transcribe_audio(audio_data=np.zeros(16000, dtype=np.float32))

    assert text == "", f"{module_name} returned {text!r} as if it were speech"
