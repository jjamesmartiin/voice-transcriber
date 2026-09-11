"""Native Windows audio-cue backend (``winsound`` + ``C:\\Windows\\Media``).

``winsound`` system aliases are the default. ``set_sound_theme`` maps the
application's theme names onto native ``C:\\Windows\\Media\\*.wav`` chimes,
mirroring the WSL PowerShell helper's ``SetSoundTheme``.
"""
from __future__ import annotations

import logging
import os

from ..base import BaseAudioCuePlayer

logger = logging.getLogger(__name__)

# System sound aliases used by ``notifications_windows.py`` (contract-locked).
_CUE_ALIASES = {
    "start": "SystemExclamation",
    "stop": "SystemAsterisk",
    "complete": "SystemExit",
}

# Theme -> (start wav, complete wav) using stock Windows media files.
_MEDIA_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Media")
_THEME_FILES = {
    "proximity": (
        os.path.join(_MEDIA_DIR, "Windows Proximity Notification.wav"),
        os.path.join(_MEDIA_DIR, "Windows Proximity Notification.wav"),
    ),
    "speech": (
        os.path.join(_MEDIA_DIR, "Speech On.wav"),
        os.path.join(_MEDIA_DIR, "Speech Off.wav"),
    ),
    "notify": (
        os.path.join(_MEDIA_DIR, "Windows Notify.wav"),
        os.path.join(_MEDIA_DIR, "Windows Notify.wav"),
    ),
    "navigation": (
        os.path.join(_MEDIA_DIR, "Windows Navigation Start.wav"),
        os.path.join(_MEDIA_DIR, "Windows Navigation Start.wav"),
    ),
    "classic": (
        os.path.join(_MEDIA_DIR, "chimes.wav"),
        os.path.join(_MEDIA_DIR, "tada.wav"),
    ),
}
_SILENT_THEMES = {"silent", "muted", "none"}


class WindowsAudioCuePlayer(BaseAudioCuePlayer):
    """Play Windows system sounds / media chimes for start/stop/complete."""

    platform = "windows"

    def __init__(self, theme: str = "proximity") -> None:
        self.theme = theme

    def set_sound_theme(self, theme: str) -> None:
        self.theme = (theme or "proximity").strip()

    def _media_file(self, name: str) -> str | None:
        theme = (self.theme or "").lower()
        if theme in _SILENT_THEMES:
            return None
        if theme in _THEME_FILES:
            start_path, done_path = _THEME_FILES[theme]
            path = start_path if name == "start" else done_path
            if os.path.exists(path):
                return path
        # Custom absolute .wav path support.
        if os.path.isabs(self.theme) and os.path.exists(self.theme):
            return self.theme
        return None

    def play_cue(self, name: str) -> None:
        try:
            import winsound
        except ImportError:
            return

        try:
            path = self._media_file(name)
            if path:
                winsound.PlaySound(path, winsound.SND_ASYNC | winsound.SND_FILENAME)
                return
            # Fall back to a system alias.
            alias = _CUE_ALIASES.get(name, "SystemAsterisk")
            winsound.PlaySound(alias, winsound.SND_ASYNC)
        except Exception as e:
            logger.debug(f"Windows audio cue failed: {e}")
