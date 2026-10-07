#!/usr/bin/env python3
"""The Linux backend must honour the user's configured push-to-talk binds.

``keybinds`` is the single OS-neutral vocabulary; this file pins the Linux
translation of it. The chord is read from the devices' *live* key state
(``_held_keys`` / ``EVIOCGKEY``) reconciled with each device's event stream, so
the fakes below move the device state before handing over the event, exactly as
the kernel does. See ``test_hotkey_modifier_latch.py`` for the remapper-safety
half of that rule.
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
KEY_LEFTCTRL = 29
KEY_LEFTSHIFT = 42
KEY_LEFTALT = 56
KEY_SPACE = 57
KEY_RIGHTCTRL = 97
KEY_F13 = 183


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
        manager.handle_key_event(FakeEvent(code, value), self)


def make_manager(binds=None, cb_start=None, cb_stop=None):
    LinuxHotkeyManager = hal.load_backend("linux", "hotkeys").LinuxHotkeyManager
    kwargs = {} if binds is None else {"binds": binds}
    manager = LinuxHotkeyManager(
        cb_start or MagicMock(), cb_stop or MagicMock(), **kwargs
    )
    manager.virtual_keyboard = MagicMock()
    manager.uinput = MagicMock()
    return manager


# ---------------------------------------------------------------------------
# Defaults and the configured-bind trigger
# ---------------------------------------------------------------------------


def test_default_bind_is_still_alt_shift():
    cb_start = MagicMock()
    manager = make_manager(cb_start=cb_start)
    kb = FakeKeyboard()
    manager.devices = [kb]
    try:
        assert [bind.chord for bind in manager.binds] == ["alt+shift"]

        kb.deliver(manager, KEY_LEFTALT, 1)
        kb.deliver(manager, KEY_LEFTSHIFT, 1)
        assert cb_start.call_count == 1
        assert manager.hotkey_active is True
        assert manager.is_bind_pressed() is True
        # The legacy name is a thin alias, not a second implementation.
        assert manager.is_alt_shift_pressed() is True
    finally:
        manager.cleanup()


def test_f13_bind_triggers_alone_but_alt_shift_does_not():
    cb_start = MagicMock()
    manager = make_manager(binds=["f13"], cb_start=cb_start)
    kb = FakeKeyboard()
    manager.devices = [kb]
    try:
        # Alt+Shift is not a configured bind here.
        kb.deliver(manager, KEY_LEFTALT, 1)
        kb.deliver(manager, KEY_LEFTSHIFT, 1)
        assert cb_start.call_count == 0
        assert manager.hotkey_active is False
        assert manager.is_bind_pressed() is False

        # F13 alone is.
        kb.deliver(manager, KEY_F13, 1)
        assert cb_start.call_count == 1
        assert manager.hotkey_active is True
    finally:
        manager.cleanup()


def test_two_binds_each_trigger_independently():
    cb_start = MagicMock()
    manager = make_manager(binds=["alt+shift", "f13"], cb_start=cb_start)
    kb = FakeKeyboard()
    manager.devices = [kb]
    try:
        kb.deliver(manager, KEY_F13, 1)
        assert cb_start.call_count == 1
        kb.deliver(manager, KEY_F13, 0)

        kb.deliver(manager, KEY_LEFTALT, 1)
        kb.deliver(manager, KEY_LEFTSHIFT, 1)
        assert cb_start.call_count == 2
    finally:
        manager.cleanup()


# ---------------------------------------------------------------------------
# Side-specific vs bare modifiers
# ---------------------------------------------------------------------------


def test_rightctrl_shift_is_side_specific():
    cb_start = MagicMock()
    manager = make_manager(binds=["rightctrl+shift"], cb_start=cb_start)
    kb = FakeKeyboard()
    manager.devices = [kb]
    try:
        # Left Ctrl + Shift must NOT satisfy ``rightctrl+shift``.
        kb.deliver(manager, KEY_LEFTCTRL, 1)
        kb.deliver(manager, KEY_LEFTSHIFT, 1)
        assert manager.is_bind_pressed() is False
        assert cb_start.call_count == 0

        # Right Ctrl + Shift does.
        kb.deliver(manager, KEY_LEFTCTRL, 0)
        kb.deliver(manager, KEY_RIGHTCTRL, 1)
        assert manager.is_bind_pressed() is True
        assert cb_start.call_count == 1
    finally:
        manager.cleanup()


def test_bare_ctrl_shift_accepts_either_side():
    for ctrl in (KEY_LEFTCTRL, KEY_RIGHTCTRL):
        cb_start = MagicMock()
        manager = make_manager(binds=["ctrl+shift"], cb_start=cb_start)
        kb = FakeKeyboard()
        manager.devices = [kb]
        try:
            kb.deliver(manager, ctrl, 1)
            kb.deliver(manager, KEY_LEFTSHIFT, 1)
            assert manager.is_bind_pressed() is True
            assert cb_start.call_count == 1
        finally:
            manager.cleanup()


# ---------------------------------------------------------------------------
# Runtime replacement
# ---------------------------------------------------------------------------


def test_set_binds_swaps_the_live_chord_at_runtime():
    cb_start = MagicMock()
    manager = make_manager(binds=["alt+shift"], cb_start=cb_start)
    kb = FakeKeyboard()
    manager.devices = [kb]
    try:
        # The shipped chord works.
        kb.deliver(manager, KEY_LEFTALT, 1)
        kb.deliver(manager, KEY_LEFTSHIFT, 1)
        assert cb_start.call_count == 1
        kb.deliver(manager, KEY_LEFTALT, 0)
        kb.deliver(manager, KEY_LEFTSHIFT, 0)

        # Add f13 alongside it: both are now live.
        assert manager.set_binds(["alt+shift", "f13"]) is True
        assert [bind.chord for bind in manager.binds] == ["alt+shift", "f13"]
        kb.deliver(manager, KEY_F13, 1)
        assert cb_start.call_count == 2
        kb.deliver(manager, KEY_F13, 0)

        # Replace with f13 only: the old chord is dead.
        assert manager.set_binds(["f13"]) is True
        kb.deliver(manager, KEY_LEFTALT, 1)
        kb.deliver(manager, KEY_LEFTSHIFT, 1)
        assert cb_start.call_count == 2
        assert manager.hotkey_active is False

        kb.deliver(manager, KEY_F13, 1)
        assert cb_start.call_count == 3
    finally:
        manager.cleanup()


def test_set_binds_rejects_unknown_key_and_keeps_previous():
    cb_start = MagicMock()
    manager = make_manager(binds=["alt+shift"], cb_start=cb_start)
    kb = FakeKeyboard()
    manager.devices = [kb]
    try:
        assert manager.set_binds("hyper+shift") is False
        assert manager.set_binds(12345) is False  # non-iterable, still must not raise
        assert [bind.chord for bind in manager.binds] == ["alt+shift"]

        # The previous binds are still live after the rejected change.
        kb.deliver(manager, KEY_LEFTALT, 1)
        kb.deliver(manager, KEY_LEFTSHIFT, 1)
        assert cb_start.call_count == 1
    finally:
        manager.cleanup()


# ---------------------------------------------------------------------------
# Space hands-free latch with a non-default bind
# ---------------------------------------------------------------------------


def test_space_latch_still_works_for_a_non_default_bind():
    cb_start = MagicMock()
    cb_stop = MagicMock()
    manager = make_manager(binds=["f13"], cb_start=cb_start, cb_stop=cb_stop)
    kb = FakeKeyboard()
    manager.devices = [kb]
    try:
        # 1. Hold F13 -> start recording.
        kb.deliver(manager, KEY_F13, 1)
        assert manager.hotkey_active is True
        cb_start.assert_called_once()

        # 2. Tap Space while holding -> latch hands-free.
        kb.deliver(manager, KEY_SPACE, 1)
        assert manager.latch_release is True

        # 3. Release F13 -> latch holds (no stop callback).
        kb.deliver(manager, KEY_F13, 0)
        assert manager.latch_release is False
        cb_stop.assert_not_called()

        # 4. Tap F13 again to conclude the hands-free recording.
        kb.deliver(manager, KEY_F13, 1)
        assert manager.hotkey_active is True
        kb.deliver(manager, KEY_F13, 0)
        assert manager.hotkey_active is False
        cb_stop.assert_called_once()
    finally:
        manager.cleanup()
