#!/usr/bin/env python3
"""
T3 Voice Transcriber - Enhanced version combining features from t2.py and simple_voice_transcriber.py
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

# Try to import clipboard and typing functionality
try:
    import pyperclip
    CLIPBOARD_AVAILABLE = True
except ImportError:
    CLIPBOARD_AVAILABLE = False

try:
    import tkinter as tk
    VISUAL_NOTIFICATIONS_AVAILABLE = True
except ImportError:
    VISUAL_NOTIFICATIONS_AVAILABLE = False

# Suppress warnings
warnings.filterwarnings("ignore", message=".*Init provider bridge failed.*")
warnings.filterwarnings("ignore", category=UserWarning)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
audio_buffer = queue.Queue()
stop_recording = threading.Event()

class VisualNotification:
    """Enhanced visual notification system"""
    def __init__(self):
        self.active = False
        self.overlay_processes = []
        self.display_env = self._detect_display_environment()
        self.available_tools = self._detect_available_tools()
        logger.info(f"Display environment: {self.display_env}")
        
    def _detect_display_environment(self):
        if os.environ.get('WAYLAND_DISPLAY'):
            return 'wayland'
        elif os.environ.get('DISPLAY'):
            return 'x11'
        else:
            return 'terminal'
    
    def _detect_available_tools(self):
        tools = []
        for tool in ['zenity', 'yad', 'kdialog', 'xmessage']:
            try:
                subprocess.run(['which', tool], capture_output=True, check=True)
                tools.append(tool)
            except:
                pass
        return tools
    
    def show_recording(self):
        if self.active:
            return
        self.active = True
        self._create_overlay("🔴 RECORDING", "#ff4444", persistent=True)
        self._show_terminal_notification("🔴 RECORDING - Release Alt+Shift to stop")
    
    def show_processing(self):
        self._cleanup_overlays()
        self._create_overlay("⚡ TRANSCRIBING", "#ffaa00", persistent=True)
        self._show_terminal_notification("⚡ TRANSCRIBING AUDIO...")
    
    def show_completed(self):
        self._cleanup_overlays()
        self._create_overlay("✅ TYPED", "#00aaff", persistent=False)
        self._show_terminal_notification("✅ TRANSCRIPTION TYPED")
        threading.Timer(2.0, self.hide_notification).start()
    
    def _create_overlay(self, text, color, persistent=False):
        if VISUAL_NOTIFICATIONS_AVAILABLE:
            try:
                self._create_tkinter_overlay(text, color, persistent)
                return
            except Exception as e:
                logger.debug(f"Tkinter overlay failed: {e}")
        
        if 'zenity' in self.available_tools:
            try:
                cmd = [
                    'zenity', '--info',
                    '--title=T3 Voice Transcriber',
                    f'--text=<span font="20" weight="bold">{text}</span>',
                    '--width=400', '--height=150'
                ]
                if not persistent:
                    cmd.append('--timeout=3')
                
                process = subprocess.Popen(cmd, stderr=subprocess.DEVNULL)
                self.overlay_processes.append(process)
                return
            except Exception as e:
                logger.debug(f"Zenity overlay failed: {e}")
    
    def _create_tkinter_overlay(self, text, color, persistent):
        overlay_script = f'''
import tkinter as tk
import time

def create_overlay():
    try:
        root = tk.Tk()
        root.title("T3 Voice Transcriber")
        root.overrideredirect(True)
        root.attributes('-topmost', True)
        root.attributes('-alpha', 0.95)
        root.configure(bg='{color}')
        
        screen_width = root.winfo_screenwidth()
        window_width = 500
        window_height = 120
        x = (screen_width - window_width) // 2
        y = 100
        
        root.geometry(f"{{window_width}}x{{window_height}}+{{x}}+{{y}}")
        
        border_frame = tk.Frame(root, bg='black', bd=3)
        border_frame.pack(fill='both', expand=True, padx=3, pady=3)
        
        inner_frame = tk.Frame(border_frame, bg='{color}')
        inner_frame.pack(fill='both', expand=True, padx=2, pady=2)
        
        label = tk.Label(
            inner_frame,
            text="{text}",
            bg='{color}',
            fg='black',
            font=('Arial', 24, 'bold'),
            pady=20
        )
        label.pack(expand=True)
        
        if {str(persistent).lower()}:
            root.mainloop()
        else:
            root.after(3000, root.quit)
            root.mainloop()
            
    except Exception as e:
        print(f"Overlay error: {{e}}")

if __name__ == "__main__":
    create_overlay()
'''
        
        overlay_file = f'/tmp/t3_overlay_{int(time.time())}.py'
        with open(overlay_file, 'w') as f:
            f.write(overlay_script)
        
        process = subprocess.Popen(['python3', overlay_file], stderr=subprocess.DEVNULL)
        self.overlay_processes.append(process)
        
        threading.Timer(10.0, lambda: self._cleanup_temp_file(overlay_file)).start()
    
    def _cleanup_temp_file(self, filepath):
        try:
            os.remove(filepath)
        except:
            pass
    
    def _cleanup_overlays(self):
        for process in self.overlay_processes:
            try:
                process.terminate()
            except:
                pass
        self.overlay_processes = []
    
    def _show_terminal_notification(self, text):
        try:
            print(f"\033[2J\033[H", end='')
            
            if "RECORDING" in text:
                print("\033[92m" + "█" * 70)
                print("█" + " " * 68 + "█")
                print("█" + f"🔴 RECORDING IN PROGRESS".center(68) + "█")
                print("█" + f"Release Alt+Shift to stop".center(68) + "█")
                print("█" + " " * 68 + "█")
                print("█" * 70 + "\033[0m")
            elif "TRANSCRIBING" in text:
                print("\033[93m" + "█" * 70)
                print("█" + " " * 68 + "█")
                print("█" + f"⚡ TRANSCRIBING AUDIO".center(68) + "█")
                print("█" + f"Processing speech to text...".center(68) + "█")
                print("█" + " " * 68 + "█")
                print("█" * 70 + "\033[0m")
            elif "TYPED" in text:
                print("\033[94m" + "█" * 70)
                print("█" + " " * 68 + "█")
                print("█" + f"✅ TEXT TYPED SUCCESSFULLY!".center(68) + "█")
                print("█" + f"Transcription complete".center(68) + "█")
                print("█" + " " * 68 + "█")
                print("█" * 70 + "\033[0m")
                
        except Exception as e:
            logger.debug(f"Terminal notification failed: {e}")
    
    def hide_notification(self):
        if not self.active:
            return
        self.active = False
        self._cleanup_overlays()
        try:
            print(f"\033[2J\033[H", end='')
            print("🎤 T3 Voice Transcriber Ready")
            print("Hold Alt+Shift to record")
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
        return alt_pressed and shift_pressed
    
    def is_hotkey_released(self):
        alt_pressed = any(self.key_states.get(key, False) for key in self.ALT_KEYS)
        shift_pressed = any(self.key_states.get(key, False) for key in self.SHIFT_KEYS)
        return not (alt_pressed and shift_pressed)
    
    def handle_key_event(self, event):
        if event.type != self.evdev.ecodes.EV_KEY:
            return
        
        key_code = event.code
        key_state = event.value
        
        if key_state in [0, 1]:
            self.key_states[key_code] = (key_state == 1)
        
        if self.is_hotkey_pressed() and not self.hotkey_active:
            logger.debug("Hotkey activated")
            self.hotkey_active = True
            self.callback_start()
        elif self.hotkey_active and self.is_hotkey_released():
            logger.debug("Hotkey released")
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
                audio_buffer.put(data)
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
        while not stop_recording.is_set():
            try:
                # Very frequent small reads like browsers
                data = stream.read(working_chunk, exception_on_overflow=False)
                frames.append(data)
                audio_buffer.put(data)
                total_chunks += 1
                
                # Monitor audio levels
                audio_data = np.frombuffer(data, dtype=np.int16)
                amplitude = np.max(np.abs(audio_data))
                max_amplitude = max(max_amplitude, amplitude)
                    
            except Exception as e:
                logger.error(f"Recording error: {e}")
                break
    
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
    
    # Add a None to mark the end of the stream
    audio_buffer.put(None)
    
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

def process_audio_stream():
    """Process the audio stream as it's being recorded - exact copy from t2.py"""
    # Get preloaded model
    model = get_model(device=DEVICE)
    
    # Collect audio data until we get None (end of stream)
    chunks = []
    while True:
        chunk = audio_buffer.get()
        if chunk is None:
            break
        chunks.append(chunk)
    
    if not chunks:
        return "", 0
    
    # Convert audio data to proper format for transcription
    audio_data = b''.join(chunks)
    
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

