#!/usr/bin/env python3
"""
T3 Voice Transcriber - Enhanced version combining features from t2.py and simple_voice_transcriber.py
Features:
- Global hotkeys (Alt+Shift) that work system-wide
- Audio device selection and configuration
- Visual notifications
- Automatic typing of transcription
- Wayland/X11 compatibility
- Windows global hotkey support
"""

import logging
import threading
import subprocess
import time
import os
import sys
import select
import json
import wave
import numpy as np
import pyaudio
import queue
import warnings

# Import transcription functionality
from transcribe2 import transcribe_audio, preload_model, get_model

# Import visual notifications module
from visual_notifications import VisualNotification

# Try to import clipboard and typing functionality
try:
    import pyperclip
    CLIPBOARD_AVAILABLE = True
except ImportError:
    CLIPBOARD_AVAILABLE = False

# Suppress warnings
warnings.filterwarnings("ignore", message=".*Init provider bridge failed.*")
warnings.filterwarnings("ignore", category=UserWarning)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Enable debug logging for troubleshooting hotkey issues
hotkey_logger = logging.getLogger(__name__ + '.hotkeys')
hotkey_logger.setLevel(logging.DEBUG)

# Audio configuration - Browser-like settings for better quality
CHUNK = 256  # Smaller buffer like browsers use (128-256 samples)
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 48000  # Default to 48kHz like browsers prefer
RECORD_SECONDS = 20
INPUT_DEVICE_INDEX = None
CONFIG_FILE = 'audio_device_config.json'
# Prioritize 48kHz like browsers, then fallback
FALLBACK_RATES = [48000, 44100, 22050, 16000, 8000]

# Device detection
def get_device():
    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        device = "cpu"
    return device

DEVICE = get_device()

# Audio buffer and control
audio_buffer = queue.Queue(maxsize=1000)  # Limit buffer size to prevent memory issues
stop_recording = threading.Event()

class WindowsGlobalHotkeys:
    """Windows-specific global hotkey system using pynput"""
    
    def __init__(self, callback_start, callback_stop):
        self.callback_start = callback_start
        self.callback_stop = callback_stop
        self.running = False
        self.hotkey_active = False
        self.listener = None
        self.alt_pressed = False
        self.shift_pressed = False
        
        # Try to import pynput
        try:
            from pynput import keyboard
            self.keyboard = keyboard
            self.available = True
            logger.info("Windows global hotkey system initialized using pynput")
        except ImportError as e:
            logger.error(f"pynput not available: {e}")
            logger.error("Install with: pip install pynput")
            self.available = False
    
    def on_press(self, key):
        """Handle key press events"""
        if not self.available:
            return
            
        try:
            # Track Alt and Shift keys
            if key == self.keyboard.Key.alt_l or key == self.keyboard.Key.alt_r:
                self.alt_pressed = True
                hotkey_logger.debug("ALT key PRESSED")
            elif key == self.keyboard.Key.shift_l or key == self.keyboard.Key.shift_r:
                self.shift_pressed = True
                hotkey_logger.debug("SHIFT key PRESSED")
            
            # Check if hotkey combination is now active
            if self.alt_pressed and self.shift_pressed and not self.hotkey_active:
                hotkey_logger.debug("Hotkey combination activated - starting recording")
                self.hotkey_active = True
                self.callback_start()
                
        except Exception as e:
            logger.error(f"Error in on_press: {e}")
    
    def on_release(self, key):
        """Handle key release events"""
        if not self.available:
            return
            
        try:
            # Track Alt and Shift keys
            if key == self.keyboard.Key.alt_l or key == self.keyboard.Key.alt_r:
                self.alt_pressed = False
                hotkey_logger.debug("ALT key RELEASED")
            elif key == self.keyboard.Key.shift_l or key == self.keyboard.Key.shift_r:
                self.shift_pressed = False
                hotkey_logger.debug("SHIFT key RELEASED")
            
            # Check if hotkey combination is no longer active
            if self.hotkey_active and not (self.alt_pressed and self.shift_pressed):
                hotkey_logger.debug("Hotkey combination released - stopping recording")
                self.hotkey_active = False
                self.callback_stop()
                
        except Exception as e:
            logger.error(f"Error in on_release: {e}")
    
    def run(self):
        """Start the global hotkey listener"""
        if not self.available:
            return False
        
        self.running = True
        logger.info("Starting Windows global hotkey listener for Alt+Shift")
        
        try:
            with self.keyboard.Listener(
                on_press=self.on_press,
                on_release=self.on_release) as listener:
                self.listener = listener
                listener.join()
            return True
        except Exception as e:
            logger.error(f"Error running Windows hotkey listener: {e}")
            return False
    
    def stop(self):
        """Stop the hotkey listener"""
        self.running = False
        if self.listener:
            try:
                self.listener.stop()
            except:
                pass

