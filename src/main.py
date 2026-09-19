#!/usr/bin/env python3
"""
Simple Voice Transcriber with Alt+Shift+K shortcut
Enhanced with Wayland-compatible global hotkeys using evdev+uinput
"""
import numpy as np
import logging
import threading
import time
import os
import sys

# Ensure local source directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import core modules
import hal
from tui import VoiceTranscriberTUI

# Import transcription functionality
# Ensure we can find t2
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import t2
from t2 import (
    preload_model, DEVICE, record_audio_stream, process_audio_stream, 
    stop_recording, load_audio_config, select_audio_device, 
    reset_terminal, get_active_device_name, IS_MUTED
)

logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
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
    from tui import VoiceTranscriberTUI
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
        
        # Proactively check microphone health on startup
        is_healthy, mic_issues = t2.check_microphone_health()
        if not is_healthy:
            warn_msg = "⚠️ HARDWARE MICROPHONE NOT DETECTED BY WSL!\n" + "\n".join([f" • {issue}" for issue in mic_issues])
            self.tui.print_warning("MICROPHONE HARDWARE WARNING", warn_msg)
        
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

        # If configured to wait on startup (default True), ensure the model is
        # 100% loaded and warmed up BEFORE initializing hotkeys so the user's very
        # first keypress transcribes instantly without waiting.
        if getattr(t2, 'WAIT_FOR_MODEL_ON_STARTUP', True):
            if self.preload_thread and self.preload_thread.is_alive():
                self.preload_thread.join(timeout=20.0)

        # Initialize global hotkey system after model is ready
        self.init_hotkeys()

    def _safe_tui_start(self):
        """Start the selected TUI; if the ratatui frontend fails, fall back to Rich."""
        try:
            self.tui.start()
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
            self.tui.start()

    def _sync_tui_state(self):
        """Sync t2 configuration state with TUI badges and visual notification"""
        import t2
        self.tui.set_active_device(get_active_device_name(include_model=False))
        self.tui.set_secondary_device(t2.SECONDARY_DEVICE_NAME)
        self.tui.set_config_state(
            backend=t2.MODEL_BACKEND,
            muted=t2.IS_MUTED,
            auto_type=t2.AUTO_TYPE,
            sound_theme=t2.SOUND_THEME,
            ui_theme=getattr(t2, 'UI_THEME', 'auto'),
            punctuation_mode=getattr(t2, 'PUNCTUATION_MODE', 'full')
        )
        if hasattr(self, 'visual_notification') and self.visual_notification:
            self.visual_notification.set_active_device(get_active_device_name(include_model=False))

    def _wire_tui_callbacks(self):
        """Wire direct terminal keyboard shortcuts from TUI"""
        self.tui.on_toggle_record = self._on_tui_toggle_record
        self.tui.on_change_device = self.change_input_device
        self.tui.on_toggle_mute = self._on_tui_toggle_mute
        self.tui.on_toggle_autotype = self._on_tui_toggle_autotype
        self.tui.on_toggle_numbers = self._on_tui_toggle_numbers
        self.tui.on_cycle_punctuation = self._on_tui_cycle_punctuation_mode
        self.tui.on_cycle_theme = self._on_tui_cycle_theme
        self.tui.on_reset_terminal = self._on_tui_reset_terminal
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

    def _on_tui_toggle_autotype(self):
        import t2
        t2.AUTO_TYPE = not t2.AUTO_TYPE
        t2.save_audio_config()
        self._sync_tui_state()
        status = "AUTO-TYPE ENABLED" if t2.AUTO_TYPE else "CLIPBOARD COPY ONLY"
        self.tui.print_event("⌨️ Auto-Type Mode", f"Output mode set to {status}", level="info")

    def _on_tui_toggle_numbers(self):
        import t2
        t2.set_number_digits(not t2.NUMBER_DIGITS)
        t2.save_audio_config()
        self._sync_tui_state()
        status = "DIGITS" if t2.NUMBER_DIGITS else "SPELLED OUT"
        self.tui.print_event("🔢 Number Conversion", f"Numbers are now transcribed as {status}", level="info")

    def _on_tui_cycle_punctuation_mode(self):
        import t2
        new_mode = t2.cycle_punctuation_mode()
        t2.save_audio_config()
        self._sync_tui_state()
        labels = {
            "full": "Full Punctuation",
            "no_terminal_period": "No Trailing Period (Semi-Formal)",
            "no_punctuation": "No Punctuation",
            "lowercase_no_punctuation": "Lowercase Without Punctuation"
        }
        disp = labels.get(new_mode, new_mode)
        self.tui.print_event("📝 Formatting Mode", f"Punctuation mode set to {disp}", level="info")

    def _on_tui_cycle_theme(self):
        import t2
        new_theme = self.tui.cycle_ui_theme()
        t2.UI_THEME = new_theme
        t2.save_audio_config()
        self._sync_tui_state()

    def _on_tui_reset_terminal(self):
        import t2
        t2.reset_terminal()
        self._sync_tui_state()
        self.tui.print_event("🔄 Terminal Reset", "Terminal state and clipboard bridge reset successfully.", level="info")

    def _on_tui_quit(self):
        self.cleanup()
        # The quit command arrives on the TUI reader thread; sys.exit() there
        # would only end that thread, so terminate the whole process.
        os._exit(0)

    def cleanup(self):
        """Clean up all resources."""
        if hasattr(self, 'tui') and self.tui:
            self.tui.stop()
        if hasattr(self, 'visual_notification'):
            self.visual_notification.cleanup()
        if hasattr(self, 'hotkey_system') and self.hotkey_system:
            self.hotkey_system.cleanup()
        
    def init_hotkeys(self):
        """Initialize the global hotkey system via the HAL."""
        try:
            self.hotkey_system = hal.create_hotkey_manager(
                platform=self.platform,
                callback_start=self.start_recording,
                callback_stop=self.stop_recording,
                callback_config=self.change_input_device,
            )
            
            if self.hotkey_system.devices:
                logger.debug("Global hotkey system initialized")
                import t2
                theme = getattr(t2, 'SOUND_THEME', None)
                if theme:
                    if hasattr(self.hotkey_system, 'set_sound_theme'):
                        self.hotkey_system.set_sound_theme(theme)
                    if hasattr(self.audio_cues, 'set_sound_theme'):
                        self.audio_cues.set_sound_theme(theme)
                # WSL forwards earcons through the same bridge process.
                if hasattr(self.audio_cues, 'set_bridge'):
                    self.audio_cues.set_bridge(self.hotkey_system)
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
        
        if self.record_thread:
            self.record_thread.join()
            
        # Start processing in a separate thread
        self.process_thread = threading.Thread(target=self.process_recording)
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
        slm_enabled = getattr(t2, 'ENABLE_SLM', True) and os.environ.get("VT_ENABLE_SLM", "1") != "0"
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
                except Exception as e:
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
                print(f"⚠️ [vLLM SLM On-Demand Polish] No changes made or guardrail triggered: Clipboard preserved.")
                return

        if (self.audio_frames is None or self.audio_frames.size == 0) and not getattr(self, 'micro_batcher', None):
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
                self.last_transcription = transcription
                self.last_finish_time = time.time()
                import t2
                auto_type_setting = getattr(t2, 'AUTO_TYPE', True)
                should_type = auto_type_setting and (t2.COPY_TO_CLIPBOARD != self.copy_to_clipboard)

                if should_type:
                    try:
                        logger.debug("Waiting for modifier release before typing...")
                        timeout = 1.0
                        start_wait = time.time()
                        while self.hotkey_system and self.hotkey_system.are_modifiers_pressed() and (time.time() - start_wait < timeout):
                            time.sleep(0.02)
                        
                        time.sleep(0.05)
                        
                        if self.hotkey_system and self.hotkey_system.type_text(transcription):
                            logger.info(f"Typed: {transcription}")
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
                        logger.info(f"Copied to clipboard: {transcription}")
                        
                        # Show completion notification only after clipboard successfully copies
                        try:
                            post_release_latency = time.time() - getattr(self, 'release_time', time.time())
                            self.visual_notification.show_completed(
                                sub_text=transcription,
                                elapsed_sec=post_release_latency,
                                rec_duration=audio_rec_duration,
                                proc_time=proc_time
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
            self.tui.print_warning("No Speech Detected", "No audio detected in recording. Press [M] to change input devices or [Space] to try again.")
            self.tui.update_state("READY")
        else:
            logger.info("Ready for next recording")

    def change_input_device(self):
        """Open audio device selection menu via hotkey or TUI shortcut"""
        if self.recording:
            if hasattr(self, 'tui') and self.tui:
                self.tui.print_warning("Settings Locked", "Cannot change settings while recording is active.")
            return

        if hasattr(self, 'tui') and self.tui:
            self.tui._pause_live()

        try:
            if select_audio_device():
                if hasattr(self, 'tui') and self.tui:
                    self.tui.print_event("⚙️ Audio Configuration", "Audio device and settings updated successfully!", level="success")
            reset_terminal()
        except Exception as e:
            if hasattr(self, 'tui') and self.tui:
                self.tui.print_error("Audio Configuration Error", str(e))
            _dbg_path = os.environ.get("VT_TUI_DEBUG")
            if _dbg_path:
                try:
                    import traceback
                    with open(_dbg_path, "a", encoding="utf-8") as _f:
                        _f.write("change_input_device error: " + repr(e) + "\n")
                        _f.write(traceback.format_exc())
                except OSError:
                    pass
            reset_terminal()
            
        self._sync_tui_state()
        if hasattr(self, 'tui') and self.tui:
            self.tui._resume_live()
            self.tui.update_state("READY")

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

if __name__ == "__main__":
    def check_permissions():
        """Check if user has proper permissions for input device access"""
        from hotkeys import is_running_in_wsl
        if is_running_in_wsl():
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
                    except:
                        group_names.append(str(gid))
                
                logger.error(f"User {current_user} is NOT in the 'input' group.")
                logger.error(f"Current groups: {', '.join(group_names)}")
                logger.error(f"Run: sudo usermod -aG input {current_user}")
                logger.error("Then LOG OUT and LOG BACK IN for changes to take effect.")
                return False
        except Exception as e:
            logger.error(f"Error checking permissions: {e}")
            return False

    if check_permissions():
        app = SimpleVoiceTranscriber()
        app.run()
    else:
        sys.exit(1)
