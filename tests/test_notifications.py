"""Tests for notifications.py - Linux visual notification system."""
import sys
import os
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

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
def notif():
    """Create a VisualNotification instance with mocked tkinter."""
    mock_tk = MagicMock()
    with patch.dict(sys.modules, {"tkinter": mock_tk}):
        import notifications
        notifications.TKINTER_AVAILABLE = True
        # Mock subprocess to prevent actual system calls
        with patch.object(notifications, "subprocess", MagicMock()):
            vn = notifications.VisualNotification(app_name="TestApp", enable_logging=False)
            # Prevent actual overlay processes
            vn._create_overlay = MagicMock()
            yield vn, notifications


class TestInit:
    def test_defaults(self, notif):
        vn, _ = notif
        assert vn.app_name == "TestApp"
        assert vn.active is False
        assert vn.overlay_processes == []

    def test_display_env_wayland(self, notif):
        vn, mod = notif
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}):
            result = vn._detect_display_environment()
            assert result == "wayland"

    def test_display_env_x11(self, notif):
        vn, _ = notif
        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            os.environ.pop("WAYLAND_DISPLAY", None)
            result = vn._detect_display_environment()
            assert result == "x11"

    def test_display_env_terminal(self, notif):
        vn, _ = notif
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("WAYLAND_DISPLAY", None)
            os.environ.pop("DISPLAY", None)
            result = vn._detect_display_environment()
            assert result == "terminal"


class TestSetActiveDevice:
    def test_set_device(self, notif):
        vn, _ = notif
        vn.set_active_device("USB Microphone")
        assert vn.active_device == "USB Microphone"


class TestShowRecording:
    def test_show_recording_output(self, notif, capsys):
        vn, _ = notif
        vn.show_recording()
        assert vn.active is True
        captured = capsys.readouterr()
        assert "RECORDING" in captured.out

    def test_show_recording_with_device(self, notif, capsys):
        vn, _ = notif
        vn.active = False
        vn.set_active_device("Test Mic")
        vn.show_recording()
        captured = capsys.readouterr()
        assert "Test Mic" in captured.out

    def test_show_recording_skips_if_active(self, notif, capsys):
        vn, _ = notif
        vn.active = True
        vn.show_recording()
        # _create_overlay should NOT be called since already active
        vn._create_overlay.assert_not_called()


class TestShowProcessing:
    def test_show_processing(self, notif, capsys):
        vn, _ = notif
        vn.show_processing()
        assert vn.active is True
        captured = capsys.readouterr()
        assert "Loading" in captured.out


class TestShowCompleted:
    def test_show_completed_basic(self, notif, capsys):
        vn, _ = notif
        vn.show_completed()
        captured = capsys.readouterr()
        assert "COMPLETED" in captured.out

    def test_show_completed_with_transcription(self, notif, capsys):
        vn, _ = notif
        vn.show_completed(sub_text="Hello world transcription")
        captured = capsys.readouterr()
        assert "Hello world transcription" in captured.out
        assert "COMPLETED" in captured.out


class TestShowError:
    def test_show_error(self, notif, capsys):
        vn, _ = notif
        vn.show_error("Something failed")
        captured = capsys.readouterr()
        assert "Something failed" in captured.out


class TestShowWarning:
    def test_show_warning(self, notif, capsys):
        vn, _ = notif
        vn.show_warning("Low disk")
        captured = capsys.readouterr()
        assert "Low disk" in captured.out


class TestTerminalNotification:
    def test_recording_color(self, notif, capsys):
        vn, _ = notif
        vn._show_terminal_notification("RECORDING in progress")
        captured = capsys.readouterr()
        assert "\033[91m" in captured.out  # Red
        assert "*" in captured.out

    def test_processing_color(self, notif, capsys):
        vn, _ = notif
        vn._show_terminal_notification("PROCESSING audio")
        captured = capsys.readouterr()
        assert "\033[93m" in captured.out  # Yellow

    def test_completed_color(self, notif, capsys):
        vn, _ = notif
        vn._show_terminal_notification("COMPLETED")
        captured = capsys.readouterr()
        assert "\033[94m" in captured.out  # Blue

    def test_error_color(self, notif, capsys):
        vn, _ = notif
        vn._show_terminal_notification("ERROR occurred")
        captured = capsys.readouterr()
        assert "\033[95m" in captured.out  # Magenta

    def test_warning_color(self, notif, capsys):
        vn, _ = notif
        vn._show_terminal_notification("WARNING issue")
        captured = capsys.readouterr()
        assert "\033[96m" in captured.out  # Cyan

    def test_generic_color(self, notif, capsys):
        vn, _ = notif
        vn._show_terminal_notification("Some info")
        captured = capsys.readouterr()
        assert "\033[92m" in captured.out  # Green

    def test_sub_text_output(self, notif, capsys):
        vn, _ = notif
        vn._show_terminal_notification("COMPLETED", sub_text="The quick brown fox")
        captured = capsys.readouterr()
        assert "The quick brown fox" in captured.out
        # Sub-text output should NOT have box formatting
        assert "┌" not in captured.out

    def test_no_sub_text_has_box(self, notif, capsys):
        vn, _ = notif
        vn._show_terminal_notification("RECORDING status")
        captured = capsys.readouterr()
        assert "┌" in captured.out
        assert "┘" in captured.out


