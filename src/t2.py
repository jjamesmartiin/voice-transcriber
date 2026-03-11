#!/usr/bin/env python3
# Optimized script to record audio and transcribe it with minimal latency
# Updated with sounddevice for robust audio capture

import queue
import sys
import os
import pyperclip
import threading
import time
import numpy as np
import sounddevice as sd
import soundfile as sf
import warnings
from transcribe2 import transcribe_audio, preload_model, get_model
import json
import tempfile
from pathlib import Path

# Suppress ONNX warnings
warnings.filterwarnings("ignore", message=".*Init provider bridge failed.*")
warnings.filterwarnings("ignore", category=UserWarning)

# Get appropriate directories for file storage
def get_data_dir():
    """Get appropriate data directory for config and temporary files"""
    if 'XDG_DATA_HOME' in os.environ:
        data_dir = Path(os.environ['XDG_DATA_HOME']) / 'vt'
    elif os.name == 'posix':
        data_dir = Path.home() / '.local' / 'share' / 'vt'
    else:
        data_dir = Path.home() / 'AppData' / 'Local' / 'vt'
    
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir

def get_temp_dir():
    """Get temporary directory for audio files"""
    if 'XDG_RUNTIME_DIR' in os.environ:
        temp_dir = Path(os.environ['XDG_RUNTIME_DIR']) / 'vt'
        temp_dir.mkdir(parents=True, exist_ok=True)
        return temp_dir
    else:
        return Path(tempfile.gettempdir())

# Audio configuration
CHANNELS = 1
RATE = 48000
RECORD_SECONDS = 20
INPUT_DEVICE_INDEX = None
PRIMARY_DEVICE_NAME = None
SECONDARY_DEVICE_NAME = None
OVERRIDE_MODE = 'auto' # 'auto', 'primary', or 'secondary'
CONFIG_FILE = get_data_dir() / 'audio_device_config.json'

def find_device_index(name):
    """Find device index by name substring match"""
    if not name:
        return None
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if d['max_input_channels'] > 0 and name.lower() in d['name'].lower():
            return i
    return None

def reset_terminal():
    """Reset terminal settings if they become wonky"""
    try:
        import os
        os.system('reset')
        # Also re-initialize termios just in case
        import termios, sys
        fd = sys.stdin.fileno()
        try:
            termios.tcgetattr(fd)
        except:
            # If it's already broken, this might help
            pass
    except:
        pass

# Global device variable
def get_device():
    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        device = "cpu"
    return device

DEVICE = get_device()

# Preload model at startup
preload_thread = preload_model(device=DEVICE)

# Audio buffering
stop_recording = threading.Event()

def load_audio_config():
    """Load audio device configuration from local file with fallback"""
    global INPUT_DEVICE_INDEX, PRIMARY_DEVICE_NAME, SECONDARY_DEVICE_NAME, OVERRIDE_MODE
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r') as f:
                config = json.loads(f.read())
                PRIMARY_DEVICE_NAME = config.get('primary_device_name')
                SECONDARY_DEVICE_NAME = config.get('secondary_device_name')
                OVERRIDE_MODE = config.get('override_mode', 'auto')
                
                # If we have an override, try that first
                if OVERRIDE_MODE == 'primary' and PRIMARY_DEVICE_NAME:
                    idx = find_device_index(PRIMARY_DEVICE_NAME)
                    if idx is not None:
                        INPUT_DEVICE_INDEX = idx
                        print(f"🔒 Manual override active: using primary device '{PRIMARY_DEVICE_NAME}' (index {idx})")
                    else:
                        print(f"⚠️ Manual override failed: Primary device '{PRIMARY_DEVICE_NAME}' not found.")
                elif OVERRIDE_MODE == 'secondary' and SECONDARY_DEVICE_NAME:
                    idx = find_device_index(SECONDARY_DEVICE_NAME)
                    if idx is not None:
                        INPUT_DEVICE_INDEX = idx
                        print(f"🔒 Manual override active: using secondary device '{SECONDARY_DEVICE_NAME}' (index {idx})")
                    else:
                        print(f"⚠️ Manual override failed: Secondary device '{SECONDARY_DEVICE_NAME}' not found.")
                
                # If no override or override failed, try the standard auto logic
                if INPUT_DEVICE_INDEX is None:
                    # Attempt to find primary
                    idx = find_device_index(PRIMARY_DEVICE_NAME)
                    if idx is not None:
                        INPUT_DEVICE_INDEX = idx
                        print(f"✅ Using primary audio device: {PRIMARY_DEVICE_NAME} (index {idx})")
                    else:
                        # Attempt to find secondary
                        idx = find_device_index(SECONDARY_DEVICE_NAME)
                        if idx is not None:
                            INPUT_DEVICE_INDEX = idx
                            print(f"⚠️ Primary device '{PRIMARY_DEVICE_NAME}' not found. Falling back to secondary: {SECONDARY_DEVICE_NAME} (index {idx})")
                        else:
                            # Fallback to index if names fail (for backward compatibility or if names are not set)
                            INPUT_DEVICE_INDEX = config.get('input_device_index')
                            if INPUT_DEVICE_INDEX is not None:
                                try:
                                    d = sd.query_devices(INPUT_DEVICE_INDEX)
                                    print(f"ℹ️ Falling back to saved device index {INPUT_DEVICE_INDEX}: {d['name']}")
                                except:
                                    INPUT_DEVICE_INDEX = None
                
                if INPUT_DEVICE_INDEX is not None:
                    sd.default.device = INPUT_DEVICE_INDEX
                else:
                    print("⚠️ No configured audio devices found. Using system default.")
    except Exception as e:
        print(f"Could not load audio config: {e}")

