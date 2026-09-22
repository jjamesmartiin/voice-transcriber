#!/usr/bin/env python3
"""
Ratatui TUI client for Voice Transcriber.

This is a drop-in replacement for :class:`tui.VoiceTranscriberTUI` that does not
draw anything itself. Instead it:

  1. opens a Unix domain socket,
  2. spawns the ``vt-tui`` Rust binary (ratatui) with ``--connect <socket>``,
     handing it the real terminal (fd 0/1/2),
  3. forwards engine state to the TUI as newline-delimited JSON, and
  4. dispatches keybind commands coming back from the TUI to the same callbacks
     that ``main.SimpleVoiceTranscriber`` wires up for the Rich TUI.

The Python engine stays authoritative (audio, ASR, clipboard, config); the Rust
process is a pure view + input device. If anything goes wrong here the caller
should fall back to the Rich TUI.
"""

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

logger = logging.getLogger(__name__)

COLOR_PALETTES = ["auto", "green", "cyan", "blue", "magenta", "yellow", "red", "white"]

_DEBUG_PATH = os.environ.get("VT_TUI_DEBUG", "")


def _dbg(msg: str):
    """Append a trace line when VT_TUI_DEBUG is set (troubleshooting aid)."""
    if not _DEBUG_PATH:
        return
    try:
        with open(_DEBUG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except OSError:
        pass


def tui_available(binary: str | None = None) -> bool:
    """True if we can plausibly run the ratatui frontend."""
    if not hasattr(socket, "AF_UNIX"):
        return False
    binary = binary or os.environ.get("VT_TUI_BIN", "")
    if not binary:
        return False
    return shutil.which(binary) is not None or os.path.exists(binary)


class RatatuiTui:
    """IPC client that mirrors the VoiceTranscriberTUI public surface."""

    def __init__(self, binary: str, socket_path: str | None = None):
        self.binary = binary
        self.socket_path = socket_path or self._default_socket_path()

        # --- state mirrored locally so callbacks/other code can read it ---
        self.state = "READY"
        self.sub_state_text = ""
        self.active_device = "Detecting..."
        self.secondary_device = None
        self.model_backend = "cohere"
        self.is_muted = True
        self.auto_type = False
        self.output_mode = "clipboard"
        self.sound_theme = "proximity"
        self.ui_theme = os.environ.get("VT_UI_THEME", "auto") or "auto"
        self.transcription_count = 0
        self.vu_level = 0.0

        # Callbacks (wired by main.SimpleVoiceTranscriber).
        self.on_toggle_record = None
        self.on_change_device = None
        self.on_toggle_mute = None
        self.on_toggle_autotype = None
        self.on_cycle_output_mode = None
        self.on_toggle_numbers = None
        self.on_toggle_middle_click = None
        self.on_cycle_theme = None
        self.on_reset_terminal = None
        self.on_quit = None

        # --- IPC plumbing ---
        self._server = None
        self._conn = None
        self._proc = None
        self._pending: list[dict] = []
        self._send_lock = threading.Lock()
        self._started = False
        self._closed = False
        self._suspended = False
        self._last_vu_sent = 0.0

    # ------------------------------------------------------------------ setup

    def _default_socket_path(self) -> str:
        base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        return os.path.join(base, f"vt-tui-{os.getuid()}.sock")

    def start(self):
        """Bind the socket, spawn the ratatui binary, and begin forwarding."""
        if self._started:
            return
        self._started = True

        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass

        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self.socket_path)
        self._server.listen(1)

        # Hand the child the real terminal file descriptors explicitly.
        self._proc = subprocess.Popen(
            [self.binary, "--connect", self.socket_path],
            stdin=0,
            stdout=1,
            stderr=2,
            close_fds=True,
        )

        self._server.settimeout(10.0)
        try:
            self._conn, _ = self._server.accept()
        except socket.timeout:
            raise RuntimeError("vt-tui did not connect within 10s")
        finally:
            self._server.settimeout(None)

        # Flush anything queued before the child connected.
        with self._send_lock:
            for msg in self._pending:
                self._write(msg)
            self._pending.clear()

        self.print_header()

        threading.Thread(target=self._read_loop, daemon=True).start()
        logger.info("Ratatui TUI connected on %s", self.socket_path)

    def stop(self):
        """Tear down the child process and socket."""
        if self._proc is None and self._server is None:
            return
        self._send({"t": "quit"})
        # Give the TUI a moment to restore the terminal itself before we resort
        # to signals, so the shell prompt lands cleanly.
        if self._proc is not None:
            try:
                self._proc.wait(timeout=2.0)
            except Exception:
                pass
        self._closed = True
        for closer in (self._conn, self._server):
            try:
                if closer is not None:
                    closer.close()
            except Exception:
                pass
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass

    # ------------------------------------------------------------------- wire

    def _send(self, msg: dict):
        with self._send_lock:
            if self._closed:
                return
            # While the TUI is suspended (Python owns the terminal for a menu)
            # it cannot render, so queue instead of dropping.
            if self._conn is None or self._suspended:
                self._pending.append(msg)
                return
            self._write(msg)

    def _write(self, msg: dict):
        try:
            self._conn.sendall((json.dumps(msg) + "\n").encode("utf-8"))
        except Exception as exc:
            logger.debug("vt-tui send failed: %s", exc)
            self._closed = True

    def _read_loop(self):
        try:
            buf = self._conn.makefile("r", encoding="utf-8", errors="replace")
            for line in buf:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("t") == "cmd":
                    self._dispatch(msg)
        except Exception as exc:
            logger.debug("vt-tui read loop ended: %s", exc)
        finally:
            # The TUI process is gone (quit key, terminal closed, crash). If it
            # exited on its own, treat it as a request to shut the app down.
            _dbg("read_loop EOF")
            if not self._closed and self.on_quit:
                try:
                    self.on_quit()
                except Exception:
                    pass

    def _dispatch(self, msg: dict):
        cmd = msg.get("cmd")
        _dbg(f"dispatch {cmd}")
        logger.debug("vt-tui command: %s", cmd)
        if cmd == "toggle_record" and self.on_toggle_record:
            self.on_toggle_record()
        elif cmd == "change_device" and self.on_change_device:
            self.on_change_device()
        elif cmd == "toggle_mute" and self.on_toggle_mute:
            self.on_toggle_mute()
        elif cmd in ("toggle_autotype", "cycle_output_mode"):
            if getattr(self, "on_cycle_output_mode", None):
                self.on_cycle_output_mode()
            elif self.on_toggle_autotype:
                self.on_toggle_autotype()
        elif cmd == "toggle_numbers" and self.on_toggle_numbers:
            self.on_toggle_numbers()
        elif cmd == "toggle_middle_click" and self.on_toggle_middle_click:
            self.on_toggle_middle_click()
        elif cmd == "cycle_theme" and self.on_cycle_theme:
            self.on_cycle_theme()
        elif cmd == "cycle_punctuation" and getattr(self, "on_cycle_punctuation", None):
            self.on_cycle_punctuation()
        elif cmd == "reset_terminal" and self.on_reset_terminal:
            self.on_reset_terminal()
        elif cmd == "quit":
            if self.on_quit:
                self.on_quit()

    # ------------------------------------------- VoiceTranscriberTUI surface

    def print_header(self):
        self._send({"t": "header"})

    def update_state(self, state, sub_text=""):
        self.state = state
        self.sub_state_text = sub_text
        self._send({"t": "state", "state": state, "sub": sub_text})

    def update_vu_level(self, level):
        self.vu_level = level
        # The TUI only renders at ~10fps; throttle to avoid flooding the socket.
        now = time.time()
        if now - self._last_vu_sent < 0.03:
            return
        self._last_vu_sent = now
        self._send({"t": "vu", "level": float(level)})

    def set_active_device(self, device_name):
        self.active_device = device_name or "Default Microphone"
        self._send({"t": "cfg", "mic": self.active_device})

    def set_secondary_device(self, device_name):
        self.secondary_device = device_name
        self._send({"t": "cfg", "secondary": device_name})

    def set_config_state(self, backend=None, muted=None, auto_type=None, output_mode=None,
                         sound_theme=None, ui_theme=None, punctuation_mode=None):
        msg = {"t": "cfg"}
        if backend is not None:
            self.model_backend = backend
            msg["backend"] = backend
        if muted is not None:
            self.is_muted = muted
            msg["muted"] = bool(muted)
        if output_mode is not None:
            self.output_mode = output_mode
            msg["output_mode"] = str(output_mode)
            self.auto_type = (output_mode in ("type", "type_fast"))
            msg["auto_type"] = bool(self.auto_type)
        elif auto_type is not None:
            self.auto_type = auto_type
            msg["auto_type"] = bool(auto_type)
            msg["output_mode"] = "type" if auto_type else "clipboard"
        if sound_theme is not None:
            self.sound_theme = sound_theme
            msg["sound_theme"] = sound_theme
        if ui_theme is not None:
            self.ui_theme = ui_theme
            msg["ui_theme"] = ui_theme
        if punctuation_mode is not None:
            self.punctuation_mode = punctuation_mode
            msg["punctuation_mode"] = punctuation_mode
        self._send(msg)

    def get_effective_color(self):
        theme = (self.ui_theme or "auto").lower()
        if theme in COLOR_PALETTES and theme != "auto":
            return theme
        accent = os.environ.get("ACCENT_COLOR", "").strip().lower()
        if accent in COLOR_PALETTES:
            return accent
        return "green"

    def cycle_ui_theme(self):
        try:
            idx = COLOR_PALETTES.index((self.ui_theme or "auto").lower())
        except ValueError:
            idx = 0
        self.ui_theme = COLOR_PALETTES[(idx + 1) % len(COLOR_PALETTES)]
        self._send({"t": "cfg", "ui_theme": self.ui_theme})
        return self.ui_theme

    def print_transcription(self, text, elapsed_sec=0.0, copy_success=True,
                            typed_success=False, device_name=None,
                            rec_duration=0.0, proc_time=0.0):
        self.transcription_count += 1
        if typed_success:
            status = "typed"
        elif copy_success:
            status = "copied"
        else:
            status = "error"
        self._send({
            "t": "tx",
            "text": text,
            "rec": float(rec_duration or 0.0),
            "proc": float(proc_time or 0.0),
            "ready": float(elapsed_sec or 0.0),
            "status": status,
        })

    def print_event(self, title, message, level="info"):
        self._send({"t": "ev", "title": str(title), "message": str(message),
                    "level": level})

    def print_warning(self, title, message):
        self.print_event(title, message, level="warning")

    def print_error(self, title, message):
        self.print_event(title, message, level="error")

    # The engine brackets its interactive menus with these. Map them onto the
    # TUI suspending/resuming so Python can own the terminal while it draws.
    def _pause_live(self):
        _dbg("pause_live -> suspend")
        self._send({"t": "suspend"})
        self._suspended = True
        time.sleep(0.25)
        # The ratatui/crossterm event reader can leave the tty in a state where
        # the Python menu's getch() returns immediately. Clear O_NONBLOCK and
        # drop any keystrokes still queued for the TUI.
        try:
            import termios
            import fcntl
            fd = sys.stdin.fileno()
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            _dbg(f"pause_live stdin nonblock={bool(flags & os.O_NONBLOCK)} isatty={sys.stdin.isatty()}")
            if flags & os.O_NONBLOCK:
                fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
            termios.tcflush(fd, termios.TCIFLUSH)
        except Exception as exc:
            _dbg(f"pause_live prepare failed: {exc}")

    def _resume_live(self):
        _dbg("resume_live -> resume")
        with self._send_lock:
            self._suspended = False
            if self._closed or self._conn is None:
                return
            self._write({"t": "resume"})
            # Replay anything the engine emitted while the menu was open (errors,
            # state changes) so nothing is silently lost.
            queued = self._pending
            self._pending = []
            for msg in queued:
                self._write(msg)
