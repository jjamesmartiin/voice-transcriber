#!/usr/bin/env python3
"""
TUI Module for Voice Transcriber - Design C: Tmux Green & Blue Session Dashboard Style
Provides a native Tmux session status bar aesthetic:
- Solid horizontal top line defining the bottom status bar styled in classic Tmux green & dark grey
- Window session tabs ([0] 0:vt*) with host, timestamp, and active mode widgets
- Dynamic terminal width adjustment
- Unlimited scrollback terminal history (pi / gemini CLI style)
- Clean horizontal divider rules with header metadata between transcriptions
- NO vertical left/right border lines so text copies cleanly
"""

import sys
import os
import socket
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

class VoiceTranscriberTUI:
    def __init__(self, app_version="1.0.0"):
        self.app_version = app_version
        self.console = Console()
        self.lock = threading.Lock()
        
        # System hostname for tmux status right widget
        try:
            self.hostname = socket.gethostname()
        except Exception:
            self.hostname = "localhost"

        # State tracking for bottom status bar
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
        self.on_toggle_backend = None
        self.on_toggle_autotype = None
        self.on_reset_terminal = None
        self.on_quit = None
        
        # Live display control
        self.live = None
        self.running = False
        self.stdin_thread = None
        self.old_termios = None
        
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
            
    def set_secondary_device(self, device_name):
        with self.lock:
            self.secondary_device = device_name
            
    def set_config_state(self, backend=None, muted=None, auto_type=None, sound_theme=None):
        with self.lock:
            if backend is not None:
                self.model_backend = backend
            if muted is not None:
                self.is_muted = muted
            if auto_type is not None:
                self.auto_type = auto_type
            if sound_theme is not None:
                self.sound_theme = sound_theme

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
        """Print Tmux Session header at start of terminal scrollback"""
        w = self._get_term_width()
        rule = "─" * w
        
        header = Text()
        header.append(f"{rule}\n", style="bold green")
        header.append(" [0] 0:voice-transcriber* ", style="bold black on green")
        header.append(f" (v{self.app_version}) ", style="bold white on grey23")
        header.append(f" │ Model: {self.model_backend.upper()} │ Mic: {self.active_device}\n", style="dim white")
        header.append(f"{rule}\n", style="bold green")
        self.console.print(header)

    def _render_status_bar(self):
        """
        Render Tmux Status Bar pinned at bottom of terminal.
        Line 1: Solid green divider line extending full width of terminal.
        Line 2: Tmux status bar with session badge, status widgets, host and keyhints.
        """
        w = self._get_term_width()
        self.spinner_index = (self.spinner_index + 1) % len(SPINNER_FRAMES)
        spinner = SPINNER_FRAMES[self.spinner_index]
        
        if self.start_time > 0:
            self.elapsed_time = time.time() - self.start_time

        bar = Text()
        
        # Solid line defining the status bar
        bar.append("─" * w + "\n", style="bold green")

        # Left Section: Tmux Session Window Tab
        bar.append("[0] 0:vt* ", style="bold black on green")
        bar.append(" ", style="reset")

        # Middle Section: Status details & active mode
        if self.state == "READY":
            bar.append("[READY] ", style="bold black on bright_green")
            bar.append(" ", style="reset")
            
            mic_short = self.active_device
            if len(mic_short) > 18:
                mic_short = mic_short[:15] + "..."
            bar.append(f"mic: {mic_short} ", style="cyan")
            bar.append("│ ", style="dim green")
            bar.append(f"model: {self.model_backend.lower()} ", style="cyan")
            bar.append("│ ", style="dim green")
            
            if self.is_muted:
                bar.append("sound: off ", style="dim red")
            else:
                bar.append("sound: on ", style="green")
            bar.append("│ ", style="dim green")

            if self.auto_type:
                bar.append("auto-type ", style="magenta")
            else:
                bar.append("clipboard ", style="cyan")
            bar.append("│ ", style="dim green")
            bar.append("[Space] Rec  [i] Mic  [m] Mute  [b] Model  [q] Quit", style="dim white")

        elif self.state == "RECORDING":
            bar.append(" [REC] ", style="bold black on bright_red")
            bar.append(" ", style="reset")
            bar.append(f"{self.elapsed_time:04.1f}s ", style="bold yellow")
            bar.append("│ ", style="dim green")
            
            # Dynamic VU bar
            bar_len = 14
            filled_len = int(min(1.0, self.vu_level * 3.5) * bar_len)
            filled_len = max(1 if self.vu_level > 0.01 else 0, filled_len)
            empty_len = bar_len - filled_len
            vu_str = "█" * filled_len + "░" * empty_len
            
            if filled_len > 10:
                bar.append(vu_str[:8], style="green")
                bar.append(vu_str[8:11], style="yellow")
                bar.append(vu_str[11:], style="red")
            elif filled_len > 6:
                bar.append(vu_str[:6], style="green")
                bar.append(vu_str[6:], style="yellow")
            else:
                bar.append(vu_str, style="green")

            bar.append(f" ({int(self.vu_level*100)}%) ", style="dim cyan")
            bar.append("│ ", style="dim green")
            bar.append("Release Alt+Shift or Space to stop", style="dim white")

        elif self.state == "PROCESSING":
            bar.append(" [PROC] ", style="bold black on yellow")
            bar.append(" ", style="reset")
            bar.append(f"{spinner} {self.elapsed_time:04.1f}s ", style="yellow")
            bar.append("│ ", style="dim green")
            if self.sub_state_text:
                bar.append(f"{self.sub_state_text}", style="white")
            else:
                bar.append(f"transcribing audio stream...", style="white")

        elif self.state == "REWRITING":
            bar.append(" [REWRITE] ", style="bold white on magenta")
            bar.append(" ", style="reset")
            bar.append(f"🤖 {spinner} {self.elapsed_time:04.1f}s ", style="magenta")
            bar.append("│ ", style="dim green")
            bar.append("slm grammar polishing...", style="white")

        else:
            bar.append(f" [{self.state}] ", style="bold white on grey23")
            bar.append(" ", style="reset")
            if self.sub_state_text:
                bar.append(f"│ {self.sub_state_text}", style="dim white")

        return bar

    def print_transcription(self, text, elapsed_sec=0.0, copy_success=True, typed_success=False, device_name=None):
        """
        Print transcription into terminal scrollback history:
        - Solid horizontal divider line across full terminal width
        - Header line: [transcribe #1] HH:MM:SS (1.24s) -- Status: Copied
        - Clean word-wrapped text (NO left/right side borders)
        - Bottom solid horizontal divider line
        """
        self._pause_live()
        try:
            with self.lock:
                self.transcription_count += 1
                count = self.transcription_count

            w = self._get_term_width()
            timestamp = datetime.now().strftime("%H:%M:%S")
            status_str = "Copied & Typed" if typed_success else ("Copied to Clipboard" if copy_success else "Clipboard Error")
            status_color = "green" if typed_success else ("cyan" if copy_success else "red")
            
            top_rule = Text()
            top_rule.append("─" * w + "\n", style="bold green")
            self.console.print(top_rule)

            header = Text()
            header.append(f"[transcribe #{count}] ", style="bold green")
            header.append(f"{timestamp} ", style="dim white")
            header.append(f"({elapsed_sec:.2f}s) ", style="dim yellow")
            header.append("── Status: ", style="dim green")
            header.append(f"{status_str}\n", style=status_color)
            self.console.print(header)

            # Transcribed text body (clean wrapped, NO vertical side borders!)
            body = Text()
            body.append(f"{text.strip()}\n", style="bold white")
            self.console.print(body)

            # Bottom solid horizontal border
            bot_rule = Text()
            bot_rule.append("─" * w + "\n", style="green")
            self.console.print(bot_rule)

        finally:
            self._resume_live()

    def print_event(self, title, message, level="info"):
        """Print system event notice into terminal scrollback"""
        self._pause_live()
        try:
            style_map = {"info": "cyan", "success": "green", "warning": "yellow", "error": "red"}
            color = style_map.get(level, "cyan")
            timestamp = datetime.now().strftime("%H:%M:%S")
            w = self._get_term_width()
            
            top = Text()
            top.append("─" * w + "\n", style=color)
            self.console.print(top)

            hdr = Text()
            hdr.append(f"[event] {title} ", style=f"bold {color}")
            hdr.append(f"[{timestamp}]\n", style="dim white")
            self.console.print(hdr)

            msg = Text()
            msg.append(f"{message}\n", style="white")
            self.console.print(msg)

            bot = Text()
            bot.append("─" * w + "\n", style=color)
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
        """Start the live refreshing TUI with Tmux status bar"""
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
        elif ch.lower() == 'i':
            if self.on_change_device:
                self.on_change_device()
        elif ch.lower() == 'm':
            if self.on_toggle_mute:
                self.on_toggle_mute()
        elif ch.lower() == 'b':
            if self.on_toggle_backend:
                self.on_toggle_backend()
        elif ch.lower() == 't':
            if self.on_toggle_autotype:
                self.on_toggle_autotype()
        elif ch.lower() == 'r':
            if self.on_reset_terminal:
                self.on_reset_terminal()
        elif ch.lower() == 'q' or ch == '\x03':  # 'q' or Ctrl+C
            if self.on_quit:
                self.on_quit()
