#!/usr/bin/env python3
"""Linux-specific HAL tests: clipboard command lines and uinput/evdev typing."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import hal  # noqa: E402


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

