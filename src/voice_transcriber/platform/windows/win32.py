"""Pointer-correct Win32 bindings shared by the Windows desktop UX modules.

Why this module exists
----------------------
When a ``ctypes`` foreign function has no declared ``argtypes``/``restype``,
ctypes assumes a C ``int``: every argument is marshalled as 32 bits and every
return value is truncated to 32 bits. Win32 is pointer-sized on purpose, and a
64-bit process routinely hands out HDC/HBRUSH/HFONT/HICON/HANDLE values that do
not fit in an ``int``.

The failure mode is silent, which is why it has to be centralised once:

* a truncated HFONT/HBRUSH is not a valid GDI handle, so drawing does nothing
  and ``DeleteObject`` fails -> the object is leaked for the life of the process;
* a truncated HICON gives a blank tray icon;
* a truncated HWND passed back to ``ShowWindow``/``InvalidateRect`` targets the
  wrong window or nothing at all;
* a truncated HANDLE passed to ``CloseHandle``/``ReleaseMutex`` fails with
  ERROR_INVALID_HANDLE, so the single-instance mutex leaks.

:func:`get_win32` returns a lazily-built bundle with every prototype the desktop
UX modules use already declared. It returns ``None`` off Windows (ctypes has no
``windll`` there), so importing this module is safe on Linux/macOS test hosts.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

# LONG_PTR: the return type of a window procedure. A 32-bit c_long (the ctypes
# default) truncates LRESULT on 64-bit Windows.
LRESULT = ctypes.c_ssize_t

# WNDPROC: HWND, UINT, WPARAM, LPARAM -> LRESULT. WINFUNCTYPE is the stdcall
# variant and only exists on Windows; CFUNCTYPE keeps this module importable on
# POSIX hosts (the type is never instantiated there).
_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", None) or ctypes.CFUNCTYPE
WNDPROC = _WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HICON),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", wintypes.RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    """NOTIFYICONDATAW as laid out from Windows 2000 onward (v3)."""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def _proto(function, restype, argtypes=None):
    """Declare one foreign function's ABI, tolerating test doubles."""
    try:
        function.restype = restype
        if argtypes is not None:
            function.argtypes = argtypes
    except (AttributeError, TypeError):  # pragma: no cover - exotic doubles
        pass
    return function


