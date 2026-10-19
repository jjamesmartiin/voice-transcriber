"""Native Windows System Tray icon using Shell_NotifyIconW via ctypes.

Provides background execution, status indicator (Idle / Recording / Transcribing),
right-click context menu (Autostart toggle, Microphone selection, Diagnostics, Quit),
and native Windows notification toasts/balloons.

The icon, its message window and the popup menus are owned by one UI thread with
a native message pump. HICONs are created once per state and destroyed in
``cleanup``; every menu built for a right-click is destroyed with ``DestroyMenu``
on the error path too. Prototypes come from :mod:`.win32` so 64-bit handles are
not truncated.
"""
from __future__ import annotations

import ctypes
import logging
import os
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Win32 Constants
NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

NIIF_INFO = 0x00000001
NIIF_WARNING = 0x00000002
NIIF_ERROR = 0x00000003

WM_DESTROY = 0x0002
WM_QUIT = 0x0012
WM_USER = 0x0400
WM_TRAYICON = WM_USER + 20
WM_COMMAND = 0x0111
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_APP_CLOSE = WM_USER + 21

MF_STRING = 0x00000000
MF_CHECKED = 0x00000008
MF_UNCHECKED = 0x00000000
MF_SEPARATOR = 0x00000800
MF_DISABLED = 0x00000002
MF_GRAYED = 0x00000001
MF_POPUP = 0x00000010

TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002

IDI_APPLICATION = 32512

# Menu IDs
CMD_STATUS = 1001
CMD_AUTOSTART = 1002
CMD_DOCTOR = 1003
CMD_QUIT = 1004
CMD_MIC_BASE = 2000


