#!/usr/bin/env python3
"""Tests for Middle Click hold (>= 0.25s) push-to-talk functionality."""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import hal  # noqa: E402

pytest.importorskip("evdev", reason="Linux evdev backend required")


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
    manager.set_middle_click_enabled(True)
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

        # A two-button pointer (no middle click) still qualifies for the chord
        two_button = FakeDevice("Two Button Mouse", [272, 273])
        assert manager._is_mouse_device(two_button) is True

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
    manager.set_middle_click_enabled(True)
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
    manager.set_middle_click_enabled(True)
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
    manager.set_middle_click_enabled(True)
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
    manager.set_middle_click_enabled(True)
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


class FakeForwarder:
    """Stand-in for the uinput virtual mouse owned by a grabbed device."""

    def __init__(self):
        self.events = []
        self.syns = 0

    def emit(self, event, value, syn=True):
        self.events.append((event, value, syn))

    def syn(self):
        self.syns += 1


def _chord_manager():
    LinuxHotkeyManager = hal.load_backend("linux", "hotkeys").LinuxHotkeyManager
    manager = LinuxHotkeyManager(MagicMock(), MagicMock())
    manager.set_middle_click_enabled(True)
    manager.virtual_keyboard = MagicMock()
    manager.uinput = MagicMock()
    # Marker tuple so the emit calls are easy to assert against.
    manager.uinput.KEY_ENTER = ("KEY_ENTER", 28)
    return manager


def test_linux_left_right_chord_sends_enter_and_swallows_clicks():
    """Left+right within the window -> Enter, and neither click leaks through."""
    manager = _chord_manager()
    fwd = FakeForwarder()
    try:
        manager._handle_mouse_button_event(FakeEvdevEvent(manager.BTN_LEFT, 1), fwd)
        manager._handle_mouse_button_event(FakeEvdevEvent(manager.BTN_RIGHT, 1), fwd)

        assert manager.virtual_keyboard.emit.call_args_list == [
            call(manager.uinput.KEY_ENTER, 1),
            call(manager.uinput.KEY_ENTER, 0),
        ]
        assert fwd.events == []
        assert manager._chord_swallow is True

        # Both releases are swallowed too, then the swallow state clears.
        manager._handle_mouse_button_event(FakeEvdevEvent(manager.BTN_LEFT, 0), fwd)
        manager._handle_mouse_button_event(FakeEvdevEvent(manager.BTN_RIGHT, 0), fwd)
        assert fwd.events == []
        assert manager._chord_swallow is False
    finally:
        manager.cleanup()


def test_linux_right_then_left_chord_is_symmetric():
    manager = _chord_manager()
    fwd = FakeForwarder()
    try:
        manager._handle_mouse_button_event(FakeEvdevEvent(manager.BTN_RIGHT, 1), fwd)
        manager._handle_mouse_button_event(FakeEvdevEvent(manager.BTN_LEFT, 1), fwd)
        assert manager.virtual_keyboard.emit.call_count == 2
        assert fwd.events == []
        manager._handle_mouse_button_event(FakeEvdevEvent(manager.BTN_LEFT, 0), fwd)
        manager._handle_mouse_button_event(FakeEvdevEvent(manager.BTN_RIGHT, 0), fwd)
        assert fwd.events == []
    finally:
        manager.cleanup()


def test_linux_single_click_passes_through_after_window():
    """A lone left click is buffered briefly, then replayed verbatim."""
    manager = _chord_manager()
    fwd = FakeForwarder()
    try:
        manager._handle_mouse_button_event(FakeEvdevEvent(manager.BTN_LEFT, 1), fwd)
        manager._handle_mouse_button_event(FakeEvdevEvent(manager.BTN_LEFT, 0), fwd)
        assert fwd.events == []  # still inside the decision window

        manager._expire_pending_mouse_chord(force=True)
        assert fwd.events == [
            ((manager.EV_KEY, manager.BTN_LEFT), 1, True),
            ((manager.EV_KEY, manager.BTN_LEFT), 0, True),
        ]
        assert manager.virtual_keyboard.emit.call_count == 0
    finally:
        manager.cleanup()


