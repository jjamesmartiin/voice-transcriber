"""WSL audio-cue backend.

The Windows host helper (``wsl_win_hotkeys.ps1``) owns the actual earcons:
``C:\\Windows\\Media\\Windows Proximity Notification.wav`` is played on hotkey
down, and the completion chime is played through the bridge. This class simply
forwards cues to the active bridge when one is attached, so cue playback lives
in exactly one place (the host).
"""
from __future__ import annotations

import logging

from ..base import BaseAudioCuePlayer

logger = logging.getLogger(__name__)


class WSLAudioCuePlayer(BaseAudioCuePlayer):
    """Forward earcon requests to the Windows host bridge."""

    platform = "wsl"

    def __init__(self, bridge=None):
        # ``bridge`` is the WSLHotkeyManager (or any object exposing
        # ``play_done_sound`` / ``set_sound_theme``).
        self.bridge = bridge

    def set_bridge(self, bridge) -> None:
        self.bridge = bridge

    def play_cue(self, name: str) -> None:
        if not self.bridge:
            return
        try:
            # The PowerShell helper already plays the *start* chime on its own
            # when it observes Alt+Shift down; only the completion chime needs a
            # guest request.
            if name in ("complete", "stop") and hasattr(self.bridge, "play_done_sound"):
                self.bridge.play_done_sound()
        except Exception as e:
            logger.debug(f"WSL audio cue failed: {e}")

    def set_sound_theme(self, theme: str) -> None:
        if self.bridge and hasattr(self.bridge, "set_sound_theme"):
            try:
                self.bridge.set_sound_theme(theme)
            except Exception:
                pass
