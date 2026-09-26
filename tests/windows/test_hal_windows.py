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
    def test_ascii_uses_virtual_keys_and_emoji_uses_unicode(self, monkeypatch):
        import ctypes

        class FakeUser32:
            def __init__(self):
                self.sendinput = 0
                self.keybd = []

            def VkKeyScanW(self, cp):
                return {
                    ord("H"): 0x0148,  # shift + VK_H
                    ord("e"): 0x65,
                    ord("l"): 0x6C,
                    ord("o"): 0x6F,
                    ord(" "): 0x20,
                }.get(cp, -1)

            def keybd_event(self, vk, scan, flags, extra):
                self.keybd.append((vk, flags))

            def SendInput(self, n, p_inputs, cb_size):
                self.sendinput += n
                return n

        fake = FakeUser32()
        monkeypatch.setattr(ctypes, "windll", type("W", (), {"user32": fake})(), raising=False)
        sink = hal.get_clipboard_sink(hal.WINDOWS)

        assert sink.type_text("Hello \U0001F680", fast=True) is True
        # ASCII/space goes through real virtual keys (fast + classic-control
        # compatible): 'H' with shift (4 events) + 'ello ' (2 each) = 14.
        assert len(fake.keybd) == 14
        # The emoji is non-typable, so its 2 UTF-16 code units use KEYEVENTF_UNICODE
        # (one SendInput call each for down + up).
        assert fake.sendinput == 4

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
        keybd_calls = []

        class FakeUser32:
            def VkKeyScanW(self, cp):
                return {ord("H"): 0x0148, ord("i"): 0x69}.get(cp, -1)

            def keybd_event(self, vk, scan, flags, extra):
                keybd_calls.append((vk, flags))

            def SendInput(self, n, p_inputs, cb_size):
                return n

        class FakeWindll:
            user32 = FakeUser32()

        monkeypatch.setattr(ctypes, "windll", FakeWindll(), raising=False)
        from unittest.mock import MagicMock
        WindowsHotkeyManager = hal.load_backend("windows", "hotkeys").WindowsHotkeyManager
        manager = WindowsHotkeyManager(MagicMock(), MagicMock())
        try:
            assert manager.type_text("Hi", fast=True) is True
            # 'H' (shift down/up + key down/up = 4) + 'i' (2) = 6 keybd_event calls
            assert len(keybd_calls) == 6
        finally:
            manager.cleanup()

