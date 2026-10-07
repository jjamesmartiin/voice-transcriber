#!/usr/bin/env python3
"""macOS-specific HAL tests: clipboard, typing, audio cues, hotkeys, and notifications."""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import hal  # noqa: E402


# ===========================================================================
# 1. Clipboard & Typing Sink Tests
# ===========================================================================
class TestMacOSClipboardSink:
    def test_pbcopy_preferred(self, monkeypatch):
        calls = []

        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0)

        monkeypatch.setattr(
            shutil, "which", lambda name: f"/usr/bin/{name}" if name == "pbcopy" else None
        )
        monkeypatch.setattr(subprocess, "run", fake_run)

        sink = hal.get_clipboard_sink(hal.MACOS)
        assert sink.copy_text("hello macos") is True
        assert calls[0][0] == ["pbcopy"]
        assert calls[0][1]["input"] == b"hello macos"

    def test_pbcopy_failure_falls_back_to_pyperclip(self, monkeypatch):
        copied_texts = []
        fake_pyperclip = types.ModuleType("pyperclip")
        fake_pyperclip.copy = lambda text: copied_texts.append(text)
        monkeypatch.setitem(sys.modules, "pyperclip", fake_pyperclip)

        monkeypatch.setattr(shutil, "which", lambda name: None)

        sink = hal.get_clipboard_sink(hal.MACOS)
        assert sink.copy_text("fallback text") is True
        assert copied_texts == ["fallback text"]

    def test_type_text_osascript(self, monkeypatch):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0)

        monkeypatch.setattr(
            shutil, "which", lambda name: f"/usr/bin/{name}" if name == "osascript" else None
        )
        monkeypatch.setattr(subprocess, "run", fake_run)

        sink = hal.get_clipboard_sink(hal.MACOS)
        assert sink.type_text('Hello "World" \\ test') is True
        assert len(calls) == 1
        assert calls[0][0] == "osascript"
        assert calls[0][1] == "-e"
        assert calls[0][2] == 'tell application "System Events" to keystroke "Hello \\"World\\" \\\\ test"'

    def test_type_text_pynput_fallback(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)

        typed_chars = []

        class FakeController:
            def type(self, text):
                typed_chars.append(text)

        fake_kb = types.ModuleType("pynput.keyboard")
        fake_kb.Controller = FakeController
        monkeypatch.setitem(sys.modules, "pynput.keyboard", fake_kb)

        sink = hal.get_clipboard_sink(hal.MACOS)
        assert sink.type_text("typed via pynput") is True
        assert typed_chars == ["typed via pynput"]

    def test_type_text_degrades_to_clipboard(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)

        # Force pynput to fail so it degrades to clipboard
        def _raise_importerror(*args, **kwargs):
            raise ImportError("pynput unavailable")

        fake_kb = types.ModuleType("pynput.keyboard")
        fake_kb.Controller = _raise_importerror
        monkeypatch.setitem(sys.modules, "pynput.keyboard", fake_kb)

        copied = []
        fake_pyperclip = types.ModuleType("pyperclip")
        fake_pyperclip.copy = lambda text: copied.append(text)
        monkeypatch.setitem(sys.modules, "pyperclip", fake_pyperclip)

        sink = hal.get_clipboard_sink(hal.MACOS)
        assert sink.type_text("degraded text") is True
        assert copied == ["degraded text"]

    def test_type_text_empty_string(self):
        sink = hal.get_clipboard_sink(hal.MACOS)
        assert sink.type_text("") is True


# ===========================================================================
# 2. Audio Cues Tests
# ===========================================================================
class TestMacOSAudioCuePlayer:
    def test_afplay_preferred(self, monkeypatch):
        calls = []

        def fake_popen(args, **kwargs):
            calls.append((args, kwargs))
            return MagicMock()

        monkeypatch.setattr(
            shutil, "which", lambda name: f"/usr/bin/{name}" if name == "afplay" else None
        )
        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        player = hal.get_audio_cue_player(hal.MACOS)
        player.play_cue("start")

        assert len(calls) == 1
        assert calls[0][0][0] == "/usr/bin/afplay"
        assert calls[0][0][1].endswith("sounds/start.mp3")

    def test_sounddevice_fallback(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)
        monkeypatch.setattr("os.path.exists", lambda path: False)

        played = []
        fake_sd = types.ModuleType("sounddevice")
        fake_sd.play = lambda data, sr: played.append((data, sr))

        fake_sf = types.ModuleType("soundfile")
        fake_sf.read = lambda path, dtype: ("fake_data", 16000)

        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
        monkeypatch.setitem(sys.modules, "soundfile", fake_sf)

        player = hal.get_audio_cue_player(hal.MACOS)
        player.play_cue("complete")

        assert len(played) == 1
        assert played[0] == ("fake_data", 16000)

    def test_sound_path(self):
        player = hal.get_audio_cue_player(hal.MACOS)
        path = player.sound_path("stop")
        assert path.endswith("sounds/stop.mp3")


