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
        self.number_mode = "auto"
        self.number_digits = True
        self.serial_collapse = True
        self.spell_command = True
        self.transcription_count = 0
        self.vu_level = 0.0
        self.hotkeys = []

        # Callbacks (wired by main.SimpleVoiceTranscriber).
        self.on_toggle_record = None
        self.on_change_device = None
        self.on_toggle_mute = None
        self.on_toggle_autotype = None
        self.on_cycle_output_mode = None
        self.on_toggle_trailing_space = None
        self.on_toggle_auto_punctuate = None
        self.on_toggle_numbers = None
        self.on_toggle_serial_collapse = None
        self.on_toggle_spell_command = None
        self.on_cycle_typing_wpm = None
        self.on_set_typing_wpm = None
        self.on_toggle_middle_click = None
        self.on_hotkey_add = None
        self.on_hotkey_remove = None
        self.on_cycle_theme = None
        self.on_set_theme = None
        self.on_cycle_punctuation = None
        self.on_set_punctuation = None
        self.on_reset_defaults = None
        self.on_reset_terminal = None
        self.on_rescan_mics = None
        self.on_quit = None

        # --- Mic monitoring: one live level stream per visible picker row ---
        self._mic_monitor_streams: dict[int, object] = {}
        self._mic_levels: dict[int, float] = {}

        # --- IPC plumbing ---
        self._server = None
        self._conn = None
        self._proc = None
        self._pending: list[dict] = []
        self._send_lock = threading.Lock()
        # Guards the VU send-throttle timestamp. One PortAudio callback thread
        # runs per monitored device, so several can race the check at once.
        self._vu_lock = threading.Lock()
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
            raise RuntimeError("vt-tui did not connect within 10s") from None
        finally:
            self._server.settimeout(None)

        # Flush anything queued before the child connected.
        with self._send_lock:
            for msg in self._pending:
                self._write(msg)
            self._pending.clear()

        self.print_header()
        self.send_device_list()

        self._orig_stdout = sys.stdout
        self._devnull = open(os.devnull, "w")
        sys.stdout = self._devnull

        threading.Thread(target=self._read_loop, daemon=True).start()
        logger.info("Ratatui TUI connected on %s", self.socket_path)

    def stop(self):
        """Tear down the child process and socket."""
        self.stop_mic_monitor()
        if hasattr(self, "_orig_stdout") and self._orig_stdout:
            sys.stdout = self._orig_stdout
        if hasattr(self, "_devnull") and self._devnull:
            try:
                self._devnull.close()
            except Exception:
                pass
            self._devnull = None
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
        elif cmd == "toggle_serial_collapse" and getattr(self, "on_toggle_serial_collapse", None):
            self.on_toggle_serial_collapse()
        elif cmd == "toggle_spell_command" and getattr(self, "on_toggle_spell_command", None):
            self.on_toggle_spell_command()
        elif cmd == "cycle_typing_wpm" and getattr(self, "on_cycle_typing_wpm", None):
            self.on_cycle_typing_wpm()
        elif cmd == "set_typing_wpm":
            wpm = msg.get("wpm") or msg.get("value")
            if wpm is not None and getattr(self, "on_set_typing_wpm", None):
                self.on_set_typing_wpm(wpm)
            elif getattr(self, "on_cycle_typing_wpm", None):
                self.on_cycle_typing_wpm()
        elif cmd == "hotkey_add" and getattr(self, "on_hotkey_add", None):
            chord = msg.get("chord") or msg.get("value") or ""
            if str(chord).strip():
                self.on_hotkey_add(str(chord).strip())
        elif cmd == "hotkey_remove" and getattr(self, "on_hotkey_remove", None):
            chord = msg.get("chord") or msg.get("value") or ""
            if str(chord).strip():
                self.on_hotkey_remove(str(chord).strip())
        elif cmd == "toggle_middle_click" and self.on_toggle_middle_click:
            self.on_toggle_middle_click()
        elif cmd == "toggle_trailing_space" and getattr(self, "on_toggle_trailing_space", None):
            self.on_toggle_trailing_space()
        elif cmd == "toggle_auto_punctuate" and getattr(self, "on_toggle_auto_punctuate", None):
            self.on_toggle_auto_punctuate()
        elif cmd == "cycle_theme" and self.on_cycle_theme:
            self.on_cycle_theme()
        elif cmd == "set_theme":
            theme = msg.get("theme")
            if theme and getattr(self, "on_set_theme", None):
                self.on_set_theme(theme)
            elif self.on_cycle_theme:
                self.on_cycle_theme()
        elif cmd in ("cycle_punctuation", "cycle_preset") and getattr(self, "on_cycle_punctuation", None):
            self.on_cycle_punctuation()
        elif cmd in ("set_punctuation", "set_preset"):
            mode = msg.get("mode") or msg.get("preset") or msg.get("value")
            if mode and getattr(self, "on_set_punctuation", None):
                self.on_set_punctuation(mode)
            elif getattr(self, "on_cycle_punctuation", None):
                self.on_cycle_punctuation()
        elif cmd == "open_preset_picker" and getattr(self, "on_open_preset_picker", None):
            self.on_open_preset_picker()
        elif cmd == "reset_defaults" and getattr(self, "on_reset_defaults", None):
            self.on_reset_defaults()
        elif cmd == "reset_terminal" and self.on_reset_terminal:
            self.on_reset_terminal()
        elif cmd == "rescan_mics" and self.on_rescan_mics:
            self.on_rescan_mics()
        elif cmd == "get_devices":
            self.send_device_list()
        elif cmd == "start_mic_monitor":
            # ``indices`` (a list) is what the picker sends so every visible row
            # meters at once; ``index`` is the older single-device form.
            indices = msg.get("indices")
            if indices is None:
                indices = msg.get("index")
            self.start_mic_monitor(indices)
        elif cmd == "stop_mic_monitor":
            self.stop_mic_monitor()
        elif cmd == "set_device":
            name = msg.get("device")
            idx = msg.get("index")
            if name:
                import t2
                t2.PRIMARY_DEVICE_NAME = name
                if idx is not None:
                    try:
                        t2.INPUT_DEVICE_INDEX = int(idx)
                        t2.set_default_input_device(t2.INPUT_DEVICE_INDEX)
                    except Exception:
                        pass
                t2.save_audio_config()
                self.set_active_device(name)
                self.print_event("🎤 Microphone Updated", f"Active microphone set to: {name}", level="success")
        elif cmd == "quit":
            if self.on_quit:
                self.on_quit()

    # ------------------------------------------- VoiceTranscriberTUI surface

    def print_header(self):
        self._send({"t": "header"})

    def send_device_list(self):
        try:
            import t2
            devs = t2.get_input_devices()
            self._send({"t": "devices", "devices": devs})
        except Exception as e:
            logger.debug(f"Failed to send devices: {e}")

    # ------------------------------------------------------- mic monitoring

    def start_mic_monitor(self, indices=None):
        """Open live level streams for ``indices`` (the picker's visible rows).

        Streams for devices that are no longer requested are closed and new ones
        are opened, so scrolling or filtering the picker diffs the set instead of
        tearing every device down and re-opening it on each keystroke.

        Platform notes (see docs/TODO.md, "Multi-device mic levels"):

        * **Linux** — verified end-to-end in a real terminal.
        * **WSL** — *unconfirmed*, but shares this exact code path. Audio arrives
          through WSLg's PulseAudio server, which usually exposes a single real
          capture source (``RDPSource``), so every visible row will most likely
          report the *same* level. That is the expected outcome there, not a bug
          in the per-device plumbing — the meters are still correct, just not
          informative. If a WSL user reports identical bars, check this first.
        * **Native Windows** — never reaches here: ``tui_available()`` requires
          ``socket.AF_UNIX``, which stock CPython does not expose on Windows, so
          the Rich TUI is used and this class is never constructed.
        * **macOS** — expected to work (nothing below branches on platform) but
          likewise unverified; a denied microphone permission fails every
          stream, which the per-device skip degrades to flat bars, not an error.
        """
        try:
            import sounddevice as sd
        except Exception as e:
            logger.debug(f"start_mic_monitor failed: {e}")
            return

        if indices is None:
            import t2
            indices = [t2.INPUT_DEVICE_INDEX] if t2.INPUT_DEVICE_INDEX is not None else []
        elif isinstance(indices, (int, str)):
            indices = [indices]

        wanted: list[int] = []
        for idx in indices:
            try:
                idx = int(idx)
            except (TypeError, ValueError):
                continue
            if idx not in wanted:
                wanted.append(idx)

        streams = getattr(self, "_mic_monitor_streams", None)
        if streams is None:
            streams = {}
            self._mic_monitor_streams = streams
        if getattr(self, "_mic_levels", None) is None:
            self._mic_levels = {}

        for idx in [i for i in streams if i not in wanted]:
            self._release_monitor_stream(streams.pop(idx))
        for idx in wanted:
            if idx not in streams:
                stream = self._open_monitor_stream(sd, idx)
                if stream is not None:
                    streams[idx] = stream

        for idx in streams:
            self._mic_levels.setdefault(idx, 0.0)
        for idx in [i for i in self._mic_levels if i not in streams]:
            del self._mic_levels[idx]

        # Push immediately so a newly opened device shows 0% rather than the
        # level the previous occupant of that row left behind. With nothing
        # monitored (e.g. a filter that matches no device) send an explicit
        # empty snapshot so the frontend clears its stale bars instead of
        # keeping the last frame's levels.
        if streams:
            self._push_vu_levels(force=True)
        else:
            self.update_vu_level(0.0, levels={}, force=True)

    def _open_monitor_stream(self, sd, device_idx):
        """Open one capture stream; returns None if the device will not cooperate.

        A device that another app holds exclusively, or that rejects our rate,
        must not take the whole picker down with it.
        """
        import numpy as np

        rate = self._pick_monitor_rate(sd, device_idx)
        if rate is None:
            return None

        def audio_callback(indata, frames, time_info, status):
            # A stream can deliver one last block after it was released, so only
            # record levels for devices that are still part of the picker view.
            if device_idx not in self._mic_monitor_streams:
                return
            rms = float(np.sqrt(np.mean(indata ** 2)))
            self._mic_levels[device_idx] = min(1.0, max(0.0, rms * 8.0))
            self._push_vu_levels()

        try:
            stream = sd.InputStream(
                device=device_idx,
                channels=1,
                samplerate=rate,
                blocksize=max(1, int(rate * 0.05)),
                callback=audio_callback,
            )
            stream.start()
            return stream
        except Exception as e:
            logger.debug(f"mic monitor: device {device_idx} at {rate}Hz failed: {e}")
            return None

    @staticmethod
    def _pick_monitor_rate(sd, device_idx):
        """Sample rate to open ``device_idx`` at, or None if it takes none.

        Asking first (`Pa_IsFormatSupported`) matters for more than tidiness: a
        *failed* ``InputStream`` open makes PortAudio write ALSA errors straight
        to stderr, which is the same terminal the TUI is drawing on, so it
        corrupts the picker's rendering. Prefer 16 kHz (the ASR path), then
        whatever the device natively runs at -- raw ALSA ``hw:`` devices
        routinely reject 16 kHz.
        """
        try:
            native = int(sd.query_devices(device_idx).get("default_samplerate", 16000) or 16000)
        except Exception:
            native = 16000

        candidates = []
        for rate in (16000, native):
            if rate > 0 and rate not in candidates:
                candidates.append(rate)

        for rate in candidates:
            try:
                sd.check_input_settings(device=device_idx, channels=1, samplerate=rate)
                return rate
            except Exception as e:
                logger.debug(f"mic monitor: device {device_idx} refuses {rate}Hz: {e}")
        return None

    @staticmethod
    def _release_monitor_stream(stream):
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass

    def stop_mic_monitor(self):
        """Close every live level stream and blank the per-device meters."""
        streams = getattr(self, "_mic_monitor_streams", None) or {}
        for idx in list(streams):
            self._release_monitor_stream(streams.pop(idx))
        self._mic_monitor_streams = {}
        self._mic_levels = {}
        self.update_vu_level(0.0, levels={}, force=True)

    def _push_vu_levels(self, force=False):
        """Send the current per-device snapshot, throttled like the scalar VU."""
        levels = dict(self._mic_levels)
        if not levels:
            return
        self.update_vu_level(max(levels.values()), levels=levels, force=force)

    def update_state(self, state, sub_text=""):
        self.state = state
        self.sub_state_text = sub_text
        self._send({"t": "state", "state": state, "sub": sub_text})

    def update_vu_level(self, level, levels=None, force=False):
        """Publish a level update to the frontend.

        ``level`` is the scalar meter used by the main screen (and by recording);
        ``levels`` optionally carries one entry per monitored device for the mic
        picker's per-row bars.
        """
        self.vu_level = level
        # The TUI only renders at ~10fps and every monitored device ticks this,
        # so gate the send under a lock: without it several mic callbacks read a
        # stale timestamp at once and all send.
        with self._vu_lock:
            now = time.time()
            if not force and now - self._last_vu_sent < 0.03:
                return
            self._last_vu_sent = now
        msg = {"t": "vu", "level": float(level)}
        if levels is not None:
            msg["levels"] = [
                {"i": int(i), "level": float(v)} for i, v in sorted(levels.items())
            ]
        self._send(msg)

    def set_active_device(self, device_name):
        self.active_device = device_name or "Default Microphone"
        self._send({"t": "cfg", "mic": self.active_device})

    def set_secondary_device(self, device_name):
        self.secondary_device = device_name
        self._send({"t": "cfg", "secondary": device_name})

    def set_config_state(self, backend=None, muted=None, auto_type=None, output_mode=None,
                         sound_theme=None, ui_theme=None, punctuation_mode=None,
                         trailing_space=None, auto_punctuate=None, number_digits=None,
                         number_mode=None, serial_collapse=None, spell_command=None,
                         middle_click_enabled=None, typing_wpm=None, hotkeys=None):
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
        if trailing_space is not None:
            self.trailing_space = bool(trailing_space)
            msg["trailing_space"] = self.trailing_space
        if auto_punctuate is not None:
            self.auto_punctuate = bool(auto_punctuate)
            msg["auto_punctuate"] = self.auto_punctuate
        if number_mode is not None:
            mode = str(number_mode).strip().lower()
            self.number_mode = mode
            msg["number_mode"] = mode
            # The Rust renderer also accepts a boolean fallback; auto means on.
            if number_digits is None:
                number_digits = mode != "words"
        if number_digits is not None:
            self.number_digits = bool(number_digits)
            msg["number_digits"] = self.number_digits
        if serial_collapse is not None:
            self.serial_collapse = bool(serial_collapse)
            msg["serial_collapse"] = self.serial_collapse
        if spell_command is not None:
            self.spell_command = bool(spell_command)
            msg["spell_command"] = self.spell_command
        if middle_click_enabled is not None:
            self.middle_click_enabled = bool(middle_click_enabled)
            msg["middle_click_enabled"] = self.middle_click_enabled
        if typing_wpm is not None:
            msg["typing_wpm"] = int(typing_wpm)
        if hotkeys is not None:
            # Canonical chord spellings, e.g. ["alt+shift", "f13"]; the frontend
            # only ever displays them and asks the engine to add/remove.
            self.hotkeys = [str(chord) for chord in hotkeys]
            msg["hotkeys"] = self.hotkeys
        self._send(msg)

    def get_effective_color(self):
        theme = (self.ui_theme or "auto").lower()
        if theme in COLOR_PALETTES and theme != "auto":
            return theme
        accent = os.environ.get("ACCENT_COLOR", "").strip().lower()
        if accent in COLOR_PALETTES:
            return accent
        return "green"

    def set_ui_theme(self, theme_name):
        """Update UI color theme dynamically"""
        if (theme_name or "").lower() in COLOR_PALETTES:
            self.ui_theme = theme_name.lower()
        else:
            self.ui_theme = "auto"
        self._send({"t": "cfg", "ui_theme": self.ui_theme})
        effective = self.get_effective_color()
        self.print_event("THEME SWITCHED", f"UI Color Theme set to '{self.ui_theme}' (Active: {effective.upper()})", level="info")

    def cycle_ui_theme(self):
        try:
            idx = COLOR_PALETTES.index((self.ui_theme or "auto").lower())
        except ValueError:
            idx = 0
        self.ui_theme = COLOR_PALETTES[(idx + 1) % len(COLOR_PALETTES)]
        self._send({"t": "cfg", "ui_theme": self.ui_theme})
        effective = self.get_effective_color()
        self.print_event("THEME SWITCHED", f"UI Color Theme set to '{self.ui_theme}' (Active: {effective.upper()})", level="info")
        return self.ui_theme

    def print_transcription(self, text, elapsed_sec=0.0, copy_success=True,
                            typed_success=False, device_name=None,
                            rec_duration=0.0, proc_time=0.0,
                            time_saved=0.0, session_time_saved=0.0,
                            lifetime_time_saved=0.0):
        self.transcription_count += 1
        if typed_success:
            status = "typed"
        elif copy_success:
            status = "copied"
        else:
            status = "error"
        payload = {
            "t": "tx",
            "text": text,
            "rec": float(rec_duration or 0.0),
            "proc": float(proc_time or 0.0),
            "ready": float(elapsed_sec or 0.0),
            "status": status,
        }
        if time_saved is not None:
            try:
                payload["time_saved"] = float(time_saved)
            except (ValueError, TypeError):
                pass
        if session_time_saved is not None:
            try:
                payload["session_time_saved"] = float(session_time_saved)
            except (ValueError, TypeError):
                pass
        if lifetime_time_saved is not None:
            try:
                payload["lifetime_time_saved"] = float(lifetime_time_saved)
            except (ValueError, TypeError):
                pass
        self._send(payload)

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
        if hasattr(self, "_orig_stdout") and self._orig_stdout:
            sys.stdout = self._orig_stdout
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
        if hasattr(self, "_devnull") and self._devnull and not self._devnull.closed:
            sys.stdout = self._devnull
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
