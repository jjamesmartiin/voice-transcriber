"""Linux audio-cue backend.

Plays the bundled ``src/sounds/*.mp3`` earcons with the first available of
``paplay`` (PipeWire/PulseAudio) or ``mpg123``, then falls back to
``sounddevice`` so a cue still plays on a bare setup. Failures are always
swallowed: a missing earcon must never interrupt dictation.
"""
from __future__ import annotations

import os
import shutil
import subprocess

from ..base import BaseAudioCuePlayer

# src/platform/linux/audio_cues.py -> src/platform/linux -> src/platform -> src
_SRC_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_SOUNDS_DIR = os.path.join(_SRC_DIR, "sounds")


class LinuxAudioCuePlayer(BaseAudioCuePlayer):
    """Play ``sounds/<name>.mp3`` through ``paplay``/``mpg123``/sounddevice."""

    platform = "linux"

    def sound_path(self, name: str) -> str:
        return os.path.join(_SOUNDS_DIR, f"{name}.mp3")

    def play_cue(self, name: str) -> None:
        sound_file = self.sound_path(name)
        for tool in ("paplay", "mpg123"):
            if shutil.which(tool):
                try:
                    subprocess.Popen(
                        [tool, sound_file], stderr=subprocess.DEVNULL
                    )
                    return
                except Exception:
                    continue
        # Cross-platform software fallback (no external binary required).
        try:
            import sounddevice as sd  # lazy: only needed when no player exists
            import soundfile as sf

            data, samplerate = sf.read(sound_file, dtype="float32")
            sd.play(data, samplerate)
        except Exception:
            pass
