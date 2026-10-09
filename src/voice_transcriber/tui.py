#!/usr/bin/env python3
"""
TUI Module for Voice Transcriber - Style 1 (Branch 1): Inline Interactive Prompt / Shell Stream
Design Philosophy (Pi / Gemini CLI Style):
- Radical departure from pinned bottom footers! Functions like an interactive REPL / CLI shell prompt.
- Status bar rendered inline at the cursor prompt line.
- When transcriptions arrive, they stream directly into shell history with clean top/bottom horizontal rules.
- NO vertical left/right border lines so text copies 100% cleanly in terminal / Tmux.
"""

import sys
import os
import time
import select
import threading
try:
    import termios
    import tty
except ImportError:
    termios = None
    tty = None
import atexit
import shutil
from datetime import datetime

from rich.console import Console
from rich.live import Live
from rich.text import Text

import console_text

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
COLOR_PALETTES = ["auto", "green", "cyan", "blue", "magenta", "yellow", "red", "white"]

def detect_system_theme_color():
    """Detect system terminal accent color"""
    env_theme = os.environ.get("VT_UI_THEME", "").strip().lower()
    if env_theme in COLOR_PALETTES and env_theme != "auto":
        return env_theme

    accent = os.environ.get("ACCENT_COLOR", "").strip().lower()
    if accent in COLOR_PALETTES:
        return accent

    term_prog = os.environ.get("TERM_PROGRAM", "").lower()
    if "vscode" in term_prog:
        return "cyan"
    elif "kitty" in term_prog:
        return "magenta"
    elif "apple_terminal" in term_prog:
        return "blue"
    elif "alacritty" in term_prog:
        return "yellow"

    return "green"


def _format_duration_helper(seconds):
    try:
        from t2 import format_duration
        return format_duration(seconds)
    except Exception:
        # Half-up rounding, matching t2.format_duration and the Rust frontend.
        total_sec = max(0, int(seconds + 0.5))
        if total_sec < 60:
            return f"{total_sec}s"
        if total_sec < 3600:
            mins = total_sec // 60
            secs = total_sec % 60
            return f"{mins}m {secs:02d}s"
        hours = total_sec // 3600
        mins = (total_sec % 3600) // 60
        return f"{hours}h {mins:02d}m"


