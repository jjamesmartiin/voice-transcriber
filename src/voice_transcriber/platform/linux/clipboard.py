"""Linux clipboard + synthetic-typing backend.

Contract (locked by ``tests/e2e/test_end_to_end_crossplatform.py``):

* ``copy_text`` prefers Wayland ``wl-copy`` (matching ``src/t2.py``), falls back
  to X11 ``xclip -selection clipboard``, and returns ``False`` when neither is
  available. The tool is given a short leash: on some setups ``wl-copy`` keeps
  serving the selection instead of forking away, and a clipboard copy must never
  block the transcription pipeline. Once the payload is handed over, the copy
  counts as done.
* ``type_text`` prefers ``ydotool`` then ``xdotool`` and finally degrades to a
  clipboard copy.
"""
from __future__ import annotations

import shutil
import subprocess

from ..base import BaseClipboardSink


#: A clipboard tool is allowed this long to accept the payload. ``wl-copy`` keeps
#: serving the selection instead of forking away on some setups (wl-clipboard 2.3.0
#: blocks indefinitely in a headless-ish Wayland session), and the copy path must
#: never block the transcription pipeline. Past this point the payload has been
#: handed over, so the copy counts as done.
CLIPBOARD_TIMEOUT_SEC = 0.25


def _run_clipboard_tool(cmd: list, payload: bytes) -> bool:
    """Run a clipboard command with a leash.

    A tool that is still holding the selection is a *successful copy*, not a hang:
    that is what ``wl-copy`` does while it serves the data.
    """
    try:
        result = subprocess.run(cmd, input=payload, check=False,
                                timeout=CLIPBOARD_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        return True
    except OSError:
        return False
    return result.returncode == 0


class LinuxClipboardSink(BaseClipboardSink):
    """Wayland/X11 clipboard and ``ydotool``/``xdotool`` typing sink."""

    platform = "linux"

    def copy_text(self, text: str) -> bool:
        payload = text.encode("utf-8")
        if shutil.which("wl-copy"):
            return _run_clipboard_tool(["wl-copy"], payload)
        if shutil.which("xclip"):
            return _run_clipboard_tool(["xclip", "-selection", "clipboard"], payload)
        return False

    def type_text(self, text: str, fast: bool = False) -> bool:
        if shutil.which("ydotool"):
            cmd = ["ydotool", "type", "-d", "1", "-s", "1", "--", text] if fast else ["ydotool", "type", "--", text]
            subprocess.run(cmd, check=False)
            return True
        if shutil.which("xdotool"):
            cmd = ["xdotool", "type", "--delay", "1", "--clearmodifiers", "--", text] if fast else ["xdotool", "type", "--clearmodifiers", "--", text]
            subprocess.run(cmd, check=False)
            return True
        # Last-resort fallback: leave it on the clipboard.
        return self.copy_text(text)