class WaylandGlobalHotkeys:
    """Global hotkey system using evdev"""
    
    def __init__(self, callback_start, callback_stop):
        self.callback_start = callback_start
        self.callback_stop = callback_stop
        self.running = False
        self.devices = []
        self.key_states = {}
        self.hotkey_active = False
        
        self.ALT_KEYS = [56, 100]  # KEY_LEFTALT, KEY_RIGHTALT
        self.SHIFT_KEYS = [42, 54]  # KEY_LEFTSHIFT, KEY_RIGHTSHIFT
        
        self.init_devices()
    
    def init_devices(self):
        try:
            import evdev
            self.evdev = evdev
        except ImportError as e:
            logger.error(f"Missing evdev: {e}")
            return False
        
        try:
            device_paths = evdev.list_devices()
            devices = []
            keyboards = []
            
            for path in device_paths:
                try:
                    device = evdev.InputDevice(path)
                    devices.append(device)
                except (PermissionError, OSError):
                    continue
            
            for device in devices:
                caps = device.capabilities()
                if evdev.ecodes.EV_KEY in caps:
                    key_caps = caps[evdev.ecodes.EV_KEY]
                    
                    has_alt = any(key in key_caps for key in [evdev.ecodes.KEY_LEFTALT, evdev.ecodes.KEY_RIGHTALT])
                    has_shift = any(key in key_caps for key in [evdev.ecodes.KEY_LEFTSHIFT, evdev.ecodes.KEY_RIGHTSHIFT])
                    
                    if has_alt and has_shift:
                        keyboards.append(device)
                        logger.info(f"Found keyboard: {device.name}")
            
            if not keyboards:
                logger.error("No suitable keyboard devices found")
                return False
                
            self.devices = keyboards
            return True
            
        except PermissionError:
            logger.error("Permission denied accessing input devices")
            logger.error("Run as root or add user to input group: sudo usermod -a -G input $USER")
            return False
        except Exception as e:
            logger.error(f"Error initializing devices: {e}")
            return False
    
    def is_hotkey_pressed(self):
        alt_pressed = any(self.key_states.get(key, False) for key in self.ALT_KEYS)
        shift_pressed = any(self.key_states.get(key, False) for key in self.SHIFT_KEYS)
        result = alt_pressed and shift_pressed
        
        # Debug log the key states occasionally
        if hasattr(self, '_debug_counter'):
            self._debug_counter += 1
        else:
            self._debug_counter = 0
            
        if self._debug_counter % 100 == 0:  # Log every 100 calls to avoid spam
            hotkey_logger.debug(f"Key states - Alt: {alt_pressed}, Shift: {shift_pressed}, Hotkey: {result}")
            
        return result
    
    def is_hotkey_released(self):
        alt_pressed = any(self.key_states.get(key, False) for key in self.ALT_KEYS)
        shift_pressed = any(self.key_states.get(key, False) for key in self.SHIFT_KEYS)
        return not (alt_pressed and shift_pressed)
    
    def handle_key_event(self, event):
        if event.type != self.evdev.ecodes.EV_KEY:
            return
        
        key_code = event.code
        key_state = event.value  # 0=release, 1=press, 2=repeat
        
        # Handle press and release (ignore repeat events for now)
        if key_state in [0, 1]:
            self.key_states[key_code] = (key_state == 1)
            
            # Debug key state changes for Alt and Shift keys
            if key_code in self.ALT_KEYS + self.SHIFT_KEYS:
                key_name = "ALT" if key_code in self.ALT_KEYS else "SHIFT"
                state_name = "PRESSED" if key_state == 1 else "RELEASED"
                hotkey_logger.debug(f"{key_name} key {state_name} (code: {key_code})")
        
        # Check if hotkey combination is now active
        hotkey_currently_pressed = self.is_hotkey_pressed()
        
        if hotkey_currently_pressed and not self.hotkey_active:
            hotkey_logger.debug("Hotkey combination activated - starting recording")
            self.hotkey_active = True
            self.callback_start()
        elif not hotkey_currently_pressed and self.hotkey_active:
            hotkey_logger.debug("Hotkey combination released - stopping recording")
            self.hotkey_active = False
            self.callback_stop()
    
    def run(self):
        if not self.devices:
            return False
        
        self.running = True
        logger.info(f"Monitoring {len(self.devices)} keyboard device(s)")
        
        while self.running:
            try:
                devices_map = {dev.fd: dev for dev in self.devices if dev.fd is not None}
                
                if not devices_map:
                    break
                
                r, w, x = select.select(devices_map, [], [], 1.0)
                
                for fd in r:
                    device = devices_map[fd]
                    try:
                        for event in device.read():
                            self.handle_key_event(event)
                    except OSError:
                        if device in self.devices:
                            self.devices.remove(device)
                        continue
                        
            except Exception as e:
                logger.error(f"Error in event loop: {e}")
                time.sleep(1)
        
        return True
    
    def stop(self):
        self.running = False

