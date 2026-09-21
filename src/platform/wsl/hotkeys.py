"""WSL global hotkey backend.

Spawns the bundled ``wsl_win_hotkeys.ps1`` helper through ``powershell.exe``
(WSL interop) and reads hotkey events from its stdin/stdout protocol. No Python
installation is required on the Windows host. Ported from the
``WSLGlobalHotkeys`` class in ``main-wsl:src/hotkeys.py``.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time

from ..base import BaseHotkeyManager

logger = logging.getLogger(__name__)


class WSLHotkeyManager(BaseHotkeyManager):
    """Zero-setup Windows global hotkey bridge for NixOS WSL."""

    def __init__(self, callback_start, callback_stop, callback_config=None):
        super().__init__(callback_start, callback_stop, callback_config)
        self.process = None
        self.reader_thread = None
        # Non-empty so ``main.py`` knows hotkeys are active.
        self.devices = ["WSL-Windows-Host-Bridge"]
        self.start()

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        # Clean up any stale background bridge processes from previous runs.
        try:
            subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process | Where-Object CommandLine "
                    "-like '*wsl_win_hotkeys.ps1*' | Stop-Process -Force "
                    "-ErrorAction SilentlyContinue",
                ],
                capture_output=True,
                timeout=3,
            )
        except Exception:
            pass

        script_dir = os.path.dirname(os.path.abspath(__file__))
        ps1_path = os.path.join(script_dir, "wsl_win_hotkeys.ps1")
        try:
            res = subprocess.run(
                ["wslpath", "-w", ps1_path],
                capture_output=True, text=True, check=True,
            )
            win_ps1 = res.stdout.strip()
        except Exception:
            win_ps1 = ps1_path

        cmd = [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", win_ps1,
        ]
        try:
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )

            ready_line = self.process.stdout.readline().strip()
            if ready_line != "READY":
                logger.warning(
                    "Unexpected status from WSL Windows hotkey bridge: %s", ready_line
                )

            self.running = True
            self.reader_thread = threading.Thread(
                target=self._reader_loop, daemon=True
            )
            self.reader_thread.start()
            logger.info(
                "🎙️ WSL Zero-Setup Windows Hotkey Bridge active! "
                "Hold Alt+Shift anywhere in Windows to speak."
            )
            return True
        except Exception as e:
            logger.error(f"Failed to start WSL Windows hotkey bridge: {e}")
            self.devices = []
            return False

    def _reader_loop(self):
        while self.running and self.process and self.process.poll() is None:
            try:
                line = self.process.stdout.readline()
                if not line:
                    break
                event = line.strip()
                if event == "HOTKEY_DOWN":
                    self.hotkey_active = True
                    threading.Thread(target=self.callback_start, daemon=True).start()
                elif event == "HOTKEY_UP":
                    self.hotkey_active = False
                    if self.callback_stop:
                        threading.Thread(
                            target=self.callback_stop,
                            kwargs={"copy_to_clipboard": True},
                            daemon=True,
                        ).start()
                elif event == "CONFIG_DOWN":
                    if self.callback_config:
                        threading.Thread(
                            target=self.callback_config, daemon=True
                        ).start()
            except Exception as e:
                logger.error(f"Error in WSL hotkey reader: {e}")
                break

    # -- state queries -----------------------------------------------------
    def are_modifiers_pressed(self):
        return self.hotkey_active

    def is_hotkey_pressed(self):
        return self.hotkey_active

    # -- bridge commands ---------------------------------------------------
    def _send(self, command: str) -> bool:
        if self.process and self.process.poll() is None:
            try:
                self.process.stdin.write(command)
                self.process.stdin.flush()
                return True
            except Exception as e:
                logger.warning(f"Failed to send {command!r} via WSL bridge: {e}")
        return False

    def type_text(self, text):
        """Trigger auto-paste into the active Windows window."""
        return self._send("PASTE\n")

    def play_done_sound(self):
        """Trigger the Windows notification sound via the bridge."""
        return self._send("PLAY_DONE\n")

    def set_sound_theme(self, theme):
        """Set the sound theme dynamically on the Windows host."""
        return self._send(f"SET_SOUND:{theme}\n")

    def set_middle_click_enabled(self, enabled: bool):
        """Toggle middle click push-to-talk mode on the Windows host."""
        self.middle_click_enabled = bool(enabled)
        return self._send(f"SET_MCLICK:{1 if enabled else 0}\n")

    def run(self):
        try:
            while self.running:
                if self.process and self.process.poll() is not None:
                    logger.error(
                        "WSL Windows hotkey bridge process exited with code %s",
                        self.process.poll(),
                    )
                    break
                time.sleep(0.2)
        except KeyboardInterrupt:
            logger.info("Shutting down hotkey monitor...")
        return True

    def stop(self):
        self.running = False
        if self.process and self.process.poll() is None:
            try:
                self.process.stdin.write("EXIT\n")
                self.process.stdin.flush()
                self.process.terminate()
            except Exception:
                pass
            self.process = None


# Backwards-compatible alias used by the original ``src/hotkeys.py``.
WSLGlobalHotkeys = WSLHotkeyManager
