#!/usr/bin/env python3
"""
T3 Voice Transcriber - Main Application
Enhanced version combining features from t2.py and simple_voice_transcriber.py
Features:
- Global hotkeys (Alt+Shift) that work system-wide
- Audio device selection and configuration
- Visual notifications
- Automatic typing of transcription
- Wayland/X11 compatibility
"""

import logging
import threading
import subprocess
import sys
import warnings

# Import our modular components
from audio_recorder import load_audio_config, record_audio_stream, stop_recording
from audio_processor import process_audio_stream, get_device
from hotkey_system import WaylandGlobalHotkeys, check_permissions
from text_typer import type_text
from transcribe import preload_model, get_model
from visual_notifications import VisualNotification
from interactive_menu import run_interactive_menu, print_usage, run_interactive_mode
from audio_recorder import select_audio_device

# Suppress warnings
warnings.filterwarnings("ignore", message=".*Init provider bridge failed.*")
warnings.filterwarnings("ignore", category=UserWarning)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class T3VoiceTranscriber:
    def __init__(self):
        self.recording = False
        self.record_thread = None
        self.process_thread = None
        self.hotkey_system = None
        self.running = False
        self.recorded_frames = []  # Store recorded audio frames
        self.device = get_device()
        
        # Load audio config
        load_audio_config()
        
        # Initialize visual notification with app name
        self.visual_notification = VisualNotification("T3 Voice Transcriber")
        
        # Preload model
        logger.info("Loading transcription model...")
        self.preload_thread = preload_model(device=self.device)
        
        # Initialize hotkeys
        self.init_hotkeys()
        
    def init_hotkeys(self):
        try:
            self.hotkey_system = WaylandGlobalHotkeys(
                callback_start=self.start_recording,
                callback_stop=self.stop_recording
            )
            
            if self.hotkey_system.devices:
                logger.info("Global hotkey system initialized")
                return True
            else:
                logger.error("Failed to initialize global hotkey system")
                return False
                
        except Exception as e:
            logger.error(f"Error initializing hotkeys: {e}")
            return False

    def start_recording(self):
        if self.recording:
            logger.debug("Already recording, ignoring start_recording call")
            return
            
        logger.debug("Starting recording session")
        
        # Wait for model to load
        if hasattr(self, 'preload_thread') and self.preload_thread.is_alive():
            logger.info("Waiting for model to load...")
            self.preload_thread.join()
        
        self.recording = True
        stop_recording.clear()
        logger.debug(f"stop_recording event cleared. Event state: {stop_recording.is_set()}")
        
        # Show notification
        try:
            self.visual_notification.show_recording("Recording - Release Alt+Shift to stop")
        except Exception as e:
            logger.warning(f"Visual notification error: {e}")
        
        # Play sound
        try:
            subprocess.Popen(['mpg123', '-q', 'app/sounds/pop2.mp3'], 
                           stderr=subprocess.DEVNULL)
        except:
            pass
        
        # Start recording
        logger.debug("Starting recording thread")
        self.record_thread = threading.Thread(target=self._record_wrapper)
        self.record_thread.daemon = True
        self.record_thread.start()

    def _record_wrapper(self):
        """Wrapper to capture recorded frames"""
        self.recorded_frames = record_audio_stream(interactive_mode=False)

    def stop_recording(self):
        if not self.recording:
            logger.debug("Not recording, ignoring stop_recording call")
            return
            
        logger.debug("Stopping recording session")
        self.recording = False
        stop_recording.set()
        logger.debug(f"stop_recording event set. Event state: {stop_recording.is_set()}")
        
        # Show processing notification
        try:
            self.visual_notification.show_processing("Transcribing audio")
        except Exception as e:
            logger.warning(f"Visual notification error: {e}")
        
        # Play sound
        # try:
        #     subprocess.Popen(['mpg123', '-q', 'app/sounds/pop2.mp3'], 
        #                    stderr=subprocess.DEVNULL)
        # except:
        #     pass
        
        # Wait for recording to finish
        if self.record_thread:
            logger.debug("Waiting for recording thread to finish")
            self.record_thread.join()
            logger.debug("Recording thread finished")
        
        # Process audio
        logger.debug("Starting transcription thread")
        self.process_thread = threading.Thread(target=self.process_and_transcribe)
        self.process_thread.daemon = True
        self.process_thread.start()

    def process_and_transcribe(self):
        try:
            result, transcribe_time = process_audio_stream(self.recorded_frames, device=self.device)
            transcription = result.strip()
            
            if transcription:
                # Type the text
                if type_text(transcription):
                    # Show completion notification
                    try:
                        self.visual_notification.show_completed("Text typed successfully")
                    except Exception as e:
                        logger.warning(f"Visual notification error: {e}")
                    
                    # Play sound
                    try:
                        subprocess.Popen(['mpg123', '-q', 'app/sounds/pop.mp3'], 
                                       stderr=subprocess.DEVNULL)
                    except:
                        pass
                    
                    logger.info(f"✅ Transcribed and typed: {transcription}")
                else:
                    # Show error for failed typing
                    try:
                        self.visual_notification.show_error("Failed to type text")
                    except Exception as e:
                        logger.warning(f"Visual notification error: {e}")
                    logger.error("Failed to type transcription")
            else:
                # Show warning for no speech detected
                try:
                    self.visual_notification.show_warning("No speech detected")
                except Exception as e:
                    logger.warning(f"Visual notification error: {e}")
                        
                logger.info("❌ No speech detected")
                self.offer_device_change()
                
        except Exception as e:
            try:
                self.visual_notification.show_error("Transcription failed")
            except:
                pass
            logger.error(f"Transcription error: {e}")
            self.offer_device_change()
        finally:
            # Clear recorded frames for next recording
            self.recorded_frames = []

    def offer_device_change(self):
        logger.info("")
        logger.info("🔧 Options:")
        logger.info("   Space: Try again")
        logger.info("   i: Change audio device")
        logger.info("   Any other key: Continue")
        logger.info("")
        
        try:
            import termios, tty
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setraw(sys.stdin.fileno())
                ch = sys.stdin.read(1)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            
            if ch == ' ':
                logger.info("🎤 Ready - hold Alt+Shift to record")
            elif ch.lower() == 'i':
                logger.info("🎤 Opening device selection...")
                if select_audio_device():
                    logger.info("✅ Audio device updated!")
                else:
                    logger.info("❌ Device selection cancelled.")
                logger.info("🎤 Ready - hold Alt+Shift to record")
            else:
                logger.info("🎤 Ready for next recording")
                
        except (KeyboardInterrupt, EOFError):
            logger.info("🎤 Ready for next recording")
        except ImportError:
            try:
                choice = input("Enter choice (Space/i/other): ").strip().lower()
                if choice == ' ' or choice == '':
                    logger.info("🎤 Ready - hold Alt+Shift to record")
                elif choice == 'i':
                    logger.info("🎤 Opening device selection...")
                    if select_audio_device():
                        logger.info("✅ Audio device updated!")
                    else:
                        logger.info("❌ Device selection cancelled.")
                    logger.info("🎤 Ready - hold Alt+Shift to record")
                else:
                    logger.info("🎤 Ready for next recording")
            except (KeyboardInterrupt, EOFError):
                logger.info("🎤 Ready for next recording")
    
    def run(self):
        if not self.hotkey_system or not self.hotkey_system.devices:
            logger.error("❌ No global hotkey system available")
            logger.error("💡 Run as root or add user to input group: sudo usermod -a -G input $USER")
            logger.error("💡 Install dependencies: pip install evdev")
            return False
        
        logger.info("🎤 T3 Voice Transcriber started!")
        logger.info(f"📱 Using device: {self.device}")
        logger.info("🔥 Hold Alt+Shift to record, release to transcribe and type")
        logger.info("🔄 Use Ctrl+C to exit")
        logger.info("💡 Global hotkeys work system-wide!")
        
        self.running = True
        
        try:
            return self.hotkey_system.run()
        except KeyboardInterrupt:
            logger.info("👋 Shutting down...")
            self.running = False
            if self.hotkey_system:
                self.hotkey_system.stop()
            self.cleanup()
            return True
        except Exception as e:
            logger.error(f"Error running hotkey system: {e}")
            self.cleanup()
            return False
    
    def cleanup(self):
        """Clean up resources when shutting down"""
        try:
            self.visual_notification.cleanup()
        except Exception as e:
            logger.debug(f"Error cleaning up visual notifications: {e}")