# ===========================================================================
# 3. Hotkey Manager Tests
# ===========================================================================
class TestMacOSHotkeyManager:
    def test_cmd_shift_push_to_talk(self):
        cb_start = MagicMock()
        cb_stop = MagicMock()

        MacOSHotkeyManager = hal.load_backend("macos", "hotkeys").MacOSHotkeyManager
        manager = MacOSHotkeyManager(cb_start, cb_stop)
        try:
            class FakeKey:
                def __init__(self, name):
                    self.name = name

            key_cmd = FakeKey("cmd")
            key_shift = FakeKey("shift")
            manager.CMD_KEYS = {key_cmd}
            manager.SHIFT_KEYS = {key_shift}

            # Press Cmd
            manager._on_press(key_cmd)
            cb_start.assert_not_called()
            assert manager.hotkey_active is False

            # Press Shift -> Cmd+Shift triggers start
            manager._on_press(key_shift)
            cb_start.assert_called_once()
            assert manager.hotkey_active is True

            # Release Shift -> triggers stop
            manager._on_release(key_shift)
            cb_stop.assert_called_once_with(copy_to_clipboard=False)
            assert manager.hotkey_active is False
        finally:
            manager.cleanup()

    def test_alt_shift_push_to_talk(self):
        cb_start = MagicMock()
        cb_stop = MagicMock()

        MacOSHotkeyManager = hal.load_backend("macos", "hotkeys").MacOSHotkeyManager
        manager = MacOSHotkeyManager(cb_start, cb_stop)
        try:
            class FakeKey:
                def __init__(self, name):
                    self.name = name

            key_alt = FakeKey("alt")
            key_shift = FakeKey("shift")
            manager.ALT_KEYS = {key_alt}
            manager.SHIFT_KEYS = {key_shift}

            # Press Alt then Shift
            manager._on_press(key_alt)
            manager._on_press(key_shift)
            cb_start.assert_called_once()
            assert manager.hotkey_active is True

            # Release Alt
            manager._on_release(key_alt)
            cb_stop.assert_called_once()
            assert manager.hotkey_active is False
        finally:
            manager.cleanup()

    def test_space_hands_free_latch(self):
        cb_start = MagicMock()
        cb_stop = MagicMock()

        MacOSHotkeyManager = hal.load_backend("macos", "hotkeys").MacOSHotkeyManager
        manager = MacOSHotkeyManager(cb_start, cb_stop)
        try:
            class FakeKey:
                def __init__(self, name, char=None):
                    self.name = name
                    self.char = char

            key_cmd = FakeKey("cmd")
            key_shift = FakeKey("shift")
            key_space = FakeKey("space", char=" ")

            manager.CMD_KEYS = {key_cmd}
            manager.SHIFT_KEYS = {key_shift}
            manager._Key = MagicMock()
            manager._Key.space = key_space

            # Activate Cmd+Shift
            manager._on_press(key_cmd)
            manager._on_press(key_shift)
            assert manager.hotkey_active is True

            # Tap Space while active
            manager._on_press(key_space)
            assert manager.latch_release is True
            manager._on_release(key_space)

            # Release Cmd+Shift -> latch is consumed, stop is NOT called
            manager._on_release(key_shift)
            manager._on_release(key_cmd)
            assert manager.latch_release is False
            assert manager.hotkey_active is False
            cb_stop.assert_not_called()

            # Tap Cmd+Shift again to conclude hands-free session
            manager._on_press(key_cmd)
            manager._on_press(key_shift)
            assert manager.hotkey_active is True
            manager._on_release(key_shift)
            assert manager.hotkey_active is False
            cb_stop.assert_called_once()
        finally:
            manager.cleanup()

    def test_ctrl_held_sets_copy_to_clipboard_mode(self):
        cb_start = MagicMock()
        cb_stop = MagicMock()

        MacOSHotkeyManager = hal.load_backend("macos", "hotkeys").MacOSHotkeyManager
        manager = MacOSHotkeyManager(cb_start, cb_stop)
        try:
            class FakeKey:
                def __init__(self, name):
                    self.name = name

            key_ctrl = FakeKey("ctrl")
            key_cmd = FakeKey("cmd")
            key_shift = FakeKey("shift")

            manager.CTRL_KEYS = {key_ctrl}
            manager.CMD_KEYS = {key_cmd}
            manager.SHIFT_KEYS = {key_shift}

            # Hold Ctrl before triggering Cmd+Shift
            manager._on_press(key_ctrl)
            manager._on_press(key_cmd)
            manager._on_press(key_shift)

            assert manager.hotkey_active is True
            assert manager.copy_to_clipboard_mode is True

            manager._on_release(key_shift)
            cb_stop.assert_called_once_with(copy_to_clipboard=True)
        finally:
            manager.cleanup()

    def test_middle_click_push_to_talk(self):
        cb_start = MagicMock()
        cb_stop = MagicMock()

        MacOSHotkeyManager = hal.load_backend("macos", "hotkeys").MacOSHotkeyManager
        manager = MacOSHotkeyManager(cb_start, cb_stop)
        try:
            manager.set_middle_click_enabled(True)
            assert manager.middle_click_enabled is True

            class FakeButton:
                middle = "middle"

            manager._pynput_mouse = MagicMock()
            manager._pynput_mouse.Button = FakeButton

            # Press middle button
            manager._on_mouse_click(0, 0, FakeButton.middle, True)
            assert manager.middle_pressed is True
            assert manager._middle_click_timer is not None

            # Simulate timer timeout
            manager._on_middle_click_hold_timeout()
            assert manager.hotkey_active is True
            assert manager.middle_click_active is True
            cb_start.assert_called_once()

            # Release middle button
            manager._on_mouse_click(0, 0, FakeButton.middle, False)
            assert manager.hotkey_active is False
            assert manager.middle_click_active is False
            cb_stop.assert_called_once_with(copy_to_clipboard=False)
        finally:
            manager.cleanup()

    def test_middle_click_short_tap_cancels_timer(self):
        cb_start = MagicMock()
        cb_stop = MagicMock()

        MacOSHotkeyManager = hal.load_backend("macos", "hotkeys").MacOSHotkeyManager
        manager = MacOSHotkeyManager(cb_start, cb_stop)
        try:
            manager.set_middle_click_enabled(True)

            class FakeButton:
                middle = "middle"

            manager._pynput_mouse = MagicMock()
            manager._pynput_mouse.Button = FakeButton

            # Quick tap: press then immediate release
            manager._on_mouse_click(0, 0, FakeButton.middle, True)
            assert manager._middle_click_timer is not None
            manager._on_mouse_click(0, 0, FakeButton.middle, False)
            assert manager._middle_click_timer is None
            assert manager.hotkey_active is False
            cb_start.assert_not_called()
            cb_stop.assert_not_called()
        finally:
            manager.cleanup()

    def test_paste_text_osascript(self, monkeypatch):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0)

        monkeypatch.setattr(
            shutil, "which", lambda name: f"/usr/bin/{name}" if name == "osascript" else None
        )
        monkeypatch.setattr(subprocess, "run", fake_run)

        MacOSHotkeyManager = hal.load_backend("macos", "hotkeys").MacOSHotkeyManager
        manager = MacOSHotkeyManager(MagicMock(), MagicMock())
        try:
            assert manager.paste_text() is True
            assert len(calls) == 1
            assert calls[0] == [
                "osascript",
                "-e",
                'tell application "System Events" to keystroke "v" using command down',
            ]
        finally:
            manager.cleanup()

    def test_paste_text_pynput_fallback(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)

        events = []

        class FakeKey:
            cmd = "cmd"

        class FakeController:
            def pressed(self, key):
                class Ctx:
                    def __enter__(self):
                        events.append(f"pressed_{key}")
                    def __exit__(self, *args):
                        events.append(f"released_{key}")
                return Ctx()

            def press(self, char):
                events.append(f"press_{char}")

            def release(self, char):
                events.append(f"release_{char}")

        fake_kb = types.ModuleType("pynput.keyboard")
        fake_kb.Controller = FakeController
        fake_kb.Key = FakeKey
        monkeypatch.setitem(sys.modules, "pynput.keyboard", fake_kb)

        MacOSHotkeyManager = hal.load_backend("macos", "hotkeys").MacOSHotkeyManager
        manager = MacOSHotkeyManager(MagicMock(), MagicMock())
        try:
            assert manager.paste_text() is True
            assert "pressed_cmd" in events
            assert "press_v" in events
            assert "release_v" in events
            assert "released_cmd" in events
        finally:
            manager.cleanup()

    def test_check_accessibility_permissions_mock(self, monkeypatch):
        import ctypes
        from voice_transcriber.platform.macos.hotkeys import check_accessibility_permissions

        class FakeAppServices:
            def AXIsProcessTrusted(self):
                return True

        monkeypatch.setattr(ctypes.cdll, "LoadLibrary", lambda path: FakeAppServices(), raising=False)
        assert check_accessibility_permissions() is True


