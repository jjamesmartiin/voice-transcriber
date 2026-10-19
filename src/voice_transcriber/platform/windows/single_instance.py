"""Single-instance process protection for Windows via a named Win32 mutex.

Ensures that double-clicking the application shortcut when Voice Transcriber
is already active does not launch a duplicate process that would fight for the
global keyboard hooks, lock audio devices exclusively, or collide on the
control port.

The mutex handle is a pointer-sized Win32 HANDLE. It is passed back to
``ReleaseMutex``/``CloseHandle`` with declared prototypes (see :mod:`.win32`):
ctypes would otherwise marshal it as a 32-bit ``int`` and the calls would fail
on 64-bit Windows, leaking the mutex until process exit.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

MUTEX_NAME = "Local\\VoiceTranscriber_App_Mutex"
ERROR_ALREADY_EXISTS = 183
# CreateMutexW leaves the last error untouched on success, so a stale
# ERROR_ALREADY_EXISTS from an earlier unrelated call would make a *primary*
# instance believe a second one is running. Clear it first.
_ERROR_SUCCESS = 0


def _kernel32():
    """kernel32 with declared prototypes, or ``None`` when not on Win32."""
    from voice_transcriber.platform.windows.win32 import get_win32

    w = get_win32()
    return w.kernel32 if w is not None else None


def acquire_single_instance_mutex(name: str = MUTEX_NAME) -> tuple[bool, object]:
    """Attempt to acquire the named Win32 mutex.

    Returns:
        tuple (is_primary: bool, mutex_handle: object):
        - If is_primary is True, this process owns the mutex and should proceed.
        - If is_primary is False, another instance is already running.
    """
    if os.environ.get("VT_SINGLE_INSTANCE_DISABLED") == "1":
        return True, None

    kernel32 = _kernel32()
    if kernel32 is None:
        # Non-Windows host or platform without windll
        return True, None

    try:
        kernel32.SetLastError(_ERROR_SUCCESS)
        handle = kernel32.CreateMutexW(None, True, name)
        if not handle:
            logger.debug("CreateMutexW returned null handle")
            return True, None

        last_error = kernel32.GetLastError()
        if last_error == ERROR_ALREADY_EXISTS:
            logger.info("Single-instance mutex '%s' already held by another process", name)
            try:
                # We did not get ownership of the pre-existing mutex, so only
                # the handle is ours to close.
                kernel32.CloseHandle(handle)
            except Exception:
                pass
            return False, None

        logger.debug("Acquired single-instance mutex '%s' (handle=%s)", name, handle)
        return True, handle
    except Exception as e:
        logger.debug("Failed to acquire Win32 mutex (%s); allowing startup", e)
        return True, None


def release_single_instance_mutex(handle: object) -> None:
    """Release and close the named mutex handle if held."""
    if not handle:
        return

    kernel32 = _kernel32()
    if kernel32 is None:
        return

    try:
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)
        logger.debug("Released single-instance mutex handle %s", handle)
    except Exception as e:
        logger.debug("Error releasing mutex handle: %s", e)
