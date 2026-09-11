"""Linux clipboard + synthetic-typing backend.

Contract (locked by ``tests/test_end_to_end_crossplatform.py``):

* ``copy_text`` prefers Wayland ``wl-copy`` (matching ``src/t2.py``), falls back
  to X11 ``xclip -selection clipboard``, and returns ``False`` when neither is
  available.
* ``type_text`` prefers ``ydotool`` then ``xdotool`` and finally degrades to a
  clipboard copy.
"""
from __future__ import annotations

import shutil
import subprocess

from ..base import BaseClipboardSink


class LinuxClipboardSink(BaseClipboardSink):
    """Wayland/X11 clipboard and ``ydotool``/``xdotool`` typing sink."""

    platform = "linux"

    def copy_text(self, text: str) -> bool:
        payload = text.encode("utf-8")
        if shutil.which("wl-copy"):
            result = subprocess.run(["wl-copy"], input=payload, check=False)
            return result.returncode == 0
        if shutil.which("xclip"):
            result = subprocess.run(
                ["xclip", "-selection", "clipboard"], input=payload, check=False
            )
            return result.returncode == 0
        return False

    def type_text(self, text: str) -> bool:
        if shutil.which("ydotool"):
            subprocess.run(["ydotool", "type", "--", text], check=False)
            return True
        if shutil.which("xdotool"):
            subprocess.run(
                ["xdotool", "type", "--clearmodifiers", "--", text], check=False
            )
            return True
        # Last-resort fallback: leave it on the clipboard.
        return self.copy_text(text)
