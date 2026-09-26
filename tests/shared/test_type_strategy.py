#!/usr/bin/env python3
"""Windows auto-type strategy: fast by default, slow for classic controls.

The pure decision helper is tested on every platform; the window-class lookup
uses a fake ``user32`` so no Windows session is required.
"""
import sys
import types
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import hal  # noqa: E402

winclip = hal.load_backend("windows", "clipboard")


class TestEffectiveDelay:
    def test_slow_mode_is_always_paced(self, monkeypatch):
        monkeypatch.delenv("VT_TYPE_FAST_CLASSES", raising=False)
        monkeypatch.delenv("VT_TYPE_SLOW_CLASSES", raising=False)
        assert winclip._effective_delay(False, "CASCADIA_HOSTING_WINDOW_CLASS") == 0.01

    @pytest.mark.parametrize("cls", ["Notepad", "RichEditD2DPT", "Edit"])
    def test_fast_falls_back_for_classic_controls(self, monkeypatch, cls):
        monkeypatch.delenv("VT_TYPE_FAST_CLASSES", raising=False)
        monkeypatch.delenv("VT_TYPE_SLOW_CLASSES", raising=False)
        assert winclip._effective_delay(True, cls) == 0.01

    @pytest.mark.parametrize("cls", ["CASCADIA_HOSTING_WINDOW_CLASS", "", "Chrome_WidgetWin_1"])
    def test_fast_stays_instant_elsewhere(self, monkeypatch, cls):
        monkeypatch.delenv("VT_TYPE_FAST_CLASSES", raising=False)
        monkeypatch.delenv("VT_TYPE_SLOW_CLASSES", raising=False)
        assert winclip._effective_delay(True, cls) == 0.0

    def test_fast_class_override_wins(self, monkeypatch):
        monkeypatch.setenv("VT_TYPE_FAST_CLASSES", "Notepad")
        assert winclip._effective_delay(True, "Notepad") == 0.0

    def test_extra_slow_class(self, monkeypatch):
        monkeypatch.delenv("VT_TYPE_FAST_CLASSES", raising=False)
        monkeypatch.setenv("VT_TYPE_SLOW_CLASSES", "Chrome_WidgetWin_1")
        assert winclip._effective_delay(True, "Chrome_WidgetWin_1") == 0.01


class TestVkKeyScanBinding:
    def test_falls_back_to_string_arg_when_int_rejected(self):
        import ctypes

        def vk(arg):
            if not isinstance(arg, str):
                raise TypeError("wrong type")
            return 0x0141 if arg.upper() == "A" else 0x41

        class FakeUser32:
            VkKeyScanW = staticmethod(vk)

        class FakeCtypes:
            c_uint16 = ctypes.c_uint16
            c_short = ctypes.c_short

        scan = winclip._vk_key_scan(FakeUser32(), FakeCtypes())
        assert scan(ord("A")) == 0x0141
        assert scan(ord("b")) == 0x41

    @pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows only")
    def test_ignores_shared_wchar_override(self):
        import ctypes

        user32 = ctypes.windll.user32
        # keyboard/pynput install this prototype; the helper must not be broken
        # by it (a plain int argument used to raise ctypes.ArgumentError).
        user32.VkKeyScanW.argtypes = [ctypes.c_wchar]
        scan = winclip._vk_key_scan(user32, ctypes)
        upper = scan(ord("A")) & 0xFFFF
        assert upper & 0xFF == 0x41
        assert (upper >> 8) & 0x01 == 1


class TestFocusedWindowClass:
    def test_falls_back_to_foreground_window(self):
        class FakeUser32:
            def GetGUIThreadInfo(self, tid, ptr):
                return 0  # nothing focused -> use foreground window

            def GetForegroundWindow(self):
                return 4242

            def GetClassNameW(self, hwnd, buf, n):
                buf.value = "CASCADIA_HOSTING_WINDOW_CLASS"
                return len(buf.value)

        assert winclip._focused_window_class(FakeUser32()) == "CASCADIA_HOSTING_WINDOW_CLASS"

    def test_returns_empty_on_error(self):
        class Boom:
            def GetGUIThreadInfo(self, *a):
                raise OSError("nope")

        assert winclip._focused_window_class(Boom()) == ""
