#!/usr/bin/env python3
"""WSL push-to-talk binds: Python <-> PowerShell bridge contract.

The host bridge is the only side that can read Windows key state, so it owns
the chord matching: Python encodes the configured binds into one ``-Binds``
argv flag and the PowerShell helper polls it with ``GetAsyncKeyState``.

The PowerShell half cannot run in CI (the WSL job is an Ubuntu runner with no
``powershell.exe``), so its parsing/wiring is pinned structurally. The Python
half is exercised live with a faked bridge process.
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
from voice_transcriber import keybinds  # noqa: E402

BRIDGE_SCRIPT = (
    SRC_DIR / "voice_transcriber" / "platform" / "wsl" / "wsl_win_hotkeys.ps1"
)


class _MockStdin:
    def __init__(self):
        self.writes = []

    def write(self, s):
        self.writes.append(s)

    def flush(self):
        pass


class _MockStdout:
    def __init__(self):
        self.lines = ["READY\n"]

    def readline(self):
        return self.lines.pop(0) if self.lines else ""


class _MockProcess:
    def __init__(self):
        self.stdin = _MockStdin()
        self.stdout = _MockStdout()

    def poll(self):
        return None

    def terminate(self):
        pass


def _patch_bridge(monkeypatch):
    """Fake powershell launches; return (commands, processes) captured."""
    commands = []
    processes = []

    def fake_popen(cmd, **kwargs):
        commands.append(list(cmd))
        proc = _MockProcess()
        processes.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess([], 0, stdout=""),
    )
    return commands, processes


def _flag(cmd):
    return cmd[cmd.index("-Binds") + 1]


def test_default_binds_encode_to_the_alt_shift_fallback():
    assert keybinds.DEFAULT_VK_BINDS == "164,165;160,161"
    assert keybinds.encode_vk_binds(keybinds.parse_binds(None)) == "164,165;160,161"


def test_launch_forwards_encoded_default_binds(monkeypatch):
    commands, _ = _patch_bridge(monkeypatch)
    WSLHotkeyManager = hal.load_backend("wsl", "hotkeys").WSLHotkeyManager
    manager = WSLHotkeyManager(MagicMock(), MagicMock())
    try:
        assert commands, "bridge was never launched"
        cmd = commands[0]
        assert "-Binds" in cmd
        assert _flag(cmd) == keybinds.DEFAULT_VK_BINDS
    finally:
        manager.stop()


def test_constructor_forwards_explicit_binds(monkeypatch):
    commands, _ = _patch_bridge(monkeypatch)
    WSLHotkeyManager = hal.load_backend("wsl", "hotkeys").WSLHotkeyManager
    manager = WSLHotkeyManager(
        MagicMock(),
        MagicMock(),
        binds=[{"keys": ["alt", "shift"]}, {"keys": ["f13"]}],
    )
    try:
        # alt+shift | f13 -> 164,165;160,161|124
        assert _flag(commands[0]) == "164,165;160,161|124"
    finally:
        manager.stop()


def test_set_binds_restarts_bridge_with_new_chords(monkeypatch):
    commands, processes = _patch_bridge(monkeypatch)
    WSLHotkeyManager = hal.load_backend("wsl", "hotkeys").WSLHotkeyManager
    manager = WSLHotkeyManager(MagicMock(), MagicMock())
    try:
        assert len(commands) == 1
        assert manager.set_binds([{"keys": ["f13"]}]) is True
        assert len(commands) == 2, "bridge should have been relaunched"
        assert _flag(commands[1]) == "124"
        # The previous helper was told to exit before the replacement launched.
        assert "EXIT\n" in processes[0].stdin.writes
        assert [b.chord for b in manager.binds] == ["f13"]
    finally:
        manager.stop()


def test_set_binds_invalid_returns_false_and_keeps_old_binds(monkeypatch):
    commands, _ = _patch_bridge(monkeypatch)
    WSLHotkeyManager = hal.load_backend("wsl", "hotkeys").WSLHotkeyManager
    manager = WSLHotkeyManager(MagicMock(), MagicMock())
    try:
        old_flag = manager._binds_flag
        assert manager.set_binds([{"keys": ["notakey"]}]) is False
        assert manager._binds_flag == old_flag
        assert [b.chord for b in manager.binds] == ["alt+shift"]
        assert len(commands) == 1, "invalid binds must not relaunch the bridge"
    finally:
        manager.stop()


class TestBridgeBindPins:
    """Structural contract for the PowerShell helper (runs on Ubuntu CI)."""

    @pytest.fixture(scope="class")
    def script(self):
        assert BRIDGE_SCRIPT.exists(), f"missing bridge script at {BRIDGE_SCRIPT}"
        return BRIDGE_SCRIPT.read_text(encoding="utf-8")

    def test_binds_parameter_declared(self, script):
        assert "[string]$Binds" in script

    def test_absent_flag_falls_back_to_spec_default(self, script):
        # The ps1 cannot import Python, so pin the literal against the module.
        assert keybinds.DEFAULT_VK_BINDS == "164,165;160,161"
        assert f'$Binds = "{keybinds.DEFAULT_VK_BINDS}"' in script

    def test_is_bind_pressed_defined_and_used(self, script):
        assert "IsBindPressed" in script
        assert "[WinInterop]::IsBindPressed($Binds)" in script
        # The hardcoded Alt+Shift matcher is gone.
        assert "IsAltShiftPressed" not in script

    def test_three_level_encoding_is_parsed(self, script):
        # '|' separates binds, ';' keys within a bind, ',' alternatives.
        assert "binds.Split('|')" in script
        assert "bind.Split(';')" in script
        assert "group.Split(',')" in script

    def test_key_satisfied_by_any_alternative_but_all_keys_required(self, script):
        # Any one code in a key group being down satisfies that key...
        assert "(GetAsyncKeyState(vk) & 0x8000) != 0" in script
        assert "keyDown = true" in script
        # ...and a bind needs every group, i.e. any bind firing the trigger.
        assert "if (!keyDown)" in script
        assert "if (bindDown)" in script
