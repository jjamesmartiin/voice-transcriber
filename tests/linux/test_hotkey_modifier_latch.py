#!/usr/bin/env python3
"""A missed key-up must never latch the Alt+Shift push-to-talk chord.

Reported failure: on this machine the keyboard runs ``kanata`` with home-row
mods, so Alt and Shift are ordinary typing keys (hold ``S`` = Alt, hold ``F`` =
Shift), and ``kanata`` + ``input-remapper`` mean input devices appear and
disappear all day. The Linux backend used to decide the chord from its own cache
of press/release events, so a single key-up lost to a remapper grabbing a device
mid-press (or an abrupt wireless drop, or an fd being reused) latched Shift
*forever* -- and the next lone Alt press then read as Alt+Shift and started a
recording nobody asked for.

These tests drive the manager through the same seam the event loop uses and
assert the decision comes from the kernel's *current* key state, not a cache.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import hal  # noqa: E402

pytest.importorskip("evdev", reason="Linux evdev backend required")

EV_KEY = 1
KEY_LEFTSHIFT = 42
KEY_LEFTALT = 56
KEY_SPACE = 57


class FakeEvent:
    """Minimal ``evdev.InputEvent`` stand-in."""

    def __init__(self, code, value, ev_type=EV_KEY):
        self.code = code
        self.value = value
        self.type = ev_type


class FakeKeyboard:
    """evdev device stand-in whose ``active_keys()`` is the live key state."""

    def __init__(self, name="fake-keyboard"):
        self.name = name
        self.path = f"/dev/input/fake-{name}"
        self.held = set()

    def active_keys(self):
        return set(self.held)

    def deliver(self, manager, code, value):
        """Apply the state change, then hand the event to the manager.

        The kernel updates a device's key state before the event becomes
        readable, so ``active_keys()`` already reflects it by the time the app
        handles the event.
        """
        if value == 1:
            self.held.add(code)
        elif value == 0:
            self.held.discard(code)
        manager.handle_key_event(FakeEvent(code, value))


def make_manager(cb_start=None, cb_stop=None):
    LinuxHotkeyManager = hal.load_backend("linux", "hotkeys").LinuxHotkeyManager
    manager = LinuxHotkeyManager(cb_start or MagicMock(), cb_stop or MagicMock())
    manager.virtual_keyboard = MagicMock()
    manager.uinput = MagicMock()
    return manager


def test_lone_alt_after_shift_device_vanished_does_not_record():
    """Shift pressed, its device disappears, then a lone Alt press: no recording."""
    cb_start = MagicMock()
    manager = make_manager(cb_start)
    vanished = FakeKeyboard("vanished-keyboard")
    present = FakeKeyboard("present-keyboard")
    manager.devices = [vanished, present]
    try:
        vanished.deliver(manager, KEY_LEFTSHIFT, 1)  # Shift down ...
        manager.devices.remove(vanished)  # ... device gone, no key-up delivered
        present.deliver(manager, KEY_LEFTALT, 1)  # lone Alt
        assert cb_start.call_count == 0
        assert manager.hotkey_active is False
    finally:
        manager.cleanup()


def test_lone_alt_with_no_shift_anywhere_does_not_record():
    cb_start = MagicMock()
    manager = make_manager(cb_start)
    kb = FakeKeyboard()
    manager.devices = [kb]
    try:
        kb.deliver(manager, KEY_LEFTALT, 1)
        kb.deliver(manager, KEY_LEFTALT, 0)
        assert cb_start.call_count == 0
    finally:
        manager.cleanup()


def test_real_alt_shift_chord_still_starts_and_stops_recording():
    """The guard must not break the actual hotkey."""
    cb_start = MagicMock()
    cb_stop = MagicMock()
    manager = make_manager(cb_start, cb_stop)
    kb = FakeKeyboard()
    manager.devices = [kb]
    try:
        kb.deliver(manager, KEY_LEFTALT, 1)
        kb.deliver(manager, KEY_LEFTSHIFT, 1)
        assert cb_start.call_count == 1
        assert manager.hotkey_active is True

        kb.deliver(manager, KEY_LEFTALT, 0)
        kb.deliver(manager, KEY_LEFTSHIFT, 0)
        assert cb_stop.call_count == 1
        assert manager.hotkey_active is False
    finally:
        manager.cleanup()


def test_chord_spanning_two_devices_still_works():
    """Alt on one device + Shift on another is a legitimate chord."""
    cb_start = MagicMock()
    manager = make_manager(cb_start)
    left = FakeKeyboard("left-keyboard")
    right = FakeKeyboard("right-keyboard")
    manager.devices = [left, right]
    try:
        left.deliver(manager, KEY_LEFTALT, 1)
        right.deliver(manager, KEY_LEFTSHIFT, 1)
        assert cb_start.call_count == 1
    finally:
        manager.cleanup()
