"""macOS visual notification backend.

Uses native macOS User Notifications via ``osascript -e 'display notification ...'``
(non-blocking, errors swallowed) and delegates status updates to the terminal UI
if present.
"""
from __future__ import annotations

import logging
import subprocess
import threading

logger = logging.getLogger(__name__)


def _escape_applescript_string(text: str) -> str:
    """Escape backslashes and double quotes for an AppleScript string literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


class MacOSVisualNotification:
    """macOS visual notifications using AppleScript and terminal UI integration."""

    def __init__(self, app_name: str = "Voice Transcriber", tui=None):
        self.app_name = app_name
        self.tui = tui
        self.active_device = None

    # -- TUI-compatible surface -------------------------------------------
    def update_state(self, *args, **kwargs):
        if self.tui and hasattr(self.tui, "update_state"):
            try:
                self.tui.update_state(*args, **kwargs)
            except Exception:
                pass

    def hide_notification(self):
        if self.tui and hasattr(self.tui, "update_state"):
            try:
                self.tui.update_state("READY")
            except Exception:
                pass

    def set_active_device(self, device_name):
        self.active_device = device_name

    def cleanup(self):
        pass

    # -- notification dispatch ---------------------------------------------
    def _display_notification(self, text: str, title: str | None = None, subtitle: str | None = None):
        title = title or self.app_name

        def _run():
            try:
                esc_text = _escape_applescript_string(text)
                esc_title = _escape_applescript_string(title)
                script = f'display notification "{esc_text}" with title "{esc_title}"'
                if subtitle:
                    esc_sub = _escape_applescript_string(subtitle)
                    script += f' subtitle "{esc_sub}"'
                subprocess.run(
                    ["osascript", "-e", script],
                    stderr=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    check=False,
                )
            except Exception as e:
                logger.debug(f"macOS notification failed: {e}")

        threading.Thread(target=_run, daemon=True).start()

    def show_recording(self, text="RECORDING"):
        logger.info("Recording...")
        if self.tui and hasattr(self.tui, "update_state"):
            self.tui.update_state("RECORDING")
        self._display_notification("Recording in progress...", title=self.app_name)

    def show_processing(self, message="Processing"):
        logger.info(message)
        if self.tui and hasattr(self.tui, "update_state"):
            self.tui.update_state("PROCESSING", str(message))
        self._display_notification(f"Loading {message}...", title=self.app_name)

    def show_completed(
        self,
        text="COMPLETED",
        sub_text=None,
        elapsed_sec=None,
        rec_duration=None,
        proc_time=None,
        **kwargs,
    ):
        msg = sub_text if sub_text else text
        logger.info(f"Transcription: {sub_text or text}")
        if self.tui and sub_text and hasattr(self.tui, "print_transcription"):
            try:
                import t2

                typed = getattr(t2, "AUTO_TYPE", False)
            except Exception:
                typed = False
            self.tui.print_transcription(
                sub_text,
                elapsed_sec=elapsed_sec or 0.0,
                copy_success=True,
                typed_success=typed,
                device_name=self.active_device,
                rec_duration=rec_duration or 0.0,
                proc_time=proc_time or 0.0,
            )
            if hasattr(self.tui, "update_state"):
                self.tui.update_state("READY")
        elif self.tui and hasattr(self.tui, "update_state"):
            self.tui.update_state("READY")

        display_msg = f"✓ {msg[:60]}..." if len(msg) > 60 else f"✓ {msg}"
        self._display_notification(
            display_msg, title=self.app_name, subtitle="Transcription Complete"
        )

    def show_error(self, message="ERROR"):
        logger.error(message)
        if self.tui and hasattr(self.tui, "print_error"):
            self.tui.print_error("ERROR", str(message))
        if self.tui and hasattr(self.tui, "update_state"):
            self.tui.update_state("READY")
        self._display_notification(
            str(message), title=self.app_name, subtitle="Error"
        )

    def show_warning(self, message="WARNING"):
        logger.warning(message)
        if self.tui and hasattr(self.tui, "print_warning"):
            self.tui.print_warning("WARNING", str(message))
        if self.tui and hasattr(self.tui, "update_state"):
            self.tui.update_state("READY")
        self._display_notification(
            str(message), title=self.app_name, subtitle="Warning"
        )
