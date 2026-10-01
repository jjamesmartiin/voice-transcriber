"""Native Windows visual notification overlay (Tkinter).

Ported from ``main-windows:src/notifications_windows.py``. The constructor is
adapted to accept the same ``app_name``/``tui`` keyword arguments as the Linux
:class:`notifications.VisualNotification`, so ``main.py`` can select it through
the HAL without special-casing the call site.
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)


class WindowsVisualNotification:
    """Windows visual notifications using a non-focus-stealing Tkinter overlay."""

    def __init__(self, app_name: str = "Voice Transcriber", tui=None):
        self.app_name = app_name
        self.tui = tui
        self.active_overlay = None

    # -- TUI-compatible surface -------------------------------------------
    def update_state(self, *args, **kwargs):
        if self.tui and hasattr(self.tui, "update_state"):
            try:
                self.tui.update_state(*args, **kwargs)
            except Exception:
                pass

    def hide_notification(self):
        """Overlays auto-close after 2.5s; flip the TUI back to READY."""
        if self.tui and hasattr(self.tui, "update_state"):
            try:
                self.tui.update_state("READY")
            except Exception:
                pass

    def set_active_device(self, device_name):
        self.active_device = device_name

    def cleanup(self):
        pass

    # -- overlays ----------------------------------------------------------
    def _show_tkinter_overlay(self, text, color="#0066cc"):
        def create_overlay():
            try:
                import tkinter as tk

                root = tk.Tk()
                root.title(self.app_name)
                root.overrideredirect(True)
                root.attributes("-topmost", True)
                root.attributes("-alpha", 0.9)
                root.attributes("-disabled", True)
                root.configure(bg=color)

                screen_width = root.winfo_screenwidth()
                window_width = 320
                window_height = 60
                x = (screen_width - window_width) // 2
                y = 100
                root.geometry(f"{window_width}x{window_height}+{x}+{y}")

                try:
                    import ctypes

                    GWL_EXSTYLE = -20
                    WS_EX_NOACTIVATE = 0x08000000
                    WS_EX_TOOLWINDOW = 0x00000080
                    ex_style = ctypes.windll.user32.GetWindowLongW(
                        root.winfo_id(), GWL_EXSTYLE
                    )
                    ctypes.windll.user32.SetWindowLongW(
                        root.winfo_id(),
                        GWL_EXSTYLE,
                        ex_style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
                    )
                except Exception:
                    pass

                label = tk.Label(
                    root, text=text, bg=color, fg="white",
                    font=("Arial", 12, "bold"),
                )
                label.pack(expand=True)

                root.after(2500, root.destroy)
                root.mainloop()
            except Exception as e:
                logger.debug(f"Tkinter overlay failed: {e}")

        self.active_overlay = threading.Thread(target=create_overlay, daemon=True)
        self.active_overlay.start()

    def _play_sound(self, sound_type):
        # Audio cue playback is handled centrally by WindowsAudioCuePlayer via hal.
        pass

    def show_recording(self):
        logger.info("Recording...")
        self._show_tkinter_overlay("● RECORDING", "#ff4444")

    def show_processing(self, message="Processing"):
        logger.info(message)
        self._show_tkinter_overlay(f"LOADING {str(message).upper()}", "#ffaa00")

    def show_completed(self, sub_text=None, **kwargs):
        message = "✓ COMPLETED"
        if sub_text:
            message = f"✓ {sub_text[:30]}..."
        logger.info(f"Transcription: {sub_text}")
        self._show_tkinter_overlay(message, "#00aa44")

    def show_error(self, message):
        logger.error(message)
        self._show_tkinter_overlay(f"✗ {str(message)[:40]}", "#cc0000")
