#!/usr/bin/env python3
"""Tests for Middle Click hold (>= 0.25s) push-to-talk functionality."""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import hal  # noqa: E402


class FakeEvdevEvent:
    def __init__(self, code, value, ev_type=1):  # EV_KEY = 1
        self.code = code
        self.value = value
        self.type = ev_type


class FakeDevice:
    def __init__(self, name, key_caps):
        self.name = name
        self.path = f"/dev/input/fake_{name.replace(' ', '_')}"
        self._key_caps = set(key_caps)

    def capabilities(self):
        return {1: self._key_caps}  # EV_KEY = 1


def test_linux_device_filtering():
    """Verify mouse devices with BTN_MIDDLE are detected and uinput is excluded."""
    LinuxHotkeyManager = hal.load_backend("linux", "hotkeys").LinuxHotkeyManager
    manager = LinuxHotkeyManager(MagicMock(), MagicMock())
    try:
        # Standard keyboard
        kb = FakeDevice("Standard Keyboard", [56, 100, 42, 54, 57, 28, 30])
        assert manager._is_keyboard_device(kb) is True
        assert manager._is_mouse_device(kb) is False
        assert manager._is_monitored_device(kb) is True

        # Mouse with BTN_MIDDLE (274)
        mouse = FakeDevice("Optical Mouse", [272, 273, 274])
        assert manager._is_keyboard_device(mouse) is False
        assert manager._is_mouse_device(mouse) is True
        assert manager._is_monitored_device(mouse) is True

        # Keyboard/remapper with BTN_MIDDLE (e.g. kanata / kmonad / laptop trackpoint keyboard)
        kanata = FakeDevice("kanata", [56, 100, 42, 54, 57, 28, 30, 274])
        assert manager._is_keyboard_device(kanata) is True
        assert manager._is_mouse_device(kanata) is False
        assert manager._is_monitored_device(kanata) is True

        # Device with KEY_A and BTN_MIDDLE should never be a mouse
        macro_kb = FakeDevice("Macro Pad", [30, 274])
        assert manager._is_mouse_device(macro_kb) is False

        # Virtual keyboard (python-uinput) should be excluded
        uinput_dev = FakeDevice("python-uinput", [56, 100, 42, 54, 274])
        assert manager._is_monitored_device(uinput_dev) is False
    finally:
        manager.cleanup()


def test_linux_quick_middle_click_does_not_trigger():
    """Quick middle click (<0.25s) should NOT trigger push-to-talk."""
    LinuxHotkeyManager = hal.load_backend("linux", "hotkeys").LinuxHotkeyManager
    cb_start = MagicMock()
    cb_stop = MagicMock()
    manager = LinuxHotkeyManager(cb_start, cb_stop)
    try:
        # Press middle click (BTN_MIDDLE = 274)
        manager.handle_key_event(FakeEvdevEvent(274, 1))
        time.sleep(0.05)  # Quick click: release after 50ms
        manager.handle_key_event(FakeEvdevEvent(274, 0))

        # Wait past the 0.25s threshold to ensure cancelled timer doesn't fire
        time.sleep(0.25)

        assert cb_start.call_count == 0
        assert cb_stop.call_count == 0
        assert manager.is_hotkey_pressed() is False
        assert manager.hotkey_active is False
    finally:
        manager.cleanup()


def test_linux_middle_click_hold_triggers_and_releases():
    """Middle click held for >=0.25s triggers push-to-talk, release stops it."""
    LinuxHotkeyManager = hal.load_backend("linux", "hotkeys").LinuxHotkeyManager
    cb_start = MagicMock()
    cb_stop = MagicMock()
    manager = LinuxHotkeyManager(cb_start, cb_stop)
    try:
        # Press middle click
        manager.handle_key_event(FakeEvdevEvent(274, 1))
        assert manager.are_modifiers_pressed() is True

        # Wait for hold threshold to elapse (0.25s + margin)
        time.sleep(0.3)

        assert cb_start.call_count == 1
        assert manager.hotkey_active is True
        assert manager.middle_click_active is True
        assert manager.is_hotkey_pressed() is True
        assert manager.are_modifiers_pressed() is True

        # Release middle click
        manager.handle_key_event(FakeEvdevEvent(274, 0))

        assert cb_stop.call_count == 1
        assert manager.hotkey_active is False
        assert manager.middle_click_active is False
        assert manager.is_hotkey_pressed() is False
        assert manager.are_modifiers_pressed() is False
    finally:
        manager.cleanup()


def test_linux_middle_click_space_latch():
    """Holding middle click, tapping Space latches hands-free recording."""
    LinuxHotkeyManager = hal.load_backend("linux", "hotkeys").LinuxHotkeyManager
    cb_start = MagicMock()
    cb_stop = MagicMock()
    manager = LinuxHotkeyManager(cb_start, cb_stop)
    try:
        # Press and hold middle click
        manager.handle_key_event(FakeEvdevEvent(274, 1))
        time.sleep(0.3)
        assert cb_start.call_count == 1

        # Tap Space (57)
        manager.handle_key_event(FakeEvdevEvent(57, 1))
        manager.handle_key_event(FakeEvdevEvent(57, 0))

        # Release middle click -> latched, so cb_stop must NOT be called
        manager.handle_key_event(FakeEvdevEvent(274, 0))
        assert cb_stop.call_count == 0

        # Now stop latched recording by pressing and holding middle click again
        manager.handle_key_event(FakeEvdevEvent(274, 1))
        time.sleep(0.3)
        # Release to stop
        manager.handle_key_event(FakeEvdevEvent(274, 0))
        assert cb_stop.call_count == 1
    finally:
        manager.cleanup()


