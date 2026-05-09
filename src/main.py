#!/usr/bin/env python3
"""
Simple Voice Transcriber with Alt+Shift+K shortcut
Enhanced with Wayland-compatible global hotkeys using evdev+uinput
"""
import numpy as np
import logging
import threading
import subprocess
import time
import os
import sys
import pyperclip
import atexit


def resource_path(relative_path):
    """Get absolute path to resource, works for dev and PyInstaller."""
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

# Import core modules
from notifications import VisualNotification
from hotkeys import WaylandGlobalHotkeys

# Import transcription functionality
# Ensure we can find t2
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import t2
from t2 import (
    preload_model, DEVICE, record_audio_stream, process_audio_stream, 
    stop_recording, load_audio_config, select_audio_device, 
    reset_terminal, get_active_device_name, IS_MUTED
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
        
        # Load saved audio device configuration
        load_audio_config()
        
        # Initialize visual notification
        self.visual_notification = VisualNotification(app_name="Voice Transcriber")
        self.visual_notification.set_active_device(get_active_device_name())
        
        # Preload model in background with loading indicator
        from t2 import MODEL_BACKEND
        print(f"Loading {MODEL_BACKEND.capitalize()} model from local files...")
        self.preload_thread = preload_model(device=DEVICE)
        
        # Initialize global hotkey system
        self.init_hotkeys()
        
    def cleanup(self):
        """Clean up all resources."""
        if hasattr(self, 'visual_notification'):
            self.visual_notification.cleanup()
        
    def init_hotkeys(self):
        """Initialize the global hotkey system"""
        try:
            callback_toggle_ui = getattr(self, 'toggle_ui_mode', None)
            hotkey_kwargs = {
                'callback_start': self.start_recording,
                'callback_stop': self.stop_recording,
                'callback_config': self.change_input_device
            }
            if callback_toggle_ui is not None:
                hotkey_kwargs['callback_toggle_ui'] = callback_toggle_ui
            
            self.hotkey_system = WaylandGlobalHotkeys(**hotkey_kwargs)
            
            if self.hotkey_system.devices:
                logger.debug("Global hotkey system initialized")
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
            
        # Wait for model to load if still loading
        # Check both the instance's initial thread and t2's global active thread (for backend switches)
        import t2
        current_thread = getattr(t2, 'active_preload_thread', None)
        
        if hasattr(self, 'preload_thread') and self.preload_thread.is_alive():
            logger.info("⏳ Waiting for transcription model to finish loading...")
            self.preload_thread.join()
        
        if current_thread and current_thread.is_alive():
            logger.info("⏳ Waiting for new transcription model to finish loading...")
            current_thread.join()
        
        self.recording = True
        self.start_time = time.time()
        stop_recording.clear()
        self.audio_frames = []
        
        # Start recording in background thread IMMEDIATELY
        self.record_thread = threading.Thread(target=self.record_audio)
        self.record_thread.daemon = True
        self.record_thread.start()
        
        # Update notification
        self.visual_notification.show_recording()
        
        # Play sound
        try:
            if not t2.IS_MUTED:
                sound_path = resource_path('sounds/start.mp3')
                subprocess.Popen(['mpg123', '-q', sound_path], 
                               stderr=subprocess.DEVNULL)
        except:
            pass

    def stop_recording(self, copy_to_clipboard=False):
        """Stop recording and start processing"""
        if not self.recording:
            return
            
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
            self.audio_frames = record_audio_stream()
        except Exception as e:
            logger.error(f"Recording error: {e}")
            self.recording = False

    def process_recording(self):
        """Process the recorded audio frames"""
        if self.audio_frames is None or self.audio_frames.size == 0:
            # Hide recording notification
            try:
                self.visual_notification.hide_notification()
            except Exception as e:
                logger.warning(f"Visual notification error: {e}")
                
            duration = time.time() - self.start_time
            if duration < 0.3:
                # Just a tap, maybe show settings or ignore
                pass
            else:
                logger.info("No audio recorded")
                # Offer to change audio device
                self.offer_device_change()
            return
            
        logger.info("Processing recording...")
        self.visual_notification.show_processing()
        
        try:
            # Double check if we need to wait for model (should be already joined in start_recording)
            import t2
            current_thread = getattr(t2, 'active_preload_thread', None)
            if current_thread and current_thread.is_alive():
                logger.info("⏳ Still waiting for transcription model...")
                current_thread.join()
            if hasattr(self, 'preload_thread') and self.preload_thread.is_alive():
                self.preload_thread.join()

            # Process the audio
            result, transcribe_time = process_audio_stream(self.audio_frames)
            
            # Clean up result
            transcription = result.strip()
            
            # Explicitly free the audio data memory after processing
            del self.audio_frames
            self.audio_frames = []
            
            if transcription:
                from t2 import COPY_TO_CLIPBOARD
                should_type = COPY_TO_CLIPBOARD != self.copy_to_clipboard
                copy_success = False
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        pyperclip.copy(transcription)
                        copy_success = True
                        logger.info(f"Copied to clipboard: {transcription}")
                        break
                    except Exception as e:
                        if attempt < max_retries - 1:
                            logger.warning(f"Clipboard copy failed (attempt {attempt+1}), retrying...")
                            time.sleep(0.5)
                        else:
                            logger.error(f"Failed to copy to clipboard after {max_retries} attempts: {e}")

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
                
                # Show completion notification with the transcribed text
                try:
                    if copy_success:
                        self.visual_notification.show_completed(sub_text=transcription)
                    else:
                        logger.error("Transcription not copied to clipboard")
                except Exception as e:
                    logger.warning(f"Visual notification error: {e}")
                
                # Play sound
                try:
                    if not t2.IS_MUTED:
                        sound_path = resource_path('sounds/pop.mp3')
                        subprocess.Popen(['mpg123', '-q', sound_path], 
                                       stderr=subprocess.DEVNULL)
                except:
                    pass
                
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
        """Offer to change audio device after failed recording"""
        logger.info("")
        logger.info("What would you like to do?")
        logger.info("   Space/Enter: Try recording again")
        logger.info("   i: Change audio input device")
        logger.info("   r: Reset terminal & clipboard (if things are wonky)")
        logger.info("   Any other key: Continue")
        logger.info("")
        
        try:
            # Use the same getch function pattern as t2.py
            import termios, tty
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setraw(sys.stdin.fileno())
                ch = sys.stdin.read(1)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            
            if ch in [' ', '\r', '\n']:  # Space or Enter
                logger.info("Ready to record - hold Alt+Shift when ready")
            elif ch.lower() == 'i':  # Input device selection
                logger.info("Opening audio device selection...")
                result = select_audio_device()
                if result:
                    logger.info("Audio device updated!")
                else:
                    logger.info("Device selection cancelled.")
                reset_terminal()
                logger.info("Ready to record - hold Alt+Shift when ready")
            elif ch.lower() == 'r':  # Reset terminal
                logger.info("Resetting terminal and clipboard...")
                reset_terminal()
                logger.info("Reset complete.")
                logger.info("Ready to record - hold Alt+Shift when ready")
            else:
                reset_terminal()
                logger.info("Ready for next recording")
                
        except (KeyboardInterrupt, EOFError):
            logger.info("Ready for next recording")
        except ImportError:
            # Windows fallback - use regular input
            try:
                choice = input("Enter choice (Space/Enter/i/r/other): ").strip().lower()
                if choice == ' ' or choice == '':
                    logger.info("Ready to record - hold Alt+Shift when ready")
                elif choice == 'i':
                    logger.info("Opening audio device selection...")
                    if select_audio_device():
                        logger.info("Audio device updated!")
                    else:
                        logger.info("Device selection cancelled.")
                    logger.info("Ready to record - hold Alt+Shift when ready")
                elif choice == 'r':
                    logger.info("Resetting terminal and clipboard...")
                    reset_terminal()
                    logger.info("Reset complete.")
                    logger.info("Ready to record - hold Alt+Shift when ready")
                else:
                    logger.info("Ready for next recording")
            except (KeyboardInterrupt, EOFError):
                logger.info("Ready for next recording")

    def toggle_ui_mode(self):
        """Toggle between CLI and GUI mode via Ctrl+Alt+O hotkey"""
        import t2
        t2.USE_GUI_MODE = not t2.USE_GUI_MODE
        t2.save_audio_config()
        mode_name = "GUI" if t2.USE_GUI_MODE else "CLI"
        logger.info(f"🔄 UI Mode switched to: {mode_name}")
        logger.info("Press Ctrl+Alt+I to open settings")
    
    def change_input_device(self):
        """Open audio device selection menu via hotkey"""
        if self.recording:
            logger.warning("Cannot change settings while recording is active")
            return
        
        import t2
        if t2.USE_GUI_MODE:
            logger.info("")
            logger.info("⚙️  Settings hotkey detected!")
            logger.info("Opening GUI settings window...")
            try:
                import settings_gui
                settings_gui.show_settings_gui()
                logger.info("Settings updated!")
            except Exception as e:
                logger.error(f"Error opening GUI settings: {e}")
        else:
            logger.info("")
            logger.info("⚙️  Settings hotkey detected!")
            logger.info("Opening audio device selection...")
            logger.info("Please interact with the terminal window")
            
            try:
                if select_audio_device():
                    logger.info("Audio device updated!")
                else:
                    logger.info("Device selection cancelled.")
                reset_terminal()
            except Exception as e:
                logger.error(f"Error in device selection: {e}")
                reset_terminal()
        
        logger.info("Ready to record - hold Alt+Shift when ready")

    
    def run(self):
        """Run the voice transcriber"""
        if not self.hotkey_system or not self.hotkey_system.devices:
            logger.error("No global hotkey system available")
            logger.error("Make sure you're running as root or in the input group")
            logger.error("Install dependencies: pip install evdev python-uinput")
            return False
        
        # Wait for model to load and warmup BEFORE starting the loop
        if hasattr(self, 'preload_thread') and self.preload_thread.is_alive():
            self.visual_notification.show_processing("Loading model")
            self.preload_thread.join()
            self.visual_notification.hide_notification()
        
        print("Voice Transcriber ready!")
        print(f"Using: {get_active_device_name()}")
        from t2 import SECONDARY_DEVICE_NAME
        if SECONDARY_DEVICE_NAME:
            print(f"Secondary: {SECONDARY_DEVICE_NAME}")
        
        # Show ready state in terminal/notifications
        self.visual_notification.hide_notification()
        
        self.running = True
        
        try:
            # Run the hotkey monitoring system
            hotkey_result = self.hotkey_system.run()
            return hotkey_result
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            return True
        except Exception as e:
            logger.error(f"Error running hotkey system: {e}")
            return False
        finally:
            self.cleanup()
            self.running = False
            if self.hotkey_system:
                self.hotkey_system.stop()

if __name__ == "__main__":
    def check_permissions():
        """Check if user has proper permissions for input device access"""
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
