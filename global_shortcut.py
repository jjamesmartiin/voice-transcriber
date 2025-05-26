#!/usr/bin/env python3
"""
Enhanced global shortcut daemon for voice transcriber
Supports multiple backends for better Wayland compatibility
"""

import os
import sys
import subprocess
import threading
import time
import signal
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Import our transcription functionality
from t2 import record_and_transcribe, preload_model, get_model, DEVICE, record_audio_stream, process_audio_stream, stop_recording

class GlobalShortcutDaemon:
    def __init__(self):
        self.alt_pressed = False
        self.shift_pressed = False
        self.recording = False
        self.record_thread = None
        self.process_thread = None
        self.backend = None
        self.running = False
        
        # Preload model in background
        logger.info("Loading transcription model...")
        self.preload_thread = preload_model(device=DEVICE)
        
        # Try to initialize the best available backend
        self.init_backend()
        
    def init_backend(self):
        """Initialize the best available keyboard monitoring backend"""
        # Try backends in order of preference
        backends = [
            ('evdev', self.init_evdev_backend),
            ('pynput', self.init_pynput_backend),
            ('polling', self.init_polling_backend)
        ]
        
        for backend_name, init_func in backends:
            try:
                if init_func():
                    self.backend = backend_name
                    logger.info(f"Using {backend_name} backend for keyboard monitoring")
                    return True
            except Exception as e:
                logger.warning(f"Failed to initialize {backend_name} backend: {e}")
                continue
        
        logger.error("No keyboard monitoring backend available")
        return False
    
    def init_evdev_backend(self):
        """Initialize evdev backend (best for Wayland)"""
        try:
            import evdev
            from evdev import InputDevice, categorize, ecodes
            
            # Find keyboard devices
            devices = [InputDevice(path) for path in evdev.list_devices()]
            keyboards = []
            
            for device in devices:
                caps = device.capabilities()
                # Check if device has keyboard capabilities (look for common keys)
                if ecodes.EV_KEY in caps:
                    key_caps = caps[ecodes.EV_KEY]
                    # Check for essential keyboard keys
                    if (ecodes.KEY_A in key_caps and ecodes.KEY_SPACE in key_caps and 
                        (ecodes.KEY_LEFTALT in key_caps or ecodes.KEY_RIGHTALT in key_caps)):
                        keyboards.append(device)
                        logger.info(f"Found keyboard device: {device.name} at {device.path}")
            
            if not keyboards:
                raise Exception("No keyboard devices found")
            
            self.keyboards = keyboards
            logger.info(f"Found {len(keyboards)} keyboard device(s)")
            return True
            
        except ImportError:
            raise Exception("evdev not available - install with: pip install evdev")
        except PermissionError:
            raise Exception("Permission denied - add user to input group: sudo usermod -a -G input $USER")
    
    def init_pynput_backend(self):
        """Initialize pynput backend (fallback)"""
        try:
            from pynput import keyboard
            from pynput.keyboard import Key, Listener
            
            # Test if pynput can create a listener
            test_listener = Listener(on_press=lambda key: None, on_release=lambda key: None)
            test_listener.start()
            test_listener.stop()
            
            self.pynput_keyboard = keyboard
            self.pynput_Key = Key
            self.pynput_Listener = Listener
            return True
            
        except ImportError:
            raise Exception("pynput not available - install with: pip install pynput")
        except Exception as e:
            raise Exception(f"pynput initialization failed: {e}")
    
    def init_polling_backend(self):
        """Initialize polling backend (last resort)"""
        # Check if we can read /proc/interrupts for keyboard activity
        try:
            with open('/proc/interrupts', 'r') as f:
                content = f.read()
                if 'keyboard' in content.lower() or 'i8042' in content.lower():
                    return True
            raise Exception("No keyboard interrupt information found")
        except:
            raise Exception("Cannot access interrupt information")
    
    def run_evdev_backend(self):
        """Run keyboard monitoring using evdev"""
        import evdev
        from evdev import categorize, ecodes
        import select
        
        logger.info("Starting evdev keyboard monitoring...")
        
        while self.running:
            try:
                # Use select to monitor multiple devices
                devices_map = {dev.fd: dev for dev in self.keyboards}
                r, w, x = select.select(devices_map, [], [], 1.0)
                
                for fd in r:
                    device = devices_map[fd]
                    try:
                        for event in device.read():
                            if event.type == ecodes.EV_KEY:
                                key_event = categorize(event)
                                
                                # Check for Alt keys
                                if event.code in [ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT]:
                                    if event.value == 1:  # Key press
                                        self.alt_pressed = True
                                    elif event.value == 0:  # Key release
                                        self.alt_pressed = False
                                        self.check_stop_recording()
                                
                                # Check for Shift keys
                                elif event.code in [ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT]:
                                    if event.value == 1:  # Key press
                                        self.shift_pressed = True
                                    elif event.value == 0:  # Key release
                                        self.shift_pressed = False
                                        self.check_stop_recording()
                                
                                # Check for K key
                                elif event.code == ecodes.KEY_K:
                                    if event.value == 1:  # Key press
                                        self.check_start_recording()
                                
                    except OSError:
                        # Device disconnected, remove it
                        logger.warning(f"Device {device.path} disconnected")
                        self.keyboards.remove(device)
                        if not self.keyboards:
                            logger.error("All keyboard devices disconnected")
                            self.running = False
                            return
                            
            except Exception as e:
                logger.error(f"Error in evdev monitoring: {e}")
                time.sleep(1)
    
    def run_pynput_backend(self):
        """Run keyboard monitoring using pynput"""
        logger.info("Starting pynput keyboard monitoring...")
        
        def on_press(key):
            try:
                if key == self.pynput_Key.alt_l or key == self.pynput_Key.alt_r:
                    self.alt_pressed = True
                    logger.debug("Alt pressed")
                elif key == self.pynput_Key.shift_l or key == self.pynput_Key.shift_r:
                    self.shift_pressed = True
                    logger.debug("Shift pressed")
                elif hasattr(key, 'char') and key.char and key.char.lower() == 'k':
                    logger.debug("K pressed")
                    self.check_start_recording()
                # Also handle if K is pressed as a special key
                elif key == getattr(self.pynput_Key, 'k', None):
                    logger.debug("K key pressed")
                    self.check_start_recording()
            except AttributeError:
                # Handle special keys that don't have char attribute
                key_name = str(key).replace('Key.', '')
                if key_name == 'k':
                    logger.debug("K special key pressed")
                    self.check_start_recording()
        
        def on_release(key):
            try:
                if key == self.pynput_Key.alt_l or key == self.pynput_Key.alt_r:
                    self.alt_pressed = False
                    logger.debug("Alt released")
                    self.check_stop_recording()
                elif key == self.pynput_Key.shift_l or key == self.pynput_Key.shift_r:
                    self.shift_pressed = False
                    logger.debug("Shift released")
                    self.check_stop_recording()
                elif hasattr(key, 'char') and key.char and key.char.lower() == 'k':
                    logger.debug("K released")
                    # Don't stop recording on K release, only on Alt/Shift release
                elif key == getattr(self.pynput_Key, 'k', None):
                    logger.debug("K key released")
                    # Don't stop recording on K release, only on Alt/Shift release
            except AttributeError:
                # Handle special keys that don't have char attribute
                key_name = str(key).replace('Key.', '')
                if key_name == 'k':
                    logger.debug("K special key released")
                    # Don't stop recording on K release, only on Alt/Shift release
        
        # Keep restarting the listener until we're told to stop
        while self.running:
            try:
                with self.pynput_Listener(on_press=on_press, on_release=on_release) as listener:
                    listener.join()  # This will block until the listener stops
            except Exception as e:
                logger.error(f"Pynput listener error: {e}")
                if self.running:
                    logger.info("Restarting listener in 1 second...")
                    time.sleep(1)
    
    def run_polling_backend(self):
        """Run keyboard monitoring using polling (very basic)"""
        logger.info("Starting polling keyboard monitoring...")
        logger.info("This is a basic fallback - use Alt+Shift+K to record")
        
        try:
            while self.running:
                try:
                    input("Press Enter to start recording (Ctrl+C to exit)...")
                    if not self.running:
                        break
                    
                    self.start_recording()
                    input("Press Enter to stop recording...")
                    self.stop_recording()
                    
                except KeyboardInterrupt:
                    logger.info("Ctrl+C pressed - exiting...")
                    self.running = False
                    break
        except Exception as e:
            logger.error(f"Polling error: {e}")
    
    def check_start_recording(self):
        """Check if Alt+Shift+K combination is pressed to start recording"""
        logger.debug(f"Checking start: alt={self.alt_pressed}, shift={self.shift_pressed}, recording={self.recording}")
        if self.alt_pressed and self.shift_pressed and not self.recording:
            logger.info("Alt+Shift+K combination detected - starting recording")
            self.start_recording()
    
    def check_stop_recording(self):
        """Check if any key in the combination is released to stop recording"""
        logger.debug(f"Checking stop: alt={self.alt_pressed}, shift={self.shift_pressed}, recording={self.recording}")
        if self.recording and (not self.alt_pressed or not self.shift_pressed):
            logger.info("Key combination released - stopping recording")
            self.stop_recording()
    
    def start_recording(self):
        """Start recording audio"""
        if self.recording:
            return
            
        logger.info("Alt+Shift+K pressed - Starting recording...")
        
        # Wait for model to load if still loading
        if hasattr(self, 'preload_thread') and self.preload_thread.is_alive():
            logger.info("Waiting for model to load...")
            self.preload_thread.join()
        
        self.recording = True
        stop_recording.clear()
        
        # Start recording in background thread
        self.record_thread = threading.Thread(target=self.record_audio)
        self.record_thread.daemon = True
        self.record_thread.start()
    
    def stop_recording(self):
        """Stop recording and process audio"""
        if not self.recording:
            return
            
        logger.info("Key combination released - Stopping recording...")
        self.recording = False
        stop_recording.set()
        
        # Wait for recording to finish
        if self.record_thread:
            self.record_thread.join()
        
        # Process the recorded audio
        self.process_thread = threading.Thread(target=self.process_and_transcribe)
        self.process_thread.daemon = True
        self.process_thread.start()
    
    def record_audio(self):
        """Record audio using the existing function"""
        try:
            from t2 import record_audio_stream
            record_audio_stream()
        except Exception as e:
            logger.error(f"Recording error: {e}")
    
    def process_and_transcribe(self):
        """Process recorded audio and transcribe"""
        try:
            # Process the audio
            from t2 import process_audio_stream
            result, transcribe_time = process_audio_stream()
            
            # Clean up result
            transcription = result.strip()
            
            if transcription:
                # Copy to clipboard
                import pyperclip
                pyperclip.copy(transcription)
                
                # Play sound
                try:
                    subprocess.Popen(['mpg123', '-q', 'sounds/pop.mp3'], 
                                   stderr=subprocess.DEVNULL)
                except:
                    pass
                
                logger.info(f"Transcribed: {transcription}")
            else:
                logger.info("No speech detected")
                
        except Exception as e:
            logger.error(f"Transcription error: {e}")
    
    def run_daemon(self):
        """Run the daemon"""
        if not self.backend:
            logger.error("No keyboard monitoring backend available")
            return False
        
        logger.info("Starting Voice Transcriber global shortcut daemon...")
        logger.info(f"Using device: {DEVICE}")
        logger.info(f"Backend: {self.backend}")
        logger.info("Hold Alt+Shift+K to record, release any key to transcribe")
        logger.info("Press Escape to exit")
        
        # Set up signal handler for clean exit
        def signal_handler(sig, frame):
            logger.info("Shutting down daemon...")
            self.running = False
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        self.running = True
        
        try:
            if self.backend == 'evdev':
                self.run_evdev_backend()
            elif self.backend == 'pynput':
                self.run_pynput_backend()
            elif self.backend == 'polling':
                self.run_polling_backend()
        except Exception as e:
            logger.error(f"Error running {self.backend} backend: {e}")
            return False
        
        return True