def load_audio_config():
    """Load audio device configuration"""
    global INPUT_DEVICE_INDEX
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                config = json.loads(f.read())
                INPUT_DEVICE_INDEX = config.get('input_device_index')
                logger.info(f"Loaded saved audio device: {INPUT_DEVICE_INDEX}")
    except Exception as e:
        logger.debug(f"Could not load audio config: {e}")

def save_audio_config():
    """Save audio device configuration"""
    try:
        config = {'input_device_index': INPUT_DEVICE_INDEX}
        with open(CONFIG_FILE, 'w') as f:
            f.write(json.dumps(config, indent=2))
        logger.info(f"Saved audio device config")
    except Exception as e:
        logger.error(f"Could not save audio config: {e}")

def select_audio_device():
    """Interactive audio device selection"""
    global INPUT_DEVICE_INDEX
    
    # Suppress PyAudio warnings
    stderr_fd = os.dup(2)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull_fd, 2)
    
    p = pyaudio.PyAudio()
    
    # Restore stderr
    os.dup2(stderr_fd, 2)
    os.close(devnull_fd)
    os.close(stderr_fd)
    
    print("\n🎤 Available Audio Input Devices:")
    print("=" * 60)
    
    input_devices = []
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info['maxInputChannels'] > 0:
            input_devices.append((i, info))
            current_marker = " ← CURRENT" if i == INPUT_DEVICE_INDEX else ""
            print(f"  {len(input_devices)-1}: Device {i} - {info['name']}")
            print(f"      Rate: {info['defaultSampleRate']} Hz, Channels: {info['maxInputChannels']}{current_marker}")
            print()
    
    p.terminate()
    
    if not input_devices:
        print("❌ No input devices found!")
        input("Press Enter to return to main menu...")
        return False
    
    print("=" * 60)
    print("Enter the number (0-{}) of the device you want to use, or 'c' to cancel:".format(len(input_devices)-1))
    
    try:
        choice = input("> ").strip().lower()
        
        if choice == 'c' or choice == '':
            print("📋 Device selection cancelled - returning to main menu")
            return False
        
        device_idx = int(choice)
        if 0 <= device_idx < len(input_devices):
            device_id, device_info = input_devices[device_idx]
            INPUT_DEVICE_INDEX = device_id
            print(f"✅ Selected: {device_info['name']}")
            save_audio_config()
            input("Press Enter to return to main menu...")
            return True
        else:
            print(f"❌ Invalid choice. Please enter 0-{len(input_devices)-1}")
            input("Press Enter to return to main menu...")
            return False
            
    except (ValueError, KeyboardInterrupt):
        print("📋 Device selection cancelled - returning to main menu")
        return False

def countdown_timer():
    """Display countdown timer during recording"""
    for i in range(RECORD_SECONDS, 0, -1):
        if stop_recording.is_set():
            break
        print(f'Recording time remaining: {i} seconds... (press space to stop)', end='\r')
        time.sleep(1)

def check_for_stop_key():
    """Check for space key to stop recording early"""
    import select
    try:
        import termios, tty
        old_settings = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin.fileno())
        
        while not stop_recording.is_set():
            if select.select([sys.stdin], [], [], 0) == ([sys.stdin], [], []):
                c = sys.stdin.read(1)
                if c == ' ':  # Space key
                    print("\nStopping recording early...")
                    stop_recording.set()
                    break
            time.sleep(0.1)
        
        # Restore terminal settings
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
    except (ImportError, AttributeError, termios.error):
        # Windows fallback
        try:
            import msvcrt
            while not stop_recording.is_set():
                if msvcrt.kbhit():
                    if msvcrt.getch() == b' ':
                        print("\nStopping recording early...")
                        stop_recording.set()
                        break
                time.sleep(0.1)
        except ImportError:
            pass

