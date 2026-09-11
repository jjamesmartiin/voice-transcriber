"""Native Windows clipboard + keystroke-injection backend.

Contract (locked by ``tests/test_end_to_end_crossplatform.py``):

* ``copy_text`` uses ``pyperclip``.
* ``type_text`` injects keystrokes via ``user32.keybd_event`` (no extra deps).

Both imports are lazy so this module is importable on Linux/WSL hosts where
``pyperclip`` or ``ctypes.windll`` are unavailable.
"""
from __future__ import annotations

import logging

from ..base import BaseClipboardSink

logger = logging.getLogger(__name__)

_KEYEVENTF_KEYUP = 0x0002
_SHIFT_VK = 0x10


class WindowsClipboardSink(BaseClipboardSink):
    """Windows clipboard (pyperclip) and ``keybd_event`` typing sink."""

    platform = "windows"

    def copy_text(self, text: str) -> bool:
        try:
            import pyperclip

            pyperclip.copy(text)
            return True
        except Exception as e:
            logger.error(f"Windows clipboard copy failed: {e}")
            return False

    def type_text(self, text: str) -> bool:
        try:
            import ctypes

            user32 = ctypes.windll.user32
            for char in text:
                scan = user32.VkKeyScanW(ord(char))
                if scan == -1:
                    continue
                vk = scan & 0xFF
                shift_needed = bool((scan >> 8) & 0x01)
                if shift_needed:
                    user32.keybd_event(_SHIFT_VK, 0, 0, 0)
                user32.keybd_event(vk, 0, 0, 0)
                user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)
                if shift_needed:
                    user32.keybd_event(_SHIFT_VK, 0, _KEYEVENTF_KEYUP, 0)
            return True
        except Exception as e:
            logger.error(f"Windows keybd_event typing failed: {e}")
            return False
