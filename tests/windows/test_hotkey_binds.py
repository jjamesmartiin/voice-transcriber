#!/usr/bin/env python3
"""Windows push-to-talk binds: the pynput-side matcher.

The trigger is no longer a hardcoded Alt+Shift test, so these exercise the
resolution from canonical ``keybinds`` names to the pynput keys a listener
actually reports. pynput is faked (the same way ``test_middle_click_hotkey``
injects button fakes) so the suite is hermetic and does not depend on a real
keyboard or on which pynput backend is installed.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import hal  # noqa: E402
from voice_transcriber import keybinds  # noqa: E402


class FakeKeyNamespace:
    """Stand-in for pynput's ``Key`` namespace; every attribute is a sentinel."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return f"Key.{name}"


class FakeKeyCode:
    """Stand-in for pynput's ``KeyCode`` class (vk- and char-based keys)."""

    @staticmethod
    def from_vk(vk, **kwargs):
        return f"vk:{vk}"

    @staticmethod
    def from_char(char, **kwargs):
        return f"char:{char}"


def _manager(binds=None):
    """A WindowsHotkeyManager with fake pynput keys, callbacks, and cleanup."""
    WindowsHotkeyManager = hal.load_backend("windows", "hotkeys").WindowsHotkeyManager
    cb_start = MagicMock()
    cb_stop = MagicMock()
    manager = WindowsHotkeyManager(cb_start, cb_stop)
    # Swap the real pynput classes for the fakes before resolving any bind.
    manager._Key = FakeKeyNamespace()
    manager._KeyCode = FakeKeyCode
    manager._resolved_groups = None
    manager._resolve_key = None
    if binds is not None:
        assert manager.set_binds(binds) is True
    return manager, cb_start, cb_stop


def test_default_binds_are_alt_shift():
    manager, cb_start, _ = _manager()
    try:
        assert [b.chord for b in manager.binds] == ["alt+shift"]
        manager._on_press("Key.alt_r")
        manager._on_press("Key.shift_r")
        assert cb_start.call_count == 1
        assert manager.hotkey_active is True
    finally:
        manager.cleanup()


def test_bare_modifier_matches_either_side():
    """A bare ``alt``/``shift`` accepts both physical sides."""
    manager, cb_start, _ = _manager([{"keys": ["alt", "shift"]}])
    try:
        manager._on_press("Key.alt_r")
        manager._on_press("Key.shift_l")
        assert cb_start.call_count == 1
    finally:
        manager.cleanup()


def test_sided_modifier_matches_only_its_own_side():
    manager, cb_start, cb_stop = _manager([{"keys": ["leftalt", "shift"]}])
    try:
        # Wrong Alt side: not the chord.
        manager._on_press("Key.alt_r")
        manager._on_press("Key.shift_l")
        assert cb_start.call_count == 0

        manager._on_release("Key.shift_l")
        manager._on_release("Key.alt_r")

        # Correct side completes it.
        manager._on_press("Key.alt_l")
        manager._on_press("Key.shift_r")
        assert cb_start.call_count == 1
        manager._on_release("Key.shift_r")
        manager._on_release("Key.alt_l")
        assert cb_stop.call_count == 1
    finally:
        manager.cleanup()


def test_rightalt_accepts_alt_gr():
    """On Windows pynput reports the right Alt as ``Key.alt_gr``."""
    manager, cb_start, _ = _manager([{"keys": ["rightalt", "shift"]}])
    try:
        manager._on_press("Key.alt_gr")
        manager._on_press("Key.shift_l")
        assert cb_start.call_count == 1
    finally:
        manager.cleanup()


def test_multi_bind_second_chord_fires():
    manager, cb_start, cb_stop = _manager([{"keys": ["alt", "shift"]}, "f13"])
    try:
        manager._on_press("Key.alt_l")
        manager._on_press("Key.shift_r")
        assert cb_start.call_count == 1
        manager._on_release("Key.shift_r")
        manager._on_release("Key.alt_l")
        assert cb_stop.call_count == 1

        # The second bind is independent.
        manager._on_press("Key.f13")
        assert cb_start.call_count == 2
        manager._on_release("Key.f13")
        assert cb_stop.call_count == 2
    finally:
        manager.cleanup()


def test_letter_bind_matches_char_keycode():
    """A letter can arrive as ``KeyCode.from_char(...)``; ``Key.ctrl_r`` sided."""
    manager, cb_start, _ = _manager([{"keys": ["ctrl", "b"]}])
    try:
        manager._on_press("Key.ctrl_r")
        manager._on_press("char:b")
        assert cb_start.call_count == 1
    finally:
        manager.cleanup()


def test_vk_keycode_matches_non_printable_bind():
    """A key with no char (e.g. F13) arrives vk-based; the vk spelling matches."""
    manager, cb_start, _ = _manager([{"keys": ["f13"]}])
    try:
        # From VK_CODES["f13"] == (0x7C,).
        manager._on_press(FakeKeyCode.from_vk(0x7C))
        assert cb_start.call_count == 1
    finally:
        manager.cleanup()


def test_invalid_binds_are_rejected_and_old_binds_survive():
    manager, cb_start, _ = _manager([{"keys": ["alt", "shift"]}])
    try:
        assert manager.set_binds([{"keys": ["notakey"]}]) is False
        assert [b.chord for b in manager.binds] == ["alt+shift"]

        # The old chord still triggers.
        manager._on_press("Key.alt_l")
        manager._on_press("Key.shift_l")
        assert cb_start.call_count == 1
    finally:
        manager.cleanup()


def test_set_binds_returns_true_for_valid_input():
    manager, _, _ = _manager()
    try:
        assert manager.set_binds(["alt+shift", {"keys": ["f13"], "action": "dictate"}]) is True
        assert [b.chord for b in manager.binds] == ["alt+shift", "f13"]
    finally:
        manager.cleanup()


def test_invalid_binds_at_construction_fall_back_to_default():
    WindowsHotkeyManager = hal.load_backend("windows", "hotkeys").WindowsHotkeyManager
    manager = WindowsHotkeyManager(MagicMock(), MagicMock(), binds=[{"keys": ["nope"]}])
    try:
        assert [b.chord for b in manager.binds] == ["alt+shift"]
    finally:
        manager.cleanup()


def test_constructor_accepts_explicit_binds():
    WindowsHotkeyManager = hal.load_backend("windows", "hotkeys").WindowsHotkeyManager
    manager = WindowsHotkeyManager(
        MagicMock(), MagicMock(), binds=[{"keys": ["leftctrl", "shift"]}]
    )
    try:
        assert [b.chord for b in manager.binds] == ["leftctrl+shift"]
    finally:
        manager.cleanup()