def record_audio_stream(interactive_mode=False):
    """Record audio directly into memory and stream to the buffer - Browser-like WebRTC style capture"""
    global RATE
    
    # Suppress PyAudio warnings
    stderr_fd = os.dup(2)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull_fd, 2)
    
    p = pyaudio.PyAudio()
    
    # Restore stderr
    os.dup2(stderr_fd, 2)
    os.close(devnull_fd)
    os.close(stderr_fd)
    
    # Show available input devices for debugging
    if interactive_mode:
        print("Available input devices:")
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if info['maxInputChannels'] > 0:
                print(f"  Device {i}: {info['name']} - Rate: {info['defaultSampleRate']}")
    
    # Get device info for optimal settings
    if INPUT_DEVICE_INDEX is not None:
        device_info = p.get_device_info_by_index(INPUT_DEVICE_INDEX)
    else:
        device_info = p.get_default_input_device_info()
    
    # Browser-like audio setup - prioritize device's native rate if it's in our list
    device_rate = int(device_info['defaultSampleRate'])
    if device_rate in FALLBACK_RATES:
        test_rates = [device_rate] + [r for r in FALLBACK_RATES if r != device_rate]
    else:
        test_rates = FALLBACK_RATES
    
    # Try different configurations until one works
    stream = None
    working_rate = None
    
    for rate in test_rates:
        # Browser-like small buffer sizes for smooth capture
        browser_chunk_sizes = [128, 256, 512]  # WebRTC typically uses 128-512 samples
        
        for chunk_size in browser_chunk_sizes:
            try:
                # Calculate buffer time (browsers aim for 2.6-5.3ms buffers)
                buffer_time_ms = (chunk_size / rate) * 1000
                
                stream = p.open(
                    format=FORMAT, 
                    channels=CHANNELS, 
                    rate=rate, 
                    input=True,
                    input_device_index=INPUT_DEVICE_INDEX,
                    frames_per_buffer=chunk_size
                )
                working_rate = rate
                working_chunk = chunk_size
                
                if interactive_mode:
                    print(f"Using: {rate} Hz, {chunk_size} samples ({buffer_time_ms:.1f}ms buffer)")
                    print(f"Device: {device_info['name']}")
                else:
                    logger.info(f"Browser-like setup: {rate} Hz, {chunk_size} samples ({buffer_time_ms:.1f}ms)")
                    logger.info(f"Device: {device_info['name']}")
                break
                
            except Exception as e:
                if interactive_mode:
                    print(f"Config {rate}Hz/{chunk_size} failed: {e}")
                else:
                    logger.debug(f"Config {rate}Hz/{chunk_size} failed: {e}")
                continue
        
        if stream is not None:
            break
    
    if stream is None:
        if interactive_mode:
            print("ERROR: Could not open audio stream with any configuration")
        else:
            logger.error("Could not open audio stream with any configuration")
        p.terminate()
        return []
    
    # Update global RATE variable for other functions
    RATE = working_rate
    
    frames = []
    max_amplitude = 0
    total_chunks = 0
    
    # Start countdown and keypress detection only in interactive mode
    if interactive_mode:
        # Start countdown in a separate thread
        countdown_thread = threading.Thread(target=countdown_timer)
        countdown_thread.daemon = True
        countdown_thread.start()
        
        # Listen for keypress to stop early
        input_thread = threading.Thread(target=check_for_stop_key)
        input_thread.daemon = True
        input_thread.start()
        
        # Calculate max chunks for the recording duration
        max_chunks = int(RATE / working_chunk * RECORD_SECONDS)
        print("Recording... Press Space to stop")
        
        # Browser-like recording with very frequent small reads
        for i in range(max_chunks):
            if stop_recording.is_set():
                break
            try:
                # Read small chunks very frequently like browsers do
                data = stream.read(working_chunk, exception_on_overflow=False)
                frames.append(data)
                total_chunks += 1
                
                # Monitor audio levels for debugging
                audio_data = np.frombuffer(data, dtype=np.int16)
                amplitude = np.max(np.abs(audio_data))
                max_amplitude = max(max_amplitude, amplitude)
                
                # Show audio level indicator every 20 chunks (less frequent display)
                if i % 20 == 0 and amplitude > 100:
                    level_bars = int(amplitude / 1000)
                    print(f"Audio level: {'█' * min(level_bars, 20)} ({amplitude})", end='\r')
                    
            except Exception as e:
                if interactive_mode:
                    print(f"Recording error: {e}")
                else:
                    logger.warning(f"Recording error: {e}")
                break
    else:
        # Global hotkey mode - record until stop signal with browser-like frequent reads
        logger.debug("Starting continuous recording loop (hotkey mode)")
        chunk_count = 0
        while not stop_recording.is_set():
            try:
                # Very frequent small reads like browsers
                data = stream.read(working_chunk, exception_on_overflow=False)
                frames.append(data)
                total_chunks += 1
                chunk_count += 1
                
                # Monitor audio levels
                audio_data = np.frombuffer(data, dtype=np.int16)
                amplitude = np.max(np.abs(audio_data))
                max_amplitude = max(max_amplitude, amplitude)
                
                # Log progress every 50 chunks to avoid spam
                if chunk_count % 50 == 0:
                    logger.debug(f"Recording... captured {chunk_count} chunks, max amplitude: {max_amplitude}")
                    
            except Exception as e:
                logger.error(f"Recording error: {e}")
                break
        
        logger.debug(f"Recording loop ended. Total chunks captured: {chunk_count}")
        logger.debug(f"stop_recording.is_set() = {stop_recording.is_set()}")
    
    if interactive_mode:
        print(f"\nFinished recording - Max audio level: {max_amplitude}")
        print(f"Captured {total_chunks} chunks at {working_rate/working_chunk:.1f} chunks/sec")
        
        if max_amplitude < 500:
            print("⚠️  WARNING: Very low audio levels detected!")
            print("   - Check microphone is connected and not muted")
            print("   - Try speaking louder or closer to microphone")
            print("   - Check system audio input settings")
    else:
        logger.info(f"Recording finished - Max audio level: {max_amplitude}")
        logger.info(f"Captured {total_chunks} chunks at {working_rate/working_chunk:.1f} chunks/sec")
        
        if max_amplitude < 500:
            logger.warning("Very low audio levels detected!")
    
    # No longer needed - we process frames directly instead of using buffer
    # audio_buffer.put(None)
    
    # Clean up
    stream.stop_stream()
    stream.close()
    p.terminate()
    
    # Save to file for backup and debugging
    try:
        filename = 'output.wav' if interactive_mode else 'temp_t3_output.wav'
        
        # Browser-like automatic gain control - boost quiet audio
        audio_data = b''.join(frames)
        if len(audio_data) > 0:
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            current_max = np.max(np.abs(audio_array))
            
            # If audio is too quiet (like browsers do AGC), boost it
            if current_max > 0 and current_max < 8000:  # Boost if below ~25% of max
                boost_factor = min(8000 / current_max, 3.0)  # Cap boost at 3x
                boosted_audio = (audio_array * boost_factor).astype(np.int16)
                audio_data = boosted_audio.tobytes()
                
                if interactive_mode:
                    print(f"Applied browser-like AGC: boosted {boost_factor:.1f}x (max: {current_max} -> {np.max(np.abs(boosted_audio))})")
                else:
                    logger.info(f"Applied AGC boost: {boost_factor:.1f}x")
        
        with wave.open(filename, 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(RATE)
            wf.writeframes(audio_data)
        if interactive_mode:
            print(f"Audio saved to {filename} for debugging")
        else:
            logger.debug(f"Audio saved to {filename} for debugging")
    except Exception as e:
        if interactive_mode:
            print(f"Warning: Could not save audio file: {e}")
        else:
            logger.warning(f"Could not save audio file: {e}")
    
    return frames

def process_audio_stream(audio_frames):
    """Process the audio frames directly - no longer uses the buffer"""
    # Get preloaded model
    model = get_model(device=DEVICE)
    
    if not audio_frames:
        return "", 0
    
    # Convert audio data to proper format for transcription
    audio_data = b''.join(audio_frames)
    
    # Start transcription immediately
    transcribe_start_time = time.time()
    
    # Process the complete audio
    temp_file = "temp_t3_output.wav"
    with wave.open(temp_file, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(RATE)
        wf.writeframes(audio_data)
    
    # Transcribe with optimized parameters
    # Suppress ONNX warnings during transcription
    stderr_fd = os.dup(2)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull_fd, 2)
    result = transcribe_audio(audio_path=temp_file, device=DEVICE)
    # Restore stderr
    os.dup2(stderr_fd, 2)
    os.close(devnull_fd)
    os.close(stderr_fd)
    
    transcribe_end_time = time.time()
    
    # Clean up temp file
    try:
        os.remove(temp_file)
    except:
        pass
    
    return result, transcribe_end_time - transcribe_start_time

def is_windows():
    """Check if running on Windows"""
    return os.name == 'nt' or sys.platform.startswith('win')

def type_text(text):
    """Type text using system tools"""
    # On Windows, try pynput for typing first
    if is_windows():
        try:
            from pynput.keyboard import Controller, Key
            keyboard_controller = Controller()
            keyboard_controller.type(text)
            logger.info("Text typed using pynput")
            return True
        except ImportError:
            logger.debug("pynput not available for typing")
        except Exception as e:
            logger.debug(f"pynput typing failed: {e}")
    
    # Linux/Unix typing methods
    try:
        # Try xdotool first (X11)
        subprocess.run(['xdotool', 'type', text], check=True, stderr=subprocess.DEVNULL)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    
    try:
        # Try ydotool (Wayland)
        subprocess.run(['ydotool', 'type', text], check=True, stderr=subprocess.DEVNULL)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    
    # Fallback to clipboard
    if CLIPBOARD_AVAILABLE:
        try:
            pyperclip.copy(text)
            if is_windows():
                logger.info("Text copied to clipboard - use Ctrl+V to paste")
            else:
                logger.info("Text copied to clipboard (typing not available)")
            return True
        except:
            pass
    
    logger.error("Could not type or copy text")
    return False

class T3VoiceTranscriber:
    def __init__(self):
        self.recording = False
        self.record_thread = None
        self.process_thread = None
        self.hotkey_system = None
        self.running = False
        self.recorded_frames = []  # Store recorded audio frames
        
        # Load audio config
        load_audio_config()
        
        # Initialize visual notification with app name
        self.visual_notification = VisualNotification("T3 Voice Transcriber")
        
        # Preload model
        logger.info("Loading transcription model...")
        self.preload_thread = preload_model(device=DEVICE)
        
        # Initialize hotkeys
        self.init_hotkeys()
        
    def init_hotkeys(self):
        try:
            if is_windows():
                # Use Windows-specific hotkey system
                self.hotkey_system = WindowsGlobalHotkeys(
                    callback_start=self.start_recording,
                    callback_stop=self.stop_recording
                )
                
                if self.hotkey_system.available:
                    logger.info("Windows global hotkey system initialized")
                    return True
                else:
                    logger.error("Failed to initialize Windows global hotkey system")
                    return False
            else:
                # Use Linux/Unix hotkey system
                self.hotkey_system = WaylandGlobalHotkeys(
                    callback_start=self.start_recording,
                    callback_stop=self.stop_recording
                )
                
                if self.hotkey_system.devices:
                    logger.info("Linux global hotkey system initialized")
                    return True
                else:
                    logger.error("Failed to initialize Linux global hotkey system")
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
        try:
            subprocess.Popen(['mpg123', '-q', 'app/sounds/pop2.mp3'], 
                           stderr=subprocess.DEVNULL)
        except:
            pass
        
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
            result, transcribe_time = process_audio_stream(self.recorded_frames)
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
        # Check if hotkey system is available
        if not self.hotkey_system:
            logger.error("❌ No global hotkey system available")
            if is_windows():
                logger.error("💡 Install dependencies: pip install pynput")
            else:
                logger.error("💡 Run as root or add user to input group: sudo usermod -a -G input $USER")
                logger.error("💡 Install dependencies: pip install evdev")
            return False
        
        # For Windows, check if pynput is available
        if is_windows() and not self.hotkey_system.available:
            logger.error("❌ Windows global hotkey system not available")
            logger.error("💡 Install dependencies: pip install pynput")
            return False
        
        # For Linux, check if devices are available
        if not is_windows() and not self.hotkey_system.devices:
            logger.error("❌ No Linux keyboard devices available")
            logger.error("💡 Run as root or add user to input group: sudo usermod -a -G input $USER")
            logger.error("💡 Install dependencies: pip install evdev")
            return False
        
        logger.info("🎤 T3 Voice Transcriber started!")
        logger.info(f"📱 Using device: {DEVICE}")
        if is_windows():
            logger.info("🔥 Hold Alt+Shift to record, release to transcribe and type")
            logger.info("💡 Global hotkeys work system-wide on Windows!")
        else:
            logger.info("🔥 Hold Alt+Shift to record, release to transcribe and type")
            logger.info("💡 Global hotkeys work system-wide on Linux!")
        logger.info("🔄 Use Ctrl+C to exit")
        
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

def check_permissions():
    """Check permissions for input device access"""
    if is_windows():
        # On Windows, check if pynput is available
        try:
            from pynput import keyboard
            logger.info("✅ pynput is available for Windows global hotkeys")
            return True
        except ImportError:
            logger.error("❌ pynput not available!")
            logger.error("🔧 Install with: pip install pynput")
            return False
    else:
        # Linux/Unix permission checks
        import grp
        import pwd
        
        if os.geteuid() == 0:
            logger.info("✅ Running as root")
            return True
        
        try:
            current_user = pwd.getpwuid(os.getuid()).pw_name
            current_gids = os.getgroups()
            input_group = grp.getgrnam('input')
            
            if input_group.gr_gid in current_gids:
                logger.info("✅ User is in input group")
                return True
            else:
                logger.error("❌ Permission issue!")
                logger.error(f"👤 Current user: {current_user}")
                logger.error("🔧 Fix with: sudo usermod -a -G input $USER")
                logger.error("   Then log out and back in")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error checking permissions: {e}")
            return False

def main():
    """Main function with interactive mode"""
    logger.info("T3 Voice Transcriber")
    logger.info(f"Using device: {DEVICE}")
    
    # Check for command line arguments
    if len(sys.argv) > 1:
        arg = sys.argv[1].strip().lower()
        
        # Load audio config
        load_audio_config()
        
        # Preload model
        preload_thread = preload_model(device=DEVICE)
        
        # Handle command line mode selection
        if arg == '1':
            # Global hotkey mode
            logger.info("Starting global hotkey mode...")
            if not check_permissions():
                logger.error("Cannot use global hotkeys without proper permissions")
                if is_windows():
                    logger.error("💡 Install with: pip install pynput")
                else:
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

def print_usage():
    """Print usage information"""
    print()
    print("Usage: python app/t3.py [MODE]")
    print()
    print("Modes:")
    if is_windows():
        print("  1        Global hotkeys (Alt+Shift) - requires pynput")
    else:
        print("  1        Global hotkeys (Alt+Shift) - requires root/input group")
    print("  2        Interactive mode (Space to record)")
    print("  3 or i   Select audio device")
    print("  4        Exit")
    print("  help     Show this help message")
    print()
    print("Examples:")
    print("  python app/t3.py 1      # Start global hotkey mode")
    print("  python app/t3.py 2      # Start interactive mode")
    print("  python app/t3.py i      # Open device selection")
    print("  python app/t3.py        # Show interactive menu")
    print()

def run_interactive_mode():
    """Run interactive mode"""
    interactive_mode_running = True
    while interactive_mode_running:
        try:
            print("> ", end="", flush=True)
            
            import termios, tty
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setraw(sys.stdin.fileno())
                ch = sys.stdin.read(1)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            
            if ch in [' ', '\r', '\n']:
                print()
                # Record and transcribe
                stop_recording.clear()
                
                # Start recording
                recorded_frames = []
                def record_wrapper():
                    nonlocal recorded_frames
                    recorded_frames = record_audio_stream(interactive_mode=True)
                
                record_thread = threading.Thread(target=record_wrapper)
                record_thread.start()
                
                logger.info("Recording... Press Space to stop")
                
                # Wait for space to stop
                while True:
                    try:
                        tty.setraw(sys.stdin.fileno())
                        ch2 = sys.stdin.read(1)
                        if ch2 == ' ':
                            stop_recording.set()
                            break
                    finally:
                        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                
                # Wait for recording to finish
                record_thread.join()
                
                # Process audio
                result, transcribe_time = process_audio_stream(recorded_frames)
                transcription = result.strip()
                
                if transcription:
                    if type_text(transcription):
                        logger.info(f"✅ Typed: {transcription}")
                    else:
                        logger.error("Failed to type text")
                else:
                    logger.info("❌ No speech detected")
                
            elif ch.lower() == 'i':
                print()
                select_audio_device()
            elif ch.lower() in ['q', 'Q']:
                print("\nExiting interactive mode...")
                interactive_mode_running = False
                break
            elif ch.lower() == 'm':
                print("\nExiting interactive mode...")
                interactive_mode_running = False
                break
            
            print("\nReady (Space=record, i=device, q=quit)")
            
        except KeyboardInterrupt:
            print("\nExiting interactive mode...")
            interactive_mode_running = False
            break
        except ImportError:
            # Fallback for systems without termios
            choice = input("Space=record, i=device, q=quit: ").strip().lower()
            if choice in ['', ' ']:
                # Simple recording without hotkey stop
                stop_recording.clear()
                
                recorded_frames = []
                def record_wrapper():
                    nonlocal recorded_frames
                    recorded_frames = record_audio_stream(interactive_mode=True)
                
                record_thread = threading.Thread(target=record_wrapper)
                record_thread.start()
                
                input("Recording... Press Enter to stop")
                stop_recording.set()
                record_thread.join()
                
                result, _ = process_audio_stream(recorded_frames)
                transcription = result.strip()
                
                if transcription:
                    if type_text(transcription):
                        logger.info(f"✅ Typed: {transcription}")
                else:
                    logger.info("❌ No speech detected")
            elif choice == 'i':
                select_audio_device()
            elif choice in ['q', 'm']:
                interactive_mode_running = False
                break

def run_interactive_menu():
    """Run the interactive menu"""
    # Load audio config
    load_audio_config()
    
    # Check if this is first run (no config file exists)
    first_run = not os.path.exists(CONFIG_FILE)
    
    # Preload model
    preload_thread = preload_model(device=DEVICE)
    
    # On first run, automatically show device selection
    if first_run:
        logger.info("")
        logger.info("🎤 FIRST RUN SETUP")
        logger.info("Let's configure your audio input device...")
        logger.info("")
        
        if select_audio_device():
            logger.info("✅ Audio device configured!")
        else:
            logger.info("❌ No device selected - using default")
        
        logger.info("")
        if is_windows():
            logger.info("🔧 WINDOWS SETUP")
            logger.info("For global hotkeys (Alt+Shift), you need:")
            logger.info("  • Install pynput: pip install pynput")
            logger.info("  • No special permissions required!")
        else:
            logger.info("🔧 LINUX KEYBOARD PERMISSIONS")
            logger.info("For global hotkeys (Alt+Shift), you need:")
            logger.info("  • Run as root, OR")
            logger.info("  • Add user to input group: sudo usermod -a -G input $USER")
            logger.info("  • Then log out and back in")
        logger.info("")
        input("Press Enter to continue to main menu...")
    
    while True:  # Main menu loop
        logger.info("")
        logger.info("Choose mode:")
        if is_windows():
            logger.info("1. Global hotkeys (Alt+Shift) - requires pynput")
        else:
            logger.info("1. Global hotkeys (Alt+Shift) - requires root/input group")
        logger.info("2. Interactive mode (Space to record)")
        logger.info("3. Select audio device")
        logger.info("i. Select audio device (shortcut)")
        logger.info("4. Exit")
        
        try:
            choice = input("> ").strip().lower()
            
            if choice == '1':
                # Global hotkey mode
                if not check_permissions():
                    logger.error("Cannot use global hotkeys without proper permissions")
                    if is_windows():
                        logger.error("💡 Install with: pip install pynput")
                    else:
                        logger.error("💡 Run as root or add user to input group: sudo usermod -a -G input $USER")
                    input("Press Enter to return to main menu...")
                    continue  # Return to menu
                
                transcriber = T3VoiceTranscriber()
                result = transcriber.run()
                # After hotkey mode exits, return to menu
                logger.info("Global hotkey mode ended")
                input("Press Enter to return to main menu...")
                continue
                
            elif choice == '2':
                # Interactive mode
                logger.info("Interactive mode - Press Space to record, 'i' for device selection, 'q' to quit")
                
                # Wait for model
                if preload_thread.is_alive():
                    logger.info("Waiting for model to load...")
                    preload_thread.join()
                    logger.info("Model loaded!")
                
                run_interactive_mode()
                # After interactive mode ends, return to main menu
                continue
                
            elif choice == '3' or choice == 'i':
                # Device selection
                select_audio_device()
                # After device selection (whether successful or cancelled), return to menu
                continue
                
            elif choice == '4':
                logger.info("Exiting...")
                break
                
            else:
                logger.info("Invalid choice. Please enter 1, 2, 3, i, or 4.")
                continue
                
        except KeyboardInterrupt:
            logger.info("\nExiting...")
            break

if __name__ == "__main__":
    main() 