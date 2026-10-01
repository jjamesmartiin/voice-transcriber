"""macOS clipboard + synthetic-typing backend.

Contract:
* ``copy_text`` prefers native macOS ``pbcopy``, falling back to ``pyperclip``.
* ``type_text`` uses AppleScript (``osascript``) via System Events or ``pynput``,
  and degrades to a clipboard copy if typing is unavailable.
"""
from __future__ import annotations

import logging
import shutil
import subprocess

from ..base import BaseClipboardSink

logger = logging.getLogger(__name__)


def _escape_applescript_string(text: str) -> str:
    """Escape text for inclusion in an AppleScript double-quoted string literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


class MacOSClipboardSink(BaseClipboardSink):
    """macOS clipboard (pbcopy / pyperclip) and keystroke injection sink."""

    platform = "macos"

    def copy_text(self, text: str) -> bool:
        payload = text.encode("utf-8")
        if shutil.which("pbcopy"):
            try:
                result = subprocess.run(["pbcopy"], input=payload, check=False)
                if result.returncode == 0:
                    return True
            except Exception as e:
                logger.debug(f"pbcopy execution failed: {e}")
        try:
            import pyperclip

            pyperclip.copy(text)
            return True
        except Exception as e:
            logger.debug(f"pyperclip fallback failed: {e}")
            return False

    def type_text(self, text: str, fast: bool = False) -> bool:
        if not text:
            return True

        if shutil.which("osascript"):
            try:
                escaped = _escape_applescript_string(text)
                script = f'tell application "System Events" to keystroke "{escaped}"'
                res = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True,
                    check=False,
                )
                if res.returncode == 0:
                    return True
            except Exception as e:
                logger.debug(f"osascript typing failed: {e}")

        # Fallback to pynput.keyboard.Controller
        try:
            from pynput.keyboard import Controller

            keyboard = Controller()
            keyboard.type(text)
            return True
        except Exception as e:
            logger.debug(f"pynput keyboard controller typing failed: {e}")

        # Last-resort fallback: leave it on the clipboard
        return self.copy_text(text)