class TestHideNotification:
    def test_hide_clears_active(self, notif):
        vn, _ = notif
        vn.active = True
        vn.hide_notification()
        assert vn.active is False

    def test_hide_when_not_active_noop(self, notif):
        vn, _ = notif
        vn.active = False
        vn.hide_notification()  # Should not error


class TestCleanup:
    def test_cleanup_kills_processes(self, notif):
        vn, _ = notif
        mock_proc = MagicMock()
        vn.overlay_processes = [mock_proc]
        vn.active = True
        vn.cleanup()
        mock_proc.terminate.assert_called_once()

    def test_cleanup_cancels_timers(self, notif):
        vn, _ = notif
        mock_timer = MagicMock()
        vn._notification_timers = [mock_timer]
        vn.cleanup()
        mock_timer.cancel.assert_called_once()
        assert vn._notification_timers == []


class TestShowNotification:
    def test_show_notification_persistent(self, notif):
        vn, _ = notif
        vn.show_notification("Test", persistent=True)
        assert vn.active is True

    def test_show_notification_non_persistent(self, notif):
        vn, _ = notif
        vn.show_notification("Test", persistent=False)
        assert vn.active is False


class TestDetectTools:
    def test_detect_tools_found(self, notif):
        vn, mod = notif
        with patch.object(mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock()
            tools = vn._detect_available_tools()
            # Should check for zenity, yad, kdialog, xmessage
            assert mock_run.call_count == 4
            assert len(tools) == 4

    def test_detect_tools_none_found(self, notif):
        vn, mod = notif
        with patch.object(mod.subprocess, "run", side_effect=Exception("not found")):
            tools = vn._detect_available_tools()
            assert tools == []


class TestCreateOverlay:
    def test_create_overlay_tkinter(self, notif):
        vn, mod = notif
        # Restore real _create_overlay
        vn._create_overlay = mod.VisualNotification._create_overlay.__get__(vn)
        mod.TKINTER_AVAILABLE = True
        with patch.object(vn, "_create_tkinter_overlay"):
            vn._create_overlay("TEST", "#ff0000", persistent=False)
            vn._create_tkinter_overlay.assert_called_once()

    def test_create_overlay_falls_back_to_zenity(self, notif):
        vn, mod = notif
        vn._create_overlay = mod.VisualNotification._create_overlay.__get__(vn)
        mod.TKINTER_AVAILABLE = True
        vn.available_tools = ["zenity"]
        with patch.object(vn, "_create_tkinter_overlay", side_effect=Exception("tkinter fail")), \
             patch.object(vn, "_create_zenity_notification"):
            vn._create_overlay("TEST", "#ff0000", persistent=False)
            vn._create_zenity_notification.assert_called_once()


class TestCleanupOverlays:
    def test_cleanup_terminate_timeout(self, notif):
        vn, _ = notif
        mock_proc = MagicMock()
        mock_proc.wait.side_effect = Exception("timeout")
        vn.overlay_processes = [mock_proc]
        vn._cleanup_overlays()
        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()


class TestConvenienceFunctions:
    def test_show_recording_notification(self, notif):
        _, mod = notif
        with patch.object(mod.VisualNotification, "_create_overlay"):
            n = mod.show_recording_notification("Test")
            assert n.active is True

    def test_show_completed_notification(self, notif):
        _, mod = notif
        with patch.object(mod.VisualNotification, "_create_overlay"):
            n = mod.show_completed_notification("Test")

    def test_show_error_notification(self, notif):
        _, mod = notif
        with patch.object(mod.VisualNotification, "_create_overlay"):
            n = mod.show_error_notification("Test")