def save_audio_config():
    """Save audio device configuration to local file"""
    try:
        config = {
            'input_device_index': INPUT_DEVICE_INDEX,
            'primary_device_name': PRIMARY_DEVICE_NAME,
            'secondary_device_name': SECONDARY_DEVICE_NAME,
            'override_mode': OVERRIDE_MODE
        }
        with open(CONFIG_FILE, 'w') as f:
            f.write(json.dumps(config, indent=2))
        print(f"Saved audio device config to {CONFIG_FILE}")
    except Exception as e:
        print(f"Could not save audio config: {e}")

def select_audio_device():
    """Interactive audio device selection with Primary/Secondary support"""
    global INPUT_DEVICE_INDEX, PRIMARY_DEVICE_NAME, SECONDARY_DEVICE_NAME, OVERRIDE_MODE
    
    # Always reset terminal before interaction to fix terminal state
    reset_terminal() 
    
    print("\n🎤 Configure Audio Input Devices:")
    print("P. Set Primary Device (currently: {})".format(PRIMARY_DEVICE_NAME or "Not Set"))
    print("S. Set Secondary Device (currently: {})".format(SECONDARY_DEVICE_NAME or "Not Set"))
    print("R. Reset Terminal (if text is invisible or wonky)")
    print("-" * 30)
    print("p. Use Primary Device (Manual Override)")
    print("s. Use Secondary Device (Manual Override)")
    print("a. Automatic Selection (Default)")
    print("-" * 30)
    print("c. Cancel")
    
    print("\nYour choice: ", end="", flush=True)
    choice = getch()
    print() # Newline after getch
    
    if choice.lower() == 'c': 
        reset_terminal()
        return False
    if choice.lower() == 'r':
        reset_terminal()
        return select_audio_device()
        
    if choice == 'p':
        OVERRIDE_MODE = 'primary'
        if PRIMARY_DEVICE_NAME:
            idx = find_device_index(PRIMARY_DEVICE_NAME)
            if idx is not None:
                INPUT_DEVICE_INDEX = idx
                sd.default.device = INPUT_DEVICE_INDEX
                print(f"✅ Manual Override: Set to Primary Device: {PRIMARY_DEVICE_NAME}")
            else:
                print(f"⚠️ Primary device '{PRIMARY_DEVICE_NAME}' not found, but override set.")
        else:
            print("⚠️ Primary device not configured yet.")
        save_audio_config()
        return True
    elif choice == 's':
        OVERRIDE_MODE = 'secondary'
        if SECONDARY_DEVICE_NAME:
            idx = find_device_index(SECONDARY_DEVICE_NAME)
            if idx is not None:
                INPUT_DEVICE_INDEX = idx
                sd.default.device = INPUT_DEVICE_INDEX
                print(f"✅ Manual Override: Set to Secondary Device: {SECONDARY_DEVICE_NAME}")
            else:
                print(f"⚠️ Secondary device '{SECONDARY_DEVICE_NAME}' not found, but override set.")
        else:
            print("⚠️ Secondary device not configured yet.")
        save_audio_config()
        return True
    elif choice.lower() == 'a':
        OVERRIDE_MODE = 'auto'
        print("✅ Mode: Automatic Selection")
        # Let record_audio_stream handle the logic for auto selection
        save_audio_config()
        return True

    if choice not in ['P', 'S']:
        print("Invalid choice.")
        return False
        
    is_primary = (choice == 'P')
    label = "Primary" if is_primary else "Secondary"
    
    print(f"\n🎤 Available Audio Input Devices for {label}:")
    print("=" * 60)
    
    devices = sd.query_devices()
    input_devices = [(i, d) for i, d in enumerate(devices) if d['max_input_channels'] > 0]
    
    for i, (device_idx, device_info) in enumerate(input_devices):
        markers = []
        if PRIMARY_DEVICE_NAME and PRIMARY_DEVICE_NAME.lower() in device_info['name'].lower():
            markers.append("PRIMARY")
        if SECONDARY_DEVICE_NAME and SECONDARY_DEVICE_NAME.lower() in device_info['name'].lower():
            markers.append("SECONDARY")
            
        marker_str = " ← " + " & ".join(markers) if markers else ""
        print(f"  {i}: {device_info['name']}{marker_str}")
    
    if not input_devices:
        print("❌ No input devices found!")
        return False
    
    try:
        prompt = f"Enter the number (0-{len(input_devices)-1}) of the device you want to use as {label}, or 'c' to cancel: "
        # Use regular input here because we need numbers (could be multi-digit)
        # But ensure we are in a sane terminal state
        print(prompt, end="", flush=True)
        choice = input().strip().lower()
        if choice == 'c' or not choice: return False
        
        device_idx = int(choice)
        if 0 <= device_idx < len(input_devices):
            selected_idx = input_devices[device_idx][0]
            selected_name = input_devices[device_idx][1]['name']
            
            if is_primary:
                PRIMARY_DEVICE_NAME = selected_name
                INPUT_DEVICE_INDEX = selected_idx
                sd.default.device = INPUT_DEVICE_INDEX
            else:
                SECONDARY_DEVICE_NAME = selected_name
            
            print(f"✅ {label} Selected: {selected_name}")
            save_audio_config()
            reset_terminal()
            return True
        else:
            print("❌ Invalid choice")
            reset_terminal()
            return False
    except Exception as e:
        print(f"❌ Selection error: {e}")
        reset_terminal()
        return False

