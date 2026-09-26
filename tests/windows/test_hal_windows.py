#!/usr/bin/env python3
"""Windows-specific HAL tests: SendInput/keybd_event typing and space-latch."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import hal  # noqa: E402


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

