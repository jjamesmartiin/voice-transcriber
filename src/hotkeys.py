#!/usr/bin/env python3
"""Backward-compatible global hotkey entry points.

The implementation now lives in the Hardware/OS Abstraction Layer under
``src/platform/``. This module is kept as a thin shim so existing callers keep
working unchanged::

    from hotkeys import create_global_hotkeys, is_running_in_wsl

    create_global_hotkeys(cb_start, cb_stop, cb_config)
    set_global_sound_theme("proximity")

New code should import the HAL façade directly::

    import hal
    manager = hal.create_hotkey_manager(...)
"""
from __future__ import annotations

import logging

import hal

logger = logging.getLogger(__name__)

# The most recently created hotkey manager, so ``set_global_sound_theme`` can
# reach it without threading a reference through every call site.
_current_hotkey_instance = None


def is_running_in_wsl() -> bool:
    """Return ``True`` when running inside WSL (delegates to the HAL)."""
    return hal.detect_platform() == hal.WSL


def set_global_sound_theme(theme):
    """Propagate a sound theme to the active bridge if one supports it."""
    global _current_hotkey_instance
    if _current_hotkey_instance and hasattr(_current_hotkey_instance, "set_sound_theme"):
        try:
            _current_hotkey_instance.set_sound_theme(theme)
        except Exception as e:
            logger.debug(f"set_global_sound_theme failed: {e}")


def create_global_hotkeys(callback_start, callback_stop, callback_config=None):
    """Create the platform-appropriate global hotkey manager via the HAL.

    Kept for backwards compatibility; prefer ``hal.create_hotkey_manager``.
    """
    global _current_hotkey_instance
    _current_hotkey_instance = hal.create_hotkey_manager(
        callback_start=callback_start,
        callback_stop=callback_stop,
        callback_config=callback_config,
    )
    return _current_hotkey_instance


def __getattr__(name):
    """Lazily expose the legacy backend class names.

    ``evdev``/``pynput``/Windows libraries are imported only when a caller
    actually asks for the class, keeping this module import-safe everywhere.
    """
    if name in ("WaylandGlobalHotkeys", "LinuxHotkeyManager"):
        return hal.load_backend("linux", "hotkeys").LinuxHotkeyManager
    if name in ("WSLGlobalHotkeys", "WSLHotkeyManager"):
        return hal.load_backend("wsl", "hotkeys").WSLHotkeyManager
    if name in ("WindowsGlobalHotkeys", "WindowsHotkeyManager"):
        return hal.load_backend("windows", "hotkeys").WindowsHotkeyManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "create_global_hotkeys",
    "is_running_in_wsl",
    "set_global_sound_theme",
    "WaylandGlobalHotkeys",
    "WSLGlobalHotkeys",
    "WindowsGlobalHotkeys",
]
