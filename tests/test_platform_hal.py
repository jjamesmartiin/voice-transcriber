#!/usr/bin/env python3
"""Unit tests for the Phase 3 Hardware/OS Abstraction Layer (``src/platform/``).

These tests exercise the *real* HAL implementation (loaded through the ``hal``
façade) rather than the reference sinks embedded in
``tests/test_end_to_end_crossplatform.py``. They verify:

* platform detection + ``VT_PLATFORM`` override semantics,
* the clipboard/cue/hotkey factories return the right backend per platform,
* Linux clipboard/typing command lines, and
* that every backend module imports cleanly on a Linux host even when
  ``pynput`` / ``winsound`` / ``ctypes.windll`` are unavailable.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import hal  # noqa: E402


class TestDetectPlatform:
    @pytest.mark.parametrize("value", [hal.LINUX, hal.WSL, hal.WINDOWS])
    def test_env_override_wins(self, monkeypatch, value):
        monkeypatch.setenv("VT_PLATFORM", value)
        assert hal.detect_platform() == value

    def test_unknown_override_is_a_hard_error(self, monkeypatch):
        monkeypatch.setenv("VT_PLATFORM", "beos")
        with pytest.raises(ValueError):
            hal.detect_platform()

    def test_autodetect_without_override(self, monkeypatch):
        monkeypatch.delenv("VT_PLATFORM", raising=False)
        assert hal.detect_platform() in hal.VALID_PLATFORMS


class TestFactories:
    @pytest.mark.parametrize(
        "platform,sink_name",
        [
            (hal.LINUX, "LinuxClipboardSink"),
            (hal.WINDOWS, "WindowsClipboardSink"),
            (hal.WSL, "WSLClipboardSink"),
        ],
    )
    def test_clipboard_factory(self, platform, sink_name):
        assert type(hal.get_clipboard_sink(platform)).__name__ == sink_name

    @pytest.mark.parametrize(
        "platform,player_name",
        [
            (hal.LINUX, "LinuxAudioCuePlayer"),
            (hal.WINDOWS, "WindowsAudioCuePlayer"),
            (hal.WSL, "WSLAudioCuePlayer"),
        ],
    )
    def test_audio_cue_factory(self, platform, player_name):
        assert type(hal.get_audio_cue_player(platform)).__name__ == player_name

    @pytest.mark.parametrize(
        "platform,manager_name",
        [
            (hal.LINUX, "LinuxHotkeyManager"),
            (hal.WINDOWS, "WindowsHotkeyManager"),
            (hal.WSL, "WSLHotkeyManager"),
        ],
    )
    def test_hotkey_factory(self, platform, manager_name):
        manager = hal.create_hotkey_manager(platform)
        try:
            assert type(manager).__name__ == manager_name
        finally:
            manager.cleanup()


class TestLinuxClipboardSink:
    def test_wl_copy_preferred(self, monkeypatch):
        calls = []

        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0)

        monkeypatch.setattr(
            shutil, "which", lambda name: f"/usr/bin/{name}" if name == "wl-copy" else None
        )
        monkeypatch.setattr(subprocess, "run", fake_run)

        assert hal.get_clipboard_sink(hal.LINUX).copy_text("hello") is True
        assert calls[0][0] == ["wl-copy"]
        assert calls[0][1]["input"] == b"hello"

    def test_xclip_fallback(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            shutil, "which", lambda name: f"/usr/bin/{name}" if name == "xclip" else None
        )
        monkeypatch.setattr(
            subprocess, "run", lambda args, **kw: calls.append(args) or subprocess.CompletedProcess(args, 0)
        )

        assert hal.get_clipboard_sink(hal.LINUX).copy_text("hi") is True
        assert calls[0] == ["xclip", "-selection", "clipboard"]

    def test_no_tool_returns_false(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)
        assert hal.get_clipboard_sink(hal.LINUX).copy_text("nowhere") is False

    def test_ydotool_then_xdotool_typing(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            shutil, "which", lambda name: f"/usr/bin/{name}" if name in ("ydotool", "xdotool") else None
        )
        monkeypatch.setattr(
            subprocess, "run", lambda args, **kw: seen.append(args) or subprocess.CompletedProcess(args, 0)
        )

        sink = hal.get_clipboard_sink(hal.LINUX)
        assert sink.type_text("one") is True
        assert seen[-1] == ["ydotool", "type", "--", "one"]

        assert sink.type_text("one", fast=True) is True
        assert seen[-1] == ["ydotool", "type", "-d", "1", "-s", "1", "--", "one"]


class TestImportSafety:
    """Every backend module must import on Linux, even without native deps."""

    @pytest.mark.parametrize(
        "platform,module",
        [
            (hal.LINUX, "hotkeys"),
            (hal.LINUX, "clipboard"),
            (hal.LINUX, "audio_cues"),
            (hal.WINDOWS, "hotkeys"),
            (hal.WINDOWS, "clipboard"),
            (hal.WINDOWS, "audio_cues"),
            (hal.WINDOWS, "notifications"),
            (hal.WSL, "hotkeys"),
            (hal.WSL, "clipboard"),
            (hal.WSL, "audio_cues"),
        ],
    )
    def test_backend_module_imports(self, platform, module):
        assert hal.load_backend(platform, module) is not None


class TestHotkeysShimBackwardCompatibility:
    def test_legacy_entry_points_exist(self):
        import hotkeys

        assert callable(hotkeys.create_global_hotkeys)
        assert callable(hotkeys.is_running_in_wsl)
        assert callable(hotkeys.set_global_sound_theme)
        assert hotkeys.WaylandGlobalHotkeys is not None
        assert hotkeys.WSLGlobalHotkeys is not None
        assert hotkeys.WindowsGlobalHotkeys is not None

    def test_is_running_in_wsl_tracks_hal(self, monkeypatch):
        import hotkeys

        monkeypatch.setenv("VT_PLATFORM", hal.WSL)
        assert hotkeys.is_running_in_wsl() is True
        monkeypatch.setenv("VT_PLATFORM", hal.LINUX)
        assert hotkeys.is_running_in_wsl() is False


class TestPasteTextSupport:
    def test_linux_hotkey_manager_paste_text(self):
        from unittest.mock import MagicMock
        LinuxHotkeyManager = hal.load_backend("linux", "hotkeys").LinuxHotkeyManager
        manager = LinuxHotkeyManager(MagicMock(), MagicMock())
        manager.virtual_keyboard = MagicMock()
        manager.uinput = MagicMock()
        try:
            assert manager.paste_text(terminal=False) is True
            assert manager.virtual_keyboard.emit.call_count >= 4

            manager.virtual_keyboard.reset_mock()
            assert manager.paste_text(terminal=True) is True
            assert manager.virtual_keyboard.emit.call_count >= 6
        finally:
            manager.cleanup()

    def test_linux_hotkey_manager_type_text_slow_and_fast(self):
        from unittest.mock import MagicMock
        LinuxHotkeyManager = hal.load_backend("linux", "hotkeys").LinuxHotkeyManager
        manager = LinuxHotkeyManager(MagicMock(), MagicMock())
        manager.virtual_keyboard = MagicMock()
        manager.uinput = MagicMock()
        try:
            # Slow mode
            assert manager.type_text("Hi", fast=False) is True
            assert manager.virtual_keyboard.emit.call_count > 0

            # Fast mode with Unicode quotes normalization
            manager.virtual_keyboard.reset_mock()
            assert manager.type_text("“Hello”", fast=True) is True
            assert manager.virtual_keyboard.emit.call_count > 0
        finally:
            manager.cleanup()


class TestWindowsClipboardSink:
    def test_windows_sendinput_unicode_typing(self, monkeypatch):
        import ctypes
        sendinput_calls = []

        class FakeUser32:
            def SendInput(self, n, p_inputs, cb_size):
                sendinput_calls.append(n)
                return n

        class FakeWindll:
            user32 = FakeUser32()

        monkeypatch.setattr(ctypes, "windll", FakeWindll(), raising=False)
        sink = hal.get_clipboard_sink(hal.WINDOWS)
        # Test Unicode + emoji text (Hello space = 6 units, 🚀 = 2 surrogate units -> 8 total units)
        assert sink.type_text("Hello 🚀", fast=True) is True
        # 1 down + 1 up call per unit -> 16 calls to SendInput
        assert len(sendinput_calls) == 16

    def test_windows_typing_fallback_to_keybd_event(self, monkeypatch):
        import ctypes
        keybd_calls = []

        class FakeUser32NoSendInput:
            def VkKeyScanW(self, char_code):
                return 0x41  # 'A'

            def keybd_event(self, vk, scan, flags, extra):
                keybd_calls.append((vk, flags))

        class FakeWindll:
            user32 = FakeUser32NoSendInput()

        monkeypatch.setattr(ctypes, "windll", FakeWindll(), raising=False)
        sink = hal.get_clipboard_sink(hal.WINDOWS)
        assert sink.type_text("A", fast=True) is True
        assert len(keybd_calls) == 2  # key down, key up


class TestWindowsHotkeyManagerLatching:
    def test_windows_space_latch(self):
        from unittest.mock import MagicMock
        cb_start = MagicMock()
        cb_stop = MagicMock()
        WindowsHotkeyManager = hal.load_backend("windows", "hotkeys").WindowsHotkeyManager
        manager = WindowsHotkeyManager(cb_start, cb_stop)
        try:
            # Simulate Alt+Shift active
            manager.hotkey_active = True

            class FakeKey:
                name = "space"
                char = " "

            manager._Key = MagicMock()
            manager._Key.space = FakeKey()
            manager._KeyCode = MagicMock()

            # Space tapped during recording -> sets latch_release
            manager._on_press(FakeKey())
            assert manager.latch_release is True

            # Release Alt+Shift while latched -> clears latch, does not stop recording
            manager._on_release(FakeKey())
            assert manager.latch_release is False
            assert manager.hotkey_active is False
            cb_stop.assert_not_called()
        finally:
            manager.cleanup()

    def test_windows_hotkey_manager_type_text_delegates(self, monkeypatch):
        import ctypes
        sendinput_calls = []

        class FakeUser32:
            def SendInput(self, n, p_inputs, cb_size):
                sendinput_calls.append(n)
                return n

        class FakeWindll:
            user32 = FakeUser32()

        monkeypatch.setattr(ctypes, "windll", FakeWindll(), raising=False)
        from unittest.mock import MagicMock
        WindowsHotkeyManager = hal.load_backend("windows", "hotkeys").WindowsHotkeyManager
        manager = WindowsHotkeyManager(MagicMock(), MagicMock())
        try:
            assert manager.type_text("Hi", fast=True) is True
            # 'H' (2 events) + 'i' (2 events) -> 4 SendInput calls
            assert len(sendinput_calls) == 4
        finally:
            manager.cleanup()


class TestWindowsTuiCompatibility:
    def test_tui_imports_and_runs_without_termios(self, monkeypatch):
        import tui
        monkeypatch.setattr(tui, "termios", None)
        monkeypatch.setattr(tui, "tty", None)
        app_tui = tui.VoiceTranscriberTUI()
        assert app_tui.state == "READY"
        app_tui.update_state("RECORDING", "Testing")
        assert app_tui.state == "RECORDING"


