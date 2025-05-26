#!/usr/bin/env python3
"""
Simple Voice Transcriber with Alt+Shift+K shortcut
Enhanced with multiple keyboard backends for better terminal compatibility
"""
import logging
import threading
import subprocess
import time
import os
import sys
import select

# Import transcription functionality
from t2 import record_and_transcribe, preload_model, get_model, DEVICE, record_audio_stream, process_audio_stream, stop_recording

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SimpleVoiceTranscriber:
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
            ('pynput', self.init_pynput_backend)
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
        """Initialize evdev backend (best for terminals and Wayland)"""
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
            self.evdev = evdev
            self.ecodes = ecodes
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

    def run_evdev_backend(self):
        """Run keyboard monitoring using evdev"""
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
                            if event.type == self.ecodes.EV_KEY:
                                # Check for Alt keys
                                if event.code in [self.ecodes.KEY_LEFTALT, self.ecodes.KEY_RIGHTALT]:
                                    if event.value == 1:  # Key press
                                        self.alt_pressed = True
                                        logger.debug("Alt pressed")
                                    elif event.value == 0:  # Key release
                                        self.alt_pressed = False
                                        logger.debug("Alt released")
                                        self.check_stop_recording()
                                
                                # Check for Shift keys
                                elif event.code in [self.ecodes.KEY_LEFTSHIFT, self.ecodes.KEY_RIGHTSHIFT]:
                                    if event.value == 1:  # Key press
                                        self.shift_pressed = True
                                        logger.debug("Shift pressed")
                                    elif event.value == 0:  # Key release
                                        self.shift_pressed = False
                                        logger.debug("Shift released")
                                        self.check_stop_recording()
                                
                                # Check for K key
                                elif event.code == self.ecodes.KEY_K:
                                    if event.value == 1:  # Key press
                                        logger.debug("K pressed")
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
            except AttributeError:
                pass
        
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
            except AttributeError:
                pass
        
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

    def check_start_recording(self):
        """Check if Alt+Shift+K combination is pressed to start recording"""
        logger.debug(f"Checking start: alt={self.alt_pressed}, shift={self.shift_pressed}, recording={self.recording}")
        if self.alt_pressed and self.shift_pressed and not self.recording:
            logger.info("🎙️ Alt+Shift+K combination detected - starting recording!")
            self.start_recording()

    def check_stop_recording(self):
        """Check if any key in the combination is released to stop recording"""
        logger.debug(f"Checking stop: alt={self.alt_pressed}, shift={self.shift_pressed}, recording={self.recording}")
        if self.recording and (not self.alt_pressed or not self.shift_pressed):
            logger.info("⏹️ Key combination released - stopping recording...")
            self.stop_recording()

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
        
        # Play start recording sound
        try:
            subprocess.Popen(['mpg123', '-q', 'app/sounds/pop2.mp3'], 
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
        
        # Play stop recording sound
        try:
            subprocess.Popen(['mpg123', '-q', 'app/sounds/pop2.mp3'], 
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
        """Record audio using the existing function"""
        try:
            record_audio_stream()
        except Exception as e:
            logger.error(f"Recording error: {e}")

    def process_and_transcribe(self):
        """Process recorded audio and transcribe"""
        try:
            # Process the audio
            result, transcribe_time = process_audio_stream()
            
            # Clean up result
            transcription = result.strip()
            
            if transcription:
                # Copy to clipboard
                import pyperclip
                pyperclip.copy(transcription)
                
                # Play sound
                try:
                    subprocess.Popen(['mpg123', '-q', 'app/sounds/pop.mp3'], 
                                   stderr=subprocess.DEVNULL)
                except:
                    pass
                
                logger.info(f"✅ Transcribed: {transcription}")
            else:
                logger.info("❌ No speech detected")
                
        except Exception as e:
            logger.error(f"Transcription error: {e}")

    def run(self):
        """Run the voice transcriber"""
        if not self.backend:
            logger.error("No keyboard monitoring backend available")
            return False
        
        logger.info("🎤 Simple Voice Transcriber started!")
        logger.info(f"📱 Using device: {DEVICE}")
        logger.info(f"🔧 Backend: {self.backend}")
        logger.info("🔥 Hold Alt+Shift+K to record, release any key to transcribe")
        logger.info("🔄 Use Ctrl+C to exit")
        
        if self.backend == 'evdev':
            logger.info("💡 Using evdev backend - works great in terminals like Alacritty!")
        
        self.running = True
        
        try:
            if self.backend == 'evdev':
                self.run_evdev_backend()
            elif self.backend == 'pynput':
                self.run_pynput_backend()
        except Exception as e:
            logger.error(f"Error running {self.backend} backend: {e}")
            return False
        
        return True

if __name__ == "__main__":
    transcriber = SimpleVoiceTranscriber()
    transcriber.run() 
