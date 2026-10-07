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

from voice_transcriber import keybinds

from ..base import BaseHotkeyManager

logger = logging.getLogger(__name__)


class WSLHotkeyManager(BaseHotkeyManager):
    """Zero-setup Windows global hotkey bridge for NixOS WSL.

    The host-side PowerShell helper polls ``GetAsyncKeyState`` and streams
    line events over stdout; this class maps them onto the shared callback
    contract. Event vocabulary:

    ``HOTKEY_DOWN``  record trigger engaged (Alt+Shift, or held middle click)
    ``LATCH_DOWN``   Space tapped mid-hold -- hands-free latch engaged
    ``LATCH_HOLD``   keys released while latched -- recording stays open
    ``HOTKEY_UP``    recording should stop

    The host decides when a release should be *swallowed* by the latch (it also
    owns the start chime, so it must not re-chime when the finishing press
    arrives); this side simply mirrors the state.
    """

    def __init__(self, callback_start, callback_stop, binds=None):
        super().__init__(callback_start, callback_stop)
        self.process = None
        self.reader_thread = None
        # Non-empty so ``main.py`` knows hotkeys are active.
        self.devices = ["WSL-Windows-Host-Bridge"]

        if binds is not None:
            try:
                self.binds = keybinds.parse_binds(binds)
            except keybinds.KeybindError as e:
                logger.warning("Ignoring invalid WSL hotkey binds: %s", e)
        #: ``-Binds`` payload, e.g. ``164,165;160,161``. The host bridge owns
        #: the actual matching because only it can read Windows key state.
        self._binds_flag = keybinds.encode_vk_binds(self.binds)
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
            "-Binds", getattr(self, "_binds_flag", keybinds.DEFAULT_VK_BINDS),
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
                "Hold %s anywhere in Windows to speak.",
                keybinds.describe_binds(self.binds),
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
                    if self.callback_start:
                        threading.Thread(
                            target=self.callback_start, daemon=True
                        ).start()
                elif event == "HOTKEY_UP":
                    self.hotkey_active = False
                    self.latch_release = False
                    if self.callback_stop:
                        threading.Thread(
                            target=self.callback_stop,
                            kwargs={"copy_to_clipboard": True},
                            daemon=True,
                        ).start()
                elif event == "LATCH_DOWN":
                    # Space tapped while the trigger was held. The host bridge
                    # owns the suppression (it withholds the HOTKEY_UP when the
                    # keys come up); we mirror the flag for parity with the
                    # Linux/Windows backends.
                    self.latch_release = True
                elif event == "LATCH_HOLD":
                    # Trigger released mid-latch: nothing is held any more, but
                    # the recording stays open until the next trigger press.
                    self.hotkey_active = False
                    self.latch_release = False
            except Exception as e:
                logger.error(f"Error in WSL hotkey reader: {e}")
                break

    # -- state queries -----------------------------------------------------
    def are_modifiers_pressed(self):
        return self.hotkey_active

    def is_hotkey_pressed(self):
        return self.hotkey_active

    # -- binds -------------------------------------------------------------
    def set_binds(self, binds) -> bool:
        """Replace the push-to-talk binds and restart the host bridge.

        The PowerShell helper polls a fixed chord list captured at launch, so
        the only way to honour a change is to relaunch it with the new
        ``-Binds`` payload. Invalid input is rejected with ``False`` and the
        running bridge keeps its old chords. Never raises into the app.
        """
        try:
            parsed = keybinds.parse_binds(binds)
        except keybinds.KeybindError as e:
            logger.warning("Ignoring invalid WSL hotkey binds: %s", e)
            return False

        self.binds = list(parsed)
        self._binds_flag = keybinds.encode_vk_binds(self.binds)
        logger.info(
            "WSL push-to-talk binds set to %s; restarting hotkey bridge",
            keybinds.describe_binds(self.binds),
        )

        if not self.running and self.process is None:
            # Nothing to restart yet; ``start()`` will use the new payload.
            return True
        try:
            self.stop()
            return bool(self.start())
        except Exception as e:  # pragma: no cover - defensive
            logger.error("Failed to restart WSL hotkey bridge: %s", e)
            return False

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

    def type_text(self, text, fast: bool = False):
        """Trigger auto-paste into the active Windows window."""
        return self._send("PASTE\n")

    def paste_text(self, terminal: bool = False) -> bool:
        """Trigger auto-paste (Ctrl+V or Ctrl+Shift+V) into the active Windows window."""
        cmd = "PASTE_TERMINAL\n" if terminal else "PASTE\n"
        return self._send(cmd)

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
        # Let the old reader exit before a restart spawns a replacement, so two
        # threads can never drain the same stream.
        thread = self.reader_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self.reader_thread = None


# Backwards-compatible alias used by the original ``src/hotkeys.py``.
WSLGlobalHotkeys = WSLHotkeyManager
