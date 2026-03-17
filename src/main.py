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
import atexit

# Import core modules
from notifications import VisualNotification
from hotkeys import WaylandGlobalHotkeys

# Import transcription functionality
# Ensure we can find t2
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import t2
from t2 import preload_model, DEVICE, record_audio_stream, process_audio_stream, stop_recording, load_audio_config, select_audio_device, reset_terminal, IS_MUTED

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
        
        # Load saved audio device configuration
        load_audio_config()
        
        # Initialize visual notification
        self.visual_notification = VisualNotification(app_name="Voice Transcriber")
        
        # Preload model in background
        logger.info("Loading transcription model...")
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
            self.hotkey_system = WaylandGlobalHotkeys(
                callback_start=self.start_recording,
                callback_stop=self.stop_recording,
                callback_config=self.change_input_device
            )
            
            if self.hotkey_system.devices:
                logger.info("✅ Global hotkey system initialized")
                return True
            else:
                logger.error("❌ Failed to initialize global hotkey system")
                return False
                
        except Exception as e:
            logger.error(f"Error initializing hotkeys: {e}")
            return False

    def start_recording(self):
        """Start recording audio"""
        if self.recording:
            return
            
        # Wait for model to load if still loading
        if hasattr(self, 'preload_thread') and self.preload_thread.is_alive():
            logger.info("Waiting for model to load...")
            self.preload_thread.join()
        
        self.recording = True
        stop_recording.clear()
        self.audio_frames = []
        
        # Show visual notification
        try:
            self.visual_notification.show_recording()
        except Exception as e:
            logger.warning(f"Visual notification error: {e}")
        
        # Play start recording sound
        try:
            if not t2.IS_MUTED:
                sound_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sounds/pop2.mp3')
                subprocess.Popen(['mpg123', '-q', sound_path], 
                               stderr=subprocess.DEVNULL)
        except:
            pass
        
        # Start recording in background thread
        self.record_thread = threading.Thread(target=self.record_audio)
        self.record_thread.daemon = True
        self.record_thread.start()

    def stop_recording(self):
        """Stop recording and process audio"""
        if not self.recording:
            return
            
        self.recording = False
        stop_recording.set()
        
        # Show processing notification
        try:
            self.visual_notification.show_processing()
        except Exception as e:
            logger.warning(f"Visual notification error: {e}")
        
        # Play stop recording sound
        try:
            if not t2.IS_MUTED:
                sound_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sounds/pop2.mp3')
                subprocess.Popen(['mpg123', '-q', sound_path], 
                               stderr=subprocess.DEVNULL)
        except:
            pass
        
        # Wait for recording to finish
        if self.record_thread:
            self.record_thread.join()
        
        # Process the recorded audio
        self.process_thread = threading.Thread(target=self.process_and_transcribe)
        self.process_thread.daemon = True
        self.process_thread.start()

    def record_audio(self):
        """Record audio using t2"""
        try:
            # We strictly don't use interactive mode here as we control start/stop via hotkeys
            self.audio_frames = record_audio_stream(interactive_mode=False)
        except Exception as e:
            logger.error(f"Recording error: {e}")

    def process_and_transcribe(self):
        """Process recorded audio and transcribe"""
        try:
            # Check if we have audio
            if self.audio_frames.size == 0:
                logger.warning("No audio frames recorded")
                self.visual_notification.hide_notification()
                return

            # Process the audio
            result, transcribe_time = process_audio_stream(self.audio_frames)
            
            # Clean up result
            transcription = result.strip()
            
            if transcription:
                # Copy to clipboard
                import pyperclip
                pyperclip.copy(transcription)
                
                # Show completion notification
                try:
                    self.visual_notification.show_completed()
                except Exception as e:
                    logger.warning(f"Visual notification error: {e}")
                
                # Play sound
                try:
                    if not t2.IS_MUTED:
                        sound_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sounds/pop.mp3')
                        subprocess.Popen(['mpg123', '-q', sound_path], 
                                       stderr=subprocess.DEVNULL)
                except:
                    pass
                
                logger.info(f"✅ Transcribed: {transcription}")
            else:
                # Hide processing notification
                try:
                    self.visual_notification.hide_notification()
                except Exception as e:
                    logger.warning(f"Visual notification error: {e}")
                        
                logger.info("❌ No speech detected")
                
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
        logger.info("🔧 What would you like to do?")
        logger.info("   Space: Try recording again")
        logger.info("   i: Change audio input device")
        logger.info("   r: Reset terminal (if text is wonky)")
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
            
            if ch == ' ':  # Space key
                logger.info("🎤 Ready to record - hold Alt+Shift when ready")
            elif ch.lower() == 'i':  # Input device selection
                logger.info("🎤 Opening audio device selection...")
                if select_audio_device():
                    logger.info("✅ Audio device updated!")
                else:
                    logger.info("❌ Device selection cancelled.")
                reset_terminal()
                logger.info("🎤 Ready to record - hold Alt+Shift when ready")
            elif ch.lower() == 'r':  # Reset terminal
                logger.info("🔄 Resetting terminal...")
                reset_terminal()
                logger.info("✅ Terminal reset complete.")
                logger.info("🎤 Ready to record - hold Alt+Shift when ready")
            else:
                reset_terminal()
                logger.info("🎤 Ready for next recording")
                
        except (KeyboardInterrupt, EOFError):
            logger.info("🎤 Ready for next recording")
        except ImportError:
            # Windows fallback - use regular input
            try:
                choice = input("Enter choice (Space/i/r/other): ").strip().lower()
                if choice == ' ' or choice == '':
                    logger.info("🎤 Ready to record - hold Alt+Shift when ready")
                elif choice == 'i':
                    logger.info("🎤 Opening audio device selection...")
                    if select_audio_device():
                        logger.info("✅ Audio device updated!")
                    else:
                        logger.info("❌ Device selection cancelled.")
                    logger.info("🎤 Ready to record - hold Alt+Shift when ready")
                elif choice == 'r':
                    logger.info("🔄 Resetting terminal...")
                    reset_terminal()
                    logger.info("✅ Terminal reset complete.")
                    logger.info("🎤 Ready to record - hold Alt+Shift when ready")
                else:
                    logger.info("🎤 Ready for next recording")
            except (KeyboardInterrupt, EOFError):
                logger.info("🎤 Ready for next recording")

    def change_input_device(self):
        """Open audio device selection menu via hotkey"""
        if self.recording:
            logger.warning("⚠️  Cannot change settings while recording is active")
            return
            
        logger.info("")
        logger.info("⚙️  Settings hotkey detected!")
        logger.info("🎤 Opening audio device selection...")
        logger.info("💡 Please interact with the terminal window")
        
        try:
            if select_audio_device():
                logger.info("✅ Audio device updated!")
            else:
                logger.info("❌ Device selection cancelled.")
            reset_terminal()
        except Exception as e:
            logger.error(f"Error in device selection: {e}")
            reset_terminal()
            
        logger.info("🎤 Ready to record - hold Alt+Shift when ready")

    
    def run(self):
        """Run the voice transcriber"""
        if not self.hotkey_system or not self.hotkey_system.devices:
            logger.error("❌ No global hotkey system available")
            logger.error("💡 Make sure you're running as root or in the input group")
            logger.error("💡 Install dependencies: pip install evdev python-uinput")
            return False
        
        logger.info("🎤 Voice Transcriber started!")
        logger.info(f"📱 Using device: {DEVICE}")
        logger.info("🔥 Hold Alt+Shift to record, release to transcribe")
        logger.info("⚙️  Press Ctrl+Alt+I to change input device")
        logger.info("🔄 Use Ctrl+C to exit")
        logger.info("💡 Global hotkeys work even when Alacritty is in focus!")
        
        self.running = True
        
        try:
            # Run the hotkey monitoring system
            hotkey_result = self.hotkey_system.run()
            return hotkey_result
        except KeyboardInterrupt:
            logger.info("👋 Shutting down...")
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
            logger.info("✅ Running as root - full input device access available")
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
                logger.info("✅ User is in input group - input device access available")
                return True
            else:
                # Get group names for display
                group_names = []
                for gid in current_gids:
                    try:
                        group_names.append(grp.getgrgid(gid).gr_name)
                    except:
                        group_names.append(str(gid))
                
                logger.error("❌ Permission issue detected!")
                logger.error(f"👤 Current user: {current_user}")
                logger.error(f"👥 Active groups: {', '.join(group_names)}")
                logger.error(f"🔢 Input group GID: {input_group.gr_gid} (not in active groups)")
                logger.error("")
                logger.error("🔧 To fix this issue, choose one of these options:")
                logger.error("")
                logger.error("   Option 1 (Recommended): Add user to input group")
                logger.error("   sudo usermod -a -G input $USER")
                logger.error("   Then log out and back in (or reboot)")
                logger.error("")
                logger.error("   Option 2: Run as root (less secure)")
                logger.error("   sudo nix-shell --run 'python3 app/simple_voice_transcriber.py'")
                logger.error("")
                logger.error("🔍 Why this is needed:")
                logger.error("   Global hotkeys on Wayland require direct access to input devices")
                logger.error("   This bypasses application-level input restrictions")
                logger.error("   Works even when Alacritty or other apps are focused")
                logger.error("")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error checking permissions: {e}")
            logger.error("💡 Try running as root: sudo nix-shell --run 'python3 app/simple_voice_transcriber.py'")
            return False
    
    def check_input_devices():
        """Check if input devices are accessible"""
        try:
            import evdev
            devices = evdev.list_devices()
            if not devices:
                logger.error("❌ No input devices found")
                return False
            
            # Try to access at least one device
            for device_path in devices:
                try:
                    device = evdev.InputDevice(device_path)
                    device.close()
                    logger.info(f"✅ Input devices accessible ({len(devices)} found)")
                    return True
                except PermissionError:
                    continue
            
            logger.error("❌ Input devices found but not accessible due to permissions")
            return False
            
        except ImportError:
            logger.error("❌ evdev not available")
            return False
        except Exception as e:
            logger.error(f"❌ Error checking input devices: {e}")
            return False
    
    # Perform comprehensive checks
    logger.info("🔍 Checking system requirements...")
    
    permissions_ok = check_permissions()
    devices_ok = check_input_devices() if permissions_ok else False
    
    if not permissions_ok:
        logger.error("")
        logger.error("⚠️  Cannot start voice transcriber due to permission issues")
        logger.error("📖 Follow the instructions above to fix permissions")
        sys.exit(1)
    
    if not devices_ok:
        logger.error("")
        logger.error("⚠️  Cannot access input devices")
        logger.error("💡 Make sure input devices are connected and accessible")
        sys.exit(1)
    
    logger.info("✅ All system checks passed!")
    logger.info("")
    
    transcriber = SimpleVoiceTranscriber()
    import atexit
    atexit.register(transcriber.cleanup)
    transcriber.run()
