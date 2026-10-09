#!/usr/bin/env python3
"""
Voice Transcriber main entry point and runtime orchestrator.
Cross-platform voice dictation with global hotkeys, streaming VAD, and instant text injection.
"""
import logging
import threading
import time
import os
import sys

# Put the ``src/`` directory on sys.path so the compatibility shims there resolve
# bare imports like ``import t2``. Deliberately the *shim* directory, not this
# package's own directory: adding ``src/voice_transcriber`` let a bare
# ``import hal`` load ``voice_transcriber/hal.py`` a second time under the plain
# name ``hal``, so one file became two module objects whose ``isinstance`` and
# ``except`` checks silently missed across the pair. See docs/TODO.md.
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

# Configure logging before importing the app modules so that their import-time
# messages are captured as well. Writes a per-user log file in addition to the
# console (see src/logging_setup.py); LOG_FILE is None if the file is disabled.
import logging_setup
LOG_FILE = logging_setup.configure_logging()

# Import core modules
import hal
import control
import stats
import console_text
from tui import VoiceTranscriberTUI

# Import transcription functionality.
#
# Deliberately no ``sys.path`` mutation here. This file's own directory is
# already on the path: Python puts a script's directory at ``sys.path[0]``, and
# the Nix wrapper puts ``share/vt`` on ``PYTHONPATH``. Appending
# ``voice_transcriber/`` as well let a later bare ``import hal`` load
# ``voice_transcriber/hal.py`` a *second* time under the plain name ``hal``, so
# one file became two module objects and ``isinstance``/``except`` checks
# silently missed across the pair. See the note in docs/TODO.md.
import t2
from t2 import (
    preload_model, DEVICE, record_audio_stream, process_audio_stream,
    stop_recording, load_audio_config, get_active_device_name
)

logger = logging.getLogger(__name__)


def create_tui():
    """Pick a TUI frontend.

    Prefer the ratatui (Rust) frontend when the binary is available, otherwise
    fall back to the built-in Rich TUI. Force Rich with ``VT_TUI=rich``.
    """
    prefer = os.environ.get("VT_TUI", "").strip().lower()
    if prefer != "rich":
        binary = os.environ.get("VT_TUI_BIN", "")
        try:
            from tui_ratatui import RatatuiTui, tui_available
            if tui_available(binary):
                logger.info("Using ratatui frontend: %s", binary)
                return RatatuiTui(binary)
            logger.info("ratatui frontend unavailable (VT_TUI_BIN=%r)", binary)
        except Exception as e:
            logger.warning("ratatui frontend unavailable, using Rich TUI: %s", e)
    return VoiceTranscriberTUI()

def copy_to_clipboard_crossplatform(text, sink=None):
    """Copy text to the platform clipboard via the HAL.

    Falls back to ``pyperclip`` if the platform sink is unavailable. Note: on
    Wayland, ``wl-copy`` may block until a window receives focus.
    """
    try:
        if (sink or hal.get_clipboard_sink()).copy_text(text):
            return True
    except Exception as e:
        logger.debug(f"HAL clipboard copy failed, falling back to pyperclip: {e}")

    try:
        import pyperclip

        pyperclip.copy(text)
        return True
    except Exception:
        return False