def test_linux_alt_shift_still_works():
    """Alt+Shift recording works normally alongside middle click support."""
    LinuxHotkeyManager = hal.load_backend("linux", "hotkeys").LinuxHotkeyManager
    cb_start = MagicMock()
    cb_stop = MagicMock()
    manager = LinuxHotkeyManager(cb_start, cb_stop)
    try:
        # Press Alt (56) + Shift (42)
        manager.handle_key_event(FakeEvdevEvent(56, 1))
        assert cb_start.call_count == 0
        manager.handle_key_event(FakeEvdevEvent(42, 1))
        assert cb_start.call_count == 1
        assert manager.hotkey_active is True

        # Release Shift (42)
        manager.handle_key_event(FakeEvdevEvent(42, 0))
        assert cb_stop.call_count == 1
        assert manager.hotkey_active is False
    finally:
        manager.cleanup()


def test_linux_middle_click_quick_click_cancels():
    """Quick middle click (<0.25s) cancels the hold timer and does not trigger PTT."""
    LinuxHotkeyManager = hal.load_backend("linux", "hotkeys").LinuxHotkeyManager
    cb_start = MagicMock()
    cb_stop = MagicMock()
    manager = LinuxHotkeyManager(cb_start, cb_stop)
    try:
        # Press middle click (274, 1)
        manager.handle_key_event(FakeEvdevEvent(274, 1))
        time.sleep(0.05)  # Quick click
        # Release middle click (274, 0)
        manager.handle_key_event(FakeEvdevEvent(274, 0))
        time.sleep(0.25)
        assert cb_start.call_count == 0
        assert cb_stop.call_count == 0
        assert manager.hotkey_active is False
    finally:
        manager.cleanup()


def test_windows_middle_click_hold():
    """WindowsHotkeyManager triggers push-to-talk on middle click hold."""
    WindowsHotkeyManager = hal.load_backend("windows", "hotkeys").WindowsHotkeyManager
    cb_start = MagicMock()
    cb_stop = MagicMock()
    manager = WindowsHotkeyManager(cb_start, cb_stop)
    try:
        from pynput.mouse import Button

        # Quick click (<0.25s)
        manager._on_mouse_click(0, 0, Button.middle, True)
        time.sleep(0.05)
        manager._on_mouse_click(0, 0, Button.middle, False)
        time.sleep(0.25)
        assert cb_start.call_count == 0
        assert cb_stop.call_count == 0

        # Hold >=0.25s
        manager._on_mouse_click(0, 0, Button.middle, True)
        assert manager.are_modifiers_pressed() is True
        time.sleep(0.3)
        assert cb_start.call_count == 1
        assert manager.hotkey_active is True
        assert manager.middle_click_active is True

        # Release
        manager._on_mouse_click(0, 0, Button.middle, False)
        assert cb_stop.call_count == 1
        assert manager.hotkey_active is False
        assert manager.middle_click_active is False
    finally:
        manager.cleanup()


def test_middle_click_toggle_disabled():
    """When middle click mode is toggled off, middle clicks are ignored for push-to-talk."""
    import t2
    LinuxHotkeyManager = hal.load_backend("linux", "hotkeys").LinuxHotkeyManager
    cb_start = MagicMock()
    cb_stop = MagicMock()
    manager = LinuxHotkeyManager(cb_start, cb_stop)
    try:
        # Disable middle click mode
        manager.set_middle_click_enabled(False)
        assert manager.middle_click_enabled is False

        # Press middle click and hold >= 0.25s
        manager.handle_key_event(FakeEvdevEvent(274, 1))
        time.sleep(0.3)

        # Should NOT have started recording
        assert cb_start.call_count == 0
        assert manager.hotkey_active is False
        assert manager.middle_click_active is False
        assert manager.is_middle_click_pressed() is False

        # Re-enable
        manager.set_middle_click_enabled(True)
        assert manager.middle_click_enabled is True

        # Now hold >= 0.25s should trigger push-to-talk
        manager.handle_key_event(FakeEvdevEvent(274, 1))
        time.sleep(0.3)
        assert cb_start.call_count == 1
        assert manager.hotkey_active is True

        manager.handle_key_event(FakeEvdevEvent(274, 0))
        assert cb_stop.call_count == 1
    finally:
        manager.cleanup()


def test_t2_middle_click_config_sync(tmp_path, monkeypatch):
    """Test t2 config loading/saving and env override for middle_click_enabled."""
    import t2
    yaml_config = tmp_path / "config.yaml"
    import yaml
    yaml_config.write_text(yaml.dump({"middle_click_enabled": True}))
    monkeypatch.setattr(t2, 'get_config_file', lambda: yaml_config)

    t2.load_audio_config()
    assert t2.MIDDLE_CLICK_ENABLED is True

    # Test toggle setter
    t2.set_middle_click_enabled(False)
    assert t2.MIDDLE_CLICK_ENABLED is False
    t2.save_audio_config()

    saved_data = yaml.safe_load(yaml_config.read_text())
    assert saved_data["middle_click_enabled"] is False

    # Test env override
    monkeypatch.setenv("VT_MIDDLE_CLICK_ENABLED", "1")
    t2.load_audio_config()
    assert t2.MIDDLE_CLICK_ENABLED is True





