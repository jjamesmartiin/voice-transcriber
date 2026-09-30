"""macOS audio-cue backend.

Plays the bundled ``src/sounds/*.mp3`` earcons using macOS native ``/usr/bin/afplay``
if available, falling back to ``sounddevice``. Failures are always swallowed so a
missing earcon never interrupts dictation.
"""
from __future__ import annotations

import os
import shutil
import subprocess

from ..base import BaseAudioCuePlayer

# src/platform/macos/audio_cues.py -> src/platform/macos -> src/platform -> src
_SRC_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_SOUNDS_DIR = os.path.join(_SRC_DIR, "sounds")


class MacOSAudioCuePlayer(BaseAudioCuePlayer):
    """Play ``sounds/<name>.mp3`` through ``afplay`` or ``sounddevice``."""

    platform = "macos"

    def sound_path(self, name: str) -> str:
        return os.path.join(_SOUNDS_DIR, f"{name}.mp3")

    def play_cue(self, name: str) -> None:
        sound_file = self.sound_path(name)
        afplay = shutil.which("afplay") or ("/usr/bin/afplay" if os.path.exists("/usr/bin/afplay") else None)
        if afplay:
            try:
                subprocess.Popen([afplay, sound_file], stderr=subprocess.DEVNULL)
                return
            except Exception:
                pass
        # Cross-platform software fallback (no external binary required).
        try:
            import sounddevice as sd
            import soundfile as sf

            data, samplerate = sf.read(sound_file, dtype="float32")
            sd.play(data, samplerate)
        except Exception:
            pass
