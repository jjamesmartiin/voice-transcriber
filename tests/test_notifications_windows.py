"""Tests for notifications_windows.py - Windows notification system."""
import sys
import os
import pytest
from unittest.mock import MagicMock, patch

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


@pytest.fixture(autouse=True)
def reset_module():
    for mod_name in list(sys.modules.keys()):
        if "notifications" in mod_name and "test" not in mod_name:
            del sys.modules[mod_name]
    yield


@pytest.fixture
def win_notif():
    """Create a Windows VisualNotification instance."""
    mock_tk = MagicMock()
    with patch.dict(sys.modules, {"tkinter": mock_tk}):
        import notifications_windows
        vn = notifications_windows.VisualNotification(app_name="TestApp")
        # Mock the tkinter overlay to prevent actual window creation
        vn._show_tkinter_overlay = MagicMock()
        yield vn, notifications_windows


class TestInit:
    def test_defaults(self, win_notif):
        vn, _ = win_notif
        assert vn.app_name == "TestApp"
        assert vn.active_overlay is None


class TestShowRecording:
    def test_show_recording(self, win_notif):
        vn, _ = win_notif
        vn.show_recording()
        vn._show_tkinter_overlay.assert_called_once()
        call_args = vn._show_tkinter_overlay.call_args
        assert "RECORDING" in call_args[0][0]

    def test_show_recording_color(self, win_notif):
        vn, _ = win_notif
        vn.show_recording()
        call_args = vn._show_tkinter_overlay.call_args
        assert call_args[0][1] == "#ff4444"


class TestShowProcessing:
    def test_show_processing(self, win_notif):
        vn, _ = win_notif
        vn.show_processing("Transcribing")
        vn._show_tkinter_overlay.assert_called_once()
        call_args = vn._show_tkinter_overlay.call_args
        assert "TRANSCRIBING" in call_args[0][0]


class TestShowCompleted:
    def test_show_completed_basic(self, win_notif):
        vn, _ = win_notif
        vn.show_completed()
        vn._show_tkinter_overlay.assert_called_once()
        call_args = vn._show_tkinter_overlay.call_args
        assert "COMPLETED" in call_args[0][0]

    def test_show_completed_with_text(self, win_notif):
        vn, _ = win_notif
        vn.show_completed(sub_text="Hello world test transcription")
        call_args = vn._show_tkinter_overlay.call_args
        assert "Hello world test transcripti" in call_args[0][0]  # Truncated to 30 chars


class TestSetActiveDevice:
    def test_set_device(self, win_notif):
        vn, _ = win_notif
        vn.set_active_device("USB Mic")
        assert vn.active_device == "USB Mic"


class TestHideAndCleanup:
    def test_hide_noop(self, win_notif):
        vn, _ = win_notif
        vn.hide_notification()  # Should not error

    def test_cleanup_noop(self, win_notif):
        vn, _ = win_notif
        vn.cleanup()  # Should not error


class TestPlaySound:
    def test_play_sound_start(self, win_notif):
        vn, _ = win_notif
        # _play_sound spawns a thread that tries to import winsound
        # On Linux this will fail silently in the daemon thread
        vn._play_sound("start")

    def test_play_sound_complete(self, win_notif):
        vn, _ = win_notif
        vn._play_sound("complete")


class TestTkinterOverlay:
    def test_show_tkinter_overlay_creates_thread(self, win_notif):
        vn, mod = win_notif
        # Restore real method to test it
        real_method = mod.VisualNotification._show_tkinter_overlay
        real_method(vn, "TEST", "#ff0000")
        import time
        time.sleep(0.1)
        # active_overlay should be a thread
        assert vn.active_overlay is not None

    def test_show_completed_with_no_sub_text(self, win_notif):
        vn, mod = win_notif
        # Restore real show_completed to test the full path
        vn._show_tkinter_overlay = MagicMock()
        vn.show_completed(sub_text=None)
        call_args = vn._show_tkinter_overlay.call_args
        assert "COMPLETED" in call_args[0][0]

    def test_show_processing_default(self, win_notif):
        vn, _ = win_notif
        vn.show_processing()  # Default message
        call_args = vn._show_tkinter_overlay.call_args
        assert "PROCESSING" in call_args[0][0]
