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
- WhisperLive client testing
"""

import logging
import threading
import subprocess
import sys
import warnings
import time
import socket

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

def check_server_connection(host="localhost", port=9090, timeout=5):
    """Check if WhisperLive server is running"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False

def run_whisperlive_test():
    """Test WhisperLive client functionality"""
    logger.info("🔬 WhisperLive Test Mode")
    logger.info("=" * 50)
    
    # Check if server is running
    logger.info("🔍 Checking WhisperLive server connection...")
    if not check_server_connection():
        logger.error("❌ Cannot connect to WhisperLive server on localhost:9090")
        logger.error("💡 Make sure to start the server first:")
        logger.error("   python -m whisper_live.server --port 9090 --backend faster_whisper")
        logger.info("")
        
        choice = input("Would you like to start the server now? (y/n): ").strip().lower()
        if choice == 'y':
            logger.info("🚀 Starting WhisperLive server...")
            logger.info("⏳ This may take 30+ seconds to load the model...")
            try:
                # Start server in background
                process = subprocess.Popen([
                    sys.executable, "-m", "whisper_live.server", 
                    "--port", "9090", "--backend", "faster_whisper"
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                
                # Wait for server to start
                logger.info("⏳ Waiting for server to start...")
                for i in range(30):
                    if check_server_connection():
                        logger.info("✅ Server is running!")
                        break
                    time.sleep(1)
                    if i % 5 == 0:
                        logger.info(f"   Still waiting... ({i+1}/30 seconds)")
                else:
                    logger.error("❌ Server failed to start within 30 seconds")
                    return
                    
            except Exception as e:
                logger.error(f"❌ Failed to start server: {e}")
                return
        else:
            logger.info("💡 Start the server manually and try again")
            return
    else:
        logger.info("✅ WhisperLive server is running!")
    
    # Import WhisperLive client
    try:
        from whisper_live.client import TranscriptionClient
    except ImportError as e:
        logger.error(f"❌ Cannot import WhisperLive client: {e}")
        logger.error("💡 Make sure WhisperLive is installed in your environment")
        return
    
    logger.info("")
    logger.info("🎤 WhisperLive Client Test")
    logger.info("Options:")
    logger.info("  1. Test microphone transcription")
    logger.info("  2. Test audio file transcription")
    logger.info("  3. Check server status")
    logger.info("  q. Quit")
    
    while True:
        try:
            choice = input("\nEnter your choice: ").strip().lower()
            
            if choice == 'q':
                logger.info("👋 Exiting WhisperLive test")
                break
                
            elif choice == '1':
                logger.info("🎤 Testing microphone transcription...")
                try:
                    client = TranscriptionClient(
                        "localhost",
                        9090,
                        lang="en",
                        model="small",
                        use_vad=False
                    )
                    logger.info("✅ Client created successfully!")
                    logger.info("🎤 Recording from microphone... (This may take a moment)")
                    logger.info("💡 Speak now, the client will handle recording automatically")
                    
                    # Call the client for microphone transcription
                    client()
                    
                except Exception as e:
                    logger.error(f"❌ Microphone test failed: {e}")
                    
            elif choice == '2':
                logger.info("📁 Testing audio file transcription...")
                file_path = input("Enter path to audio file (or press Enter for default): ").strip()
                if not file_path:
                    file_path = "output.wav"  # Default file from the project
                
                try:
                    client = TranscriptionClient(
                        "localhost",
                        9090,
                        lang="en",
                        model="small",
                        use_vad=False
                    )
                    logger.info("✅ Client created successfully!")
                    logger.info(f"🎵 Transcribing file: {file_path}")
                    
                    # Call the client for file transcription
                    client(file_path)
                    
                except Exception as e:
                    logger.error(f"❌ File transcription test failed: {e}")
                    
            elif choice == '3':
                logger.info("🔍 Checking server status...")
                if check_server_connection():
                    logger.info("✅ Server is responding on localhost:9090")
                    try:
                        # Try to create client to test connection
                        client = TranscriptionClient(
                            "localhost",
                            9090,
                            lang="en",
                            model="small",
                            use_vad=False
                        )
                        logger.info("✅ Client can connect to server successfully!")
                    except Exception as e:
                        logger.error(f"❌ Client connection test failed: {e}")
                else:
                    logger.error("❌ Server is not responding on localhost:9090")
                    
            else:
                logger.info("❌ Invalid choice. Please enter 1, 2, 3, or q")
                
        except (KeyboardInterrupt, EOFError):
            logger.info("\n👋 Exiting WhisperLive test")
            break

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
            
        elif arg == '5' or arg == 'w' or arg == 'whisper':
            # WhisperLive test mode
            logger.info("Starting WhisperLive test mode...")
            run_whisperlive_test()
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