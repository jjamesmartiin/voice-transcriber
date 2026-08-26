#!/usr/bin/env python3
"""
TUI Module for Voice Transcriber
Provides a clean, line-free terminal interface.
Eliminates decorative border/rule lines around text so copying text in the terminal window never catches line characters.
"""

import sys
import os
import time
import select
import threading
import termios
import tty
import atexit
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
        """Print clean header line into history without border/divider lines"""
        header = Text()
        header.append("Voice Transcriber ", style="bold cyan")
        header.append(f"v{self.app_version} ", style="dim cyan")
        header.append(f"│ Model: {self.model_backend.upper()} │ Mic: {self.active_device}\n", style="dim white")
        self.console.print(header)

    def _render_status_bar(self):
        """Render pinned single-line bottom status bar"""
        self.spinner_index = (self.spinner_index + 1) % len(SPINNER_FRAMES)
        spinner = SPINNER_FRAMES[self.spinner_index]
        
        if self.start_time > 0:
            self.elapsed_time = time.time() - self.start_time

        line = Text()
        
        if self.state == "READY":
            line.append("🟢 READY ", style="green")
            line.append("│ ", style="dim white")
            
            mic_short = self.active_device
            if len(mic_short) > 22:
                mic_short = mic_short[:19] + "..."
            line.append(f"Mic: {mic_short} ", style="cyan")
            line.append("│ ", style="dim white")
            
            line.append(f"Model: {self.model_backend.upper()} ", style="cyan")
            line.append("│ ", style="dim white")
            
            if self.is_muted:
                line.append("Sound: Off ", style="dim red")
            else:
                line.append("Sound: On ", style="green")
            line.append("│ ", style="dim white")

            if self.auto_type:
                line.append("Auto-Type ", style="magenta")
            else:
                line.append("Clipboard ", style="cyan")
            line.append("│ ", style="dim white")
            
            line.append("Alt+Shift to record • [Space] Rec  [i] Dev  [m] Mute  [b] Model  [q] Quit", style="dim white")

        elif self.state == "RECORDING":
            line.append("🎙️ 🔴 RECORDING ", style="bold red blink")
            line.append(f"[{self.elapsed_time:04.1f}s] ", style="yellow")
            line.append("│ ", style="dim white")
            
            # VU level meter bar (18 blocks)
            bar_len = 18
            filled_len = int(min(1.0, self.vu_level * 3.5) * bar_len)
            filled_len = max(1 if self.vu_level > 0.01 else 0, filled_len)
            empty_len = bar_len - filled_len
            vu_str = "█" * filled_len + "░" * empty_len
            
            if filled_len > 14:
                line.append(vu_str[:11], style="green")
                line.append(vu_str[11:15], style="yellow")
                line.append(vu_str[15:], style="red")
            elif filled_len > 9:
                line.append(vu_str[:9], style="green")
                line.append(vu_str[9:], style="yellow")
            else:
                line.append(vu_str, style="green")

            line.append(f" ({int(self.vu_level*100)}%) ", style="dim cyan")
            line.append("│ ", style="dim white")
            line.append("Release Alt+Shift or press Space to finish", style="dim white")

        elif self.state == "PROCESSING":
            line.append(f"{spinner} ", style="yellow")
            line.append("PROCESSING AUDIO ", style="yellow")
            line.append(f"[{self.elapsed_time:04.1f}s] ", style="dim yellow")
            line.append("│ ", style="dim white")
            if self.sub_state_text:
                line.append(f"{self.sub_state_text}", style="white")
            else:
                line.append(f"Transcribing audio stream with {self.model_backend.capitalize()}...", style="white")

        elif self.state == "REWRITING":
            line.append(f"🤖 {spinner} ", style="magenta")
            line.append("SLM RETRO-POLISHING ", style="magenta")
            line.append(f"[{self.elapsed_time:04.1f}s] ", style="dim magenta")
            line.append("│ ", style="dim white")
            line.append("Refining grammar and formatting...", style="white")

        else:
            line.append(f"⚙️ {self.state} ", style="white")
            if self.sub_state_text:
                line.append(f"│ {self.sub_state_text}", style="dim white")

        return line

    def print_transcription(self, text, elapsed_sec=0.0, copy_success=True, typed_success=False, device_name=None):
        """
        Print completed transcription without decorative border lines:
        - [#15] Timestamp (1.24s)  Copied & Typed
        - Clean word-wrapped text (no border lines to interfere with copying)
        """
        self._pause_live()
        try:
            with self.lock:
                self.transcription_count += 1
                count = self.transcription_count

            timestamp = datetime.now().strftime("%H:%M:%S")
            
            header = Text()
            header.append(f"[#{count}] ", style="bold cyan")
            header.append(f"{timestamp} ", style="dim white")
            header.append(f"({elapsed_sec:.2f}s)  ", style="dim yellow")
            
            if typed_success:
                header.append("Copied & Typed", style="green")
            elif copy_success:
                header.append("Copied to Clipboard", style="cyan")
            else:
                header.append("Clipboard Error", style="red")

            self.console.print(header)

            # Full word-wrapped text (clean, no border lines)
            body = Text()
            body.append(f"{text.strip()}\n", style="bold white")
            self.console.print(body)
        finally:
            self._resume_live()

    def print_event(self, title, message, level="info"):
        """Print system event notice in clean line-free text style"""
        self._pause_live()
        try:
            style_map = {
                "info": "cyan",
                "success": "green",
                "warning": "yellow",
                "error": "red"
            }
            color = style_map.get(level, "cyan")
            timestamp = datetime.now().strftime("%H:%M:%S")
            
            text = Text()
            text.append(f"⚙️  {title} ", style=f"bold {color}")
            text.append(f"[{timestamp}]\n", style="dim white")
            text.append(f"{message}\n", style="white")
            
            self.console.print(text)
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
        """Start the live refreshing TUI with clean line-free status bar"""
        if self.running:
            return
        
        self.running = True
        
        # Initial clean header line
        self.print_header()

        # Create Rich Live display pinned at bottom
        self.live = Live(
            self._render_status_bar(),
            console=self.console,
            refresh_per_second=10,
            transient=True,
            auto_refresh=True
        )
        self.live.start()
        
        # Start terminal stdin keypress listener
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