def test_linux_clicks_outside_window_do_not_chord():
    """Once the first window elapses, a later second button is not a chord."""
    manager = _chord_manager()
    fwd = FakeForwarder()
    try:
        manager._handle_mouse_button_event(FakeEvdevEvent(manager.BTN_LEFT, 1), fwd)
        manager._expire_pending_mouse_chord(force=True)  # left press leaks through
        manager._handle_mouse_button_event(FakeEvdevEvent(manager.BTN_LEFT, 0), fwd)
        manager._handle_mouse_button_event(FakeEvdevEvent(manager.BTN_RIGHT, 1), fwd)
        manager._expire_pending_mouse_chord(force=True)  # right press leaks through
        manager._handle_mouse_button_event(FakeEvdevEvent(manager.BTN_RIGHT, 0), fwd)

        assert manager.virtual_keyboard.emit.call_count == 0
        codes = [event[0][1] for event in fwd.events]
        assert codes == [
            manager.BTN_LEFT,
            manager.BTN_LEFT,
            manager.BTN_RIGHT,
            manager.BTN_RIGHT,
        ]
    finally:
        manager.cleanup()


def test_linux_grabbed_middle_hold_records_and_is_eaten():
    """A middle press held past the tap window becomes a recording (no paste)."""
    cb_start = MagicMock()
    cb_stop = MagicMock()
    LinuxHotkeyManager = hal.load_backend("linux", "hotkeys").LinuxHotkeyManager
    manager = LinuxHotkeyManager(cb_start, cb_stop)
    manager.set_middle_click_enabled(True)
    manager.virtual_keyboard = MagicMock()
    manager.uinput = MagicMock()
    manager.uinput.KEY_ENTER = ("KEY_ENTER", 28)
    fwd = FakeForwarder()
    try:
        manager._handle_grabbed_event(None, fwd, FakeEvdevEvent(274, 1))
        assert fwd.events == []  # buffered during the tap window
        assert manager.key_states.get(274) is True

        # Tap window elapses while still held -> promote to push-to-talk.
        manager._expire_pending_middle_click(force=True)
        assert fwd.events == []
        assert manager.middle_click_active is True
        assert manager.hotkey_active is True
        assert cb_start.call_count == 1

        # Release is swallowed and stops the recording.
        manager._handle_grabbed_event(None, fwd, FakeEvdevEvent(274, 0))
        assert fwd.events == []
        assert manager.middle_click_active is False
        assert cb_stop.call_count == 1
        assert manager.virtual_keyboard.emit.call_count == 0
    finally:
        manager.cleanup()


def test_linux_grabbed_middle_quick_tap_passes_through():
    """Press+release inside the tap window replays as a normal middle click."""
    cb_start = MagicMock()
    LinuxHotkeyManager = hal.load_backend("linux", "hotkeys").LinuxHotkeyManager
    manager = LinuxHotkeyManager(cb_start, MagicMock())
    manager.set_middle_click_enabled(True)
    manager.virtual_keyboard = MagicMock()
    manager.uinput = MagicMock()
    fwd = FakeForwarder()
    try:
        manager._handle_grabbed_event(None, fwd, FakeEvdevEvent(274, 1))
        manager._handle_grabbed_event(None, fwd, FakeEvdevEvent(274, 0))
        assert fwd.events == [
            ((manager.EV_KEY, 274), 1, True),
            ((manager.EV_KEY, 274), 0, True),
        ]
        assert cb_start.call_count == 0
        assert manager.middle_click_active is False
    finally:
        manager.cleanup()


def test_linux_grabbed_middle_click_forwarded_when_disabled():
    """If mouse mode is off, a grabbed middle click is still replayed normally."""
    manager = _chord_manager()
    fwd = FakeForwarder()
    try:
        manager.middle_click_enabled = False
        manager._handle_grabbed_event(None, fwd, FakeEvdevEvent(274, 1))
        manager._handle_grabbed_event(None, fwd, FakeEvdevEvent(274, 0))
        assert ((manager.EV_KEY, 274), 1, False) in fwd.events
        assert ((manager.EV_KEY, 274), 0, False) in fwd.events
    finally:
        manager.cleanup()


def test_linux_grabbed_motion_and_sync_are_forwarded():
    """Movement/scroll frames are replayed so the cursor keeps working."""
    manager = _chord_manager()
    fwd = FakeForwarder()
    try:
        manager._forward_event(fwd, FakeEvdevEvent(0, 5, ev_type=manager.EV_REL))
        manager._forward_event(fwd, FakeEvdevEvent(0, 0, ev_type=manager.EV_SYN))
        assert fwd.events == [((manager.EV_REL, 0), 5, False)]
        assert fwd.syns == 1
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