def type_text(text):
    """Type text using system tools"""
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
        
        # Load audio config
        load_audio_config()
        
        # Initialize visual notification
        self.visual_notification = VisualNotification()
        
        # Preload model
        logger.info("Loading transcription model...")
        self.preload_thread = preload_model(device=DEVICE)
        
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
            return
            
        # Wait for model to load
        if hasattr(self, 'preload_thread') and self.preload_thread.is_alive():
            logger.info("Waiting for model to load...")
            self.preload_thread.join()
        
        self.recording = True
        stop_recording.clear()
        
        # Show notification
        try:
            self.visual_notification.show_recording()
        except Exception as e:
            logger.warning(f"Visual notification error: {e}")
        
        # Play sound
        try:
            subprocess.Popen(['mpg123', '-q', 'app/sounds/pop2.mp3'], 
                           stderr=subprocess.DEVNULL)
        except:
            pass
        
        # Start recording
        self.record_thread = threading.Thread(target=lambda: record_audio_stream(interactive_mode=False))
        self.record_thread.daemon = True
        self.record_thread.start()

    def stop_recording(self):
        if not self.recording:
            return
            
        self.recording = False
        stop_recording.set()
        
        # Show processing notification
        try:
            self.visual_notification.show_processing()
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
            self.record_thread.join()
        
        # Process audio
        self.process_thread = threading.Thread(target=self.process_and_transcribe)
        self.process_thread.daemon = True
        self.process_thread.start()

    def process_and_transcribe(self):
        try:
            result, transcribe_time = process_audio_stream()
            transcription = result.strip()
            
            if transcription:
                # Type the text
                if type_text(transcription):
                    # Show completion notification
                    try:
                        self.visual_notification.show_completed()
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
                    logger.error("Failed to type transcription")
            else:
                # Hide notification
                try:
                    self.visual_notification.hide_notification()
                except Exception as e:
                    logger.warning(f"Visual notification error: {e}")
                        
                logger.info("❌ No speech detected")
                self.offer_device_change()
                
        except Exception as e:
            try:
                self.visual_notification.hide_notification()
            except:
                pass
            logger.error(f"Transcription error: {e}")
            self.offer_device_change()

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
        logger.info(f"📱 Using device: {DEVICE}")
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
            return True
        except Exception as e:
            logger.error(f"Error running hotkey system: {e}")
            return False