class WindowsSystemTray:
    """Manages the Windows taskbar notification area (System Tray) icon."""

    def __init__(
        self,
        app_name: str = "Voice Transcriber",
        on_quit: Optional[Callable[[], None]] = None,
        on_device_changed: Optional[Callable[[str], None]] = None,
    ):
        self.app_name = app_name
        self.on_quit = on_quit
        self.on_device_changed = on_device_changed
        self.hwnd = None
        self.thread = None
        self._thread_id = None
        self._running = False
        self._ready_event = threading.Event()
        self.current_state = "READY"
        self._icons = {}
        self._nid = None
        self._tray_added = False
        self._nid_lock = threading.Lock()
        self._mic_menu = {}

        self._start_thread()

    def _start_thread(self):
        from voice_transcriber.platform.windows.win32 import get_win32

        if get_win32() is None:
            logger.debug("Win32 windll unavailable, system tray disabled")
            return

        self._running = True
        self.thread = threading.Thread(target=self._ui_thread_run, daemon=True, name="VT_Tray_UI")
        self.thread.start()
        self._ready_event.wait(timeout=2.0)

    # -- icon --------------------------------------------------------------
    def _create_color_icon(self, user32, gdi32, color_bgr: int):
        """Generate a 16x16 circle icon of the specified color.

        Every GDI object is released even if a drawing call fails: a leaked
        screen DC or memory DC per icon is a permanent handle leak, and there
        are three icons per process.
        """
        from ctypes import wintypes

        from voice_transcriber.platform.windows.win32 import ICONINFO

        hdc = mem_dc = hbm_color = hbm_mask = None
        try:
            hdc = user32.GetDC(None)
            if not hdc:
                raise OSError("GetDC returned no device context")
            mem_dc = gdi32.CreateCompatibleDC(hdc)
            if not mem_dc:
                raise OSError("CreateCompatibleDC failed")
            hbm_color = gdi32.CreateCompatibleBitmap(hdc, 16, 16)
            hbm_mask = gdi32.CreateBitmap(16, 16, 1, 1, None)
            if not hbm_color or not hbm_mask:
                raise OSError("bitmap creation failed")

            old_bm = gdi32.SelectObject(mem_dc, hbm_color)
            try:
                # Black background for the colour bitmap
                bg_brush = gdi32.CreateSolidBrush(0x00000000)
                try:
                    rect = wintypes.RECT(0, 0, 16, 16)
                    user32.FillRect(mem_dc, ctypes.byref(rect), bg_brush)
                finally:
                    if bg_brush:
                        gdi32.DeleteObject(bg_brush)

                brush = gdi32.CreateSolidBrush(color_bgr)
                pen = gdi32.CreatePen(0, 1, color_bgr)
                old_brush = old_pen = None
                try:
                    if brush:
                        old_brush = gdi32.SelectObject(mem_dc, brush)
                    if pen:
                        old_pen = gdi32.SelectObject(mem_dc, pen)
                    gdi32.Ellipse(mem_dc, 2, 2, 14, 14)
                finally:
                    if old_brush:
                        gdi32.SelectObject(mem_dc, old_brush)
                    if old_pen:
                        gdi32.SelectObject(mem_dc, old_pen)
                    if brush:
                        gdi32.DeleteObject(brush)
                    if pen:
                        gdi32.DeleteObject(pen)
            finally:
                if old_bm:
                    gdi32.SelectObject(mem_dc, old_bm)

            ii = ICONINFO()
            ii.fIcon = True
            ii.hbmMask = hbm_mask
            ii.hbmColor = hbm_color
            # CreateIconIndirect copies the bitmaps, so ours are still ours to free.
            return user32.CreateIconIndirect(ctypes.byref(ii))
        except Exception as e:
            logger.debug("Failed creating dynamic tray icon: %s", e)
            return user32.LoadIconW(None, IDI_APPLICATION)
        finally:
            if mem_dc:
                gdi32.DeleteDC(mem_dc)
            if hdc:
                user32.ReleaseDC(None, hdc)
            if hbm_color:
                gdi32.DeleteObject(hbm_color)
            if hbm_mask:
                gdi32.DeleteObject(hbm_mask)

    # -- UI thread ---------------------------------------------------------
    def _ui_thread_run(self):
        from ctypes import wintypes

        from voice_transcriber.platform.windows import win32

        w = win32.get_win32()
        if w is None:
            self._ready_event.set()
            return
        user32, gdi32, kernel32, shell32 = w.user32, w.gdi32, w.kernel32, w.shell32

        self._thread_id = kernel32.GetCurrentThreadId()

        # Cache icons for Idle (Blue), Recording (Red), Processing (Amber)
        self._icons["READY"] = self._create_color_icon(user32, gdi32, 0x00FF8800)  # Blue/Cyan BGR
        self._icons["RECORDING"] = self._create_color_icon(user32, gdi32, 0x004444FF)  # Red BGR
        self._icons["PROCESSING"] = self._create_color_icon(user32, gdi32, 0x0000AAFF)  # Amber BGR

        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_TRAYICON:
                try:
                    event = int(lparam) & 0xFFFF
                except (TypeError, ValueError):  # pragma: no cover - defensive
                    event = lparam
                if event == WM_RBUTTONUP:
                    self._show_context_menu(hwnd)
                elif event == WM_LBUTTONDBLCLK:
                    self._on_double_click()
                return 0
            if msg == WM_APP_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wnd_proc_ref = win32.WNDPROC(wnd_proc)

        hinst = kernel32.GetModuleHandleW(None)
        class_name = f"VT_Tray_Class_{id(self)}"

        wcex = win32.WNDCLASSEXW()
        wcex.cbSize = ctypes.sizeof(win32.WNDCLASSEXW)
        wcex.lpfnWndProc = self._wnd_proc_ref
        wcex.hInstance = hinst
        wcex.lpszClassName = class_name

        registered = bool(user32.RegisterClassExW(ctypes.byref(wcex)))
        if not registered:
            logger.debug("Failed to register tray window class")
            self._ready_event.set()
            return

        # Create hidden message window
        hwnd = user32.CreateWindowExW(
            0, class_name, "VT_Tray_Window", 0, 0, 0, 0, 0, 0, 0, hinst, 0
        )
        if not hwnd:
            logger.debug("Failed creating tray message window")
            user32.UnregisterClassW(class_name, hinst)
            self._ready_event.set()
            return

        self.hwnd = hwnd

        nid = win32.NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(win32.NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self._icons.get("READY") or user32.LoadIconW(None, IDI_APPLICATION)
        nid.szTip = self._tip_for("READY")
        with self._nid_lock:
            self._nid = nid
            self._tray_added = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
        self._ready_event.set()

        # cleanup() may have run while this thread was still starting; the pump
        # would then never run and the window would leak until process exit.
        if not self._running:
            user32.DestroyWindow(hwnd)

        # Message pump
        msg = wintypes.MSG()
        try:
            while self._running:
                ret = user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
                if ret <= 0:  # 0 == WM_QUIT, -1 == error
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._destroy_tray_icon(shell32)
            self.hwnd = None
            try:
                user32.UnregisterClassW(class_name, hinst)
            except Exception:  # pragma: no cover - OS dependent
                pass

    def _destroy_tray_icon(self, shell32):
        with self._nid_lock:
            nid = self._nid
            if nid is None or not self._tray_added:
                return
            self._tray_added = False
            try:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            except Exception as e:  # pragma: no cover - OS dependent
                logger.debug("Failed removing tray icon: %s", e)

    def _tip_for(self, state: str) -> str:
        if state == "RECORDING":
            return f"{self.app_name} - Recording..."[:127]
        if state == "PROCESSING":
            return f"{self.app_name} - Transcribing speech..."[:127]
        return f"{self.app_name} - Ready (Alt+Shift to dictate)"[:127]

    # -- menu --------------------------------------------------------------
    def _build_mic_menu(self, user32, h_menu):
        """Append the Microphones submenu. Returns the popup handle or None.

        The command id -> (PortAudio index, name) map is kept for
        ``_handle_menu_command``: re-enumerating devices when the click arrives
        would race a hot-plug and could apply the wrong index to a name.
        """
        import t2

        h_mic_menu = user32.CreatePopupMenu()
        if not h_mic_menu:
            return None
        self._mic_menu = {}
        try:
            for position, dev in enumerate(t2.get_input_devices()):
                index = dev.get("index")
                name = str(dev.get("name") or "")
                if index is None or not name:
                    continue
                label = str(dev.get("display_name") or name)[:40]
                flag = MF_STRING | (MF_CHECKED if dev.get("is_active") else MF_UNCHECKED)
                cmd_id = CMD_MIC_BASE + position
                user32.AppendMenuW(h_mic_menu, flag, cmd_id, label)
                self._mic_menu[cmd_id] = (index, name)
            if not self._mic_menu:
                user32.AppendMenuW(
                    h_mic_menu, MF_STRING | MF_GRAYED, CMD_MIC_BASE, "Default Microphone"
                )
        except Exception as e:
            logger.debug("Could not list microphones for the tray menu: %s", e)
            self._mic_menu = {}
            try:
                user32.AppendMenuW(
                    h_mic_menu, MF_STRING | MF_GRAYED, CMD_MIC_BASE, "Default Microphone"
                )
            except Exception:
                pass
        return h_mic_menu

    def _show_context_menu(self, hwnd):
        from ctypes import wintypes

        from voice_transcriber.platform.windows import win32
        from voice_transcriber.platform.windows.autostart import is_autostart_enabled

        w = win32.get_win32()
        if w is None:
            return
        user32 = w.user32

        h_menu = user32.CreatePopupMenu()
        if not h_menu:
            return
        try:
            # 1. Status header (disabled)
            state_label = f"{self.app_name} ({self.current_state.capitalize()})"
            user32.AppendMenuW(h_menu, MF_STRING | MF_GRAYED | MF_DISABLED, CMD_STATUS, state_label)
            user32.AppendMenuW(h_menu, MF_SEPARATOR, 0, None)

            # 2. Start with Windows toggle
            autostart_on = is_autostart_enabled()
            check_flag = MF_CHECKED if autostart_on else MF_UNCHECKED
            user32.AppendMenuW(h_menu, MF_STRING | check_flag, CMD_AUTOSTART, "Start with Windows")

            # 3. Microphones submenu
            h_mic_menu = self._build_mic_menu(user32, h_menu)
            if h_mic_menu:
                user32.AppendMenuW(h_menu, MF_POPUP, h_mic_menu, "Microphones")

            # 4. Diagnostics / Doctor
            user32.AppendMenuW(h_menu, MF_STRING, CMD_DOCTOR, "Run Diagnostics (Doctor)")
            user32.AppendMenuW(h_menu, MF_SEPARATOR, 0, None)

            # 5. Quit
            user32.AppendMenuW(h_menu, MF_STRING, CMD_QUIT, "Quit")

            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            # Required so the menu dismisses correctly when the user clicks away.
            user32.SetForegroundWindow(hwnd)
            cmd = user32.TrackPopupMenuEx(
                h_menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, pt.x, pt.y, hwnd, None
            )
            user32.PostMessageW(hwnd, 0, 0, 0)
        except Exception as e:
            logger.debug("Tray menu failed: %s", e)
            cmd = 0
        finally:
            user32.DestroyMenu(h_menu)

        self._handle_menu_command(cmd)

    def _handle_menu_command(self, cmd: int):
        if not cmd:
            return
        if cmd == CMD_AUTOSTART:
            from voice_transcriber.platform.windows.autostart import (
                is_autostart_enabled,
                set_autostart_enabled,
            )
            new_state = not is_autostart_enabled()
            if set_autostart_enabled(new_state):
                status_text = "enabled" if new_state else "disabled"
                self.show_balloon("Autostart Setting", f"Start with Windows has been {status_text}.")
            else:
                self.show_balloon(
                    "Autostart Setting", "Could not update the startup setting.", is_error=True
                )
        elif cmd == CMD_DOCTOR:
            try:
                import t2
                is_healthy, issues = t2.check_microphone_health()
                if is_healthy:
                    self.show_balloon("Diagnostics Complete", "All systems healthy! Audio input ready.")
                else:
                    self.show_balloon("Microphone Warning", "\n".join(issues), is_error=True)
            except Exception as e:
                self.show_balloon("Diagnostics Error", str(e), is_error=True)
        elif cmd in self._mic_menu:
            index, name = self._mic_menu[cmd]
            try:
                if self._apply_device(index, name):
                    self.show_balloon("Microphone Switched", f"Active microphone: {name}"[:255])
                    if self.on_device_changed:
                        try:
                            self.on_device_changed(name)
                        except Exception as e:
                            logger.debug("Device-change callback failed: %s", e)
            except Exception as e:
                logger.debug("Failed setting microphone from tray: %s", e)
                self.show_balloon("Microphone Error", f"Could not switch to {name}"[:255], is_error=True)
        elif cmd == CMD_QUIT:
            logger.info("Quit requested via System Tray")
            callback = self.on_quit
            if callback is None:
                # No owner to unwind: removing the icon first is still better
                # than leaving a ghost in the notification area.
                from voice_transcriber.platform.windows.win32 import get_win32
                w = get_win32()
                if w is not None:
                    self._destroy_tray_icon(w.shell32)
                os._exit(0)
            else:
                # Never call back into the engine from the message-pump thread:
                # its cleanup joins this thread and would deadlock.
                threading.Thread(
                    target=callback, daemon=True, name="VT_Tray_Quit"
                ).start()

    @staticmethod
    def _apply_device(index, name) -> bool:
        """Point the shared audio configuration at the chosen input device."""
        import t2

        t2.PRIMARY_DEVICE_NAME = name
        t2.INPUT_DEVICE_INDEX = index
        t2.set_default_input_device(index)
        t2.save_audio_config()
        return True

    def _on_double_click(self):
        self.show_balloon("Voice Transcriber", "Hold Alt+Shift to speak anywhere. Release to transcribe.")

    # -- state (any thread) ------------------------------------------------
    def set_state(self, state: str):
        """Update the icon and tooltip based on state (READY / RECORDING / PROCESSING)."""
        self.current_state = state.upper()
        self._modify_icon(NIF_ICON | NIF_TIP)

    def show_balloon(self, title: str, text: str, is_error: bool = False):
        """Show a native Windows notification toast / balloon tip."""
        self._modify_icon(
            NIF_INFO | NIF_TIP,
            title=str(title)[:63],
            text=str(text)[:255],
            info_flags=NIIF_ERROR if is_error else NIIF_INFO,
        )

    def _modify_icon(self, flags, title=None, text=None, info_flags=0):
        from voice_transcriber.platform.windows.win32 import get_win32

        w = get_win32()
        if w is None:
            return
        with self._nid_lock:
            nid = self._nid
            if nid is None:
                return
            nid.uFlags = flags
            if flags & NIF_ICON:
                nid.hIcon = self._icons.get(self.current_state) or self._icons.get("READY")
            if flags & NIF_TIP:
                nid.szTip = self._tip_for(self.current_state)
            if flags & NIF_INFO:
                nid.szInfoTitle = title or ""
                nid.szInfo = text or ""
                nid.dwInfoFlags = info_flags
            try:
                # Tests install a plain double; a real NOTIFYICONDATAW must be
                # passed by reference.
                nid_ref = ctypes.byref(nid) if isinstance(nid, ctypes.Structure) else nid
                w.shell32.Shell_NotifyIconW(NIM_MODIFY, nid_ref)
            except Exception as e:  # pragma: no cover - OS dependent
                logger.debug("Shell_NotifyIconW(MODIFY) failed: %s", e)

    def cleanup(self):
        """Remove tray icon and stop the UI thread. Never blocks on a dead pump."""
        from voice_transcriber.platform.windows.win32 import get_win32

        self._running = False
        w = get_win32()
        hwnd = self.hwnd
        thread = self.thread
        if w is not None:
            try:
                self._destroy_tray_icon(w.shell32)
                if hwnd:
                    w.user32.PostMessageW(hwnd, WM_APP_CLOSE, 0, 0)
                elif self._thread_id:
                    w.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception as e:  # pragma: no cover - OS dependent
                logger.debug("Tray teardown post failed: %s", e)

        if thread is not None and thread is not threading.current_thread():
            if thread.is_alive():
                thread.join(timeout=2.0)
                if thread.is_alive():
                    logger.debug("Tray UI thread did not exit within 2s")

        # Unpublish the icon first: a late set_state()/show_balloon() from the
        # audio thread must not NIM_MODIFY an icon that is about to be freed.
        with self._nid_lock:
            self._nid = None
            self._tray_added = False

        # Free the HICONs we created. (The IDI_APPLICATION fallback is a shared
        # system handle; DestroyIcon on it is a documented no-op.)
        icons, self._icons = self._icons, {}
        for icon in icons.values():
            if icon and w is not None:
                try:
                    w.user32.DestroyIcon(icon)
                except Exception:
                    pass
        self.hwnd = None
