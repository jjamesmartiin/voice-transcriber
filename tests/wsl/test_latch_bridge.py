#!/usr/bin/env python3
"""WSL hands-free latch: PowerShell bridge contract + Python event mapping.

The latch (tap ``Space`` while holding the record trigger) is split in two for
WSL, because the host owns both the trigger state and the start chime:

* ``wsl_win_hotkeys.ps1`` polls ``GetAsyncKeyState(VK_SPACE)``, emits
  ``LATCH_DOWN`` when Space is tapped mid-hold, and — crucially — **withholds**
  the ``HOTKEY_UP`` when the keys come up, emitting ``LATCH_HOLD`` instead so
  the recording stays open. It must own that decision because it also plays the
  start chime: a naive re-press would chime again while already recording.
* ``WSLHotkeyManager._reader_loop`` maps those events onto the shared callback
  contract and mirrors ``latch_release``.

The PowerShell half cannot run in CI (the WSL job is an Ubuntu runner with no
``powershell.exe``), so it is pinned structurally: dropping the Space poll, the
latch guard, or the withheld release fails these tests. Real end-to-end
verification of the host script requires running on Windows/WSL.
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"

BRIDGE_SCRIPT = SRC_DIR / "voice_transcriber" / "platform" / "wsl" / "wsl_win_hotkeys.ps1"


def _manager_with_events(events):
    """Build a WSLHotkeyManager whose bridge stdout replays ``events``."""
    import hal

    WSLHotkeyManager = hal.load_backend("wsl", "hotkeys").WSLHotkeyManager
    manager = WSLHotkeyManager.__new__(WSLHotkeyManager)
    manager.callback_start = MagicMock()
    manager.callback_stop = MagicMock()
    manager.hotkey_active = False
    manager.latch_release = False
    manager.running = True

    class FakeStdout:
        def __init__(self, lines):
            self.lines = [line + "\n" for line in lines]

        def readline(self):
            return self.lines.pop(0) if self.lines else ""

    manager.process = MagicMock()
    manager.process.poll.return_value = None
    manager.process.stdout = FakeStdout(events)
    return manager


class TestWSLHostBridgeLatch:
    """Structural contract for the PowerShell bridge (runs on Ubuntu CI)."""

    @pytest.fixture(scope="class")
    def script(self):
        assert BRIDGE_SCRIPT.exists(), f"missing bridge script at {BRIDGE_SCRIPT}"
        return BRIDGE_SCRIPT.read_text(encoding="utf-8")

    def test_space_is_polled(self, script):
        assert "VK_SPACE" in script
        assert "IsSpacePressed" in script
        assert "[WinInterop]::IsSpacePressed()" in script

    def test_latch_events_are_emitted(self, script):
        assert '"LATCH_DOWN"' in script
        assert '"LATCH_HOLD"' in script

    def test_space_only_latches_while_the_trigger_is_held(self, script):
        # Guards against IPC noise: an ordinary Space keystroke while typing must
        # not emit anything. Also stops a second tap re-emitting the latch.
        assert "-not $wasSpaceDown -and $wasHotkeyDown -and -not $latched" in script

    def test_release_is_withheld_while_latched(self, script):
        # The trigger-up branch must consult $latched and emit LATCH_HOLD rather
        # than HOTKEY_UP, otherwise the recording would stop on key release.
        release_branch = script.split("elseif (-not $isHotkeyDown -and $wasHotkeyDown)")[1]
        release_branch = release_branch.split("Space tapped while the trigger is held")[0]
        assert "$latched" in release_branch
        assert '"LATCH_HOLD"' in release_branch
        assert '"HOTKEY_UP"' in release_branch  # the non-latched path still stops

    def test_start_chime_fires_once_and_not_when_finishing(self, script):
        # The host owns the start chime, so the finishing press (which merely
        # ends an already-running latched recording) must stay silent.
        assert script.count("[WinInterop]::PlayStartSound()") == 1

        # ...and it lives in the "not already recording" branch of the press.
        press = script.split("if ($isHotkeyDown -and -not $wasHotkeyDown) {")[1]
        press = press.split("} elseif (-not $isHotkeyDown")[0]
        finishing, fresh = press.split("} else {")
        assert '"HOTKEY_UP"' in finishing
        assert "[WinInterop]::PlayStartSound()" not in finishing
        assert "[WinInterop]::PlayStartSound()" in fresh

    def test_latch_state_mirrors_the_shared_contract(self, script):
        # `$latched` plays the role of BaseHotkeyManager.latch_release, and
        # `$recording` tracks whether a trigger-hold is still open.
        assert "$latched = $false" in script
        assert "$recording = $false" in script


class TestWSLLatchEventMapping:
    """The Python half: host events -> the shared hotkey callbacks."""

    def test_full_latch_workflow(self):
        manager = _manager_with_events(
            [
                "HOTKEY_DOWN",  # 1. hold Alt+Shift -> start recording
                "LATCH_DOWN",  # 2. tap Space      -> latch hands-free
                "LATCH_HOLD",  # 3. release keys   -> keep recording
                "HOTKEY_UP",  # 4. tap again      -> finish
            ]
        )

        manager._reader_loop()
        time.sleep(0.05)

        manager.callback_start.assert_called_once()
        manager.callback_stop.assert_called_once_with(copy_to_clipboard=True)
        assert manager.hotkey_active is False
        assert manager.latch_release is False

    def test_latch_hold_does_not_stop_recording(self):
        manager = _manager_with_events(["HOTKEY_DOWN", "LATCH_DOWN", "LATCH_HOLD"])

        manager._reader_loop()
        time.sleep(0.05)

        manager.callback_start.assert_called_once()
        manager.callback_stop.assert_not_called()
        # Nothing is physically held any more, but the recording is open.
        assert manager.hotkey_active is False

    def test_latch_flag_is_visible_between_the_two_halves(self):
        """`latch_release` mirrors the Linux/Windows backends mid-latch."""
        manager = _manager_with_events(["HOTKEY_DOWN", "LATCH_DOWN"])

        manager._reader_loop()
        time.sleep(0.05)

        assert manager.latch_release is True
        manager.callback_stop.assert_not_called()

    def test_plain_push_to_talk_still_stops_on_release(self):
        manager = _manager_with_events(["HOTKEY_DOWN", "HOTKEY_UP"])

        manager._reader_loop()
        time.sleep(0.05)

        manager.callback_start.assert_called_once()
        manager.callback_stop.assert_called_once_with(copy_to_clipboard=True)
        assert manager.latch_release is False
