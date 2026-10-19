"""Native Windows visual notification overlay and system tray integration.

Combines:
1. WindowsOverlayWindow: Focus-safe (WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
   floating recording indicator that never steals cursor focus.
2. WindowsSystemTray: Background taskbar tray icon with dynamic states, right-click
   configuration menu, and native toast/balloon notifications.

Callbacks
---------
The tray is a settings surface with no engine knowledge. ``on_quit`` and
``on_device_changed`` are supplied by the engine (see ``main.py``) so that
"Quit" unwinds through the normal shutdown path (control socket, audio devices,
hotkeys, mutex) instead of calling ``os._exit``, and a microphone picked from
the tray refreshes the TUI badge.
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Optional

from voice_transcriber.platform.windows.overlay import WindowsOverlayWindow
from voice_transcriber.platform.windows.tray import WindowsSystemTray

logger = logging.getLogger(__name__)


class WindowsVisualNotification:
    """Windows visual notifications combining focus-safe floating pill and system tray."""

    def __init__(
        self,
        app_name: str = "Voice Transcriber",
        tui=None,
        on_quit: Optional[Callable[[], None]] = None,
        on_device_changed: Optional[Callable[[str], None]] = None,
    ):
        self.app_name = app_name
        self.tui = tui
        self.active_device = None

        # Floating recording pill overlay
        try:
            self.overlay = WindowsOverlayWindow(title=f"{app_name} Overlay")
        except Exception as e:
            logger.debug("Failed initializing Windows overlay: %s", e)
            self.overlay = None

        # System tray icon
        if os.environ.get("VT_DISABLE_TRAY") != "1":
            try:
                self.tray = WindowsSystemTray(
                    app_name=app_name,
                    on_quit=on_quit,
                    on_device_changed=on_device_changed,
                )
            except Exception as e:
                logger.debug("Failed initializing Windows system tray: %s", e)
                self.tray = None
        else:
            self.tray = None

    # -- TUI-compatible surface -------------------------------------------
    def update_state(self, *args, **kwargs):
        tui = self.tui
        if tui and hasattr(tui, "update_state"):
            try:
                tui.update_state(*args, **kwargs)
            except Exception:
                pass

    def hide_notification(self):
        """Dismiss active overlay and reset state."""
        overlay = self.overlay
        if overlay:
            overlay.hide()
        tray = self.tray
        if tray:
            tray.set_state("READY")
        self.update_state("READY")

    def set_active_device(self, device_name):
        self.active_device = device_name

    def cleanup(self):
        """Clean up both overlay window and system tray icon."""
        overlay, self.overlay = self.overlay, None
        if overlay:
            try:
                overlay.cleanup()
            except Exception as e:
                logger.debug("Overlay cleanup error: %s", e)

        tray, self.tray = self.tray, None
        if tray:
            try:
                tray.cleanup()
            except Exception as e:
                logger.debug("Tray cleanup error: %s", e)

    # -- Visual notifications & States -------------------------------------
    # Each method snapshots the component it talks to: cleanup() (shutdown) can
    # null either one from another thread between the `if` and the call, and an
    # AttributeError there would kill an audio worker mid-recording.
    def show_recording(self):
        logger.info("Recording...")
        overlay = self.overlay
        if overlay:
            overlay.show_recording()
        tray = self.tray
        if tray:
            tray.set_state("RECORDING")
        self.update_state("RECORDING")

    def show_processing(self, message: str = "Processing"):
        logger.info(message)
        overlay = self.overlay
        if overlay:
            overlay.show_processing(str(message))
        tray = self.tray
        if tray:
            tray.set_state("PROCESSING")
        self.update_state("PROCESSING", message)

    def show_completed(self, sub_text=None, **kwargs):
        message = "✓ COMPLETED"
        if sub_text:
            message = f"✓ {sub_text[:30]}..."
        logger.info(f"Transcription: {sub_text}")

        overlay = self.overlay
        if overlay:
            overlay.show_completed(sub_text)
        tray = self.tray
        if tray:
            tray.set_state("READY")

        tui = self.tui
        if tui and sub_text and hasattr(tui, "print_transcription"):
            try:
                import t2
                typed = getattr(t2, "AUTO_TYPE", False)
            except Exception:
                typed = False
            tui.print_transcription(
                sub_text,
                elapsed_sec=kwargs.get("elapsed_sec") or 0.0,
                copy_success=True,
                typed_success=typed,
                device_name=getattr(self, "active_device", None),
                rec_duration=kwargs.get("rec_duration") or 0.0,
                proc_time=kwargs.get("proc_time") or 0.0,
                time_saved=kwargs.get("time_saved", 0.0),
                session_time_saved=kwargs.get("session_time_saved", 0.0),
            )
            if hasattr(tui, "update_state"):
                tui.update_state("READY")

    def show_error(self, message: str):
        logger.error(message)
        overlay = self.overlay
        if overlay:
            overlay.show_error(str(message))
        tray = self.tray
        if tray:
            tray.set_state("READY")
            tray.show_balloon("Voice Transcriber Error", str(message), is_error=True)

    def show_warning(self, title: str, message: str):
        logger.warning("%s: %s", title, message)
        tray = self.tray
        if tray:
            tray.show_balloon(title, message, is_error=False)

    # Legacy Tkinter helper maintained for test compatibility
    def _show_tkinter_overlay(self, text, color="#0066cc"):
        overlay = self.overlay
        if overlay:
            overlay.show_processing(text)
