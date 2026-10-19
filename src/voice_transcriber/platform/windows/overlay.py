"""Focus-safe floating recording pill overlay for Windows.

Crucial Invariant:
This window MUST be created with WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_LAYERED
and shown with SW_SHOWNOACTIVATE so it NEVER steals focus from the user's active application
(Notepad, Word, browser, terminal, IDE) while dictating.

The window owns no Python state that the audio/ASR threads mutate directly: they
publish ``current_text``/``state_color`` and ask the UI thread to repaint. Every
GDI object created for a paint is destroyed before the paint returns, on the
error path too, and the Win32 prototypes come from :mod:`.win32` (64-bit safe).
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

# Win32 Constants
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_LAYERED = 0x00080000
WS_POPUP = 0x80000000
SW_SHOWNOACTIVATE = 4
SW_HIDE = 0
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
LWA_ALPHA = 0x00000002

WM_DESTROY = 0x0002
WM_PAINT = 0x000F
WM_USER = 0x0400
WM_UPDATE_STATE = WM_USER + 10
# Ask the UI thread to destroy the window and leave the pump. Posted by
# cleanup() from another thread; DestroyWindow is what actually frees the HWND
# (posting WM_DESTROY only calls PostQuitMessage and leaks the window).
WM_APP_CLOSE = WM_USER + 11

COLOR_BG = 0x001E1E1E        # Dark slate
COLOR_RECORDING = 0x004444FF # Red (BGR format for Win32 COLORREF)
COLOR_PROCESSING = 0x0000AAFF # Amber/Orange (BGR)
COLOR_COMPLETED = 0x0040C828  # Green (BGR)
COLOR_ERROR = 0x003333DD     # Red-dark (BGR)
COLOR_TEXT = 0x00FFFFFF      # White

DT_SINGLELINE = 0x00000020
DT_VCENTER = 0x00000004
DT_END_ELLIPSIS = 0x00008000

MONITOR_DEFAULTTONEAREST = 0x00000002
LOGPIXELSX = 88


def plan_geometry(work_area, width=240, height=46, scale=1.0):
    """Where to put the pill, given a monitor work area ``(l, t, r, b)``.

    Top-centre of that monitor, ``65`` scaled pixels below its top edge and
    never above it (a taskbar at the top moves the pill down instead of making
    it hide under the bar). Pure so it can be pinned on a POSIX test host.
    """
    if not work_area:
        return 0, 0
    left, top, right, bottom = work_area
    width = int(round(width * scale))
    height = int(round(height * scale))
    x = left + max(0, ((right - left) - width) // 2)
    margin = int(round(65 * scale))
    y = top + margin
    # Keep the whole pill inside the work area even on a short/scaled screen.
    if y + height > bottom:
        y = max(top, bottom - height)
    return x, y


class WindowsOverlayWindow:
    """Floating semi-transparent pill overlay that displays dictation status.

    Runs on a dedicated UI thread with a native Win32 message pump to prevent
    blocking the audio or ASR pipeline.
    """

    def __init__(self, title: str = "Voice Transcriber Overlay"):
        self.title = title
        self.hwnd = None
        self.thread = None
        self._thread_id = None
        self._running = False
        self._ready_event = threading.Event()
        self._auto_hide_timer = None
        # Bumped on every state change; an auto-hide timer only hides the state
        # it was scheduled for, so a stale timer cannot blank a newer pill.
        self._generation = 0
        self._state_lock = threading.Lock()

        # State fields
        self.current_state = "IDLE"
        self.current_text = ""
        self.state_color = COLOR_RECORDING

        # Try to initialize if on Windows
        self._start_thread()

    def _start_thread(self):
        from voice_transcriber.platform.windows.win32 import get_win32

        if get_win32() is None:
            logger.debug("Win32 windll unavailable, overlay disabled")
            return

        self._running = True
        self.thread = threading.Thread(target=self._ui_thread_run, daemon=True, name="VT_Overlay_UI")
        self.thread.start()
        self._ready_event.wait(timeout=2.0)

    # -- UI thread ---------------------------------------------------------
    def _ui_thread_run(self):
        import ctypes
        from ctypes import wintypes

        from voice_transcriber.platform.windows import win32

        w = win32.get_win32()
        if w is None:
            self._ready_event.set()
            return
        user32, gdi32, kernel32 = w.user32, w.gdi32, w.kernel32

        self._thread_id = kernel32.GetCurrentThreadId()

        # One process-wide DPI mode: per-monitor-v2 when the OS has it, so
        # GetSystemMetrics/GetMonitorInfo are physical pixels and the pill is not
        # bitmap-stretched on a scaled display. Fails harmlessly (returns 0) if a
        # window already exists or the API is absent.
        try:
            set_dpi = getattr(user32, "SetProcessDpiAwarenessContext", None)
            if set_dpi is not None:
                set_dpi(ctypes.c_void_p(-4))  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        except Exception as e:  # pragma: no cover - OS dependent
            logger.debug("Could not set DPI awareness: %s", e)

        width, height = 240, 46
        x, y = self._resolve_origin(user32, width, height)

        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_PAINT:
                self._paint(hwnd, user32, gdi32, width, height)
                return 0
            if msg == WM_APP_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wnd_proc_ref = win32.WNDPROC(wnd_proc)  # keep alive for the class

        hinst = kernel32.GetModuleHandleW(None)
        class_name = f"VT_Overlay_Class_{id(self)}"

        class_brush = gdi32.CreateSolidBrush(COLOR_BG)
        wcex = win32.WNDCLASSEXW()
        wcex.cbSize = ctypes.sizeof(win32.WNDCLASSEXW)
        wcex.style = 0x0003  # CS_HREDRAW | CS_VREDRAW
        wcex.lpfnWndProc = self._wnd_proc_ref
        wcex.hInstance = hinst
        wcex.lpszClassName = class_name
        wcex.hbrBackground = class_brush

        registered = bool(user32.RegisterClassExW(ctypes.byref(wcex)))
        if not registered:
            logger.debug("Failed to register overlay window class")
            if class_brush:
                gdi32.DeleteObject(class_brush)
            self._ready_event.set()
            return

        # Crucial Focus-Safe ExStyles:
        # WS_EX_NOACTIVATE ensures the overlay window NEVER steals input focus
        # WS_EX_TOOLWINDOW hides it from Alt+Tab
        # WS_EX_TOPMOST keeps it visible over full-screen apps
        # WS_EX_LAYERED enables true alpha transparency
        ex_style = WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_LAYERED

        hwnd = user32.CreateWindowExW(
            ex_style,
            class_name,
            self.title,
            WS_POPUP,
            x, y, width, height,
            0, 0, hinst, 0,
        )

        if not hwnd:
            logger.debug("Failed to create overlay window")
            user32.UnregisterClassW(class_name, hinst)
            if class_brush:
                gdi32.DeleteObject(class_brush)
            self._ready_event.set()
            return

        self.hwnd = hwnd

        # Set alpha transparency (230 / 255 ~ 90% opacity)
        user32.SetLayeredWindowAttributes(hwnd, 0, 230, LWA_ALPHA)

        # Rounded rectangle shape (the region is owned by the window once set)
        hrgn = gdi32.CreateRoundRectRgn(0, 0, width, height, 22, 22)
        if hrgn:
            user32.SetWindowRgn(hwnd, hrgn, True)

        self._ready_event.set()

        # cleanup() may have run while this thread was still starting; in that
        # case the pump below would never touch the window and it would leak
        # until process exit.
        if not self._running:
            user32.DestroyWindow(hwnd)

        # Message pump. Any PostQuitMessage (WM_DESTROY) or WM_QUIT posted to
        # the thread ends it, so cleanup() can never leave this blocked forever.
        msg = wintypes.MSG()
        try:
            while self._running:
                ret = user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
                if ret <= 0:  # 0 == WM_QUIT, -1 == error
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            # Free what the class registered, whether we exited via WM_QUIT or
            # because _running was cleared. Idempotent: an already-destroyed
            # class/window just fails these calls.
            self.hwnd = None
            try:
                user32.UnregisterClassW(class_name, hinst)
            except Exception:  # pragma: no cover - OS dependent
                pass
            if class_brush:
                gdi32.DeleteObject(class_brush)

    def _resolve_origin(self, user32, width, height):
        """Top-centre of the monitor under the cursor, with safe fallbacks.

        The user is dictating *somewhere*; putting the pill on the monitor they
        are looking at beats hard-coding the primary display (multi-monitor).
        """
        from voice_transcriber.platform.windows.win32 import MONITORINFO

        work_area = None
        try:
            import ctypes
            from ctypes import wintypes

            pt = wintypes.POINT()
            if user32.GetCursorPos(ctypes.byref(pt)):
                hmon = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
                if hmon:
                    info = MONITORINFO()
                    info.cbSize = ctypes.sizeof(MONITORINFO)
                    if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                        r = info.rcWork
                        work_area = (r.left, r.top, r.right, r.bottom)
        except Exception as e:  # pragma: no cover - OS dependent
            logger.debug("Monitor query failed: %s", e)

        scale = 1.0
        try:
            get_dpi = getattr(user32, "GetDpiForSystem", None)
            if get_dpi is not None:
                dpi = int(get_dpi() or 96)
                if dpi > 0:
                    scale = dpi / 96.0
        except Exception:  # pragma: no cover - OS dependent
            scale = 1.0

        if work_area is None:
            # Virtualised (primary) screen metrics: still a sane top-centre.
            screen_w = int(user32.GetSystemMetrics(0) or 0)
            screen_h = int(user32.GetSystemMetrics(1) or 0)
            if screen_w <= 0:
                return 0, 0
            work_area = (0, 0, screen_w, screen_h)

        return plan_geometry(work_area, width, height, scale)

    def _paint(self, hwnd, user32, gdi32, width, height):
        """Repaint the pill, destroying every GDI object it creates."""
        import ctypes
        from ctypes import wintypes

        from voice_transcriber.platform.windows.win32 import PAINTSTRUCT

        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        if not hdc:
            return
        # Snapshot so a repaint is internally consistent and the audio thread
        # only ever sees two plain attribute writes.
        text = self.current_text
        dot_color = self.state_color
        try:
            rect = wintypes.RECT(0, 0, width, height)
            bg = gdi32.CreateSolidBrush(COLOR_BG)
            try:
                user32.FillRect(hdc, ctypes.byref(rect), bg)
            finally:
                if bg:
                    gdi32.DeleteObject(bg)

            # Status dot at (18, 16)-(30, 28)
            dot_brush = gdi32.CreateSolidBrush(dot_color)
            dot_pen = gdi32.CreatePen(0, 1, dot_color)
            old_brush = old_pen = None
            try:
                if dot_brush:
                    old_brush = gdi32.SelectObject(hdc, dot_brush)
                if dot_pen:
                    old_pen = gdi32.SelectObject(hdc, dot_pen)
                gdi32.Ellipse(hdc, 18, 16, 30, 28)
            finally:
                if old_brush:
                    gdi32.SelectObject(hdc, old_brush)
                if old_pen:
                    gdi32.SelectObject(hdc, old_pen)
                if dot_brush:
                    gdi32.DeleteObject(dot_brush)
                if dot_pen:
                    gdi32.DeleteObject(dot_pen)

            gdi32.SetBkMode(hdc, 1)  # TRANSPARENT
            gdi32.SetTextColor(hdc, COLOR_TEXT)
            hfont = gdi32.CreateFontW(
                17, 0, 0, 0, 700, 0, 0, 0, 0, 0, 0, 5, 0, "Segoe UI"
            )
            old_font = None
            try:
                if hfont:
                    old_font = gdi32.SelectObject(hdc, hfont)
                text_rect = wintypes.RECT(38, 0, width - 10, height)
                user32.DrawTextW(
                    hdc, text, -1, ctypes.byref(text_rect),
                    DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS,
                )
            finally:
                if old_font:
                    gdi32.SelectObject(hdc, old_font)
                if hfont:
                    gdi32.DeleteObject(hfont)
        except Exception as e:
            # A failed paint must never unwind into the ctypes callback.
            logger.debug("Overlay paint failed: %s", e)
        finally:
            user32.EndPaint(hwnd, ctypes.byref(ps))

    # -- state (any thread) ------------------------------------------------
    def _cancel_timer(self):
        timer = self._auto_hide_timer
        self._auto_hide_timer = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:  # pragma: no cover - Timer.cancel has no raising path
                pass

    def _update_and_show(self, text: str, color_bgr: int) -> int:
        """Publish ``text``/``color_bgr`` and show the pill. Returns the token."""
        self._cancel_timer()
        with self._state_lock:
            self._generation += 1
            token = self._generation
            self.current_text = text
            self.state_color = color_bgr

        from voice_transcriber.platform.windows.win32 import get_win32

        w = get_win32()
        hwnd = self.hwnd
        if w is None or not hwnd:
            return token
        try:
            x, y = self._resolve_origin(w.user32, 240, 46)
            w.user32.SetWindowPos(hwnd, 0, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
            w.user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            w.user32.InvalidateRect(hwnd, None, True)
        except Exception as e:  # pragma: no cover - OS dependent
            logger.debug("Overlay update failed: %s", e)
        return token

    def _schedule_hide(self, token: int, auto_hide_ms: int):
        timer = threading.Timer(auto_hide_ms / 1000.0, self._hide_if_current, (token,))
        timer.daemon = True
        self._auto_hide_timer = timer
        timer.start()

    def _hide_if_current(self, token: int):
        # A timer scheduled for a state the user has already left must not blank
        # the pill that replaced it.
        with self._state_lock:
            current = self._generation
        if current == token:
            self.hide()

    def show_recording(self):
        """Display the active recording pill."""
        self._update_and_show("Recording...", COLOR_RECORDING)

    def show_processing(self, message: str = "Transcribing..."):
        """Display the transcribing/processing state."""
        self._update_and_show(message, COLOR_PROCESSING)

    def show_completed(self, sub_text: str | None = None, auto_hide_ms: int = 1500):
        """Display completion and schedule auto-hide."""
        display = f"✓ {sub_text[:22]}..." if sub_text else "✓ Finished"
        token = self._update_and_show(display, COLOR_COMPLETED)
        self._schedule_hide(token, auto_hide_ms)

    def show_error(self, message: str = "Error", auto_hide_ms: int = 2500):
        """Display error status and auto-hide."""
        token = self._update_and_show(f"✗ {str(message)[:24]}", COLOR_ERROR)
        self._schedule_hide(token, auto_hide_ms)

    def hide(self):
        """Hide the overlay window without destroying it."""
        from voice_transcriber.platform.windows.win32 import get_win32

        self._cancel_timer()
        with self._state_lock:
            self._generation += 1
        w = get_win32()
        hwnd = self.hwnd
        if w is None or not hwnd:
            return
        try:
            w.user32.ShowWindow(hwnd, SW_HIDE)
        except Exception as e:  # pragma: no cover - OS dependent
            logger.debug("Overlay hide failed: %s", e)

    def cleanup(self):
        """Tear down window and message loop. Never blocks on a dead pump."""
        from voice_transcriber.platform.windows.win32 import get_win32

        self._cancel_timer()
        self._running = False
        with self._state_lock:
            self._generation += 1

        w = get_win32()
        hwnd = self.hwnd
        thread = self.thread
        if w is not None:
            try:
                if hwnd:
                    w.user32.PostMessageW(hwnd, WM_APP_CLOSE, 0, 0)
                elif self._thread_id and getattr(w.user32, "PostThreadMessageW", None):
                    # Window creation failed after the thread started: wake the
                    # pump where it blocks so join() cannot time out.
                    w.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT
            except Exception as e:  # pragma: no cover - OS dependent
                logger.debug("Overlay teardown post failed: %s", e)

        if thread is not None and thread is not threading.current_thread():
            if thread.is_alive():
                thread.join(timeout=2.0)
                if thread.is_alive():
                    logger.debug("Overlay UI thread did not exit within 2s")
        self.hwnd = None
