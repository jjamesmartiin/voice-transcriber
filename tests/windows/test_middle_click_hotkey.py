#!/usr/bin/env python3
"""Windows middle-click hold push-to-talk (mocked pynput, no real mouse)."""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import hal  # noqa: E402


def test_windows_middle_click_hold(wait_until):
    """WindowsHotkeyManager triggers push-to-talk on middle click hold."""
    WindowsHotkeyManager = hal.load_backend("windows", "hotkeys").WindowsHotkeyManager
    cb_start = MagicMock()
    cb_stop = MagicMock()
    manager = WindowsHotkeyManager(cb_start, cb_stop)
    manager.set_middle_click_enabled(True)
    try:
        from pynput.mouse import Button

        # Quick click (<0.25s)
        manager._on_mouse_click(0, 0, Button.middle, True)
        time.sleep(0.05)
        manager._on_mouse_click(0, 0, Button.middle, False)
        time.sleep(0.25)
        assert cb_start.call_count == 0
        assert cb_stop.call_count == 0

        # Hold >=0.25s. The hold fires from a threading.Timer, so wait for the
        # transition: a fixed sleep races the scheduler on a loaded runner.
        manager._on_mouse_click(0, 0, Button.middle, True)
        assert manager.are_modifiers_pressed() is True
        assert wait_until(
            lambda: cb_start.call_count == 1 and manager.hotkey_active is True
        ), "the hold delay elapsed but push-to-talk never started"
        assert manager.middle_click_active is True

        # Release
        manager._on_mouse_click(0, 0, Button.middle, False)
        assert cb_stop.call_count == 1
        assert manager.hotkey_active is False
        assert manager.middle_click_active is False
    finally:
        manager.cleanup()


def test_windows_middle_click_disabled_ignores_events():
    """With mouse mode off, middle clicks never start push-to-talk."""
    WindowsHotkeyManager = hal.load_backend("windows", "hotkeys").WindowsHotkeyManager
    cb_start = MagicMock()
    cb_stop = MagicMock()
    manager = WindowsHotkeyManager(cb_start, cb_stop)
    manager.set_middle_click_enabled(False)
    try:
        from pynput.mouse import Button

        manager._on_mouse_click(0, 0, Button.middle, True)
        time.sleep(0.3)
        manager._on_mouse_click(0, 0, Button.middle, False)
        assert cb_start.call_count == 0
        assert cb_stop.call_count == 0
        assert manager.hotkey_active is False
        assert manager.middle_click_active is False
    finally:
        manager.cleanup()


def test_windows_disabling_mouse_mode_mid_hold_stops_recording(wait_until):
    """Toggling mouse mode off during a hold cancels it and stops recording."""
    WindowsHotkeyManager = hal.load_backend("windows", "hotkeys").WindowsHotkeyManager
    cb_start = MagicMock()
    cb_stop = MagicMock()
    manager = WindowsHotkeyManager(cb_start, cb_stop)
    manager.set_middle_click_enabled(True)
    try:
        from pynput.mouse import Button

        manager._on_mouse_click(0, 0, Button.middle, True)
        assert wait_until(
            lambda: cb_start.call_count == 1 and manager.middle_click_active is True
        ), "the hold delay elapsed but push-to-talk never started"

        manager.set_middle_click_enabled(False)
        assert manager.middle_click_active is False
        assert manager.hotkey_active is False
        assert cb_stop.call_count == 1

        # A release after disabling must not fire a second stop.
        manager._on_mouse_click(0, 0, Button.middle, False)
        assert cb_stop.call_count == 1

        # Regression: that release is dropped by the `not middle_click_enabled`
        # guard, so the pressed flag has to be cleared by the disable itself.
        # Left set, `are_modifiers_pressed()` reports "still held" on every later
        # recording and the engine adds its full hold-wait (0.4-1.0 s) each time.
        assert manager.middle_pressed is False
        assert manager.are_modifiers_pressed() is False
    finally:
        manager.cleanup()
