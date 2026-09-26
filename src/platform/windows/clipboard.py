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

    def type_text(self, text: str, fast: bool = False) -> bool:
        try:
            import ctypes
            import time
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            delay = 0.001 if fast else 0.01

            # Preferred: SendInput with KEYEVENTF_UNICODE for complete Unicode and emoji fidelity
            if hasattr(user32, "SendInput"):
                try:
                    INPUT_KEYBOARD = 1
                    KEYEVENTF_KEYUP = 0x0002
                    KEYEVENTF_UNICODE = 0x0004
                    ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

                    class KEYBDINPUT(ctypes.Structure):
                        _fields_ = [
                            ("wVk", wintypes.WORD),
                            ("wScan", wintypes.WORD),
                            ("dwFlags", wintypes.DWORD),
                            ("time", wintypes.DWORD),
                            ("dwExtraInfo", ULONG_PTR),
                        ]

                    class HARDWAREINPUT(ctypes.Structure):
                        _fields_ = [
                            ("uMsg", wintypes.DWORD),
                            ("wParamL", wintypes.WORD),
                            ("wParamH", wintypes.WORD),
                        ]

                    class MOUSEINPUT(ctypes.Structure):
                        _fields_ = [
                            ("dx", wintypes.LONG),
                            ("dy", wintypes.LONG),
                            ("mouseData", wintypes.DWORD),
                            ("dwFlags", wintypes.DWORD),
                            ("time", wintypes.DWORD),
                            ("dwExtraInfo", ULONG_PTR),
                        ]

                    class _INPUT_UNION(ctypes.Union):
                        _fields_ = [
                            ("mi", MOUSEINPUT),
                            ("ki", KEYBDINPUT),
                            ("hi", HARDWAREINPUT),
                        ]

                    class INPUT(ctypes.Structure):
                        _anonymous_ = ("_union",)
                        _fields_ = [
                            ("type", wintypes.DWORD),
                            ("_union", _INPUT_UNION),
                        ]

                    # UTF-16LE 16-bit code units (natively supports surrogate pairs like emojis)
                    utf16_bytes = text.encode("utf-16-le")
                    code_units = [
                        utf16_bytes[i] | (utf16_bytes[i + 1] << 8)
                        for i in range(0, len(utf16_bytes), 2)
                    ]

                    for unit in code_units:
                        down = INPUT(type=INPUT_KEYBOARD)
                        down.ki = KEYBDINPUT(wVk=0, wScan=unit, dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=0)
                        up = INPUT(type=INPUT_KEYBOARD)
                        up.ki = KEYBDINPUT(wVk=0, wScan=unit, dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)

                        user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
                        user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))
                        if not fast and delay > 0:
                            time.sleep(delay)
                    return True
                except Exception as e:
                    logger.debug(f"SendInput Unicode typing failed, falling back to keybd_event: {e}")

            # Fallback: legacy keybd_event with VkKeyScanW
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
                if not fast and delay > 0:
                    time.sleep(delay)
            return True
        except Exception as e:
            logger.error(f"Windows typing failed: {e}")
            return self.copy_text(text)
