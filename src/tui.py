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
import termios
import tty
import atexit
import shutil
from datetime import datetime

from rich.console import Console
from rich.live import Live
from rich.text import Text

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


class VoiceTranscriberTUI:
    def __init__(self, app_version="1.0.3", ui_theme="auto"):
        self.app_version = app_version
        self.console = Console()
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
        self.on_toggle_numbers = None
        self.on_toggle_middle_click = None
        self.on_cycle_theme = None
        self.on_reset_terminal = None
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
            
    def set_config_state(self, backend=None, muted=None, auto_type=None, sound_theme=None, ui_theme=None, punctuation_mode=None):
        with self.lock:
            if backend is not None:
                self.model_backend = backend
            if muted is not None:
                self.is_muted = muted
            if auto_type is not None:
                self.auto_type = auto_type
            if sound_theme is not None:
                self.sound_theme = sound_theme
            if ui_theme is not None:
                self.ui_theme = ui_theme
            if punctuation_mode is not None:
                self.punctuation_mode = punctuation_mode
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

            if self.auto_type:
                prompt.append("auto-type ", style="magenta")
            else:
                prompt.append("clipboard ", style="cyan")
            prompt.append("│ ", style="dim white")
            prompt.append("[Space] Rec  [M] Mic  [m] Mute  [n] Numbers  [o] Mouse  [c] Clipboard  [t] Theme  [q] Quit", style="dim white")

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
            prompt.append("Release Alt+Shift or Middle Click to finish · Space while holding = hands-free", style="dim white")

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

    def print_transcription(self, text, elapsed_sec=0.0, copy_success=True, typed_success=False, device_name=None, rec_duration=0.0, proc_time=0.0):
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
            top_rule.append("│ ", style="dim white")
            top_rule.append(f"{status_str} ", style=status_color)
            rendered_len = len(top_rule.plain)
            right_len = max(2, w - rendered_len)
            top_rule.append("─" * right_len + "\n", style=f"bold {color}")
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
            style_map = {"info": color, "success": "green", "warning": "yellow", "error": "red"}
            event_color = style_map.get(level, color)
            timestamp = datetime.now().strftime("%H:%M:%S")
            w = self._get_term_width()
            
            top = Text()
            top.append("─" * 4, style=event_color)
            top.append(f" ⚙️ {title} ", style=f"bold {event_color}")
            top.append(f" [{timestamp}] ", style="dim white")
            right_len = max(0, w - 16 - len(title))
            top.append("─" * right_len + "\n", style=event_color)
            self.console.print(top)

            msg = Text()
            msg.append(f"  {message}\n", style="white")
            self.console.print(msg)

            bot = Text()
            bot.append("─" * w + "\n", style=event_color)
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
            fd = sys.stdin.fileno()
            if os.isatty(fd):
                self.old_termios = termios.tcgetattr(fd)
                tty.setcbreak(fd)
                atexit.register(self._restore_terminal)
                
                self.stdin_thread = threading.Thread(target=self._stdin_loop, daemon=True)
                self.stdin_thread.start()
        except Exception:
            pass

    def _restore_terminal(self):
        if self.old_termios:
            try:
                fd = sys.stdin.fileno()
                termios.tcsetattr(fd, termios.TCSADRAIN, self.old_termios)
            except Exception:
                pass
            self.old_termios = None

    def _stdin_loop(self):
        """Loop listening for single terminal keypresses"""
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
        """Route terminal keypresses to callbacks"""
        if ch in [' ', '\r', '\n']:
            if self.on_toggle_record:
                self.on_toggle_record()
        elif ch in ['M', 'i', 'I']:
            if self.on_change_device:
                self.on_change_device()
        elif ch == 'm':
            if self.on_toggle_mute:
                self.on_toggle_mute()
        elif ch.lower() == 'c':
            if self.on_toggle_autotype:
                self.on_toggle_autotype()
        elif ch.lower() == 't':
            if self.on_cycle_theme:
                self.on_cycle_theme()
            else:
                self.cycle_ui_theme()
        elif ch.lower() == 'n':
            if self.on_toggle_numbers:
                self.on_toggle_numbers()
        elif ch.lower() == 'o':
            if self.on_toggle_middle_click:
                self.on_toggle_middle_click()
        elif ch.lower() == 'r':
            if self.on_reset_terminal:
                self.on_reset_terminal()
        elif ch.lower() == 'q' or ch == '\x03':  # 'q' or Ctrl+C
            if self.on_quit:
                self.on_quit()