# ===========================================================================
# 4. Visual Notification Tests
# ===========================================================================
class TestMacOSVisualNotification:
    def test_notification_dispatches_osascript(self, monkeypatch):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        MacOSVisualNotification = hal.load_backend("macos", "notifications").MacOSVisualNotification
        notifier = MacOSVisualNotification(app_name="Voice Transcriber Test")

        notifier.show_recording()
        time.sleep(0.1)  # Allow background daemon thread to execute
        assert len(calls) >= 1
        assert "display notification \"Recording in progress...\" with title \"Voice Transcriber Test\"" in calls[0][2]

    def test_notification_with_tui_updates(self, monkeypatch):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        fake_tui = MagicMock()
        MacOSVisualNotification = hal.load_backend("macos", "notifications").MacOSVisualNotification
        notifier = MacOSVisualNotification(app_name="Voice Transcriber", tui=fake_tui)

        notifier.show_processing("Transcribing audio")
        fake_tui.update_state.assert_called_with("PROCESSING", "Transcribing audio")

        notifier.show_completed(text="COMPLETED", sub_text="Hello world transcription")
        fake_tui.print_transcription.assert_called_once()
        assert fake_tui.update_state.call_count >= 2

        notifier.show_error("Failed to load model")
        fake_tui.print_error.assert_called_with("ERROR", "Failed to load model")

        notifier.show_warning("Audio clipped")
        fake_tui.print_warning.assert_called_with("WARNING", "Audio clipped")

        notifier.hide_notification()
        fake_tui.update_state.assert_called_with("READY")