class SimpleVoiceTranscriber:
    def __init__(self):
        self.recording = False
        self.record_thread = None
        self.process_thread = None
        self.hotkey_system = None
        self.running = False
        self.audio_frames = []
        self.copy_to_clipboard = False
        self.start_time = 0

        # Cumulative session stats for time saved tracking
        self.session_words = 0
        self.session_time_saved_sec = 0.0
        self.session_transcriptions = 0

        # Lifetime stats (persisted across sessions by src/stats.py).
        # Counted once per engine launch; the transcription totals are folded
        # in as dictations complete.
        self.lifetime_words = 0
        self.lifetime_time_saved_sec = 0.0
        self.lifetime_transcriptions = 0
        self.lifetime_sessions = 0
        self._refresh_lifetime_stats(record_session=True)

        # Initialize the TUI frontend (ratatui if available, else Rich).
        self.tui = create_tui()
        self._wire_tui_callbacks()

        # Detect the host platform once and select HAL backends from it.
        self.platform = hal.detect_platform()
        self.clipboard_sink = hal.get_clipboard_sink(self.platform)
        self.audio_cues = hal.get_audio_cue_player(self.platform)

        # Load saved audio device configuration FIRST before starting TUI live display
        load_audio_config()
        self._sync_tui_state()
        self._safe_tui_start()

        # Preload model in background with live loading spinner animation
        from t2 import MODEL_BACKEND
        self.tui.update_state("PROCESSING", f"Loading {MODEL_BACKEND.capitalize()} model weights...")
        self.preload_thread = preload_model(device=DEVICE)

        # Non-blocking model-load tracking: the app must stay fully responsive
        # even when loading the model stalls (e.g. slow/flaky connection to
        # huggingface.co). Recording may start immediately; transcription waits
        # in the background for the load to finish (or fail).
        self._model_ready_event = threading.Event()
        self.model_load_error = None
        self._load_started_at = time.time()

        # Proactively check microphone health on startup. A *muted* default
        # source lands here too: the device is present, opens fine, and records
        # silence, so reporting it as missing hardware would be a lie.
        is_healthy, mic_issues = t2.check_microphone_health()
        if not is_healthy:
            platform_name = self.platform.upper()
            warn_msg = f"⚠️ MICROPHONE PROBLEM DETECTED ({platform_name})!\n" + "\n".join([f" • {issue}" for issue in mic_issues])
            self.tui.print_warning("MICROPHONE WARNING", warn_msg)

        # Initialize visual notification (platform-appropriate backend)
        self.visual_notification = hal.get_visual_notification(
            app_name="Voice Transcriber", tui=self.tui
        )
        self.visual_notification.set_active_device(get_active_device_name())

        # State tracking for SLM On-Demand Quick-Tap retro-polishing
        self.last_transcription = ""
        self.last_finish_time = 0.0

        # Surface first-run model-download progress (GitHub release assets) in the TUI.
        try:
            import model_download
            model_download.set_status_handler(self._on_model_download_status)
        except Exception:
            pass

        # Watch the background model load without ever blocking: the watcher sets
        # _model_ready_event and flips the TUI to READY once loading finishes (or
        # fails). Started at construction so every entry point (interactive app,
        # tests) shares the same non-blocking behavior.
        self._start_model_load_watcher()

        # Out-of-process control API (see control.py). Started here -- after
        # every attribute a control verb touches exists, but BEFORE the
        # model-ready join below, so the socket comes up in ~1s instead of
        # waiting out a slow first-run model load. Best-effort: a bind conflict
        # or a platform without AF_UNIX must never stop the app.
        self.control_server = control.ControlServer(self)
        if self.control_server.start():
            logger.debug("Control API listening on %s", self.control_server.socket_path)
        else:
            logger.debug("Control API unavailable (no AF_UNIX or socket bind failed)")

        # If configured to wait on startup (default True), ensure the model is
        # 100% loaded and warmed up BEFORE initializing hotkeys so the user's very
        # first keypress transcribes instantly without waiting.
        if getattr(t2, 'WAIT_FOR_MODEL_ON_STARTUP', True):
            if self.preload_thread and self.preload_thread.is_alive():
                self.preload_thread.join(timeout=20.0)
                if self.preload_thread.is_alive():
                    logger.warning(
                        "Model still loading after 20s; registering hotkeys anyway "
                        "(the first dictation will wait for the model)."
                    )

        # Initialize global hotkey system after model is ready
        self.init_hotkeys()

    def _safe_tui_start(self):
        """Start the selected TUI; if the ratatui frontend fails, fall back to Rich.

        The fallback is guarded too. A TUI that cannot start at all — a console
        whose encoding rejects the UI's glyphs, an unusable stdout — used to
        propagate out of ``__init__`` and exit 1 with a traceback the user could
        not act on. A frontend that cannot start is a problem to report, not a
        reason to crash: the engine and the control API still work.
        """
        try:
            self.tui.start()
            return
        except Exception as e:
            logger.warning("ratatui frontend failed to start (%s); using Rich TUI", e)
            try:
                self.tui.stop()
            except Exception:
                pass

        from tui import VoiceTranscriberTUI

        self.tui = VoiceTranscriberTUI()
        self._wire_tui_callbacks()
        self._sync_tui_state()
        try:
            self.tui.start()
        except Exception as e:
            logger.error("Could not start any terminal UI: %s", e)
            console_text.safe_print(
                f"✗ Voice Transcriber could not start its terminal UI "
                f"({type(e).__name__}: {e}).",
                "  The engine is running and still reachable over the control API.",
                "  To fix the UI, try a UTF-8 terminal: `chcp 65001` on Windows, or",
                "  `VT_ASCII=1` to replace the box-drawing and emoji glyphs with ASCII.",
            )

    def _sync_tui_state(self):
        """Sync t2 configuration state with TUI badges and visual notification"""
        import t2
        from voice_transcriber import keybinds
        self.tui.set_active_device(get_active_device_name(include_model=False))
        self.tui.set_secondary_device(t2.SECONDARY_DEVICE_NAME)
        self.tui.set_config_state(
            backend=t2.MODEL_BACKEND,
            muted=t2.IS_MUTED,
            auto_type=t2.AUTO_TYPE,
            output_mode=getattr(t2, 'OUTPUT_MODE', 'clipboard'),
            sound_theme=t2.SOUND_THEME,
            ui_theme=getattr(t2, 'UI_THEME', 'auto'),
            punctuation_mode=getattr(t2, 'PUNCTUATION_MODE', 'full'),
            structure_mode=getattr(t2, 'get_structure_mode', lambda: 'off')(),
            cleanup_mode=getattr(t2, 'get_cleanup_mode', lambda: 'full')(),
            formatter=getattr(t2, 'get_formatter', lambda: 'off')(),
            formatter_model=getattr(t2, 'get_formatter_model', lambda: 's1-mini')(),
            formatter_style=getattr(t2, 'get_formatter_style', lambda: 'semi-formal')(),
            formatter_context=getattr(t2, 'get_formatter_context', lambda: 'general')(),
            trailing_space=getattr(t2, 'AUTO_TYPE_TRAILING_SPACE', True),
            auto_punctuate=getattr(t2, 'AUTO_TYPE_AUTO_PUNCTUATE', True),
            number_digits=getattr(t2, 'NUMBER_DIGITS', True),
            number_mode=getattr(t2, 'NUMBER_MODE', 'auto'),
            serial_collapse=getattr(t2, 'SERIAL_COLLAPSE', True),
            spell_command=getattr(t2, 'SPELL_COMMAND', True),
            middle_click_enabled=getattr(t2, 'MIDDLE_CLICK_ENABLED', False),
            typing_wpm=getattr(t2, 'TYPING_WPM', 40),
            hotkeys=[
                b.chord for b in keybinds.parse_binds(getattr(t2, 'HOTKEY_BINDS', None))
            ],
        )
        if hasattr(self, 'visual_notification') and self.visual_notification:
            self.visual_notification.set_active_device(get_active_device_name(include_model=False))

    def _wire_tui_callbacks(self):
        """Wire direct terminal keyboard shortcuts from TUI"""
        self.tui.on_toggle_record = self._on_tui_toggle_record
        self.tui.on_toggle_mute = self._on_tui_toggle_mute
        self.tui.on_toggle_autotype = self._on_tui_cycle_output_mode
        self.tui.on_cycle_output_mode = self._on_tui_cycle_output_mode
        self.tui.on_toggle_trailing_space = self._on_tui_toggle_trailing_space
        self.tui.on_toggle_auto_punctuate = self._on_tui_toggle_auto_punctuate
        self.tui.on_toggle_numbers = self._on_tui_toggle_numbers
        self.tui.on_toggle_serial_collapse = self._on_tui_toggle_serial_collapse
        self.tui.on_toggle_spell_command = self._on_tui_toggle_spell_command
        self.tui.on_cycle_typing_wpm = self._on_tui_cycle_typing_wpm
        self.tui.on_set_typing_wpm = self._on_tui_set_typing_wpm
        self.tui.on_toggle_middle_click = self._on_tui_toggle_middle_click
        self.tui.on_hotkey_add = self._on_tui_hotkey_add
        self.tui.on_hotkey_remove = self._on_tui_hotkey_remove
        self.tui.on_cycle_punctuation = self._on_tui_cycle_punctuation_mode
        self.tui.on_set_punctuation = self._on_tui_set_punctuation_mode
        self.tui.on_cycle_structure = self._on_tui_cycle_structure_mode
        self.tui.on_cycle_cleanup = self._on_tui_cycle_cleanup_mode
        self.tui.on_cycle_formatter = self._on_tui_cycle_formatter
        self.tui.on_cycle_formatter_model = self._on_tui_cycle_formatter_model
        self.tui.on_cycle_formatter_style = self._on_tui_cycle_formatter_style
        self.tui.on_cycle_formatter_context = self._on_tui_cycle_formatter_context
        self.tui.on_reset_defaults = self._on_tui_reset_defaults
        self.tui.on_open_preset_picker = self.open_preset_picker
        self.tui.on_cycle_theme = self._on_tui_cycle_theme
        self.tui.on_set_theme = self._on_tui_set_theme
        self.tui.on_open_theme_picker = self.open_theme_picker
        self.tui.on_open_settings_picker = self.open_settings_picker
        self.tui.on_open_mic_picker = self.open_mic_picker
        self.tui.on_reset_terminal = self._on_tui_reset_terminal
        self.tui.on_rescan_mics = self._on_tui_rescan_mics
        self.tui.on_quit = self._on_tui_quit

    def _on_tui_toggle_record(self):
        # While the Alt+Shift hotkey is physically held, Space is the global
        # "hold the recording" (hands-free latch) signal, not a terminal toggle.
        if self.hotkey_system and getattr(self.hotkey_system, 'is_hotkey_pressed', None) and self.hotkey_system.is_hotkey_pressed():
            return
        if self.recording:
            self.stop_recording()
        else:
            self.start_recording()

    def _preload_threads(self):
        """Return every model-preload thread that must finish before transcribing."""
        import t2 as _t2mod
        threads = []
        if getattr(self, 'preload_thread', None) is not None:
            threads.append(self.preload_thread)
        active = getattr(_t2mod, 'active_preload_thread', None)
        if active is not None and active not in threads:
            threads.append(active)
        return threads

    def _start_model_load_watcher(self):
        """Spawn a daemon watcher that updates the UI once the model finishes loading."""
        threading.Thread(target=self._watch_model_load, daemon=True).start()

    def _on_model_download_status(self, stage, pct, text):
        """Show first-run model-download progress in the TUI prompt line.

        Never clobbers the state of an in-progress recording/processing.
        """
        if self.recording:
            return
        process = getattr(self, 'process_thread', None)
        if process is not None and process.is_alive():
            return
        try:
            self.tui.update_state("PROCESSING", text)
        except Exception:
            pass

    def _watch_model_load(self):
        """Wait for the background model preload to finish, then update the UI.

        Runs on its own daemon thread so the main/hotkey loops never block on a
        slow or failed model load (e.g. a stalled connection to huggingface.co).
        """
        try:
            for t in self._preload_threads():
                if t is not None:
                    t.join()
        except Exception as e:
            logger.debug(f"Model-load watcher error: {e}")

        # Only a load that actually produced a model counts as success.
        try:
            import transcribe2
            backend_mod = transcribe2.get_backend()  # already imported by the preload
            loaded = getattr(backend_mod, '_model', None) is not None
        except Exception:
            loaded = False
        if not loaded:
            self.model_load_error = (
                "model weights failed to load (check your connection to "
                "huggingface.co, then restart the app to retry)"
            )
        self._model_ready_event.set()

        # Don't clobber the UI state of an in-progress recording/processing; the
        # recording path will surface the outcome (success or error) on its own.
        busy = bool(getattr(self, 'recording', False)) or (
            getattr(self, 'process_thread', None) is not None and self.process_thread.is_alive()
        )
        if busy:
            logger.info("Model finished loading while a recording was active.")
            return
        try:
            self.visual_notification.hide_notification()  # also flips TUI state to READY
        except Exception:
            pass
        try:
            import t2 as _t2mod
            elapsed = time.time() - getattr(self, '_load_started_at', time.time())
            if self.model_load_error:
                self.tui.update_state("READY", "Model load failed")
                self.tui.print_error("Model Load Failed", self.model_load_error)
            else:
                self.tui.update_state("READY")
                self.tui.print_event("✅ Model Ready",
                    f"{str(getattr(_t2mod, 'MODEL_BACKEND', 'model')).capitalize()} model loaded in {elapsed:.1f}s — ready to transcribe.")
        except Exception as e:
            logger.debug(f"Model-load watcher UI update error: {e}")

    def _on_tui_toggle_mute(self):
        import t2
        t2.IS_MUTED = not t2.IS_MUTED
        t2.save_audio_config()
        self._sync_tui_state()
        status = "MUTED" if t2.IS_MUTED else "SOUND ENABLED"
        self.tui.print_event("🔊 Sound Toggle", f"Sound effects are now {status}", level="info")

    def _on_tui_cycle_output_mode(self):
        import t2
        new_mode = t2.cycle_output_mode()
        t2.save_audio_config()
        self._sync_tui_state()
        labels = {
            "clipboard": "CLIPBOARD ONLY",
            "type": "AUTO-TYPE (SLOW/SAFE)",
            "type_fast": "AUTO-TYPE (FAST/OPTIMIZED)",
        }
        self.tui.print_event("📋 Output Mode", f"Output mode set to {labels.get(new_mode, new_mode.upper())}", level="info")
        if new_mode in ("type", "type_fast") and getattr(t2, 'AUTO_TYPE_AUTO_PUNCTUATE', True):
            self.tui.print_event("✍️ Formatting Mode", "Punctuation mode automatically set to Full Punctuation", level="info")

    _on_tui_toggle_autotype = _on_tui_cycle_output_mode

    def _on_tui_toggle_trailing_space(self):
        import t2
        new_state = t2.toggle_auto_type_trailing_space()
        t2.save_audio_config()
        self._sync_tui_state()
        status = "ENABLED (Appends ' ')" if new_state else "DISABLED (Exact Text)"
        self.tui.print_event("␣ Trailing Space", f"Auto-type trailing space is now {status}", level="info")

    def _on_tui_toggle_auto_punctuate(self):
        import t2
        new_state = t2.toggle_auto_type_auto_punctuate()
        t2.save_audio_config()
        self._sync_tui_state()
        status = "ENABLED (Full + Period)" if new_state else "DISABLED (Preserve User Punctuation)"
        self.tui.print_event("✍️ Auto-Punctuate", f"Auto-type full punctuation enforcement is now {status}", level="info")

    def _on_tui_toggle_numbers(self):
        import t2
        mode = t2.cycle_number_mode()
        t2.save_audio_config()
        self._sync_tui_state()
        status = {
            "auto": "AUTO (consecutive numbers only)",
            "digits": "ALL DIGITS",
            "words": "WORDS ONLY",
        }.get(mode, mode.upper())
        self.tui.print_event("🔢 Number Conversion", f"Number formatting is now {status}", level="info")

    def _on_tui_toggle_serial_collapse(self):
        import t2
        enabled = t2.toggle_serial_collapse()
        t2.save_audio_config()
        self._sync_tui_state()
        status = "COLLAPSED (ABC123)" if enabled else "SPACED (A B C 1 2 3)"
        self.tui.print_event("🔤 Serial/Codes", f"Serial number & code formatting is now {status}", level="info")

    def _on_tui_toggle_spell_command(self):
        import t2
        enabled = t2.toggle_spell_command()
        t2.save_audio_config()
        self._sync_tui_state()
        status = "ENABLED (say 'spell C A T')" if enabled else "DISABLED"
        self.tui.print_event("✍️ Spell Command", f"Verbal spell command is now {status}", level="info")

    def _on_tui_cycle_typing_wpm(self):
        import t2
        new_wpm = t2.cycle_typing_wpm()
        t2.save_audio_config()
        self._sync_tui_state()
        self.tui.print_event("⚡ Typing Speed", f"Typing speed is now {new_wpm} WPM", level="info")

    def _on_tui_set_typing_wpm(self, wpm):
        import t2
        try:
            val = int(wpm)
            if val > 0:
                t2.set_typing_wpm(val)
                t2.save_audio_config()
                self._sync_tui_state()
                self.tui.print_event("⚡ Typing Speed", f"Typing speed is now {val} WPM", level="info")
        except (ValueError, TypeError):
            pass

    def _on_tui_toggle_middle_click(self):
        import t2
        t2.set_middle_click_enabled(not t2.MIDDLE_CLICK_ENABLED)
        t2.save_audio_config()
        if self.hotkey_system and hasattr(self.hotkey_system, "set_middle_click_enabled"):
            self.hotkey_system.set_middle_click_enabled(t2.MIDDLE_CLICK_ENABLED)
        self._sync_tui_state()
        status = "ENABLED" if t2.MIDDLE_CLICK_ENABLED else "DISABLED"
        self.tui.print_event("🖱️ Mouse Hotkey", f"Middle click hold mode is now {status}", level="info")

    def _push_binds_at_hotkeys(self):
        """Hand the current binds to the live manager, not just to ``t2``.

        ``t2.set_hotkey_binds`` reaches a manager created by
        ``hotkeys.create_global_hotkeys`` via the shim; this engine builds its
        manager straight from the HAL, so a rebind has to be pushed explicitly.
        """
        import t2

        if self.hotkey_system and hasattr(self.hotkey_system, "set_binds"):
            self.hotkey_system.set_binds(t2.HOTKEY_BINDS)

    def _on_tui_hotkey_add(self, chord):
        """Bind one more chord to dictation (settings modal -> Hotkeys)."""
        import t2
        from voice_transcriber import keybinds

        try:
            current = keybinds.parse_binds(t2.HOTKEY_BINDS)
            keys = keybinds.parse_chord(chord)
            if any(bind.keys == keys for bind in current):
                self.tui.print_event(
                    "⌨️ Hotkeys",
                    f"{keybinds.format_chord(keys)} is already bound",
                    level="warning",
                )
                return
            t2.set_hotkey_binds(
                current + [keybinds.Bind(keys, keybinds.DEFAULT_ACTION)]
            )
            self._push_binds_at_hotkeys()
            t2.save_audio_config()
            self._sync_tui_state()
            self.tui.print_event(
                "⌨️ Hotkeys",
                f"Bound {keybinds.format_chord(keys)} to dictation",
                level="info",
            )
        except keybinds.KeybindError as e:
            self.tui.print_event("⌨️ Hotkeys", str(e), level="error")

    def _on_tui_hotkey_remove(self, chord):
        """Drop one chord from dictation (settings modal -> Hotkeys)."""
        import t2
        from voice_transcriber import keybinds

        try:
            current = keybinds.parse_binds(t2.HOTKEY_BINDS)
            keys = keybinds.parse_chord(chord)
            remaining = [bind for bind in current if bind.keys != keys]
            if len(remaining) == len(current):
                self.tui.print_event(
                    "⌨️ Hotkeys",
                    f"{keybinds.format_chord(keys)} is not bound",
                    level="warning",
                )
                return
            if not remaining:
                # Keeps push-to-talk reachable from the keyboard: the last bind
                # can only be *changed*, never removed down to nothing.
                self.tui.print_event(
                    "⌨️ Hotkeys",
                    "At least one bind is required; add another first",
                    level="warning",
                )
                return
            t2.set_hotkey_binds(remaining)
            self._push_binds_at_hotkeys()
            t2.save_audio_config()
            self._sync_tui_state()
            self.tui.print_event(
                "⌨️ Hotkeys",
                f"Unbound {keybinds.format_chord(keys)}",
                level="info",
            )
        except keybinds.KeybindError as e:
            self.tui.print_event("⌨️ Hotkeys", str(e), level="error")

    def _on_tui_cycle_punctuation_mode(self):
        import t2
        new_mode = t2.cycle_punctuation_mode()
        t2.save_audio_config()
        self._sync_tui_state()
        disp = t2.get_preset_display_name(new_mode)
        self.tui.print_event("✨ Mode Preset", f"Preset set to {disp}", level="info")

    def _on_tui_set_punctuation_mode(self, new_mode):
        import t2
        t2.set_punctuation_mode(new_mode)
        t2.save_audio_config()
        self._sync_tui_state()
        disp = t2.get_preset_display_name(new_mode)
        self.tui.print_event("✨ Mode Preset", f"Active preset set to {disp}", level="success")

    def _on_tui_cycle_structure_mode(self):
        import t2
        new_mode = t2.toggle_structure_mode()
        self._sync_tui_state()
        effective = t2.get_effective_structure_mode()
        detail = new_mode
        if effective != new_mode:
            # Say why the setting is not what is in force, rather than silently
            # typing something other than what the modal shows.
            detail = f"{new_mode} (typed text uses {effective} — a newline is an Enter keypress)"
        self.tui.print_event("📋 List Formatting", f"Structure mode: {detail}", level="info")

    def _on_tui_cycle_cleanup_mode(self):
        import t2
        new_mode = t2.toggle_cleanup_mode()
        self._sync_tui_state()
        self.tui.print_event("🧹 Cleanup Mode", f"Cleanup set to {new_mode}", level="info")

    def _on_tui_cycle_formatter(self):
        import t2
        new_mode = t2.toggle_formatter()
        self._sync_tui_state()
        if new_mode == "on":
            # Turning it on is the one case where the setting can be inert, so say
            # so here rather than letting it look like the feature is broken.
            message = "Rewrites the transcript locally"
            if t2.get_effective_formatter() != "on":
                message += " — inactive while cleanup mode is off"
        else:
            message = "Off: deterministic cleanup only"
        self.tui.print_event("🪄 Formatter", message, level="info")

    def _on_tui_cycle_formatter_model(self):
        import t2
        new_model = t2.cycle_formatter_model()
        self._sync_tui_state()
        self.tui.print_event("🧠 Formatter Backend", f"Using {new_model}", level="info")

    def _on_tui_cycle_formatter_style(self):
        import t2
        new_style = t2.cycle_formatter_style()
        self._sync_tui_state()
        self.tui.print_event("✍️ Formatter Style", f"Writing style: {new_style}", level="info")

    def _on_tui_cycle_formatter_context(self):
        import t2
        new_context = t2.cycle_formatter_context()
        self._sync_tui_state()
        self.tui.print_event("✉️ Formatter Context", f"Context: {new_context}", level="info")

    def _on_tui_reset_defaults(self):
        """Restore every user-tunable setting to its shipped default."""
        import t2
        if getattr(self, 'recording', False):
            self.tui.print_warning("Settings Locked", "Cannot change settings while recording is active.")
            return
        t2.reset_to_defaults()
        # Re-apply the settings that are mirrored into hardware-facing objects.
        hotkeys = getattr(self, 'hotkey_system', None)
        if hotkeys:
            if hasattr(hotkeys, "set_middle_click_enabled"):
                hotkeys.set_middle_click_enabled(getattr(t2, 'MIDDLE_CLICK_ENABLED', False))
            if hasattr(hotkeys, "set_sound_theme"):
                hotkeys.set_sound_theme(getattr(t2, 'SOUND_THEME', 'proximity'))
        audio_cues = getattr(self, 'audio_cues', None)
        if audio_cues and hasattr(audio_cues, "set_sound_theme"):
            audio_cues.set_sound_theme(getattr(t2, 'SOUND_THEME', 'proximity'))
        self._sync_tui_state()
        self.tui.print_event(
            "↩️ Reset to Defaults",
            "All settings restored to their shipped defaults. Microphone choice and dictionary kept.",
            level="success",
        )

    def open_preset_picker(self):
        """Open interactive mode preset picker modal"""
        if self.recording:
            if hasattr(self, "tui") and self.tui:
                self.tui.print_warning("Settings Locked", "Cannot change settings while recording is active.")
            return

        if hasattr(self, "tui") and self.tui:
            self.tui._pause_live()

        try:
            import t2
            chosen = t2.select_preset_picker(getattr(t2, "PUNCTUATION_MODE", "full"))
            t2.reset_terminal()
            if chosen:
                t2.set_punctuation_mode(chosen)
                t2.save_audio_config()
                self._sync_tui_state()
                disp = t2.get_preset_display_name(chosen)
                self.tui.print_event("✨ Mode Preset", f"Active preset set to {disp}", level="success")
        except Exception as e:
            logger.debug(f"Preset picker error: {e}")
            import t2
            t2.reset_terminal()

        self._sync_tui_state()
        if hasattr(self, "tui") and self.tui:
            self.tui._resume_live()
            self.tui.update_state("READY")

    def _on_tui_cycle_theme(self):
        import t2
        new_theme = self.tui.cycle_ui_theme()
        t2.UI_THEME = new_theme
        t2.save_audio_config()
        self._sync_tui_state()

    def _on_tui_set_theme(self, new_theme):
        import t2
        self.tui.set_ui_theme(new_theme)
        t2.UI_THEME = new_theme
        t2.save_audio_config()
        self._sync_tui_state()

    def open_theme_picker(self):
        """Open interactive theme picker modal"""
        if self.recording:
            if hasattr(self, 'tui') and self.tui:
                self.tui.print_warning("Settings Locked", "Cannot change settings while recording is active.")
            return

        if hasattr(self, 'tui') and self.tui:
            self.tui._pause_live()

        try:
            import t2
            chosen = t2.select_theme_picker(getattr(t2, 'UI_THEME', 'auto'))
            t2.reset_terminal()
            if chosen:
                self.tui.set_ui_theme(chosen)
                t2.UI_THEME = chosen
                t2.save_audio_config()
                self._sync_tui_state()
        except Exception as e:
            logger.debug(f"Theme picker error: {e}")
            import t2
            t2.reset_terminal()

        self._sync_tui_state()
        if hasattr(self, 'tui') and self.tui:
            self.tui._resume_live()
            self.tui.update_state("READY")

    def open_settings_picker(self):
        """Open interactive settings & configuration modal"""
        if self.recording:
            if hasattr(self, 'tui') and self.tui:
                self.tui.print_warning("Settings Locked", "Cannot change settings while recording is active.")
            return

        if hasattr(self, 'tui') and self.tui:
            self.tui._pause_live()

        try:
            import t2
            if t2.select_settings_picker():
                if hasattr(self, 'tui') and self.tui:
                    self.tui.print_event("⚙️ Configuration", "Settings updated successfully!", level="success")
            t2.reset_terminal()
            if hasattr(self, 'visual_notification') and self.visual_notification:
                self.visual_notification.set_active_device(get_active_device_name(include_model=False))
        except Exception as e:
            if hasattr(self, 'tui') and self.tui:
                self.tui.print_error("Configuration Error", str(e))
            _dbg_path = os.environ.get("VT_TUI_DEBUG")
            if _dbg_path:
                try:
                    import traceback
                    with open(_dbg_path, "a", encoding="utf-8") as _f:
                        _f.write("open_settings_picker error: " + repr(e) + "\n")
                        _f.write(traceback.format_exc())
                except OSError:
                    pass
            import t2
            t2.reset_terminal()

        self._sync_tui_state()
        if hasattr(self, 'tui') and self.tui:
            self.tui._resume_live()
            self.tui.update_state("READY")

    def open_mic_picker(self):
        """Open interactive microphone picker modal"""
        if self.recording:
            if hasattr(self, 'tui') and self.tui:
                self.tui.print_warning("Settings Locked", "Cannot change settings while recording is active.")
            return

        if hasattr(self, 'tui') and self.tui:
            self.tui._pause_live()

        try:
            import t2
            chosen = t2.select_microphone_picker()
            if chosen:
                if hasattr(self, 'tui') and self.tui:
                    self.tui.print_event("🎤 Microphone", f"Active microphone set to: {chosen}", level="success")
                if hasattr(self, 'visual_notification') and self.visual_notification:
                    self.visual_notification.set_active_device(chosen)
            t2.reset_terminal()
        except Exception as e:
            if hasattr(self, 'tui') and self.tui:
                self.tui.print_error("Microphone Selection Error", str(e))
            _dbg_path = os.environ.get("VT_TUI_DEBUG")
            if _dbg_path:
                try:
                    import traceback
                    with open(_dbg_path, "a", encoding="utf-8") as _f:
                        _f.write("open_mic_picker error: " + repr(e) + "\n")
                        _f.write(traceback.format_exc())
                except OSError:
                    pass
            import t2
            t2.reset_terminal()

        self._sync_tui_state()
        if hasattr(self, 'tui') and self.tui:
            self.tui._resume_live()
            self.tui.update_state("READY")

    def _on_tui_rescan_mics(self):
        """Re-enumerate audio devices and report what came back.

        PortAudio builds its device list once, at startup, and leaves out any
        device it cannot open at that moment -- so a mic another app was holding
        then is missing from the picker until the engine restarts. This makes
        that recoverable from the settings modal (and from `rescan-mics`)
        instead of by quitting the app.
        """
        import t2
        if getattr(self, 'recording', False):
            self.tui.print_warning(
                "Settings Locked",
                "Cannot re-scan audio devices while recording is active.",
            )
            return {
                "ok": False, "count": 0, "devices": [], "device": None,
                "missing": [], "notice": "", "message": "recording in progress",
            }

        summary = t2.rescan_audio_devices()
        self._sync_tui_state()
        if summary.get("notice"):
            self.tui.print_warning(
                "🎙️ Microphones",
                f"{summary['message']}. Close what is using it and re-scan again.",
            )
        else:
            self.tui.print_event(
                "🎙️ Microphones",
                summary.get("message", "Audio devices re-scanned"),
                level="success" if summary.get("ok") else "error",
            )
        return summary

    def _on_tui_reset_terminal(self):
        import t2
        t2.reset_terminal()
        self._sync_tui_state()
        self.tui.print_event("🔄 Terminal Reset", "Terminal state and clipboard bridge reset successfully.", level="info")

    def _on_tui_quit(self):
        self.cleanup()
        # The quit command arrives on the TUI reader thread; sys.exit() there
        # would only end that thread, so terminate the whole process.
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        os._exit(0)

    def cleanup(self):
        """Clean up all resources."""
        if getattr(self, 'control_server', None):
            self.control_server.stop()
        if hasattr(self, 'tui') and self.tui:
            self.tui.stop()
        if hasattr(self, 'visual_notification'):
            self.visual_notification.cleanup()
        if hasattr(self, 'hotkey_system') and self.hotkey_system:
            self.hotkey_system.cleanup()

    def init_hotkeys(self):
        """Initialize the global hotkey system via the HAL."""
        import t2

        try:
            self.hotkey_system = hal.create_hotkey_manager(
                platform=self.platform,
                callback_start=self.start_recording,
                callback_stop=self.stop_recording,
                binds=getattr(t2, "HOTKEY_BINDS", None),
            )

            if self.hotkey_system.devices:
                logger.debug("Global hotkey system initialized")
                theme = getattr(t2, 'SOUND_THEME', None)
                if theme:
                    if hasattr(self.hotkey_system, 'set_sound_theme'):
                        self.hotkey_system.set_sound_theme(theme)
                    if hasattr(self.audio_cues, 'set_sound_theme'):
                        self.audio_cues.set_sound_theme(theme)
                if hasattr(self.hotkey_system, 'set_middle_click_enabled'):
                    self.hotkey_system.set_middle_click_enabled(getattr(t2, 'MIDDLE_CLICK_ENABLED', False))
                # WSL forwards earcons through the same bridge process.
                if hasattr(self.audio_cues, 'set_bridge'):
                    self.audio_cues.set_bridge(self.hotkey_system)
                if hasattr(self.clipboard_sink, 'set_bridge'):
                    self.clipboard_sink.set_bridge(self.hotkey_system)
                return True
            else:
                logger.error("Failed to initialize global hotkey system")
                return False

        except Exception as e:
            logger.error(f"Error initializing hotkeys: {e}")
            return False

    def start_recording(self):
        """Start recording audio"""
        if self.recording:
            return

        # NEVER block on the model here: audio capture is independent of the ASR
        # weights. If the model is still loading (slow first load, flaky network),
        # recording starts right away and transcription waits for the load in a
        # background thread (_watch_model_load flips the UI to READY when done).
        # This keeps the hotkeys and TUI fully responsive during the load.
        if not self._model_ready_event.is_set():
            self.tui.print_event("⏳ Model Loading",
                "Model is still loading — recording now; it will transcribe automatically once the model is ready.")

        self.recording = True
        self.start_time = time.time()
        stop_recording.clear()
        self.audio_frames = []

        # Start streaming micro-batcher
        from micro_batcher import StreamingMicroBatcher
        self.micro_batcher = StreamingMicroBatcher(sample_rate=16000, tui=self.tui)
        self.micro_batcher.start()

        # Start recording in background thread IMMEDIATELY
        self.record_thread = threading.Thread(target=self.record_audio)
        self.record_thread.daemon = True
        self.record_thread.start()

        # Update notification
        self.visual_notification.show_recording()

        # Play the platform start earcon (WSL forwards to the Windows host).
        try:
            if not t2.IS_MUTED:
                self.audio_cues.play_cue("start")
        except Exception:
            pass

    def stop_recording(self, copy_to_clipboard=False):
        """Stop recording and start processing"""
        if not self.recording:
            return

        self.release_time = time.time()
        self.recording = False
        self.copy_to_clipboard = copy_to_clipboard
        stop_recording.set()

        # Start processing in a separate thread, joining the recording thread
        # there so the hotkey monitoring loop is never blocked.
        def _process_worker():
            if self.record_thread:
                self.record_thread.join()
            self.process_recording()

        self.process_thread = threading.Thread(target=_process_worker)
        self.process_thread.daemon = True
        self.process_thread.start()

    def record_audio(self):
        """Recording worker thread"""
        try:
            cb = self.micro_batcher.feed_audio if hasattr(self, 'micro_batcher') and self.micro_batcher else None
            self.audio_frames = record_audio_stream(stream_callback=cb)
        except Exception as e:
            logger.error(f"Recording error: {e}")
            self.recording = False

    def process_recording(self):
        """Process the recorded audio frames"""
        rec_duration = time.time() - getattr(self, 'start_time', time.time())
        audio_rec_duration = max(0.0, getattr(self, 'release_time', time.time()) - getattr(self, 'start_time', time.time()))
        time_since_last = time.time() - getattr(self, 'last_finish_time', 0.0)
        last_text = getattr(self, 'last_transcription', "").strip()

        # Check for Quick-Tap SLM On-Demand retro-polish trigger (only if SLM is enabled and audio is empty tap)
        import t2
        slm_enabled = bool(getattr(t2, 'ENABLE_SLM', False)) and os.environ.get("VT_ENABLE_SLM", "0") == "1"
        if slm_enabled and rec_duration < 0.35 and time_since_last < 15.0 and last_text and (self.audio_frames is None or len(self.audio_frames) == 0):
            logger.info("🤖 Quick-Tap SLM On-Demand retro-polish triggered!")
            self.visual_notification.show_processing()
            from post_processor import process_slm_llm_rewrite, clean_speech_transcription
            t0_slm = time.time()
            polished = process_slm_llm_rewrite(last_text, timeout_sec=3.0).strip()
            # Final safety pass: strip any punctuation/quote artifacts the SLM may have added
            polished = clean_speech_transcription(polished, skip_slm=True).strip()
            slm_elapsed = (time.time() - t0_slm) * 1000

            if polished and polished != last_text:
                print(f"🤖 [vLLM SLM On-Demand Polish] Executed in {slm_elapsed:.1f}ms: '{last_text}' -> '{polished}'")
                self.last_transcription = polished
                self.last_finish_time = time.time()

                # Hide processing notification immediately before queueing clipboard
                try:
                    self.visual_notification.hide_notification()
                except Exception:
                    pass

                def finalize_slm():
                    # Blocks if Wayland strict focus is active (e.g. GNOME top bar)
                    if copy_to_clipboard_crossplatform(polished, self.clipboard_sink):
                        self.visual_notification.show_completed(
                            sub_text=polished,
                            elapsed_sec=(slm_elapsed / 1000.0),
                            rec_duration=audio_rec_duration,
                            proc_time=(slm_elapsed / 1000.0)
                        )

                # Spawn background thread to queue up clipboard copy and notification
                threading.Thread(target=finalize_slm, daemon=True).start()

                return
            else:
                print("⚠️ [vLLM SLM On-Demand Polish] No changes made or guardrail triggered: Clipboard preserved.")
                return

        micro_batcher = getattr(self, 'micro_batcher', None)
        if not micro_batcher and (self.audio_frames is None or len(self.audio_frames) == 0):
            # Hide recording notification
            try:
                self.visual_notification.hide_notification()
            except Exception as e:
                logger.warning(f"Visual notification error: {e}")

            if rec_duration < 0.3:
                pass
            else:
                logger.info("No audio recorded")
                self.offer_device_change()
            return

        logger.info("Processing recording...")
        self.visual_notification.show_processing()

        try:
            # Wait for the model on this background thread ONLY — never on the UI
            # or hotkey threads. If the load ultimately fails, report it clearly
            # and bail out instead of pasting a load-error string to the clipboard.
            if not self._model_ready_event.is_set():
                logger.info("⏳ Audio captured; waiting for the model to finish loading...")
                self._model_ready_event.wait()
                if self.model_load_error:
                    logger.error(f"Model load failed: {self.model_load_error}")
                    try:
                        self.visual_notification.show_error(f"Model load failed: {self.model_load_error}")
                    except Exception:
                        pass
                    return

            t0_proc = time.time()
            # Retrieve text from micro-batcher or fallback (skip_slm=True for instant ASR dictation)
            if hasattr(self, 'micro_batcher') and self.micro_batcher:
                transcription = self.micro_batcher.finish_and_get_text(skip_slm=True).strip()
            else:
                result, transcribe_time = process_audio_stream(self.audio_frames)
                from post_processor import clean_speech_transcription
                transcription = clean_speech_transcription(result.strip(), skip_slm=True)
            proc_time = time.time() - t0_proc

            # Explicitly free the audio data memory after processing
            del self.audio_frames
            self.audio_frames = []

            if transcription:
                import t2
                effective_mode = getattr(t2, 'OUTPUT_MODE', 'clipboard')
                # If Ctrl was held during push-to-talk activation, override to clipboard only
                if self.copy_to_clipboard:
                    effective_mode = "clipboard"

                if effective_mode in ("type", "type_fast"):
                    canon_preset = getattr(t2, 'get_canonical_preset_name', lambda x: x)(getattr(t2, 'PUNCTUATION_MODE', 'full'))
                    if getattr(t2, 'AUTO_TYPE_AUTO_PUNCTUATE', True) and canon_preset == 'full':
                        # Ensure terminal punctuation (period if missing) on transcription
                        trimmed = transcription.rstrip()
                        if trimmed and not trimmed.endswith(('.', '!', '?', ':', ';', '…')):
                            transcription = trimmed + '.'
                        else:
                            transcription = trimmed

                self.last_transcription = transcription
                self.last_finish_time = time.time()

                words = len(transcription.strip().split())
                time_saved = t2.calculate_time_saved(transcription, rec_duration + proc_time)
                self.session_words = getattr(self, 'session_words', 0) + words
                self.session_time_saved_sec = getattr(self, 'session_time_saved_sec', 0.0) + time_saved
                self.session_transcriptions = getattr(self, 'session_transcriptions', 0) + 1

                # Fold this dictation into the persistent all-time totals.
                self._refresh_lifetime_stats(words=words, time_saved_sec=time_saved)

                if effective_mode in ("type", "type_fast"):
                    try:
                        logger.debug("Waiting for modifier release before typing...")
                        is_fast = (effective_mode == "type_fast")
                        timeout = 0.4 if is_fast else 1.0
                        start_wait = time.time()
                        had_modifiers = bool(self.hotkey_system and self.hotkey_system.are_modifiers_pressed())
                        while self.hotkey_system and self.hotkey_system.are_modifiers_pressed() and (time.time() - start_wait < timeout):
                            time.sleep(0.01 if is_fast else 0.02)

                        if had_modifiers:
                            time.sleep(0.01 if is_fast else 0.05)

                        add_space = getattr(t2, 'AUTO_TYPE_TRAILING_SPACE', True)
                        text_to_type = (transcription + ' ') if add_space else transcription
                        if self.hotkey_system and self.hotkey_system.type_text(text_to_type, fast=is_fast):
                            logger.info("Typed (%s, %d chars)", 'fast' if is_fast else 'slow', len(text_to_type))
                            logger.debug("Typed text: %s", text_to_type)
                        else:
                            raise Exception("uinput typing failed or not available")
                    except Exception as e:
                        logger.error(f"Error typing transcription: {e}")
                        logger.warning("Typing failed, but it's available in your clipboard")

                # Hide processing notification immediately so it doesn't linger while queued
                try:
                    self.visual_notification.hide_notification()
                except Exception as e:
                    logger.warning(f"Visual notification error: {e}")

                def finalize_transcription():
                    # This blocking call queues up the copy until GNOME shell releases focus
                    copy_success = copy_to_clipboard_crossplatform(transcription, self.clipboard_sink)

                    if copy_success:
                        logger.info("Copied transcription to clipboard (%d chars)", len(transcription))
                        logger.debug("Copied text: %s", transcription)

                        # Show completion notification only after clipboard successfully copies
                        try:
                            post_release_latency = time.time() - getattr(self, 'release_time', time.time())
                            self.visual_notification.show_completed(
                                sub_text=transcription,
                                elapsed_sec=post_release_latency,
                                rec_duration=audio_rec_duration,
                                proc_time=proc_time,
                                time_saved=time_saved,
                                session_time_saved=self.session_time_saved_sec,
                                lifetime_time_saved=self.lifetime_time_saved_sec,
                            )
                        except Exception as e:
                            logger.warning(f"Visual notification error: {e}")

                        # Play the platform completion earcon.
                        try:
                            if not getattr(t2, 'IS_MUTED', False):
                                self.audio_cues.play_cue("complete")
                        except Exception:
                            pass
                    else:
                        logger.error("Failed to copy transcription to clipboard")

                # Spawn background thread to wait for clipboard access
                threading.Thread(target=finalize_transcription, daemon=True).start()

            else:
                # Hide processing notification
                try:
                    self.visual_notification.hide_notification()
                except Exception as e:
                    logger.warning(f"Visual notification error: {e}")

                logger.info("No speech detected")

                # Offer to change audio device
                self.offer_device_change()

        except Exception as e:
            # Hide processing notification on error
            try:
                self.visual_notification.hide_notification()
            except Exception as e2:
                logger.warning(f"Visual notification error: {e2}")

            logger.error(f"Transcription error: {e}")

            # Also offer device change on error
            self.offer_device_change()

    def offer_device_change(self):
        """Show non-blocking notice for audio device change/retry"""
        if hasattr(self, 'tui') and self.tui:
            self.tui.print_warning("No Speech Detected", "No audio detected in recording. Press [s] for Settings to pick another input device, or [Space] to try again.")
            self.tui.update_state("READY")
        else:
            logger.info("Ready for next recording")

    # ------------------------------------------------------------------
    # Control API (see src/control.py)
    # ------------------------------------------------------------------
    def _control_status(self, verb: str = "status", **extra) -> dict:
        """Snapshot returned by control-API ``status``-style replies."""
        return {
            "ok": True,
            "cmd": verb,
            "state": getattr(self.tui, "state", "UNKNOWN"),
            "recording": bool(getattr(self, "recording", False)),
            "device": get_active_device_name(include_model=False),
            "model": getattr(t2, "MODEL_BACKEND", "cohere"),
            "muted": bool(getattr(t2, "IS_MUTED", False)),
            "output_mode": getattr(t2, "OUTPUT_MODE", "clipboard"),
            "number_mode": getattr(t2, "NUMBER_MODE", "auto"),
            "punctuation_mode": getattr(t2, "PUNCTUATION_MODE", "full"),
            "structure_mode": getattr(t2, "get_effective_structure_mode", lambda: "off")(),
            "structure_setting": getattr(t2, "get_structure_mode", lambda: "off")(),
            "cleanup_mode": getattr(t2, "get_cleanup_mode", lambda: "full")(),
            # Configured vs effective, like the structure pair above: with
            # cleanup off the formatter cannot run, and `status` should say so
            # rather than report "on" for something that is inert.
            "formatter": getattr(t2, "get_effective_formatter", lambda: "off")(),
            "formatter_setting": getattr(t2, "get_formatter", lambda: "off")(),
            "formatter_model": getattr(t2, "get_formatter_model", lambda: "s1-mini")(),
            "formatter_style": getattr(t2, "get_formatter_style", lambda: "semi-formal")(),
            "formatter_context": getattr(t2, "get_formatter_context", lambda: "general")(),
            "ui_theme": getattr(t2, "UI_THEME", "auto"),
            "middle_click": bool(getattr(t2, "MIDDLE_CLICK_ENABLED", False)),
            "last_transcription": getattr(self, "last_transcription", ""),
            "session_words": getattr(self, "session_words", 0),
            "session_time_saved_sec": getattr(self, "session_time_saved_sec", 0.0),
            "lifetime_words": getattr(self, "lifetime_words", 0),
            "lifetime_time_saved_sec": getattr(self, "lifetime_time_saved_sec", 0.0),
            "lifetime_time_saved": t2.format_duration(getattr(self, "lifetime_time_saved_sec", 0.0)),
            "lifetime_transcriptions": getattr(self, "lifetime_transcriptions", 0),
            "lifetime_sessions": getattr(self, "lifetime_sessions", 0),
            "typing_wpm": getattr(t2, "TYPING_WPM", 40),
            **extra,
        }

    def _refresh_lifetime_stats(self, words=0, time_saved_sec=0.0, record_session=False):
        """Sync the in-memory lifetime counters with the persisted stats file.

        Called once at startup (``record_session=True``) and after every
        successful dictation. Either way the stats file is created if it does
        not exist yet, so a first launch always leaves one behind.

        Failures are swallowed by :mod:`stats`; the in-memory values then keep
        their previous value so the UI never breaks over a stat.
        """
        try:
            if record_session:
                record = stats.record_session_start()
            elif words or time_saved_sec:
                record = stats.record_transcription(words, time_saved_sec)
            else:
                record = stats.ensure()
        except Exception as e:  # never let bookkeeping break dictation
            logger.warning(f"Could not update lifetime stats: {e}")
            return

        self.lifetime_words = int(record.get("words", 0))
        self.lifetime_time_saved_sec = float(record.get("time_saved_sec", 0.0))
        self.lifetime_transcriptions = int(record.get("transcriptions", 0))
        self.lifetime_sessions = int(record.get("sessions", 0))

    @staticmethod
    def _control_on_off(value):
        """Parse on/off/true/false into True/False, or None meaning "toggle"."""
        if value is None:
            return None
        text = str(value).strip().lower()
        if text in ("on", "true", "1", "yes", "enable", "enabled"):
            return True
        if text in ("off", "false", "0", "no", "disable", "disabled"):
            return False
        if text in ("toggle", "flip", ""):
            return None
        raise ValueError(f"expected on/off, got {value!r}")

    def _control_settle(self, timeout: float = 1.0) -> None:
        """Wait briefly for the UI state to agree with ``self.recording``.

        ``stop_recording`` returns as soon as transcription is *scheduled*, so
        without this a ``stop`` reply would still claim ``RECORDING``. Returns
        as soon as the two agree, so the common case costs nothing.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if bool(self.recording) == (getattr(self.tui, "state", None) == "RECORDING"):
                return
            time.sleep(0.02)

    def _control_hotkey(self, verb, value):
        """List/add/remove/reset the push-to-talk binds (control-API verb).

        One verb carrying a small command keeps the catalogue flat: the value is
        ``list`` (the default), ``add <chord>``, ``remove <chord>`` or
        ``reset``. Every reply carries the resulting bind list, so a caller
        never has to follow up to discover what happened.
        """
        import t2
        from voice_transcriber import keybinds

        current = keybinds.parse_binds(t2.HOTKEY_BINDS)
        action, _, argument = (value or "list").partition(" ")
        action = action.strip().lower()
        argument = argument.strip()

        if action in ("", "list", "ls", "show"):
            pass
        elif action in ("keys", "keynames", "names", "vocabulary"):
            # The names a user may type, straight from the one declaration table
            # (keybinds.KEYS) the backends resolve through -- so what a UI shows
            # cannot drift from what the matcher accepts.
            return self._control_status(
                verb,
                hotkeys=[bind.chord for bind in current],
                keys=keybinds.catalogue(),
            )
        elif action == "add":
            if not argument:
                raise ValueError("hotkey add needs a chord, e.g. 'hotkey add ctrl+shift'")
            keys = keybinds.parse_chord(argument)
            if any(bind.keys == keys for bind in current):
                raise ValueError(f"{keybinds.format_chord(keys)} is already bound")
            current = current + [keybinds.Bind(keys, keybinds.DEFAULT_ACTION)]
        elif action in ("remove", "rm", "delete", "del", "unbind"):
            if not argument:
                raise ValueError("hotkey remove needs a chord, e.g. 'hotkey remove f13'")
            keys = keybinds.parse_chord(argument)
            remaining = [bind for bind in current if bind.keys != keys]
            if len(remaining) == len(current):
                raise ValueError(
                    f"{keybinds.format_chord(keys)} is not bound; "
                    f"bound: {keybinds.describe_binds(current)}"
                )
            if not remaining:
                # Push-to-talk has to stay reachable from the keyboard, so the
                # last bind can be changed but never removed to nothing.
                raise ValueError("at least one bind is required; add another first")
            current = remaining
        elif action in ("reset", "default", "defaults"):
            current = list(keybinds.DEFAULT_BINDS)
        else:
            raise ValueError(
                f"unknown hotkey action {action!r}; "
                "use list, add <chord>, remove <chord> or reset"
            )

        if action not in ("", "list", "ls", "show"):
            t2.set_hotkey_binds(current)
            # ``t2`` propagates through the ``hotkeys`` shim's
            # ``_current_hotkey_instance``, which is only set by
            # ``create_global_hotkeys`` -- the engine builds its manager
            # straight from the HAL, so push at it directly too (this is what
            # the middle-click handler has always done).
            if self.hotkey_system and hasattr(self.hotkey_system, "set_binds"):
                self.hotkey_system.set_binds(t2.HOTKEY_BINDS)
            t2.save_audio_config()
            self._sync_tui_state()
            current = keybinds.parse_binds(t2.HOTKEY_BINDS)
        return self._control_status(verb, hotkeys=[bind.chord for bind in current])

    def handle_control(self, cmd, request):
        """Dispatch one control-API request.

        The programmatic equivalent of the terminal UI, so external programs --
        and tests -- can drive a real running engine. Transport and the verb
        list live in :mod:`control`.
        """
        verb = control.normalize_verb(cmd)

        raw_value = request.get("value")
        for key in ("name", "mode"):
            if raw_value is None:
                raw_value = request.get(key)
        value = None if raw_value is None else str(raw_value).strip()

        def need_value():
            if not value:
                raise ValueError(f"{verb} needs a value")

        if verb == "help":
            return {"ok": True, "cmd": verb, "verbs": dict(control.VERBS)}

        if verb in ("doctor", "check"):
            import doctor

            return doctor.run_doctor(json_format=True)

        if verb == "ping":
            return {"ok": True, "cmd": verb, "pid": os.getpid()}

        if verb in ("status", "state"):
            return self._control_status(verb)

        if verb == "wait":
            # Block until the engine stops being busy, so callers never have to
            # poll. Note this also covers the initial model load, which reports
            # as PROCESSING while the weights are read in.
            try:
                timeout = float(value) if value else 30.0
            except ValueError:
                raise ValueError(f"wait expects seconds, got {value!r}") from None
            deadline = time.monotonic() + max(0.0, timeout)
            while time.monotonic() < deadline:
                if getattr(self.tui, "state", None) != "PROCESSING":
                    break
                time.sleep(0.05)
            return self._control_status(verb, timed_out=time.monotonic() >= deadline)

        # -- recording -----------------------------------------------------
        if verb in ("start", "start-recording"):
            self.start_recording()
            self._control_settle()
            return self._control_status(verb)

        if verb in ("stop", "stop-recording"):
            self.stop_recording(copy_to_clipboard=bool(request.get("copy", True)))
            self._control_settle()
            return self._control_status(verb)

        if verb in ("toggle", "toggle-recording"):
            self._on_tui_toggle_record()
            self._control_settle()
            return self._control_status(verb)

        # -- modal pickers (the settings modal is the config hub) ----------
        if verb in ("settings", "open-settings"):
            self.open_settings_picker()
            return self._control_status(verb)

        if verb in ("mic", "open-mic", "microphone"):
            self.open_mic_picker()
            return self._control_status(verb)

        # -- microphone ----------------------------------------------------
        if verb in ("mics", "list-mics", "devices"):
            return {
                "ok": True,
                "cmd": verb,
                "devices": [d.get("name") for d in t2.get_input_devices() if d.get("name")],
            }

        if verb in ("rescan-mics", "rescan-microphones"):
            summary = self._on_tui_rescan_mics()
            return {
                "ok": bool(summary.get("ok")),
                "cmd": verb,
                "devices": summary.get("devices", []),
                "device": summary.get("device"),
                "missing": summary.get("missing", []),
                "message": summary.get("message", ""),
            }

        if verb in ("set-mic", "set-microphone"):
            need_value()
            wanted = value.lower()
            for dev in t2.get_input_devices():
                name = str(dev.get("name", ""))
                if wanted in name.lower():
                    t2.PRIMARY_DEVICE_NAME = name
                    t2.INPUT_DEVICE_INDEX = dev.get("index")
                    t2.set_default_input_device(dev.get("index"))
                    t2.save_audio_config()
                    self._sync_tui_state()
                    return self._control_status(verb, device=name)
            raise ValueError(f"no input device matching {value!r}")

        # -- appearance / formatting ---------------------------------------
        if verb == "theme":
            if value:
                self._on_tui_set_theme(value)
                return self._control_status(verb, theme=value)
            self.open_theme_picker()
            return self._control_status(verb)

        if verb in ("output", "output-mode"):
            need_value()
            mode = t2.set_output_mode(value)
            t2.save_audio_config()
            self._sync_tui_state()
            return self._control_status(verb, output_mode=mode)

        if verb in ("numbers", "number-digits"):
            need_value()
            t2.set_number_digits(value)
            t2.save_audio_config()
            self._sync_tui_state()
            return self._control_status(verb)

        if verb in ("punctuation", "preset", "punctuation-mode"):
            need_value()
            t2.set_punctuation_mode(value)
            t2.save_audio_config()
            self._sync_tui_state()
            return self._control_status(verb)

        if verb in ("structure", "structure-mode"):
            need_value()
            t2.set_structure_mode(value)
            t2.save_audio_config()
            self._sync_tui_state()
            return self._control_status(verb)

        if verb in ("cleanup", "cleanup-mode"):
            need_value()
            t2.set_cleanup_mode(value)
            t2.save_audio_config()
            self._sync_tui_state()
            return self._control_status(verb)

        if verb in ("formatter", "formatter-mode"):
            need_value()
            t2.set_formatter(value)
            t2.save_audio_config()
            self._sync_tui_state()
            return self._control_status(verb)

        if verb in ("formatter-model", "formatter_model"):
            need_value()
            t2.set_formatter_model(value)
            t2.save_audio_config()
            self._sync_tui_state()
            return self._control_status(verb)

        if verb in ("formatter-style", "formatter_style"):
            need_value()
            t2.set_formatter_style(value)
            t2.save_audio_config()
            self._sync_tui_state()
            return self._control_status(verb)

        if verb in ("formatter-context", "formatter_context"):
            need_value()
            t2.set_formatter_context(value)
            t2.save_audio_config()
            self._sync_tui_state()
            return self._control_status(verb)

        if verb == "trailing-space":
            state = self._control_on_off(value)
            toggle = t2.get_auto_type_trailing_space()
            t2.set_auto_type_trailing_space(toggle if state is None else state)
            t2.save_audio_config()
            self._sync_tui_state()
            return self._control_status(verb, trailing_space=t2.get_auto_type_trailing_space())

        if verb == "auto-punctuate":
            state = self._control_on_off(value)
            toggle = t2.get_auto_type_auto_punctuate()
            t2.set_auto_type_auto_punctuate(toggle if state is None else state)
            t2.save_audio_config()
            self._sync_tui_state()
            return self._control_status(verb, auto_punctuate=t2.get_auto_type_auto_punctuate())

        if verb == "serial":
            state = self._control_on_off(value)
            t2.set_serial_collapse(
                (not t2.SERIAL_COLLAPSE) if state is None else state
            )
            t2.save_audio_config()
            self._sync_tui_state()
            return self._control_status(verb, serial_collapse=t2.SERIAL_COLLAPSE)

        if verb == "spell":
            state = self._control_on_off(value)
            t2.set_spell_command(
                (not t2.SPELL_COMMAND) if state is None else state
            )
            t2.save_audio_config()
            self._sync_tui_state()
            return self._control_status(verb, spell_command=t2.SPELL_COMMAND)

        if verb in ("wpm", "typing-wpm", "set-wpm"):
            if value:
                try:
                    val = int(value)
                    if val <= 0:
                        raise ValueError
                except ValueError:
                    raise ValueError(f"WPM must be a positive integer, got {value!r}") from None
                t2.set_typing_wpm(val)
                t2.save_audio_config()
                self._sync_tui_state()
                return self._control_status(verb, typing_wpm=t2.TYPING_WPM)
            else:
                new_wpm = t2.cycle_typing_wpm()
                t2.save_audio_config()
                self._sync_tui_state()
                return self._control_status(verb, typing_wpm=new_wpm)

        if verb == "hotkey":
            return self._control_hotkey(verb, value)

        # -- toggles -------------------------------------------------------
        if verb == "middle-click":
            state = self._control_on_off(value)
            t2.set_middle_click_enabled(
                (not t2.MIDDLE_CLICK_ENABLED) if state is None else state
            )
            t2.save_audio_config()
            if self.hotkey_system and hasattr(self.hotkey_system, "set_middle_click_enabled"):
                self.hotkey_system.set_middle_click_enabled(t2.MIDDLE_CLICK_ENABLED)
            self._sync_tui_state()
            return self._control_status(verb, middle_click=bool(t2.MIDDLE_CLICK_ENABLED))

        if verb == "mute":
            state = self._control_on_off(value)
            t2.IS_MUTED = (not t2.IS_MUTED) if state is None else state
            t2.save_audio_config()
            self._sync_tui_state()
            return self._control_status(verb)

        # -- lifecycle -----------------------------------------------------
        if verb in ("reset-defaults", "reset"):
            self._on_tui_reset_defaults()
            return self._control_status(verb)

        if verb == "reset-terminal":
            self._on_tui_reset_terminal()
            return self._control_status(verb)

        if verb == "quit":
            # Let this reply reach the client before tearing the process down;
            # the request is being served on a worker thread.
            threading.Timer(0.25, self._on_tui_quit).start()
            return {"ok": True, "cmd": verb, "message": "shutting down"}

        raise ValueError(f"unknown control verb {verb!r} (try 'help')")

    def run(self):
        """Run the voice transcriber with Live TUI"""
        if not self.hotkey_system or not self.hotkey_system.devices:
            if hasattr(self, 'tui') and self.tui:
                self.tui.print_error("Hotkey System Error", "No global hotkey system available.\nMake sure you're running as root or in the input group.\nRun: sudo usermod -aG input $USER")
            return False

        # Start TUI live dashboard & input listener
        if hasattr(self, 'tui') and self.tui:
            self.tui.start()

        # Enter the hotkey loop IMMEDIATELY, without waiting for the model (the
        # model-load watcher was already started in __init__). A slow or stalled
        # load must never freeze the app; recordings made meanwhile wait for the
        # model in background threads.
        # Warm the input stream so the first push-to-talk press captures
        # instantly instead of paying the PortAudio device-open cost then.
        try:
            threading.Thread(target=t2.prewarm_input_stream, daemon=True).start()
        except Exception:
            pass

        self.running = True

        try:
            # Run the hotkey monitoring system
            hotkey_result = self.hotkey_system.run()
            return hotkey_result
        except KeyboardInterrupt:
            return True
        except Exception as e:
            if hasattr(self, 'tui') and self.tui:
                self.tui.print_error("Runtime Error", str(e))
            return False
        finally:
            self.cleanup()
            self.running = False
            if self.hotkey_system:
                self.hotkey_system.stop()

def check_permissions():
    """Check if user has proper permissions for input device access"""
    from hotkeys import is_running_in_wsl
    if is_running_in_wsl() or sys.platform.startswith("win") or sys.platform == "darwin" or hal.detect_platform() in (hal.WINDOWS, hal.MACOS):
        return True

    import grp
    import pwd

    # Check if running as root
    if os.geteuid() == 0:
        logger.debug("Running as root - full input device access available")
        return True

    # Check if user is in input group
    try:
        current_user = pwd.getpwuid(os.getuid()).pw_name

        # Get current groups using os.getgroups() which reflects actual active groups
        current_gids = os.getgroups()

        # Get input group info
        input_group = grp.getgrnam('input')

        # Check if user is in input group (by GID)
        if input_group.gr_gid in current_gids:
            logger.debug("User is in input group - input device access available")
            return True
        else:
            # Get group names for display
            group_names = []
            for gid in current_gids:
                try:
                    group_names.append(grp.getgrgid(gid).gr_name)
                except Exception:
                    group_names.append(str(gid))

            logger.error(f"User {current_user} is NOT in the 'input' group.")
            logger.error(f"Current groups: {', '.join(group_names)}")
            logger.error(f"Run: sudo usermod -aG input {current_user}")
            logger.error("Then LOG OUT and LOG BACK IN for changes to take effect.")
            return False
    except Exception as e:
        logger.error(f"Error checking permissions: {e}")
        return False


def cli():
    """Main CLI entry point for Voice Transcriber."""
    # Never let a stream that cannot encode our glyphs (legacy Windows codepage,
    # LANG=C behind a pipe, PYTHONIOENCODING=ascii) break any output path.
    console_text.harden_standard_streams()

    if LOG_FILE:
        logger.info("Log file: %s", LOG_FILE)

    # `python src/main.py <verb>` drives an already-running instance instead of
    # launching a second one. A leading `-` means "not a control verb", so
    # bare/flagged launches keep behaving exactly as before.
    _argv = sys.argv[1:]
    if _argv and not _argv[0].startswith("-"):
        _control_exit = control.run_cli(_argv)
        if _control_exit is not None:
            sys.exit(_control_exit)

    if check_permissions():
        app = SimpleVoiceTranscriber()
        sys.exit(0 if app.run() else 1)
    else:
        sys.exit(1)


if __name__ == "__main__":
    cli()
