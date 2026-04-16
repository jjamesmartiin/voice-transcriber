"""Tests for transcribe2.py - Backend dispatcher."""
import sys
import os
import pytest
from unittest.mock import MagicMock, patch

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


@pytest.fixture(autouse=True)
def reset_module():
    """Reset transcribe2 module state."""
    for mod_name in list(sys.modules.keys()):
        if mod_name in ("transcribe2", "transcribe_whisper", "transcribe_cohere"):
            del sys.modules[mod_name]
    yield


@pytest.fixture
def mock_backends():
    """Provide mock whisper and cohere backends."""
    mock_whisper = MagicMock()
    mock_whisper.transcribe_audio.return_value = "whisper result"
    mock_whisper.preload_model.return_value = MagicMock()
    mock_whisper.get_model.return_value = MagicMock()
    mock_whisper.unload_model.return_value = None

    mock_cohere = MagicMock()
    mock_cohere.transcribe_audio.return_value = "cohere result"
    mock_cohere.preload_model.return_value = MagicMock()
    mock_cohere.get_model.return_value = MagicMock()
    mock_cohere.unload_model.return_value = None

    def fake_import(name):
        if name == "transcribe_whisper":
            return mock_whisper
        elif name == "transcribe_cohere":
            return mock_cohere
        raise ImportError(f"No mock for {name}")

    with patch("importlib.import_module", side_effect=fake_import):
        import transcribe2
        transcribe2._backend = None
        transcribe2._current_backend_name = "whisper"
        yield transcribe2, mock_whisper, mock_cohere


class TestGetBackend:
    def test_default_is_whisper(self, mock_backends):
        t2, mock_w, _ = mock_backends
        backend = t2.get_backend()
        assert backend is mock_w

    def test_cohere_backend(self, mock_backends):
        t2, _, mock_c = mock_backends
        t2._current_backend_name = "cohere"
        t2._backend = None
        backend = t2.get_backend()
        assert backend is mock_c

    def test_backend_cached(self, mock_backends):
        t2, mock_w, _ = mock_backends
        b1 = t2.get_backend()
        b2 = t2.get_backend()
        assert b1 is b2


class TestSetBackend:
    def test_switch_to_cohere(self, mock_backends):
        t2, mock_w, mock_c = mock_backends
        t2.get_backend()  # Load whisper
        t2.set_backend("cohere")
        assert t2._current_backend_name == "cohere"
        assert t2._backend is None  # Cleared for reload
        mock_w.unload_model.assert_called_once()

    def test_switch_same_backend_noop(self, mock_backends):
        t2, mock_w, _ = mock_backends
        t2.get_backend()  # Load whisper
        t2.set_backend("whisper")
        mock_w.unload_model.assert_not_called()

    def test_switch_changes_backend_name(self, mock_backends):
        t2, _, _ = mock_backends
        t2.get_backend()
        t2.set_backend("cohere")
        assert t2._current_backend_name == "cohere"
        # After switching, loading backend should give cohere
        backend = t2.get_backend()
        assert backend is not None


class TestTranscribeAudio:
    def test_delegates_to_whisper(self, mock_backends):
        t2, mock_w, _ = mock_backends
        result = t2.transcribe_audio(audio_data="audio", sample_rate=16000)
        assert result == "whisper result"
        mock_w.transcribe_audio.assert_called_once_with(
            audio_data="audio", audio_path=None, sample_rate=16000, device="cpu", language="en"
        )

    def test_delegates_to_cohere(self, mock_backends):
        t2, _, mock_c = mock_backends
        t2._current_backend_name = "cohere"
        t2._backend = None
        result = t2.transcribe_audio(audio_path="/tmp/a.wav")
        assert result == "cohere result"


class TestPreloadModel:
    def test_delegates(self, mock_backends):
        t2, mock_w, _ = mock_backends
        t2.preload_model(device="cuda")
        mock_w.preload_model.assert_called_once_with(device="cuda")


class TestGetModel:
    def test_delegates(self, mock_backends):
        t2, mock_w, _ = mock_backends
        t2.get_model(device="cpu")
        mock_w.get_model.assert_called_once_with(device="cpu")
