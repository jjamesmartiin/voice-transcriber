#!/usr/bin/env python3
"""Cross-platform mouse-mode (middle-click push-to-talk) contract.

This file lives in the shared tier, so it runs in all three CI jobs:

    Linux   -> tests/shared + tests/linux
    Windows -> tests/shared + tests/windows
    WSL     -> tests/shared + tests/wsl

It pins the invariants every backend must honour regardless of OS:

* mouse mode defaults off and ``set_middle_click_enabled`` is really overridden
  (not the no-op base implementation),
* a middle press only becomes push-to-talk after the backend's hold delay, and
  releasing it stops the recording,
* a quick tap never triggers push-to-talk,
* the ``hotkeys.set_global_middle_click_enabled`` shim reaches the live manager.

The behavioural checks drive each backend through its native injection seam and
skip cleanly when that seam is unavailable on the host. The Linux-only
suppression extras (tap-passthrough replay, left+right -> Enter, no X11 paste)
are covered in ``tests/linux/test_middle_click_hotkey.py``; the Windows host
bridge script is covered in ``tests/wsl/test_mouse_mode.py``.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import hal  # noqa: E402


CLASS_NAMES = {
    hal.LINUX: "LinuxHotkeyManager",
    hal.WINDOWS: "WindowsHotkeyManager",
    hal.WSL: "WSLHotkeyManager",
}


def _manager_class(platform: str):
    """Return the concrete backend class without instantiating it."""
    module = hal.load_backend(platform, "hotkeys")
    return getattr(module, CLASS_NAMES[platform])


class _FakeKeyEvent:
    """Minimal stand-in for an evdev InputEvent (EV_KEY)."""

    def __init__(self, code: int, value: int):
        self.type = 1  # EV_KEY
        self.code = code
        self.value = value


class _ScriptedDevice:
    """Device stand-in whose ``active_keys()`` is the state a test scripts."""

    def __init__(self):
        self.name = "scripted-device"
        self.path = "/dev/input/event-scripted"
        self.held = set()

    def active_keys(self):
        return set(self.held)

    def close(self):
        pass


class TestMouseModeApi:
    @pytest.mark.parametrize("platform", sorted(CLASS_NAMES))
    def test_backend_overrides_mouse_mode_toggle(self, platform):
        """Each backend must implement its own mouse-mode wiring."""
        fn = _manager_class(platform).set_middle_click_enabled
        assert fn.__module__ != "vt_platform.base", (
            f"{CLASS_NAMES[platform]} inherits the no-op base "
            "set_middle_click_enabled instead of overriding it"
        )

    def test_base_manager_defaults_off_and_toggles(self):
        from vt_platform.base import BaseHotkeyManager

        manager = BaseHotkeyManager()
        assert manager.middle_click_enabled is False
        manager.set_middle_click_enabled(True)
        assert manager.middle_click_enabled is True
        manager.set_middle_click_enabled(False)
        assert manager.middle_click_enabled is False


class TestGlobalToggleShim:
    def test_shim_reaches_active_manager(self, monkeypatch):
        import hotkeys

        fake = MagicMock()
        monkeypatch.setattr(hotkeys, "_current_hotkey_instance", None)
        hotkeys.register_manager(fake)
        hotkeys.set_global_middle_click_enabled(True)
        hotkeys.set_global_middle_click_enabled(False)
        assert [c.args[0] for c in fake.set_middle_click_enabled.call_args_list] == [
            True,
            False,
        ]

    def test_shim_is_safe_without_a_manager(self, monkeypatch):
        import hotkeys

        monkeypatch.setattr(hotkeys, "_current_hotkey_instance", None)
        hotkeys.set_global_middle_click_enabled(True)  # must not raise

    def test_registration_is_identity_scoped(self, monkeypatch):
        """A stale teardown must not steal the live manager's registration.

        Restarting hotkeys (or toggling them off and on) leaves the old manager's
        cleanup running after a new one has registered; clearing unconditionally
        there would silently disable every ``set_global_*`` propagation.
        """
        import hotkeys

        monkeypatch.setattr(hotkeys, "_current_hotkey_instance", None)
        first, second = MagicMock(), MagicMock()
        hotkeys.register_manager(first)
        hotkeys.register_manager(second)

        hotkeys.release_manager(first)  # the older instance, mid-teardown
        assert hotkeys._current_hotkey_instance is second

        hotkeys.release_manager(second)
        assert hotkeys._current_hotkey_instance is None


def _make_manager(platform: str):
    """Build a manager and return (manager, inject, cb_start, cb_stop).

    ``inject(pressed)`` drives a middle press/release through the backend's own
    event entry point. Skips when the backend cannot be exercised on this host.
    """
    cb_start = MagicMock()
    cb_stop = MagicMock()
    manager = hal.create_hotkey_manager(
        platform, callback_start=cb_start, callback_stop=cb_stop
    )
    manager.set_middle_click_enabled(True)

    if platform == hal.LINUX:
        if getattr(manager, "evdev", None) is None:
            manager.cleanup()
            pytest.skip("evdev not available on this host")
        code = manager.MIDDLE_MOUSE_KEYS[0]
        # The backend decides push-to-talk from each device's live key state, so
        # the injected press must move a device's state, exactly as the kernel
        # does before it makes the event readable.
        device = _ScriptedDevice()
        manager.devices = [device]

        def inject(pressed: bool):
            if pressed:
                device.held.add(code)
            else:
                device.held.discard(code)
            manager.handle_key_event(_FakeKeyEvent(code, 1 if pressed else 0))

    elif platform == hal.WINDOWS:
        mouse = getattr(manager, "_pynput_mouse", None)
        if mouse is None:
            manager.cleanup()
            pytest.skip("pynput mouse listener not available on this host")

        def inject(pressed: bool):
            manager._on_mouse_click(0, 0, mouse.Button.middle, pressed)

    else:  # pragma: no cover - WSL events arrive from the host bridge
        manager.cleanup()
        pytest.skip("WSL middle-click comes from the PowerShell host bridge")

    return manager, inject, cb_start, cb_stop


@pytest.mark.slow
class TestMiddleClickHoldContract:
    @pytest.mark.parametrize("platform", [hal.LINUX, hal.WINDOWS])
    def test_quick_tap_never_triggers(self, platform, require_tap):
        manager, inject, cb_start, cb_stop = _make_manager(platform)
        try:
            delay = manager.MIDDLE_CLICK_HOLD_DELAY
            pressed_at = time.monotonic()
            inject(True)
            time.sleep(0.02)
            inject(False)
            require_tap(manager, time.monotonic() - pressed_at)
            # Inside the hold delay, and after it: neither may start anything.
            assert cb_start.call_count == 0
            assert cb_stop.call_count == 0
            time.sleep(delay + 0.3)
            assert cb_start.call_count == 0
            assert cb_stop.call_count == 0
            assert manager.hotkey_active is False
        finally:
            manager.cleanup()

    @pytest.mark.parametrize("platform", [hal.LINUX, hal.WINDOWS])
    def test_hold_starts_and_release_stops(self, platform, wait_until):
        manager, inject, cb_start, cb_stop = _make_manager(platform)
        try:
            delay = manager.MIDDLE_CLICK_HOLD_DELAY
            inject(True)
            assert cb_start.call_count == 0  # not yet: still inside the hold delay

            assert wait_until(
                lambda: cb_start.call_count == 1 and manager.hotkey_active is True,
                delay + 5.0,
            ), "the hold delay elapsed but push-to-talk never started"

            inject(False)
            assert wait_until(
                lambda: cb_stop.call_count == 1 and manager.hotkey_active is False,
                5.0,
            ), "releasing the middle button did not stop the recording"
        finally:
            manager.cleanup()

    @pytest.mark.parametrize("platform", [hal.LINUX, hal.WINDOWS])
    def test_disabling_mouse_mode_ignores_further_events(self, platform):
        manager, inject, cb_start, cb_stop = _make_manager(platform)
        try:
            manager.set_middle_click_enabled(False)
            inject(True)
            # A *minimum* wait, unlike the other two: nothing may fire, so
            # waiting longer can only make this assertion stronger.
            time.sleep(manager.MIDDLE_CLICK_HOLD_DELAY + 0.3)
            inject(False)
            assert cb_start.call_count == 0
            assert cb_stop.call_count == 0
            assert manager.hotkey_active is False
        finally:
            manager.cleanup()