class TestMacOSHotkeyBinds:
    """User-configured binds on macOS (the shipped chord keeps Cmd-or-Alt)."""

    @staticmethod
    def _manager(binds=None):
        MacOSHotkeyManager = hal.load_backend("macos", "hotkeys").MacOSHotkeyManager
        manager = MacOSHotkeyManager(MagicMock(), MagicMock())
        if binds is not None:
            assert manager.set_binds(binds) is True
        return manager

    @staticmethod
    def _fake_key_namespace(**attrs):
        class FakeKey:
            def __init__(self, name):
                self.name = name

            def __repr__(self):
                return f"<Key {self.name}>"

        return FakeKey, types.SimpleNamespace(
            **{name: FakeKey(name) for name in attrs}
        )

    def test_a_user_bind_replaces_the_shipped_chord(self):
        manager = self._manager("ctrl+shift")
        try:
            FakeKey, keys = self._fake_key_namespace(ctrl_l="ctrl_l", shift_l="shift_l")
            manager._Key = keys
            # Alt is no longer part of the trigger.
            manager.pressed_keys = {FakeKey("alt_l"), FakeKey("shift_l")}
            assert manager._is_main_hotkey_pressed() is False
            manager.pressed_keys = {keys.ctrl_l, keys.shift_l}
            assert manager._is_main_hotkey_pressed() is True
        finally:
            manager.cleanup()

    def test_bare_alt_still_accepts_cmd_on_macos(self):
        """``alt`` means the modifier by the space bar, which is Cmd here."""
        manager = self._manager("alt+shift")
        try:
            FakeKey, keys = self._fake_key_namespace(shift_l="shift_l")
            manager._Key = keys
            cmd = FakeKey("cmd_l")
            manager.CMD_KEYS = {cmd}
            manager.pressed_keys = {cmd, keys.shift_l}
            # The shipped chord is special-cased to the legacy behaviour, so use
            # a chord with an extra key to exercise the expansion path.
            assert manager.set_binds("alt+shift+space") is True
            manager.pressed_keys = {cmd, keys.shift_l}
            assert manager._is_main_hotkey_pressed() is False  # space missing
        finally:
            manager.cleanup()

    def test_an_unusable_chord_is_refused_and_leaves_the_old_one_live(self):
        manager = self._manager()
        try:
            before = list(manager.binds)
            assert manager.set_binds("hyper+shift") is False
            assert list(manager.binds) == before
            assert manager.set_binds(12345) is False
            assert list(manager.binds) == before
        finally:
            manager.cleanup()