def record_audio_stream(interactive_mode=False):
    """Record audio using sounddevice with fallback and auto-recovery support"""
    global INPUT_DEVICE_INDEX
    
    # Manual Override Logic
    if OVERRIDE_MODE == 'primary' and PRIMARY_DEVICE_NAME:
        primary_idx = find_device_index(PRIMARY_DEVICE_NAME)
        if primary_idx is not None:
            if INPUT_DEVICE_INDEX != primary_idx:
                print(f"🔒 Manual Override: Using primary device '{PRIMARY_DEVICE_NAME}'")
                INPUT_DEVICE_INDEX = primary_idx
                sd.default.device = INPUT_DEVICE_INDEX
        else:
            print(f"❌ Manual Override Failed: Primary device '{PRIMARY_DEVICE_NAME}' not found!")
    elif OVERRIDE_MODE == 'secondary' and SECONDARY_DEVICE_NAME:
        secondary_idx = find_device_index(SECONDARY_DEVICE_NAME)
        if secondary_idx is not None:
            if INPUT_DEVICE_INDEX != secondary_idx:
                print(f"🔒 Manual Override: Using secondary device '{SECONDARY_DEVICE_NAME}'")
                INPUT_DEVICE_INDEX = secondary_idx
                sd.default.device = INPUT_DEVICE_INDEX
        else:
            print(f"❌ Manual Override Failed: Secondary device '{SECONDARY_DEVICE_NAME}' not found!")
    elif PRIMARY_DEVICE_NAME:
        # Auto-recovery: Always try to see if the primary device has returned before starting
        primary_idx = find_device_index(PRIMARY_DEVICE_NAME)
        if primary_idx is not None:
            if INPUT_DEVICE_INDEX != primary_idx:
                print(f"✨ Primary device '{PRIMARY_DEVICE_NAME}' detected! Switching back.")
                INPUT_DEVICE_INDEX = primary_idx
                sd.default.device = INPUT_DEVICE_INDEX
        elif SECONDARY_DEVICE_NAME:
            # If primary is gone, ensure we at least use the secondary if it's available
            secondary_idx = find_device_index(SECONDARY_DEVICE_NAME)
            if secondary_idx is not None and INPUT_DEVICE_INDEX != secondary_idx:
                print(f"ℹ️ Using secondary device: {SECONDARY_DEVICE_NAME}")
                INPUT_DEVICE_INDEX = secondary_idx
                sd.default.device = INPUT_DEVICE_INDEX

    q = queue.Queue()

    def callback(indata, frames, time, status):
        """This is called (from a separate thread) for each audio block."""
        if status:
            print(status, file=sys.stderr)
        q.put(indata.copy())

    def perform_recording(device_idx):
        nonlocal q
        frames = []
        try:
            with sd.InputStream(samplerate=RATE, channels=CHANNELS, callback=callback, device=device_idx):
                while not stop_recording.is_set():
                    try:
                        frames.append(q.get(timeout=0.2))
                    except queue.Empty:
                        continue
            return frames
        except Exception as e:
            print(f"Error opening audio device {device_idx}: {e}")
            return None

    # Interactive mode helpers
    if interactive_mode:
        countdown_thread = threading.Thread(target=countdown_timer)
        countdown_thread.daemon = True
        countdown_thread.start()
        
        input_thread = threading.Thread(target=check_for_stop_key)
        input_thread.daemon = True
        input_thread.start()
        
        print("Recording... Press Space to stop")
    
    # Try primary/current device
    frames = perform_recording(INPUT_DEVICE_INDEX)
    
    # If it failed, try to find a fallback (only if not in manual override mode)
    if frames is None and OVERRIDE_MODE == 'auto':
        print("🔄 Attempting fallback to secondary device...")
        fallback_idx = find_device_index(SECONDARY_DEVICE_NAME)
        if fallback_idx is not None and fallback_idx != INPUT_DEVICE_INDEX:
            print(f"⚠️ Primary device failed. Trying secondary: {SECONDARY_DEVICE_NAME} (index {fallback_idx})")
            frames = perform_recording(fallback_idx)
            if frames is not None:
                # Update current device if fallback succeeded
                INPUT_DEVICE_INDEX = fallback_idx
                sd.default.device = INPUT_DEVICE_INDEX
                print("✅ Fallback successful!")
        else:
            print("❌ No valid secondary device found or secondary device is the same as failed device.")
    elif frames is None:
        print(f"❌ Recording failed on {OVERRIDE_MODE} device.")

    return np.concatenate(frames, axis=0) if frames else np.array([])