def check_permissions():
    """Check if user has necessary permissions for global shortcuts"""
    issues = []
    
    # Check if user is in input group (for evdev)
    try:
        import grp
        input_group = grp.getgrnam('input')
        if os.getuid() not in input_group.gr_gid and os.getlogin() not in input_group.gr_mem:
            issues.append("User not in 'input' group. Run: sudo usermod -a -G input $USER")
    except:
        pass
    
    # Check if /dev/input devices are accessible
    input_devices = list(Path('/dev/input').glob('event*'))
    accessible_devices = []
    for device in input_devices:
        try:
            with open(device, 'rb'):
                accessible_devices.append(device)
        except PermissionError:
            continue
    
    if not accessible_devices:
        issues.append("No accessible input devices found")
    
    return issues

def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--check':
        # Check system compatibility
        logger.info("Checking system compatibility...")
        
        issues = check_permissions()
        if issues:
            logger.warning("Permission issues found:")
            for issue in issues:
                logger.warning(f"  - {issue}")
            logger.info("You may need to log out and back in after adding user to input group")
        else:
            logger.info("System appears compatible")
        
        return
    
    if len(sys.argv) > 1 and sys.argv[1] == '--transcribe':
        # Direct transcription call (for compatibility)
        logger.info("Direct transcription triggered...")
        try:
            # Quick model load and transcribe
            preload_thread = preload_model(device=DEVICE)
            if preload_thread.is_alive():
                preload_thread.join()
            
            result = record_and_transcribe()
            if result:
                # Notification removed - transcription complete silently
                pass
        except Exception as e:
            logger.error(f"Direct transcription error: {e}")
    else:
        # Run as daemon
        daemon = GlobalShortcutDaemon()
        if not daemon.run_daemon():
            logger.error("Failed to start daemon")
            sys.exit(1)

if __name__ == "__main__":
    main() 