"""Native Windows clipboard + keystroke-injection backend.

Contract (locked by ``tests/e2e/test_end_to_end_crossplatform.py``):

* ``copy_text`` uses ``pyperclip``.
* ``type_text`` injects keystrokes via ``user32.keybd_event`` (no extra deps).

Both imports are lazy so this module is importable on Linux/WSL hosts where
``pyperclip`` or ``ctypes.windll`` are unavailable.
"""
from __future__ import annotations

import logging
import os

from ..base import BaseClipboardSink

logger = logging.getLogger(__name__)

_KEYEVENTF_KEYUP = 0x0002
_SHIFT_VK = 0x10

# Classic text controls that drop, merge, or mis-case rapid injected input.
# Fast mode falls back to slow-mode pacing for these so text lands intact.
_DEFAULT_SLOW_CLASSES = {
    "Edit",
    "Notepad",
    "NotepadTextBox",
    "RichEditD2DPT",
    "RichEdit20W",
    "RichEdit20A",
    "RICHEDIT50W",
}


def _slow_classes():
    extra = os.environ.get("VT_TYPE_SLOW_CLASSES", "")
    return _DEFAULT_SLOW_CLASSES | {c.strip() for c in extra.split(",") if c.strip()}


def _fast_classes():
    forced = os.environ.get("VT_TYPE_FAST_CLASSES", "")
    return {c.strip() for c in forced.split(",") if c.strip()}


def _effective_delay(fast, focused_class):
    """Seconds to wait between injected characters.

    Fast mode stays instant for terminals and modern apps, but falls back to
    slow-mode pacing for classic text controls (Notepad / RichEdit / Edit) that
    garble rapid injected input. Override per window class with
    ``VT_TYPE_FAST_CLASSES`` / ``VT_TYPE_SLOW_CLASSES`` (comma-separated).
    """
    if not fast:
        return 0.01
    if focused_class and focused_class in _fast_classes():
        return 0.0
    if focused_class and focused_class in _slow_classes():
        return 0.01
    return 0.0


def _vk_key_scan(user32, ctypes):
    """``VkKeyScanW`` bound as ``int codepoint -> int``.

    ``pynput`` / ``keyboard`` set ``argtypes`` on the shared
    ``user32.VkKeyScanW`` prototype, which makes an int argument raise
    ``ctypes.ArgumentError``. Try the provided handle first (so test fakes and
    unmodified hosts work); if it rejects an int, use an independent user32
    binding instead of mutating the shared prototype.
    """
    fn = user32.VkKeyScanW
    _independent = [None]

    def _get_independent():
        if _independent[0] is None:
            try:
                f = ctypes.WinDLL("user32").VkKeyScanW
                f.argtypes = [ctypes.c_uint16]
                f.restype = ctypes.c_short
                _independent[0] = f
            except Exception:
                _independent[0] = False
        return _independent[0] or None

    def _call(cp):
        try:
            return fn(cp)
        except Exception:
            pass
        other = _get_independent()
        if other is not None:
            try:
                return other(cp)
            except Exception:
                pass
        # Shared prototype may expect a 1-char string instead of an int.
        try:
            return fn(chr(cp))
        except Exception:
            return 0xFFFF

    return _call


def _focused_window_class(user32):
    """Window class of the control that will receive typed input (best effort)."""
    try:
        import ctypes
        from ctypes import wintypes

        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND),
                ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND),
                ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT),
            ]

        gti = GUITHREADINFO()
        gti.cbSize = ctypes.sizeof(GUITHREADINFO)
        hwnd = None
        if user32.GetGUIThreadInfo(0, ctypes.byref(gti)) and gti.hwndFocus:
            hwnd = gti.hwndFocus
        if not hwnd:
            hwnd = user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        return buf.value
    except Exception:
        return ""


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
        if not text:
            return True
        text = text.replace("\r\n", "\n")
        try:
            import ctypes
            import time
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            # VkKeyScanW returns a signed SHORT and pynput/keyboard may have
            # overridden its prototype; bind it independently (see helper).
            _vk_scan = _vk_key_scan(user32, ctypes)
            # Fast by default, but drop to slow pacing for classic controls
            # (Notepad/RichEdit/Edit) that cannot keep up with rapid injection.
            per_char_delay = _effective_delay(fast, _focused_window_class(user32))

            # Real virtual-key injection (keybd_event) is fast *and* compatible
            # with classic Win32 edit controls (Notepad etc.) that drop or merge
            # rapid KEYEVENTF_UNICODE events. Use it for every character the
            # current keyboard layout can produce, and fall back to Unicode input
            # only for the rest (emoji, non-Latin, ...). This keeps fast mode
            # instant for normal dictation.
            VK_SHIFT = 0x10
            VK_RETURN = 0x0D
            VK_TAB = 0x09
            KEYEVENTF_KEYUP = 0x0002
            INPUT_KEYBOARD = 1
            KEYEVENTF_UNICODE = 0x0004
            unicode_gap = 0.003

            ulong_ptr = (
                ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
            )

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [
                    ("wVk", wintypes.WORD),
                    ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ulong_ptr),
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
                    ("dwExtraInfo", ulong_ptr),
                ]

            class _INPUT_UNION(ctypes.Union):
                _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

            class INPUT(ctypes.Structure):
                _anonymous_ = ("_union",)
                _fields_ = [("type", wintypes.DWORD), ("_union", _INPUT_UNION)]

            def _emit_vk(vk, shift=False):
                if shift:
                    user32.keybd_event(VK_SHIFT, 0, 0, 0)
                user32.keybd_event(vk, 0, 0, 0)
                user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
                if shift:
                    user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)

            def _emit_unicode_unit(unit):
                down = INPUT(type=INPUT_KEYBOARD)
                down.ki = KEYBDINPUT(wVk=0, wScan=unit, dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=0)
                up = INPUT(type=INPUT_KEYBOARD)
                up.ki = KEYBDINPUT(wVk=0, wScan=unit, dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)
                user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
                user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))

            for char in text:
                if char in ("\r", "\n"):
                    _emit_vk(VK_RETURN)
                elif char == "\t":
                    _emit_vk(VK_TAB)
                else:
                    cp = ord(char)
                    scan = _vk_scan(cp) if cp < 0x10000 else -1
                    scan &= 0xFFFF
                    if scan == 0xFFFF:
                        # Non-typable on this keyboard layout: emit UTF-16 code units.
                        encoded = char.encode("utf-16-le")
                        for i in range(0, len(encoded), 2):
                            _emit_unicode_unit(encoded[i] | (encoded[i + 1] << 8))
                            time.sleep(unicode_gap)
                    else:
                        _emit_vk(scan & 0xFF, bool((scan >> 8) & 0x01))
                if per_char_delay:
                    time.sleep(per_char_delay)
            return True
        except Exception as e:
            try:
                logger.error(f"Windows typing failed: {e}")
            except Exception:
                pass
            return self.copy_text(text)