def countdown_timer():
    """Display countdown timer"""
    for i in range(RECORD_SECONDS, 0, -1):
        if stop_recording.is_set(): break
        print(f'Recording: {i}s... (press space to stop)', end='\r')
        time.sleep(1)

def check_for_stop_key():
    """Check for space key"""
    import select
    try:
        import termios, tty
        old_settings = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin.fileno())
        while not stop_recording.is_set():
            if select.select([sys.stdin], [], [], 0) == ([sys.stdin], [], []):
                c = sys.stdin.read(1)
                if c == ' ':
                    stop_recording.set()
                    break
            time.sleep(0.1)
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
    except:
        pass

def process_audio_stream(audio_data=None):
    """Process audio frames. If None, it's expected to be passed in."""
    if audio_data is None or len(audio_data) == 0:
        return "", 0
        
    model = get_model(device=DEVICE)
    
    transcribe_start_time = time.time()
    
    temp_dir = get_temp_dir()
    temp_file = temp_dir / "temp_output.wav"
    
    sf.write(str(temp_file), audio_data, RATE)
        
    # Transcribe
    try:
        result = transcribe_audio(audio_path=str(temp_file), device=DEVICE)
    finally:
        pass
        
    transcribe_end_time = time.time()
    
    try:
        temp_file.unlink()
    except:
        pass
        
    return result, transcribe_end_time - transcribe_start_time

def record_and_transcribe():
    """Record audio and transcribe it"""
    process_start_time = time.time()
    stop_recording.clear()
    
    frames = record_audio_stream(interactive_mode=True)
    
    # Transcribe
    result, transcribe_time = process_audio_stream(frames)
    
    transcription = result.strip()
    
    try:
        pyperclip.copy(transcription)
        print("Transcription copied to clipboard")
    except:
        pass
        
    print(f"Total time: {time.time() - process_start_time:.2f}s")
    print(f"\nTranscription: {transcription}")
    return transcription

def getch():
    """Get single character with echo"""
    try:
        import termios, tty
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            ch = sys.stdin.read(1)
            # Echo the character manually to be sure it shows up
            sys.stdout.write(ch)
            sys.stdout.flush()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return ch
    except:
        return sys.stdin.read(1)

def main():
    print("T2 Transcription Tool (Optimized)")
    print(f"Using device: {DEVICE}")
    load_audio_config()
    
    if preload_thread.is_alive():
        print("Waiting for model...")
        preload_thread.join()
        print("Model ready!")
        
    while True:
        try:
            print("> ", end="", flush=True)
            ch = getch()
            if ch in [' ', '\r', '\n']:
                print()
                record_and_transcribe()
            elif ch.lower() == 'i':
                print()
                select_audio_device()
            elif ch.lower() == 'r':
                print("\nResetting terminal...")
                reset_terminal()
            elif ch.lower() == 'q':
                break
        except KeyboardInterrupt:
            break

if __name__ == "__main__":
    main()