def _format_optional_duration(value):
    """Format a duration for the badge, or return ``None`` when it shows nothing.

    Accepts either a number of seconds or an already-formatted string (the
    frontends accept both). Zero, negative, empty and ``None`` values all mean
    "don't render this part of the badge".
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return _format_duration_helper(value) if value > 0 else None
    text_value = str(value).strip()
    return text_value or None


class VoiceTranscriberTUI:
    def __init__(self, app_version="1.3.1", ui_theme="auto"):
        self.app_version = app_version
        # Wrapped so a stream that cannot encode our glyphs (legacy codepage,
        # LANG=C, PYTHONIOENCODING=ascii) gets ASCII stand-ins instead of Rich
        # re-raising UnicodeEncodeError and losing the output entirely.
        #
        # ``legacy_windows=False`` is part of that guarantee, not a style choice:
        # Rich's legacy win32 renderer writes through the console handle (via
        # colorama) rather than through this file, so on a cp1252 console it
        # raised UnicodeEncodeError on glyphs like ``❯`` *despite* the wrapper.
        # Windows 10+ understands ANSI escape sequences, so routing everything
        # through the stream costs nothing there and lets an un-encodable glyph
        # degrade to ``?`` instead of taking the UI down.
        self.console = Console(
            file=console_text.EncodingSafeStream(),
            legacy_windows=False,
        )
        self.lock = threading.Lock()
        self.ui_theme = ui_theme

        # State tracking for prompt line
        self.state = "READY"  # READY, RECORDING, PROCESSING, REWRITING, CONFIG
        self.sub_state_text = ""
        self.start_time = 0.0
        self.elapsed_time = 0.0
        self.vu_level = 0.0
        self.spinner_index = 0

        # Incremental transcription counter
        self.transcription_count = 0

        # Audio & system metadata
        self.active_device = "Detecting..."
        self.secondary_device = None
        self.model_backend = "cohere"
        self.is_muted = True
        self.auto_type = False
        self.output_mode = "clipboard"
        self.trailing_space = True
        self.auto_punctuate = True
        self.number_digits = True
        self.number_mode = "auto"
        self.serial_collapse = True
        self.spell_command = True
        self.hotkeys = []
        self.middle_click_enabled = False
        self.punctuation_mode = "full"
        self.structure_mode = "off"
        self.cleanup_mode = "full"
        self.formatter = "off"
        self.formatter_model = "s1-mini"
        self.formatter_style = "semi-formal"
        self.formatter_context = "general"
        self.copy_to_clipboard = True
        self.sound_theme = "proximity"
        self.last_transcription = ""

        # Callbacks for terminal keypresses
        self.on_toggle_record = None
        self.on_change_device = None
        self.on_toggle_mute = None
        # Backend selection is config-file only (edit config/config.yaml, restart);
        # no runtime model toggle to avoid loading the other ASR backend unexpectedly.
        self.on_toggle_autotype = None
        self.on_cycle_output_mode = None
        self.on_toggle_trailing_space = None
        self.on_toggle_auto_punctuate = None
        self.on_toggle_numbers = None
        self.on_cycle_typing_wpm = None
        self.on_toggle_middle_click = None
        self.on_cycle_theme = None
        self.on_set_theme = None
        self.on_cycle_punctuation = None
        self.on_cycle_structure = None
        self.on_cycle_cleanup = None
        self.on_cycle_formatter = None
        self.on_cycle_formatter_model = None
        self.on_cycle_formatter_style = None
        self.on_cycle_formatter_context = None
        self.on_set_punctuation = None
        self.on_open_theme_picker = None
        self.on_open_preset_picker = None
        self.on_open_settings_picker = None
        self.on_open_mic_picker = None
        self.on_set_theme = None
        self.on_reset_terminal = None
        self.on_rescan_mics = None
        self.on_quit = None

        # Live display control
        self.live = None
        self.running = False
        self.stdin_thread = None
        self.old_termios = None

    def get_effective_color(self):
        """Return resolved primary color name"""
        with self.lock:
            theme = self.ui_theme.lower()
            if theme == "auto":
                return detect_system_theme_color()
            if theme in COLOR_PALETTES:
                return theme
            return "green"

    def set_ui_theme(self, theme_name):
        """Update UI color theme dynamically"""
        with self.lock:
            if theme_name.lower() in COLOR_PALETTES:
                self.ui_theme = theme_name.lower()
            else:
                self.ui_theme = "auto"
        if self.live and self.running:
            self.live.update(self._render_status_bar())

    def cycle_ui_theme(self):
        """Cycle through available UI color themes"""
        with self.lock:
            try:
                curr_idx = COLOR_PALETTES.index(self.ui_theme.lower())
                next_idx = (curr_idx + 1) % len(COLOR_PALETTES)
            except ValueError:
                next_idx = 1
            self.ui_theme = COLOR_PALETTES[next_idx]
            new_theme = self.ui_theme

        effective = self.get_effective_color()
        self.print_event("THEME SWITCHED", f"UI Color Theme set to '{new_theme}' (Active: {effective.upper()})", level="info")
        return new_theme

    def _get_term_width(self):
        """Get current terminal width dynamically for responsive borders"""
        try:
            width = self.console.width
            if width and width > 10:
                return width
        except Exception:
            pass
        return shutil.get_terminal_size((80, 24)).columns

    def set_active_device(self, device_name):
        with self.lock:
            self.active_device = device_name or "Default Microphone"
        if self.live and self.running:
            self.live.update(self._render_status_bar())

    def set_secondary_device(self, device_name):
        with self.lock:
            self.secondary_device = device_name
        if self.live and self.running:
            self.live.update(self._render_status_bar())

    def set_config_state(self, backend=None, muted=None, auto_type=None, output_mode=None, sound_theme=None, ui_theme=None, punctuation_mode=None, structure_mode=None, cleanup_mode=None, formatter=None, formatter_model=None, formatter_style=None, formatter_context=None, trailing_space=None, auto_punctuate=None, number_digits=None, number_mode=None, serial_collapse=None, spell_command=None, middle_click_enabled=None, typing_wpm=None, hotkeys=None):
        with self.lock:
            if backend is not None:
                self.model_backend = backend
            if muted is not None:
                self.is_muted = muted
            if output_mode is not None:
                self.output_mode = output_mode
                self.auto_type = (output_mode in ("type", "type_fast"))
            elif auto_type is not None:
                self.auto_type = auto_type
                self.output_mode = "type" if auto_type else "clipboard"
            if sound_theme is not None:
                self.sound_theme = sound_theme
            if ui_theme is not None:
                self.ui_theme = ui_theme
            if punctuation_mode is not None:
                self.punctuation_mode = punctuation_mode
            if structure_mode is not None:
                self.structure_mode = structure_mode
            if cleanup_mode is not None:
                self.cleanup_mode = cleanup_mode
            if formatter is not None:
                self.formatter = formatter
            if formatter_model is not None:
                self.formatter_model = formatter_model
            if formatter_style is not None:
                self.formatter_style = formatter_style
            if formatter_context is not None:
                self.formatter_context = formatter_context
            if trailing_space is not None:
                self.trailing_space = bool(trailing_space)
            if auto_punctuate is not None:
                self.auto_punctuate = bool(auto_punctuate)
            if number_mode is not None:
                self.number_mode = str(number_mode).strip().lower()
            if number_digits is not None:
                self.number_digits = bool(number_digits)
            if serial_collapse is not None:
                self.serial_collapse = bool(serial_collapse)
            if spell_command is not None:
                self.spell_command = bool(spell_command)
            if middle_click_enabled is not None:
                self.middle_click_enabled = bool(middle_click_enabled)
            if typing_wpm is not None:
                self.typing_wpm = int(typing_wpm)
            if hotkeys is not None:
                # Shared with the ratatui frontend's signature. This TUI does not
                # render the chords yet, but ``main.py`` calls whichever frontend
                # it got with the same keywords, so it must accept them — the
                # omission was a TypeError on every startup that used Rich, which
                # is the default on native Windows.
                self.hotkeys = [str(chord) for chord in hotkeys]
        if self.live and self.running:
            self.live.update(self._render_status_bar())

    def update_state(self, state, sub_text=""):
        with self.lock:
            self.state = state
            self.sub_state_text = sub_text
            if state in ["RECORDING", "PROCESSING", "REWRITING"] and self.start_time == 0:
                self.start_time = time.time()
            elif state == "READY":
                self.start_time = 0.0
                self.elapsed_time = 0.0
                self.vu_level = 0.0

    def update_vu_level(self, level):
        with self.lock:
            self.vu_level = max(level, self.vu_level * 0.7)

    def print_header(self):
        """Print CLI Prompt Session banner at start of terminal scrollback"""
        color = self.get_effective_color()
        header = Text()
        header.append("vt ", style=f"bold {color}")
        header.append("❯ ", style=f"bold bright_{color}" if color != "white" else "bold white")
        header.append(f"voice transcriber v{self.app_version} active ", style="bold white")
        header.append(f"(model: {self.model_backend.upper()} │ mic: {self.active_device})\n", style="dim white")
        self.console.print(header)

    def _render_status_bar(self):
        """
        Render Style 1: Inline Interactive Prompt Line at the cursor position.
        """
        color = self.get_effective_color()
        self.spinner_index = (self.spinner_index + 1) % len(SPINNER_FRAMES)
        spinner = SPINNER_FRAMES[self.spinner_index]

        if self.start_time > 0:
            self.elapsed_time = time.time() - self.start_time

        prompt = Text()
        prompt.append("❯ ", style=f"bold {color}")

        if self.state == "READY":
            prompt.append("ready ", style=f"bold {color}")
            prompt.append("│ ", style="dim white")

            mic_short = self.active_device
            if len(mic_short) > 18:
                mic_short = mic_short[:15] + "..."
            prompt.append(f"mic: {mic_short} ", style="cyan")
            prompt.append("│ ", style="dim white")
            prompt.append(f"model: {self.model_backend.lower()} ", style="cyan")
            prompt.append("│ ", style="dim white")

            if self.is_muted:
                prompt.append("sound: off ", style="dim red")
            else:
                prompt.append("sound: on ", style="green")
            prompt.append("│ ", style="dim white")

            out_mode = getattr(self, "output_mode", "clipboard")
            if out_mode == "type":
                prompt.append("auto-type (slow) ", style="bold green")
            elif out_mode == "type_fast":
                prompt.append("auto-type (fast) ", style="bold cyan")
            else:
                prompt.append("clipboard ", style="cyan")

            if getattr(self, "auto_type", False):
                prompt.append("│ ", style="dim white")
                if getattr(self, "trailing_space", True):
                    prompt.append("space: on ", style="green")
                else:
                    prompt.append("space: off ", style="dim white")

                prompt.append("│ ", style="dim white")
                if getattr(self, "auto_punctuate", True):
                    prompt.append("auto-punct: on ", style="green")
                else:
                    prompt.append("auto-punct: off ", style="dim white")

            prompt.append("│ ", style="dim white")
            punc_disp = {
                "no_terminal_period": "casual",
                "casual": "casual",
                "no_punctuation": "autocorrect",
                "autocorrect": "autocorrect",
                "aesthetic_lowercase": "aesthetic",
                "aesthetic": "aesthetic",
                "lowercase_no_punctuation": "gen-z",
                "gen_z": "gen-z",
            }.get(getattr(self, "punctuation_mode", "default"), "default")
            prompt.append(f"preset: {punc_disp} ", style="cyan")

            prompt.append("│ ", style="dim white")
            num_mode = getattr(self, "number_mode", None)
            if num_mode not in ("auto", "digits", "words"):
                num_mode = "digits" if getattr(self, "number_digits", True) else "words"
            num_labels = {
                "auto": ("num: auto ", "cyan"),
                "digits": ("num: digits ", "green"),
                "words": ("num: words ", "dim white"),
            }
            num_label, num_style = num_labels[num_mode]
            prompt.append(num_label, style=num_style)

            prompt.append("│ ", style="dim white")
            if getattr(self, "serial_collapse", True):
                prompt.append("codes: joined ", style="cyan")
            else:
                prompt.append("codes: spaced ", style="dim white")

            prompt.append("│ ", style="dim white")
            if getattr(self, "middle_click_enabled", False):
                prompt.append("mouse: on ", style="green")
            else:
                prompt.append("mouse: off ", style="dim white")

            prompt.append("\n  [Space] Rec (tap again = stop, hands-free)  [s/S/,] Settings  [r] Reset  [q] Quit", style="dim white")

        elif self.state == "RECORDING":
            prompt.append("RECORDING ", style="bold white on red")
            prompt.append(" ", style="reset")
            prompt.append(f"[{self.elapsed_time:04.1f}s] ", style="bold yellow")
            prompt.append("│ ", style="dim white")

            # Dynamic VU bar
            bar_len = 16
            filled_len = int(min(1.0, self.vu_level * 3.5) * bar_len)
            filled_len = max(1 if self.vu_level > 0.01 else 0, filled_len)
            empty_len = bar_len - filled_len
            vu_str = "█" * filled_len + "░" * empty_len

            if filled_len > 12:
                prompt.append(vu_str[:9], style="green")
                prompt.append(vu_str[9:13], style="yellow")
                prompt.append(vu_str[13:], style="red")
            elif filled_len > 7:
                prompt.append(vu_str[:7], style="green")
                prompt.append(vu_str[7:], style="yellow")
            else:
                prompt.append(vu_str, style="green")

            prompt.append(f" ({int(self.vu_level*100)}%) ", style="dim cyan")
            prompt.append("│ ", style="dim white")
            prompt.append("[Space] Stop · release Alt+Shift / Middle-Click if you used push-to-talk", style="dim white")

        elif self.state == "PROCESSING":
            prompt.append(f"{spinner} PROCESSING AUDIO [{self.elapsed_time:04.1f}s] ", style="bold yellow")
            prompt.append("│ ", style="dim white")
            if self.sub_state_text:
                prompt.append(f"{self.sub_state_text}", style="white")
            else:
                prompt.append(f"Transcribing stream with {self.model_backend.capitalize()}...", style="white")

        elif self.state == "REWRITING":
            prompt.append(f"🤖 {spinner} REFINING GRAMMAR [{self.elapsed_time:04.1f}s] ", style="bold magenta")
            prompt.append("│ ", style="dim white")
            prompt.append("SLM polishing text...", style="white")

        else:
            prompt.append(f"{self.state} ", style="bold white")
            if self.sub_state_text:
                prompt.append(f"│ {self.sub_state_text}", style="dim white")

        return prompt

    def print_transcription(self, text, elapsed_sec=0.0, copy_success=True, typed_success=False, device_name=None, rec_duration=0.0, proc_time=0.0, time_saved=0.0, session_time_saved=0.0, lifetime_time_saved=0.0):
        """
        Print transcription directly into interactive shell scrollback stream:
        - Top horizontal divider line with prompt tag, recording duration, processing time, and post-release latency
        - Clean word-wrapped body (NO left/right side borders)
        - Bottom horizontal divider line
        """
        self._pause_live()
        try:
            with self.lock:
                self.transcription_count += 1
                count = self.transcription_count

            w = self._get_term_width()
            color = self.get_effective_color()
            timestamp = datetime.now().strftime("%H:%M:%S")
            status_str = "Copied & Typed" if typed_success else ("Copied to Clipboard" if copy_success else "Clipboard Error")
            status_color = "green" if typed_success else ("cyan" if copy_success else "red")

            top_rule = Text()
            top_rule.append("─" * 4, style=f"bold {color}")
            top_rule.append(f" ❯ #{count} ", style=f"bold {color}")
            top_rule.append(f" {timestamp} ", style="dim white")
            if rec_duration and rec_duration > 0.0:
                top_rule.append("│ ", style="dim white")
                top_rule.append(f"rec: {rec_duration:.2f}s ", style="cyan")
            if proc_time and proc_time > 0.0:
                top_rule.append("│ ", style="dim white")
                top_rule.append(f"proc: {proc_time:.2f}s ", style="yellow")
                top_rule.append("│ ", style="dim white")
                top_rule.append(f"ready: {elapsed_sec:.2f}s ", style="bright_cyan")
            else:
                top_rule.append("│ ", style="dim white")
                top_rule.append(f"proc: {elapsed_sec:.2f}s ", style="yellow")
            if time_saved:
                if isinstance(time_saved, (int, float)):
                    saved_str = f"+{_format_duration_helper(time_saved)}" if time_saved > 0 else None
                else:
                    saved_str = str(time_saved).strip()
                    if saved_str and not saved_str.startswith("+"):
                        saved_str = f"+{saved_str}"

                if saved_str:
                    session_str = _format_optional_duration(session_time_saved)
                    lifetime_str = _format_optional_duration(lifetime_time_saved)
                    if session_str and lifetime_str and lifetime_str != session_str:
                        totals = f"(session: {session_str} · total: {lifetime_str})"
                    elif lifetime_str:
                        # Fresh session: the all-time total is the interesting number.
                        totals = f"(total: {lifetime_str})"
                    elif session_str:
                        totals = f"(session: {session_str})"
                    else:
                        totals = None

                    top_rule.append("│ ", style="dim white")
                    badge = (
                        f"⚡ saved: {saved_str} {totals} "
                        if totals
                        else f"⚡ saved: {saved_str} "
                    )
                    top_rule.append(badge, style="bold green")
            top_rule.append("│ ", style="dim white")
            top_rule.append(f"{status_str}\n", style=status_color)
            self.console.print(top_rule)

            # Transcribed text body with prompt icon indent (clean wrapped, NO vertical side borders!)
            body = Text()
            body.append(f"  {text.strip()}\n", style="bold white")
            self.console.print(body)

            # Bottom solid horizontal border
            bot_rule = Text()
            bot_rule.append("─" * w + "\n", style=f"{color}")
            self.console.print(bot_rule)

        finally:
            self._resume_live()

    def print_event(self, title, message, level="info"):
        """Print system event notice into terminal scrollback"""
        self._pause_live()
        try:
            color = self.get_effective_color()
            style_map = {"info": color, "success": color, "warning": color, "error": "red"}
            event_color = style_map.get(level, color)
            timestamp = datetime.now().strftime("%H:%M:%S")
            w = self._get_term_width()

            top = Text()
            top.append("─" * 4, style=f"bold {event_color}")
            top.append(f" ⚙️ {title} ", style=f"bold {event_color}")
            top.append(f" [{timestamp}] ", style="dim white")
            rendered_len = len(top.plain)
            right_len = max(2, w - rendered_len)
            top.append("─" * right_len + "\n", style=f"bold {event_color}")
            self.console.print(top)

            msg = Text()
            msg.append(f"  {message}\n", style="white")
            self.console.print(msg)

            bot = Text()
            bot.append("─" * w + "\n", style=f"{event_color}")
            self.console.print(bot)
        finally:
            self._resume_live()

    def print_warning(self, title, message):
        self.print_event(f"⚠️ {title}", message, level="warning")

    def print_error(self, title, message):
        self.print_event(f"❌ {title}", message, level="error")

    def _pause_live(self):
        if self.live:
            self.live.stop()

    def _resume_live(self):
        if self.live and self.running:
            self.live.start()

    def start(self):
        """Start the live refreshing TUI with prompt line"""
        if self.running:
            return

        self.running = True
        self.print_header()

        self.live = Live(
            self._render_status_bar(),
            console=self.console,
            refresh_per_second=10,
            transient=True,
            auto_refresh=True
        )
        self.live.start()
        self._start_stdin_listener()

    def stop(self):
        """Stop the live TUI and restore terminal settings"""
        self.running = False
        if self.live:
            try:
                self.live.stop()
            except Exception:
                pass
            self.live = None

        self._restore_terminal()

    def _start_stdin_listener(self):
        """Start background thread watching stdin for direct terminal keypresses"""
        try:
            if os.name == 'nt':
                self.stdin_thread = threading.Thread(target=self._stdin_loop, daemon=True)
                self.stdin_thread.start()
                return

            fd = sys.stdin.fileno()
            if os.isatty(fd) and termios and tty:
                self.old_termios = termios.tcgetattr(fd)
                tty.setcbreak(fd)
                atexit.register(self._restore_terminal)

                self.stdin_thread = threading.Thread(target=self._stdin_loop, daemon=True)
                self.stdin_thread.start()
        except Exception:
            pass

    def _restore_terminal(self):
        if self.old_termios and termios:
            try:
                fd = sys.stdin.fileno()
                termios.tcsetattr(fd, termios.TCSADRAIN, self.old_termios)
            except Exception:
                pass
            self.old_termios = None

    def _stdin_loop(self):
        """Loop listening for single terminal keypresses"""
        if os.name == 'nt':
            try:
                import msvcrt
                while self.running:
                    try:
                        if msvcrt.kbhit():
                            ch = msvcrt.getwch()
                            if ch:
                                self._handle_keypress(ch)
                        else:
                            time.sleep(0.05)
                    except Exception:
                        break
            except ImportError:
                pass
            return

        fd = sys.stdin.fileno()
        while self.running:
            try:
                r, _, _ = select.select([fd], [], [], 0.2)
                if r:
                    ch = sys.stdin.read(1)
                    if not ch:
                        break
                    self._handle_keypress(ch)
            except Exception:
                break

    def _handle_keypress(self, ch):
        """Route terminal keypresses to callbacks.

        The settings modal is the single configuration entry point: ``s``,
        ``S`` and ``,`` open it, and the microphone, theme and preset pickers
        are reached from inside it. ``Space`` / ``Enter`` toggles recording
        (tap to start, tap again to stop — hands-free/hands-latched).
        """
        if ch in [' ', '\r', '\n']:
            if self.on_toggle_record:
                self.on_toggle_record()
        elif ch in (',', 's', 'S'):
            if getattr(self, 'on_open_settings_picker', None):
                self.on_open_settings_picker()
        elif ch.lower() == 'r':
            if self.on_reset_terminal:
                self.on_reset_terminal()
        elif ch.lower() == 'q' or ch == '\x03':  # 'q' or Ctrl+C
            if self.on_quit:
                self.on_quit()
