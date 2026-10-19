"""Windows autostart registration via HKCU Run registry key.

Enables or disables starting Voice Transcriber automatically when the user
logs into Windows.
"""
from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
DEFAULT_APP_NAME = "Voice Transcriber"


def _get_winreg():
    try:
        import winreg
        return winreg
    except ImportError:
        return None


def _open_run_key(winreg, access):
    """Open HKCU\\...\\Run for *access*, creating it if it is missing.

    The Run key exists on every normal Windows profile, but a fresh or
    locked-down one may not, and OpenKey would then fail the write with a
    FileNotFoundError instead of registering the app.
    """
    try:
        return winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, access)
    except FileNotFoundError:
        return winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, access)


def is_autostart_enabled(app_name: str = DEFAULT_APP_NAME) -> bool:
    """Return True if an autostart entry exists for this app in HKCU\\...\\Run."""
    winreg = _get_winreg()
    if winreg is None:
        return False

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, app_name)
            return bool(val)
    except (FileNotFoundError, OSError):
        return False


def set_autostart_enabled(
    enabled: bool,
    app_name: str = DEFAULT_APP_NAME,
    command: str | None = None,
) -> bool:
    """Enable or disable starting with Windows.

    Args:
        enabled: True to register, False to unregister.
        app_name: Name of the value under HKCU\\...\\Run.
        command: Command string to execute. If None, derives from sys.executable.

    Returns:
        True on success, False on error.
    """
    winreg = _get_winreg()
    if winreg is None:
        logger.debug("winreg unavailable on this platform")
        return False

    try:
        if enabled:
            if not command:
                if getattr(sys, "frozen", False):
                    command = f'"{sys.executable}"'
                else:
                    launcher = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
                    # A bare `"python.exe" ""` entry would run a nonexistent
                    # script and log an invisible startup failure.
                    command = f'"{sys.executable}"' + (f' "{launcher}"' if launcher else "")

            with _open_run_key(winreg, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, command)
            logger.info("Registered autostart command: %s", command)
            return True
        else:
            with _open_run_key(winreg, winreg.KEY_SET_VALUE) as key:
                try:
                    winreg.DeleteValue(key, app_name)
                    logger.info("Removed autostart entry for %s", app_name)
                except FileNotFoundError:
                    pass
            return True
    except OSError as e:
        logger.warning("Failed to update autostart registry key: %s", e)
        return False