class Win32:
    """The four DLLs the desktop UX needs, with every prototype declared."""

    def __init__(self, windll):
        self.windll = windll
        self.user32 = getattr(windll, "user32", None)
        self.gdi32 = getattr(windll, "gdi32", None)
        self.kernel32 = getattr(windll, "kernel32", None)
        self.shell32 = getattr(windll, "shell32", None)
        self._declare()

    def _declare(self):
        u, g, k, s = self.user32, self.gdi32, self.kernel32, self.shell32

        if u is not None:
            p = _proto
            p(u.GetSystemMetrics, ctypes.c_int, [ctypes.c_int])
            p(u.GetDC, wintypes.HDC, [wintypes.HWND])
            p(u.ReleaseDC, ctypes.c_int, [wintypes.HWND, wintypes.HDC])
            p(u.BeginPaint, wintypes.HDC, [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)])
            p(u.EndPaint, wintypes.BOOL, [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)])
            p(u.FillRect, ctypes.c_int, [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH])
            p(u.DrawTextW, ctypes.c_int,
              [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
               ctypes.POINTER(wintypes.RECT), wintypes.UINT])
            p(u.RegisterClassExW, wintypes.ATOM, [ctypes.POINTER(WNDCLASSEXW)])
            p(u.UnregisterClassW, wintypes.BOOL, [wintypes.LPCWSTR, wintypes.HINSTANCE])
            p(u.CreateWindowExW, wintypes.HWND,
              [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
               ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
               wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID])
            p(u.DestroyWindow, wintypes.BOOL, [wintypes.HWND])
            p(u.ShowWindow, wintypes.BOOL, [wintypes.HWND, ctypes.c_int])
            p(u.SetWindowPos, wintypes.BOOL,
              [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
               ctypes.c_int, ctypes.c_int, wintypes.UINT])
            p(u.InvalidateRect, wintypes.BOOL,
              [wintypes.HWND, ctypes.POINTER(wintypes.RECT), wintypes.BOOL])
            p(u.SetLayeredWindowAttributes, wintypes.BOOL,
              [wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD])
            p(u.SetWindowRgn, ctypes.c_int, [wintypes.HWND, wintypes.HRGN, wintypes.BOOL])
            p(u.PostMessageW, wintypes.BOOL,
              [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM])
            p(u.PostThreadMessageW, wintypes.BOOL,
              [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM])
            p(u.DefWindowProcW, LRESULT,
              [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM])
            # GetMessageW returns -1 on error, 0 on WM_QUIT, non-zero otherwise;
            # a BOOL restype would turn -1 into "truthy" and spin.
            p(u.GetMessageW, ctypes.c_int,
              [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT])
            p(u.TranslateMessage, wintypes.BOOL, [ctypes.POINTER(wintypes.MSG)])
            p(u.DispatchMessageW, LRESULT, [ctypes.POINTER(wintypes.MSG)])
            p(u.PostQuitMessage, None, [ctypes.c_int])
            p(u.CreatePopupMenu, wintypes.HMENU, [])
            p(u.AppendMenuW, wintypes.BOOL,
              [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR])
            # Returns the chosen command id under TPM_RETURNCMD, so it is wider
            # than a BOOL.
            p(u.TrackPopupMenuEx, wintypes.UINT,
              [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
               wintypes.HWND, wintypes.LPVOID])
            p(u.DestroyMenu, wintypes.BOOL, [wintypes.HMENU])
            p(u.GetCursorPos, wintypes.BOOL, [ctypes.POINTER(wintypes.POINT)])
            p(u.SetForegroundWindow, wintypes.BOOL, [wintypes.HWND])
            p(u.CreateIconIndirect, wintypes.HICON, [ctypes.POINTER(ICONINFO)])
            p(u.DestroyIcon, wintypes.BOOL, [wintypes.HICON])
            # second parameter is MAKEINTRESOURCE(id), i.e. a small integer
            # masquerading as a pointer: LPVOID, not LPCWSTR.
            p(u.LoadIconW, wintypes.HICON, [wintypes.HINSTANCE, wintypes.LPVOID])
            p(u.MonitorFromPoint, wintypes.HANDLE,
              [wintypes.POINT, wintypes.DWORD])
            p(u.GetMonitorInfoW, wintypes.BOOL,
              [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)])
            if hasattr(u, "GetDpiForSystem"):
                p(u.GetDpiForSystem, wintypes.UINT, [])
            if hasattr(u, "SetProcessDpiAwarenessContext"):
                p(u.SetProcessDpiAwarenessContext, wintypes.BOOL, [wintypes.HANDLE])

        if g is not None:
            p = _proto
            p(g.CreateSolidBrush, wintypes.HBRUSH, [wintypes.COLORREF])
            p(g.CreatePen, wintypes.HPEN, [ctypes.c_int, ctypes.c_int, wintypes.COLORREF])
            p(g.CreateFontW, wintypes.HFONT,
              [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
               wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
               wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
               wintypes.LPCWSTR])
            p(g.SelectObject, wintypes.HGDIOBJ, [wintypes.HDC, wintypes.HGDIOBJ])
            p(g.DeleteObject, wintypes.BOOL, [wintypes.HGDIOBJ])
            p(g.Ellipse, wintypes.BOOL,
              [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int])
            p(g.SetBkMode, ctypes.c_int, [wintypes.HDC, ctypes.c_int])
            p(g.SetTextColor, wintypes.COLORREF, [wintypes.HDC, wintypes.COLORREF])
            p(g.CreateRoundRectRgn, wintypes.HRGN,
              [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
               ctypes.c_int, ctypes.c_int])
            p(g.CreateCompatibleDC, wintypes.HDC, [wintypes.HDC])
            p(g.DeleteDC, wintypes.BOOL, [wintypes.HDC])
            p(g.CreateCompatibleBitmap, wintypes.HBITMAP,
              [wintypes.HDC, ctypes.c_int, ctypes.c_int])
            p(g.CreateBitmap, wintypes.HBITMAP,
              [ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, wintypes.LPVOID])
            p(g.GetDeviceCaps, ctypes.c_int, [wintypes.HDC, ctypes.c_int])

        if k is not None:
            p = _proto
            p(k.GetModuleHandleW, wintypes.HMODULE, [wintypes.LPCWSTR])
            p(k.CreateMutexW, wintypes.HANDLE,
              [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR])
            p(k.GetLastError, wintypes.DWORD, [])
            p(k.SetLastError, None, [wintypes.DWORD])
            p(k.CloseHandle, wintypes.BOOL, [wintypes.HANDLE])
            p(k.ReleaseMutex, wintypes.BOOL, [wintypes.HANDLE])
            p(k.GetCurrentThreadId, wintypes.DWORD, [])

        if s is not None:
            _proto(s.Shell_NotifyIconW, wintypes.BOOL,
                   [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)])


_cache: dict[int, Win32] = {}


def get_win32() -> "Win32 | None":
    """Return the declared Win32 bundle, or ``None`` when not on Win32.

    Cached per ``windll`` object (tests swap in doubles, which must not inherit
    the real bundle's prototypes).
    """
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return None
    key = id(windll)
    bundle = _cache.get(key)
    if bundle is not None and bundle.windll is windll:
        return bundle
    bundle = Win32(windll)
    _cache[key] = bundle
    return bundle