def check_permissions():
    """Check permissions for input device access"""
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
        logger.info("🔧 KEYBOARD PERMISSIONS")
        logger.info("For global hotkeys (Alt+Shift), you need:")
        logger.info("  • Run as root, OR")
        logger.info("  • Add user to input group: sudo usermod -a -G input $USER")
        logger.info("  • Then log out and back in")
        logger.info("")
        input("Press Enter to continue to main menu...")
    
    while True:  # Main menu loop
        logger.info("")
        logger.info("Choose mode:")
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
                            record_thread = threading.Thread(target=lambda: record_audio_stream(interactive_mode=True))
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
                            result, transcribe_time = process_audio_stream()
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
                            print("\nReturning to main menu...")
                            interactive_mode_running = False
                            break
                        elif ch.lower() == 'm':
                            print("\nReturning to main menu...")
                            interactive_mode_running = False
                            break
                        
                        print("\nReady (Space=record, i=device, q=main menu)")
                        
                    except KeyboardInterrupt:
                        print("\nReturning to main menu...")
                        interactive_mode_running = False
                        break
                    except ImportError:
                        # Fallback for systems without termios
                        choice = input("Space=record, i=device, q=main menu: ").strip().lower()
                        if choice in ['', ' ']:
                            # Simple recording without hotkey stop
                            stop_recording.clear()
                            record_thread = threading.Thread(target=lambda: record_audio_stream(interactive_mode=True))
                            record_thread.start()
                            
                            input("Recording... Press Enter to stop")
                            stop_recording.set()
                            record_thread.join()
                            
                            result, _ = process_audio_stream()
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