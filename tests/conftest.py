"""Shared fixtures and helpers for voice-transcriber tests."""
import sys
import os
import pytest
import numpy as np

# Add src to path for imports
SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


@pytest.fixture
def mock_audio_data():
    """Return a fake 1-second 16kHz mono float32 audio array."""
    return np.random.randn(16000).astype(np.float32)


@pytest.fixture
def mock_audio_data_stereo():
    """Return a fake stereo audio array."""
    return np.random.randn(16000, 2).astype(np.float32)


@pytest.fixture
def mock_audio_data_48k():
    """Return a fake 1-second 48kHz mono audio array (needs resampling)."""
    return np.random.randn(48000).astype(np.float32)


@pytest.fixture
def tmp_audio_file(tmp_path):
    """Create a temporary audio file path."""
    return str(tmp_path / "test_audio.wav")
