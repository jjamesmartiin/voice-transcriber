#!/usr/bin/env python3
"""WSL mouse-mode (middle-click push-to-talk) tests.

WSL splits the feature in two:

* the Python ``WSLHotkeyManager`` side, which relays ``SET_MCLICK`` over stdin
  and turns ``HOTKEY_DOWN``/``HOTKEY_UP`` into record start/stop, and
* the host side, ``wsl_win_hotkeys.ps1``, which polls ``GetAsyncKeyState`` for
  ``VK_MBUTTON`` and applies the hold threshold.

The Python half is exercised live with a mocked bridge. The PowerShell half
cannot run in CI (the WSL job is an Ubuntu runner with no ``powershell.exe``),
so its wiring is pinned structurally: if someone drops the middle-button gate
or the hold threshold the test fails. Real end-to-end verification of the host
script requires running on Windows/WSL.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import hal  # noqa: E402

BRIDGE_SCRIPT = REPO_ROOT / "src" / "voice_transcriber" / "platform" / "wsl" / "wsl_win_hotkeys.ps1"


def _mock_bridge(monkeypatch):
    """Build a WSLHotkeyManager whose powershell bridge is fully faked."""

    class MockStdin:
        def __init__(self):
            self.writes = []

        def write(self, s):
            self.writes.append(s)

        def flush(self):
            pass

    class MockStdout:
        def __init__(self):
            self.lines = ["READY\n"]

        def readline(self):
            return self.lines.pop(0) if self.lines else ""

    class MockProcess:
        def __init__(self):
            self.stdin = MockStdin()
            self.stdout = MockStdout()

        def poll(self):
            return None

        def terminate(self):
            pass

    mock_proc = MockProcess()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: mock_proc)
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0)
    )

    WSLHotkeyManager = hal.load_backend("wsl", "hotkeys").WSLHotkeyManager
    manager = WSLHotkeyManager(MagicMock(), MagicMock())
    return manager, mock_proc


class TestWSLMouseModeIPC:
    def test_toggle_sends_set_mclick_and_tracks_state(self, monkeypatch):
        manager, proc = _mock_bridge(monkeypatch)
        try:
            assert manager.middle_click_enabled is False

            assert manager.set_middle_click_enabled(True) is True
            assert manager.middle_click_enabled is True
            assert "SET_MCLICK:1\n" in proc.stdin.writes

            assert manager.set_middle_click_enabled(False) is True
            assert manager.middle_click_enabled is False
            assert "SET_MCLICK:0\n" in proc.stdin.writes
        finally:
            manager.stop()

    def test_reader_drives_record_start_and_stop(self, monkeypatch):
        """HOTKEY_DOWN/HOTKEY_UP from the host start and stop recording."""
        WSLHotkeyManager = hal.load_backend("wsl", "hotkeys").WSLHotkeyManager
        cb_start = MagicMock()
        cb_stop = MagicMock()

        manager = WSLHotkeyManager.__new__(WSLHotkeyManager)
        manager.callback_start = cb_start
        manager.callback_stop = cb_stop
        manager.running = True
        manager.hotkey_active = False

        class FakeStdout:
            def __init__(self, events):
                self.events = [e + "\n" for e in events]

            def readline(self):
                return self.events.pop(0) if self.events else ""

        manager.process = MagicMock()
        manager.process.poll.return_value = None
        manager.process.stdout = FakeStdout(["HOTKEY_DOWN", "HOTKEY_UP"])

        manager._reader_loop()

        import time

        time.sleep(0.05)
        cb_start.assert_called_once()
        cb_stop.assert_called_once()


class TestWSLHostBridgeMouseMode:
    """Structural contract for the PowerShell bridge (runs on Ubuntu CI)."""

    @pytest.fixture(scope="class")
    def script(self):
        assert BRIDGE_SCRIPT.exists(), f"missing bridge script at {BRIDGE_SCRIPT}"
        return BRIDGE_SCRIPT.read_text(encoding="utf-8")

    def test_middle_button_is_polled_and_gated(self, script):
        assert "VK_MBUTTON" in script
        assert "IsMButtonPressed" in script
        assert "MiddleClickEnabled" in script

    def test_set_mclick_command_is_parsed(self, script):
        assert '"SET_MCLICK:"' in script
        assert "SetMiddleClickEnabled" in script

    def test_hold_threshold_and_trigger_are_wired(self, script):
        # A middle press must be held (not tapped) before it counts.
        assert "TotalMilliseconds -ge 250" in script
        # Recording starts when a configured bind OR the held middle button is down.
        assert "$isHotkeyDown = $isBindDown -or $mButtonActive" in script

    def test_hotkey_events_are_emitted(self, script):
        assert '"HOTKEY_DOWN"' in script
        assert '"HOTKEY_UP"' in script
