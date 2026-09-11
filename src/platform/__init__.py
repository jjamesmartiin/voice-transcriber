"""Hardware/OS Abstraction Layer (HAL) for Voice Transcriber.

This package is the single place that knows whether the process is running on
native Linux, inside WSL, or on native Windows. Everything else in the codebase
asks this package for the right clipboard sink, audio-cue player and hotkey
manager instead of branching on ``os.uname()`` / ``sys.platform`` itself.

The package directory is named ``platform`` as required by the convergence
plan. Because ``src/`` is placed on ``sys.path`` at runtime, loading this
package through the plain ``import platform`` name would shadow Python's
standard-library :mod:`platform` module. Consumers must therefore import the
:mod:`hal` façade (``src/hal.py``), which loads this package under the private
module name ``vt_platform``. See ``src/hal.py`` for details.
"""
from __future__ import annotations

import os
import sys

# ---------------------------------------------------------------------------
# Shadow guard
# ---------------------------------------------------------------------------
# This package directory is named ``platform`` (required by the convergence
# plan) and ``src/`` is on ``sys.path``. If any module does a plain
# ``import platform`` before the stdlib module is cached, Python would resolve
# it to *this* package and third-party libraries (numpy, torch, pynput, ...)
# would lose ``platform.uname()`` etc.
#
# When we detect we were imported as the stdlib name, we immediately replace
# ``sys.modules['platform']`` with the real stdlib module and re-execute it, so
# callers transparently get the standard library. The HAL itself is always
# loaded by the ``hal`` façade under the private name ``vt_platform``.
if __name__ == "platform":  # pragma: no cover - depends on import order
    try:
        import importlib.util as _ilu

        _stdlib_spec = _ilu.spec_from_file_location(
            "platform", os.path.join(os.path.dirname(os.__file__), "platform.py")
        )
        _stdlib_module = _ilu.module_from_spec(_stdlib_spec)
        sys.modules["platform"] = _stdlib_module
        _stdlib_spec.loader.exec_module(_stdlib_module)
        globals().update(_stdlib_module.__dict__)
    except Exception:
        # Best effort: if delegation fails, fall through to the HAL definitions.
        pass

LINUX = "linux"
WSL = "wsl"
WINDOWS = "windows"

VALID_PLATFORMS = (LINUX, WSL, WINDOWS)


def _detect_wsl() -> bool:
    """Host-level WSL detection (mirrors ``hotkeys.is_running_in_wsl``)."""
    if os.path.exists("/mnt/wslg"):
        return True
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    if os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop"):
        return True
    try:
        if "microsoft" in os.uname().release.lower():
            return True
    except AttributeError:
        # os.uname() is not available on Windows.
        pass
    return False


def detect_platform() -> str:
    """Return the active target platform: ``linux`` | ``wsl`` | ``windows``.

    ``VT_PLATFORM`` (set by tests / CI / launchers) always wins. When unset we
    auto-detect the host: native Windows by ``sys.platform``, WSL by the same
    heuristics as ``hotkeys.is_running_in_wsl``, otherwise native Linux.

    An unknown ``VT_PLATFORM`` value is a hard error: silently guessing across
    platforms is exactly the class of bug the HAL exists to eliminate.
    """
    override = os.environ.get("VT_PLATFORM", "").strip().lower()
    if override:
        if override not in VALID_PLATFORMS:
            raise ValueError(
                f"Unknown VT_PLATFORM={override!r}; expected one of {VALID_PLATFORMS}"
            )
        return override

    if sys.platform.startswith("win"):
        return WINDOWS
    if _detect_wsl():
        return WSL
    return LINUX


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------
def get_clipboard_sink(platform: str | None = None):
    """Return the :class:`~platform.base.BaseClipboardSink` for ``platform``."""
    platform = platform or detect_platform()
    if platform == LINUX:
        from .linux.clipboard import LinuxClipboardSink as _Cls
    elif platform == WINDOWS:
        from .windows.clipboard import WindowsClipboardSink as _Cls
    elif platform == WSL:
        from .wsl.clipboard import WSLClipboardSink as _Cls
    else:
        raise ValueError(f"Unknown platform: {platform!r}")
    return _Cls()


def get_audio_cue_player(platform: str | None = None):
    """Return the :class:`~platform.base.BaseAudioCuePlayer` for ``platform``."""
    platform = platform or detect_platform()
    if platform == LINUX:
        from .linux.audio_cues import LinuxAudioCuePlayer as _Cls
    elif platform == WINDOWS:
        from .windows.audio_cues import WindowsAudioCuePlayer as _Cls
    elif platform == WSL:
        from .wsl.audio_cues import WSLAudioCuePlayer as _Cls
    else:
        raise ValueError(f"Unknown platform: {platform!r}")
    return _Cls()


def create_hotkey_manager(
    platform: str | None = None,
    callback_start=None,
    callback_stop=None,
    callback_config=None,
):
    """Return the :class:`~platform.base.BaseHotkeyManager` for ``platform``.

    The concrete backend is imported lazily so that, for example, importing the
    Linux backend never requires ``pynput`` and vice versa.
    """
    platform = platform or detect_platform()
    if platform == LINUX:
        from .linux.hotkeys import LinuxHotkeyManager as _Cls
    elif platform == WINDOWS:
        from .windows.hotkeys import WindowsHotkeyManager as _Cls
    elif platform == WSL:
        from .wsl.hotkeys import WSLHotkeyManager as _Cls
    else:
        raise ValueError(f"Unknown platform: {platform!r}")
    return _Cls(
        callback_start=callback_start,
        callback_stop=callback_stop,
        callback_config=callback_config,
    )


def get_visual_notification(*, app_name: str = "Voice Transcriber", tui=None):
    """Return a platform-appropriate visual notification backend.

    Windows uses the native Tkinter/winsound overlay; Linux and WSL use the
    generic TUI-aware notification object from :mod:`notifications`.
    """
    platform = detect_platform()
    if platform == WINDOWS:
        from .windows.notifications import WindowsVisualNotification

        return WindowsVisualNotification(app_name=app_name, tui=tui)
    from notifications import VisualNotification

    return VisualNotification(app_name=app_name, tui=tui)


__all__ = [
    "LINUX",
    "WSL",
    "WINDOWS",
    "VALID_PLATFORMS",
    "detect_platform",
    "get_clipboard_sink",
    "get_audio_cue_player",
    "create_hotkey_manager",
    "get_visual_notification",
]