def main():
    """Main function with interactive mode"""
    device = get_device()
    logger.info("T3 Voice Transcriber")
    logger.info(f"Using device: {device}")
    
    # Check for command line arguments
    if len(sys.argv) > 1:
        arg = sys.argv[1].strip().lower()
        
        # Load audio config
        load_audio_config()
        
        # Preload model
        preload_thread = preload_model(device=device)
        
        # Handle command line mode selection
        if arg == '1':
            # Global hotkey mode
            logger.info("Starting global hotkey mode...")
            if not check_permissions():
                logger.error("Cannot use global hotkeys without proper permissions")
                logger.error("💡 Run as root or add user to input group: sudo usermod -a -G input $USER")
                return
            
            transcriber = T3VoiceTranscriber()
            transcriber.run()
            return
            
        elif arg == '2':
            # Interactive mode
            logger.info("Starting interactive mode...")
            logger.info("Interactive mode - Press Space to record, 'i' for device selection, 'q' to quit")
            
            # Wait for model
            if preload_thread.is_alive():
                logger.info("Waiting for model to load...")
                preload_thread.join()
                logger.info("Model loaded!")
            
            run_interactive_mode()
            return
            
        elif arg == '3' or arg == 'i':
            # Device selection
            logger.info("Opening device selection...")
            if select_audio_device():
                logger.info("✅ Audio device configured!")
            else:
                logger.info("❌ Device selection cancelled")
            return
            
        elif arg in ['4', 'exit', 'quit']:
            logger.info("Exiting...")
            return
            
        elif arg in ['help', '-h', '--help']:
            print_usage()
            return
            
        else:
            logger.error(f"Invalid argument: {arg}")
            print_usage()
            return
    
    # No arguments provided - run interactive menu
    run_interactive_menu()

if __name__ == "__main__":
    main() 