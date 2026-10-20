#!/usr/bin/env python3
"""Hermetic tests for Windows desktop UX features:
- Single-instance Win32 named mutex
- Autostart HKCU registry management
- Focus-safe floating overlay pill (WS_EX_NOACTIVATE)
- System tray integration and balloon notifications
- Combined WindowsVisualNotification facade
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import pytest


class TestSingleInstanceMutex:
    def test_primary_instance_acquires_mutex(self, monkeypatch):
        import ctypes
        from voice_transcriber.platform.windows import single_instance

        fake_kernel32 = MagicMock()
        fake_kernel32.CreateMutexW.return_value = 12345
        fake_kernel32.GetLastError.return_value = 0

        monkeypatch.setattr(ctypes, "windll", type("W", (), {"kernel32": fake_kernel32})(), raising=False)
        monkeypatch.delenv("VT_SINGLE_INSTANCE_DISABLED", raising=False)

        is_primary, handle = single_instance.acquire_single_instance_mutex("TestMutex")
        assert is_primary is True
        assert handle == 12345
        fake_kernel32.CreateMutexW.assert_called_once()

    def test_secondary_instance_detects_existing_mutex(self, monkeypatch):
        import ctypes
        from voice_transcriber.platform.windows import single_instance

        fake_kernel32 = MagicMock()
        fake_kernel32.CreateMutexW.return_value = 12345
        fake_kernel32.GetLastError.return_value = single_instance.ERROR_ALREADY_EXISTS

        monkeypatch.setattr(ctypes, "windll", type("W", (), {"kernel32": fake_kernel32})(), raising=False)
        monkeypatch.delenv("VT_SINGLE_INSTANCE_DISABLED", raising=False)

        is_primary, handle = single_instance.acquire_single_instance_mutex("TestMutex")
        assert is_primary is False
        assert handle is None
        fake_kernel32.CloseHandle.assert_called_once_with(12345)

    def test_release_mutex_closes_handle(self, monkeypatch):
        import ctypes
        from voice_transcriber.platform.windows import single_instance

        fake_kernel32 = MagicMock()
        monkeypatch.setattr(ctypes, "windll", type("W", (), {"kernel32": fake_kernel32})(), raising=False)

        single_instance.release_single_instance_mutex(12345)
        fake_kernel32.ReleaseMutex.assert_called_once_with(12345)
        fake_kernel32.CloseHandle.assert_called_once_with(12345)

    def test_disabled_env_var_returns_true(self, monkeypatch):
        from voice_transcriber.platform.windows import single_instance

        monkeypatch.setenv("VT_SINGLE_INSTANCE_DISABLED", "1")
        is_primary, handle = single_instance.acquire_single_instance_mutex("TestMutex")
        assert is_primary is True
        assert handle is None


class TestAutostartRegistry:
    def test_is_autostart_enabled_when_key_exists(self, monkeypatch):
        from voice_transcriber.platform.windows import autostart

        fake_winreg = MagicMock()
        fake_key = MagicMock()
        fake_winreg.OpenKey.return_value.__enter__.return_value = fake_key
        fake_winreg.QueryValueEx.return_value = ('"C:\\path\\app.exe"', 1)
        fake_winreg.HKEY_CURRENT_USER = 0x80000001
        fake_winreg.KEY_READ = 0x20019

        monkeypatch.setattr(autostart, "_get_winreg", lambda: fake_winreg)
        assert autostart.is_autostart_enabled("TestApp") is True

    def test_is_autostart_enabled_when_not_found(self, monkeypatch):
        from voice_transcriber.platform.windows import autostart

        fake_winreg = MagicMock()
        fake_key = MagicMock()
        fake_winreg.OpenKey.return_value.__enter__.return_value = fake_key
        fake_winreg.QueryValueEx.side_effect = FileNotFoundError
        fake_winreg.HKEY_CURRENT_USER = 0x80000001
        fake_winreg.KEY_READ = 0x20019

        monkeypatch.setattr(autostart, "_get_winreg", lambda: fake_winreg)
        assert autostart.is_autostart_enabled("TestApp") is False

    def test_set_autostart_enabled_writes_value(self, monkeypatch):
        from voice_transcriber.platform.windows import autostart

        fake_winreg = MagicMock()
        fake_key = MagicMock()
        fake_winreg.OpenKey.return_value.__enter__.return_value = fake_key
        fake_winreg.HKEY_CURRENT_USER = 0x80000001
        fake_winreg.KEY_SET_VALUE = 0x0002
        fake_winreg.REG_SZ = 1

        monkeypatch.setattr(autostart, "_get_winreg", lambda: fake_winreg)
        res = autostart.set_autostart_enabled(True, "TestApp", command='"C:\\app.exe"')
        assert res is True
        fake_winreg.SetValueEx.assert_called_once_with(
            fake_key, "TestApp", 0, 1, '"C:\\app.exe"'
        )

    def test_set_autostart_disabled_deletes_value(self, monkeypatch):
        from voice_transcriber.platform.windows import autostart

        fake_winreg = MagicMock()
        fake_key = MagicMock()
        fake_winreg.OpenKey.return_value.__enter__.return_value = fake_key
        fake_winreg.HKEY_CURRENT_USER = 0x80000001
        fake_winreg.KEY_SET_VALUE = 0x0002

        monkeypatch.setattr(autostart, "_get_winreg", lambda: fake_winreg)
        res = autostart.set_autostart_enabled(False, "TestApp")
        assert res is True
        fake_winreg.DeleteValue.assert_called_once_with(fake_key, "TestApp")


class TestFocusSafeOverlay:
    def test_overlay_styles_include_noactivate_and_layered(self):
        from voice_transcriber.platform.windows import overlay

        # Crucial invariants:
        # WS_EX_NOACTIVATE ensures input focus is never stolen from active window
        assert overlay.WS_EX_NOACTIVATE == 0x08000000
        assert overlay.WS_EX_TOOLWINDOW == 0x00000080
        assert overlay.WS_EX_TOPMOST == 0x00000008
        assert overlay.WS_EX_LAYERED == 0x00080000
        assert overlay.SW_SHOWNOACTIVATE == 4

    def test_overlay_methods_update_state_without_raising(self, monkeypatch):
        from voice_transcriber.platform.windows.overlay import WindowsOverlayWindow

        # Patch _start_thread to not run real win32 message pump
        monkeypatch.setattr(WindowsOverlayWindow, "_start_thread", lambda self: None)

        ov = WindowsOverlayWindow("Test")
        ov.hwnd = 9999

        import ctypes
        fake_user32 = MagicMock()
        monkeypatch.setattr(ctypes, "windll", type("W", (), {"user32": fake_user32})(), raising=False)

        ov.show_recording()
        assert ov.current_text == "Recording..."
        fake_user32.SetWindowPos.assert_called()
        fake_user32.ShowWindow.assert_called_with(9999, 4)  # SW_SHOWNOACTIVATE
        fake_user32.InvalidateRect.assert_called_with(9999, None, True)

        ov.show_processing("Transcribing...")
        assert ov.current_text == "Transcribing..."

        ov.show_completed("Hello World")
        assert "Hello World" in ov.current_text

        ov.hide()
        fake_user32.ShowWindow.assert_called_with(9999, 0)  # SW_HIDE

        ov.cleanup()


class TestSystemTray:
    def test_system_tray_state_updates(self, monkeypatch):
        from voice_transcriber.platform.windows.tray import WindowsSystemTray

        monkeypatch.setattr(WindowsSystemTray, "_start_thread", lambda self: None)
        tray = WindowsSystemTray("Test Tray")
        tray.hwnd = 8888
        tray._nid = MagicMock()
        tray._icons = {"READY": 1, "RECORDING": 2, "PROCESSING": 3}

        import ctypes
        fake_shell32 = MagicMock()
        fake_user32 = MagicMock()
        monkeypatch.setattr(
            ctypes,
            "windll",
            type("W", (), {"shell32": fake_shell32, "user32": fake_user32})(),
            raising=False,
        )

        tray.set_state("RECORDING")
        assert tray.current_state == "RECORDING"
        fake_shell32.Shell_NotifyIconW.assert_called()

        tray.show_balloon("Title", "Message")
        assert tray._nid.szInfoTitle == "Title"
        assert tray._nid.szInfo == "Message"

        tray.cleanup()


class TestWindowsVisualNotificationIntegration:
    def test_visual_notification_coordinates_overlay_and_tray(self, monkeypatch):
        from voice_transcriber.platform.windows.notifications import WindowsVisualNotification

        mock_overlay = MagicMock()
        mock_tray = MagicMock()

        monkeypatch.setattr(
            "voice_transcriber.platform.windows.notifications.WindowsOverlayWindow",
            lambda *args, **kwargs: mock_overlay,
        )
        monkeypatch.setattr(
            "voice_transcriber.platform.windows.notifications.WindowsSystemTray",
            lambda *args, **kwargs: mock_tray,
        )

        notifier = WindowsVisualNotification(app_name="TestApp")
        assert notifier.overlay is mock_overlay
        assert notifier.tray is mock_tray

        notifier.show_recording()
        mock_overlay.show_recording.assert_called_once()
        mock_tray.set_state.assert_called_with("RECORDING")

        notifier.show_processing("Converting audio")
        mock_overlay.show_processing.assert_called_with("Converting audio")
        mock_tray.set_state.assert_called_with("PROCESSING")

        notifier.show_completed("Test text")
        mock_overlay.show_completed.assert_called_with("Test text")
        mock_tray.set_state.assert_called_with("READY")

        notifier.show_warning("Warning Title", "Mic Muted")
        mock_tray.show_balloon.assert_called_with("Warning Title", "Mic Muted", is_error=False)

        notifier.show_error("Crash Error")
        mock_tray.show_balloon.assert_called_with(
            "Voice Transcriber Error", "Crash Error", is_error=True
        )

        notifier.cleanup()
        mock_overlay.cleanup.assert_called_once()
        mock_tray.cleanup.assert_called_once()


# ---------------------------------------------------------------------------
# Audit regressions (2026-10-19)
#
# Every test below pins a defect that was actually present: truncated 64-bit
# HANDLEs, GDI objects leaked on the paint path, a stale auto-hide timer hiding
# a newer pill, a tray "Quit" that bypassed engine shutdown, a Microphones menu
# calling t2 functions that do not exist, and a shutdown race in
# WindowsVisualNotification.
# ---------------------------------------------------------------------------

import threading  # noqa: E402
import types  # noqa: E402


class TestWin32PointerCorrectness:
    """ctypes defaults to 32-bit ints for return values and arguments."""

    def _bundle(self, monkeypatch):
        import ctypes

        from voice_transcriber.platform.windows import win32

        monkeypatch.setattr(ctypes, "windll", MagicMock(), raising=False)
        win32._cache.clear()
        return win32, win32.get_win32()

    def test_handle_returning_functions_are_declared_pointer_typed(self, monkeypatch):
        import ctypes
        from ctypes import wintypes

        _, w = self._bundle(monkeypatch)
        assert w is not None
        # Every HFONT/HBRUSH/HICON/HWND/HANDLE-returning call used by the
        # overlay, the tray and the single-instance mutex.
        assert w.user32.CreateWindowExW.restype is wintypes.HWND
        assert w.user32.BeginPaint.restype is wintypes.HDC
        assert w.user32.CreateIconIndirect.restype is wintypes.HICON
        assert w.gdi32.CreateSolidBrush.restype is wintypes.HBRUSH
        assert w.gdi32.CreateFontW.restype is wintypes.HFONT
        assert w.gdi32.CreatePen.restype is wintypes.HPEN
        assert w.gdi32.CreateRoundRectRgn.restype is wintypes.HRGN
        assert w.gdi32.SelectObject.restype is wintypes.HGDIOBJ
        assert w.kernel32.GetModuleHandleW.restype is wintypes.HMODULE
        assert w.kernel32.CreateMutexW.restype is wintypes.HANDLE
        # GetMessageW returns -1 on error; a BOOL restype makes -1 truthy and
        # turns a broken pump into a busy loop.
        assert w.user32.GetMessageW.restype is ctypes.c_int

    def test_handle_arguments_are_declared_pointer_typed(self, monkeypatch):
        from ctypes import wintypes

        _, w = self._bundle(monkeypatch)
        assert w.kernel32.CloseHandle.argtypes == [wintypes.HANDLE]
        assert w.kernel32.ReleaseMutex.argtypes == [wintypes.HANDLE]
        assert w.gdi32.DeleteObject.argtypes == [wintypes.HGDIOBJ]
        assert w.user32.PostMessageW.argtypes[:2] == [wintypes.HWND, wintypes.UINT]
        assert w.shell32.Shell_NotifyIconW.argtypes[0] is wintypes.DWORD

    def test_message_and_result_types_are_pointer_sized(self):
        import ctypes
        from ctypes import wintypes

        from voice_transcriber.platform.windows import win32

        # LRESULT is LONG_PTR and WPARAM/LPARAM are UINT_PTR/LONG_PTR; a 32-bit
        # c_long in a window procedure truncates pointer-sized payloads.
        assert ctypes.sizeof(win32.LRESULT) == ctypes.sizeof(ctypes.c_void_p)
        assert ctypes.sizeof(wintypes.WPARAM) == ctypes.sizeof(ctypes.c_void_p)
        assert ctypes.sizeof(wintypes.LPARAM) == ctypes.sizeof(ctypes.c_void_p)
        assert win32.WNDPROC._restype_ is win32.LRESULT

    def test_icon_resource_id_is_not_marshalled_as_a_string(self, monkeypatch):
        """LoadIconW(NULL, 32512) is MAKEINTRESOURCE, not a wchar_t* ."""
        _, w = self._bundle(monkeypatch)
        assert w.user32.LoadIconW.argtypes[1] is not None
        # Passing the integer must not raise ctypes.ArgumentError.
        w.user32.LoadIconW(None, 32512)


class TestSingleInstanceMutexCorrectness:
    def test_last_error_is_cleared_before_create_mutex(self, monkeypatch):
        import ctypes

        from voice_transcriber.platform.windows import single_instance

        calls = []
        fake_kernel32 = MagicMock()
        fake_kernel32.SetLastError.side_effect = lambda value: calls.append(("set", value))
        fake_kernel32.CreateMutexW.side_effect = lambda *a: (calls.append(("create",)), 42)[1]
        fake_kernel32.GetLastError.return_value = 0
        monkeypatch.setattr(
            ctypes, "windll", type("W", (), {"kernel32": fake_kernel32})(), raising=False
        )
        monkeypatch.delenv("VT_SINGLE_INSTANCE_DISABLED", raising=False)

        is_primary, handle = single_instance.acquire_single_instance_mutex("M")
        assert (is_primary, handle) == (True, 42)
        # A stale ERROR_ALREADY_EXISTS from an unrelated earlier call would
        # otherwise make the first instance exit believing one is running.
        assert calls.index(("set", 0)) < calls.index(("create",))

    def test_close_handle_has_the_handle_prototype(self, monkeypatch):
        import ctypes
        from ctypes import wintypes

        from voice_transcriber.platform.windows import single_instance

        fake_kernel32 = MagicMock()
        fake_kernel32.CreateMutexW.return_value = 0x7FF612345678
        fake_kernel32.GetLastError.return_value = single_instance.ERROR_ALREADY_EXISTS
        monkeypatch.setattr(
            ctypes, "windll", type("W", (), {"kernel32": fake_kernel32})(), raising=False
        )

        is_primary, _ = single_instance.acquire_single_instance_mutex("M")
        assert is_primary is False
        assert fake_kernel32.CloseHandle.argtypes == [wintypes.HANDLE]


class TestOverlayPaintAndTeardown:
    def _overlay(self, monkeypatch, windll=None):
        from voice_transcriber.platform.windows.overlay import WindowsOverlayWindow

        monkeypatch.setattr(WindowsOverlayWindow, "_start_thread", lambda self: None)
        ov = WindowsOverlayWindow("Test")
        if windll is not None:
            import ctypes

            monkeypatch.setattr(ctypes, "windll", windll, raising=False)
        return ov

    def test_paint_creates_and_deletes_the_same_number_of_gdi_objects(self, monkeypatch):
        """Every GDI object made for one WM_PAINT is freed before it returns."""
        fake_user32 = MagicMock()
        fake_gdi32 = MagicMock()
        # Distinct, truthy handles so the "was it created?" guards all fire.
        handles = [101, 102, 103, 104]  # bg brush, dot brush, pen, font
        fake_gdi32.CreateSolidBrush.side_effect = lambda *a: handles.pop(0)
        fake_gdi32.CreatePen.side_effect = lambda *a: handles.pop(0)
        fake_gdi32.CreateFontW.side_effect = lambda *a: handles.pop(0)
        fake_gdi32.SelectObject.return_value = 9000
        fake_user32.BeginPaint.return_value = 5555

        ov = self._overlay(
            monkeypatch,
            type("W", (), {"user32": fake_user32, "gdi32": fake_gdi32})(),
        )
        ov.current_text = "Recording..."
        ov._paint(777, fake_user32, fake_gdi32, 240, 46)

        deleted = sorted(c.args[0] for c in fake_gdi32.DeleteObject.call_args_list)
        assert deleted == [101, 102, 103, 104]
        # select background, dot brush, dot pen, then restore all three
        assert fake_gdi32.SelectObject.call_count == 6
        fake_user32.EndPaint.assert_called_once()

    def test_paint_still_frees_and_endpaints_after_a_drawing_failure(self, monkeypatch):
        fake_user32 = MagicMock()
        fake_gdi32 = MagicMock()
        handles = [101, 102, 103]  # bg brush, dot brush, font
        fake_gdi32.CreateSolidBrush.side_effect = lambda *a: handles.pop(0)
        fake_gdi32.CreatePen.return_value = 110
        fake_gdi32.CreateFontW.side_effect = lambda *a: handles.pop(0)
        fake_gdi32.SelectObject.return_value = 9000
        fake_user32.BeginPaint.return_value = 5555
        fake_user32.DrawTextW.side_effect = OSError("gdi blew up")

        ov = self._overlay(
            monkeypatch,
            type("W", (), {"user32": fake_user32, "gdi32": fake_gdi32})(),
        )
        ov._paint(777, fake_user32, fake_gdi32, 240, 46)  # must not raise

        deleted = [c.args[0] for c in fake_gdi32.DeleteObject.call_args_list]
        assert sorted(deleted) == [101, 102, 103, 110]  # nothing leaked by the failure
        fake_user32.EndPaint.assert_called_once()

    def test_cleanup_asks_the_ui_thread_to_destroy_the_window(self, monkeypatch):
        """Posting WM_DESTROY only quit the pump: the HWND was never destroyed."""
        from voice_transcriber.platform.windows import overlay

        fake_user32 = MagicMock()
        ov = self._overlay(
            monkeypatch, type("W", (), {"user32": fake_user32})(),
        )
        ov.hwnd = 9999
        ov.cleanup()

        fake_user32.PostMessageW.assert_called_once_with(9999, overlay.WM_APP_CLOSE, 0, 0)
        assert ov.hwnd is None

    def test_cleanup_is_safe_without_a_window(self, monkeypatch):
        ov = self._overlay(monkeypatch, MagicMock())
        ov.hwnd = None
        ov.cleanup()  # must not raise, must not block
        assert ov.hwnd is None

    def test_stale_auto_hide_timer_cannot_hide_a_newer_state(self, monkeypatch):
        import voice_transcriber.platform.windows.overlay as overlay

        timers = []

        class FakeTimer:
            def __init__(self, interval, function, args=()):
                self.function = function
                self.args = args
                self.cancelled = False
                timers.append(self)

            def start(self):
                pass

            def cancel(self):
                self.cancelled = True

            def is_alive(self):
                return not self.cancelled

        monkeypatch.setattr(overlay.threading, "Timer", FakeTimer)
        ov = self._overlay(monkeypatch, MagicMock())
        ov.hwnd = 0  # hide() has no Win32 work to do
        ov.hide = MagicMock()

        ov.show_completed("first")       # schedules timer #1
        ov.show_error("boom")            # cancels #1, schedules timer #2
        timers[0].function(*timers[0].args)  # late fire of the stale timer
        ov.hide.assert_not_called()
        timers[1].function(*timers[1].args)
        ov.hide.assert_called_once()

    def test_geometry_centres_on_the_given_monitor(self):
        from voice_transcriber.platform.windows.overlay import plan_geometry

        # Second monitor to the right of a 1920px primary.
        x, y = plan_geometry((1920, 0, 3840, 1080), 240, 46)
        assert x == 1920 + ((1920 - 240) // 2)
        assert y == 65
        # A taskbar at the top of the work area pushes the pill down, never up.
        x, y = plan_geometry((0, 40, 1920, 1080), 240, 46)
        assert y == 40 + 65
        # Short screen: keep the pill fully inside the work area.
        x, y = plan_geometry((0, 0, 800, 80), 240, 46)
        assert y + 46 <= 80
        # No monitor information at all still yields a usable origin.
        assert plan_geometry(None) == (0, 0)


class TestTrayLifecycleAndMenu:
    def _tray(self, monkeypatch, windll=None):
        import voice_transcriber.platform.windows.tray as tray_mod

        monkeypatch.setattr(tray_mod.WindowsSystemTray, "_start_thread", lambda self: None)
        tray = tray_mod.WindowsSystemTray("Test Tray")
        if windll is not None:
            import ctypes

            monkeypatch.setattr(ctypes, "windll", windll, raising=False)
        return tray

    def test_quit_runs_the_engine_callback_off_the_message_pump_thread(self, monkeypatch):
        import voice_transcriber.platform.windows.tray as tray_mod

        tray = self._tray(monkeypatch, MagicMock())
        # Simulate "this is the tray UI thread".
        tray.thread = threading.current_thread()
        done = threading.Event()
        seen = {}

        def on_quit():
            seen["thread"] = threading.current_thread()
            done.set()

        tray.on_quit = on_quit
        monkeypatch.setattr(
            tray_mod.os, "_exit", lambda code: pytest.fail("os._exit bypassed cleanup")
        )
        tray._handle_menu_command(tray_mod.CMD_QUIT)
        assert done.wait(2.0), "the quit callback never ran"
        assert seen["thread"] is not threading.current_thread()

    def test_quit_without_a_callback_removes_the_icon_before_exiting(self, monkeypatch):
        import voice_transcriber.platform.windows.tray as tray_mod
        from voice_transcriber.platform.windows import win32

        fake_shell32 = MagicMock()
        fake_shell32.Shell_NotifyIconW.return_value = 1
        tray = self._tray(
            monkeypatch, type("W", (), {"shell32": fake_shell32, "user32": MagicMock()})()
        )
        nid = win32.NOTIFYICONDATAW()
        nid.hWnd = 1234
        nid.uID = 1
        tray._nid = nid
        tray._tray_added = True
        tray.on_quit = None

        exits = []
        monkeypatch.setattr(tray_mod.os, "_exit", lambda code: exits.append(code))
        tray._handle_menu_command(tray_mod.CMD_QUIT)

        assert exits == [0]
        fake_shell32.Shell_NotifyIconW.assert_called_once()
        assert fake_shell32.Shell_NotifyIconW.call_args.args[0] == tray_mod.NIM_DELETE

    def test_microphone_menu_uses_the_real_t2_api(self, monkeypatch):
        """t2 exposes get_input_devices(); get_device_names/set_device never existed."""
        import sys as _sys

        import voice_transcriber.platform.windows.tray as tray_mod

        fake_t2 = types.ModuleType("t2")
        fake_t2.get_input_devices = lambda: [
            {"index": 4, "name": "Blue Snowball", "display_name": "Blue Snowball", "is_active": True},
            {"index": 9, "name": "AirPods", "display_name": "AirPods", "is_active": False},
        ]
        # Deliberately absent: the tray used to call these and silently show an
        # empty menu.
        assert not hasattr(fake_t2, "get_device_names")
        monkeypatch.setitem(_sys.modules, "t2", fake_t2)

        tray = self._tray(monkeypatch, MagicMock())
        user32 = MagicMock()
        user32.CreatePopupMenu.return_value = 4321

        assert tray._build_mic_menu(user32, 111) == 4321
        appended = [(c.args[1], c.args[2], c.args[3]) for c in user32.AppendMenuW.call_args_list
                    if c.args[0] == 4321]
        assert (tray_mod.MF_STRING | tray_mod.MF_CHECKED,
                tray_mod.CMD_MIC_BASE, "Blue Snowball") in appended
        assert (tray_mod.MF_STRING,
                tray_mod.CMD_MIC_BASE + 1, "AirPods") in appended
        assert tray._mic_menu == {
            tray_mod.CMD_MIC_BASE: (4, "Blue Snowball"),
            tray_mod.CMD_MIC_BASE + 1: (9, "AirPods"),
        }

    def test_clicking_a_microphone_applies_the_mapped_device(self, monkeypatch):
        import sys as _sys

        import voice_transcriber.platform.windows.tray as tray_mod

        fake_t2 = types.ModuleType("t2")
        fake_t2.get_input_devices = lambda: [
            {"index": 7, "name": "USB Mic", "display_name": "USB Mic", "is_active": False}
        ]
        monkeypatch.setitem(_sys.modules, "t2", fake_t2)

        tray = self._tray(monkeypatch, MagicMock())
        tray._mic_menu = {tray_mod.CMD_MIC_BASE: (7, "USB Mic")}
        applied = []
        monkeypatch.setattr(
            tray_mod.WindowsSystemTray, "_apply_device",
            staticmethod(lambda index, name: applied.append((index, name)) or True),
        )
        changed = []
        tray.on_device_changed = changed.append

        tray._handle_menu_command(tray_mod.CMD_MIC_BASE)
        assert applied == [(7, "USB Mic")]
        assert changed == ["USB Mic"]

    def test_dismissed_menu_does_nothing(self, monkeypatch):
        import voice_transcriber.platform.windows.tray as tray_mod

        tray = self._tray(monkeypatch, MagicMock())
        monkeypatch.setattr(
            tray_mod.os, "_exit", lambda code: pytest.fail("dismissed menu exited the app")
        )
        tray.on_quit = lambda: pytest.fail("dismissed menu quit the app")
        tray._handle_menu_command(0)  # TrackPopupMenuEx returns 0 when dismissed

    def test_a_failing_menu_still_destroys_the_popup(self, monkeypatch):
        import voice_transcriber.platform.windows.tray as tray_mod

        fake_user32 = MagicMock()
        fake_user32.CreatePopupMenu.return_value = 777
        tray = self._tray(
            monkeypatch, type("W", (), {"user32": fake_user32, "shell32": MagicMock()})()
        )

        def boom(*args, **kwargs):
            raise OSError("registry is on fire")

        monkeypatch.setattr(tray_mod, "is_autostart_enabled", boom, raising=False)
        # _show_context_menu imports it lazily from autostart; patch there too.
        import voice_transcriber.platform.windows.autostart as autostart_mod

        monkeypatch.setattr(autostart_mod, "is_autostart_enabled", boom)
        tray._show_context_menu(5555)

        fake_user32.DestroyMenu.assert_called_once_with(777)

    def test_tooltip_and_balloon_text_fit_the_win32_buffers(self, monkeypatch):
        tray = self._tray(monkeypatch, MagicMock())
        tray._nid = MagicMock()
        tray._icons = {"READY": 1}

        tray.show_balloon("T" * 200, "M" * 400)
        assert len(tray._nid.szInfoTitle) <= 63
        assert len(tray._nid.szInfo) <= 255

        tray.current_state = "RECORDING"
        tray.set_state("RECORDING")
        assert len(tray._nid.szTip) <= 127


class TestNotificationShutdownRace:
    def test_cleanup_during_show_does_not_kill_the_audio_thread(self, monkeypatch):
        from voice_transcriber.platform.windows.notifications import WindowsVisualNotification

        notifier = WindowsVisualNotification.__new__(WindowsVisualNotification)
        notifier.tui = None
        notifier.active_device = None
        notifier.tray = None

        class VanishOnCall:
            def show_recording(self):
                notifier.overlay = None  # cleanup() ran on another thread

        notifier.overlay = VanishOnCall()
        notifier.show_recording()  # used to raise AttributeError
        assert notifier.overlay is None

    def test_cleanup_nulls_both_components_even_if_one_raises(self):
        from voice_transcriber.platform.windows.notifications import WindowsVisualNotification

        notifier = WindowsVisualNotification.__new__(WindowsVisualNotification)
        notifier.tui = None
        overlay = MagicMock()
        overlay.cleanup.side_effect = RuntimeError("overlay teardown failed")
        notifier.overlay = overlay
        notifier.tray = MagicMock()

        notifier.cleanup()
        assert notifier.overlay is None
        assert notifier.tray is None


class TestAutostartCommandConstruction:
    def _fake_winreg(self):
        fake_winreg = MagicMock()
        fake_winreg.OpenKey.return_value.__enter__.return_value = MagicMock()
        fake_winreg.HKEY_CURRENT_USER = 0x80000001
        fake_winreg.KEY_SET_VALUE = 0x0002
        fake_winreg.REG_SZ = 1
        return fake_winreg

    def test_registered_command_quotes_interpreter_and_script(self, monkeypatch):
        import os.path as _ospath
        import sys as _sys

        from voice_transcriber.platform.windows import autostart

        fake_winreg = self._fake_winreg()
        monkeypatch.setattr(autostart, "_get_winreg", lambda: fake_winreg)
        monkeypatch.setattr(_sys, "frozen", False, raising=False)
        monkeypatch.setattr(_sys, "argv", ["src/main.py"])

        assert autostart.set_autostart_enabled(True, "App") is True
        value = fake_winreg.SetValueEx.call_args.args[-1]
        assert value == f'"{_sys.executable}" "{_ospath.abspath("src/main.py")}"'

    def test_missing_script_argument_is_not_registered_as_an_empty_string(self, monkeypatch):
        import sys as _sys

        from voice_transcriber.platform.windows import autostart

        fake_winreg = self._fake_winreg()
        monkeypatch.setattr(autostart, "_get_winreg", lambda: fake_winreg)
        monkeypatch.setattr(_sys, "frozen", False, raising=False)
        monkeypatch.setattr(_sys, "argv", [])

        assert autostart.set_autostart_enabled(True, "App") is True
        value = fake_winreg.SetValueEx.call_args.args[-1]
        assert value == f'"{_sys.executable}"'
        assert '""' not in value

    def test_a_missing_run_key_is_created_instead_of_failing(self, monkeypatch):
        from voice_transcriber.platform.windows import autostart

        fake_winreg = self._fake_winreg()
        fake_key = MagicMock()
        fake_winreg.OpenKey.side_effect = FileNotFoundError("no Run key")
        fake_winreg.CreateKeyEx.return_value.__enter__.return_value = fake_key
        monkeypatch.setattr(autostart, "_get_winreg", lambda: fake_winreg)

        assert autostart.set_autostart_enabled(True, "App", command='"C:\\app.exe"') is True
        fake_winreg.SetValueEx.assert_called_once_with(fake_key, "App", 0, 1, '"C:\\app.exe"')

    def test_registry_failures_never_escape(self, monkeypatch):
        from voice_transcriber.platform.windows import autostart

        fake_winreg = self._fake_winreg()
        fake_winreg.OpenKey.side_effect = PermissionError("denied")
        fake_winreg.CreateKeyEx.side_effect = PermissionError("denied")
        monkeypatch.setattr(autostart, "_get_winreg", lambda: fake_winreg)
        assert autostart.set_autostart_enabled(True, "App") is False
        assert autostart.set_autostart_enabled(False, "App") is False


class TestSingleInstanceCliWiring:
    """The mutex must be taken before the engine starts and released on exit."""

    def _import_main(self):
        import main as vt_main

        return vt_main

    def test_second_instance_exits_without_starting_the_engine(self, monkeypatch):
        import sys as _sys

        vt_main = self._import_main()
        from voice_transcriber.platform.windows import single_instance

        commands = []
        monkeypatch.setattr(vt_main.control, "send_command", lambda cmd, **kwargs: commands.append((cmd, kwargs)))
        monkeypatch.setattr(vt_main.hal, "detect_platform", lambda: vt_main.hal.WINDOWS)
        monkeypatch.setattr(single_instance, "acquire_single_instance_mutex", lambda: (False, None))
        monkeypatch.setattr(_sys, "argv", ["main.py"])
        monkeypatch.setattr(
            vt_main, "check_permissions", lambda: pytest.fail("the engine must not start twice")
        )
        monkeypatch.setattr(
            vt_main, "SimpleVoiceTranscriber", lambda: pytest.fail("the engine must not start twice")
        )

        with pytest.raises(SystemExit) as exc:
            vt_main.cli()
        assert exc.value.code == 0
        assert commands == [("ping", {"notify": True})]

    def test_primary_instance_warns_on_ping_notify(self):
        vt_main = self._import_main()
        app = vt_main.SimpleVoiceTranscriber.__new__(vt_main.SimpleVoiceTranscriber)
        app.visual_notification = MagicMock()

        res = app.handle_control("ping", {"notify": True})
        assert res["ok"] is True
        assert res["cmd"] == "ping"
        app.visual_notification.show_warning.assert_called_once()
        warning_args = app.visual_notification.show_warning.call_args[0]
        assert "Voice Transcriber" in warning_args[0]
        assert "Already active" in warning_args[1]

    def test_primary_instance_releases_the_mutex_when_startup_fails(self, monkeypatch):
        import sys as _sys

        vt_main = self._import_main()
        from voice_transcriber.platform.windows import single_instance

        released = []
        monkeypatch.setattr(vt_main.hal, "detect_platform", lambda: vt_main.hal.WINDOWS)
        monkeypatch.setattr(
            single_instance, "acquire_single_instance_mutex", lambda: (True, 0xC0FFEE)
        )
        monkeypatch.setattr(single_instance, "release_single_instance_mutex", released.append)
        monkeypatch.setattr(_sys, "argv", ["main.py"])
        monkeypatch.setattr(vt_main, "check_permissions", lambda: False)

        with pytest.raises(SystemExit) as exc:
            vt_main.cli()
        assert exc.value.code == 1
        assert released == [0xC0FFEE]
