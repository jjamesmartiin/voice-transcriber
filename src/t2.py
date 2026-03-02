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
CONFIG_FILE = get_data_dir() / 'audio_device_config.json'

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
    """Load audio device configuration from local file"""
    global INPUT_DEVICE_INDEX
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r') as f:
                config = json.loads(f.read())
                INPUT_DEVICE_INDEX = config.get('input_device_index')
                sd.default.device = INPUT_DEVICE_INDEX
                print(f"Loaded saved audio device: {INPUT_DEVICE_INDEX}")
    except Exception as e:
        print(f"Could not load audio config: {e}")

def save_audio_config():
    """Save audio device configuration to local file"""
    try:
        config = {'input_device_index': INPUT_DEVICE_INDEX}
        with open(CONFIG_FILE, 'w') as f:
            f.write(json.dumps(config, indent=2))
        print(f"Saved audio device config to {CONFIG_FILE}")
    except Exception as e:
        print(f"Could not save audio config: {e}")

def select_audio_device():
    """Interactive audio device selection"""
    global INPUT_DEVICE_INDEX
    
    print("\n🎤 Available Audio Input Devices:")
    print("=" * 60)
    
    devices = sd.query_devices()
    input_devices = [(i, d) for i, d in enumerate(devices) if d['max_input_channels'] > 0]
    
    for i, (device_idx, device_info) in enumerate(input_devices):
        current_marker = " ← CURRENT" if device_idx == sd.default.device[0] else ""
        print(f"  {i}: {device_info['name']}{current_marker}")
    
    if not input_devices:
        print("❌ No input devices found!")
        return False
    
    try:
        choice = input(f"Enter the number (0-{len(input_devices)-1}) of the device you want to use, or 'c' to cancel: ").strip().lower()
        if choice == 'c': return False
        
        device_idx = int(choice)
        if 0 <= device_idx < len(input_devices):
            INPUT_DEVICE_INDEX = input_devices[device_idx][0]
            sd.default.device = INPUT_DEVICE_INDEX
            print(f"✅ Selected: {sd.query_devices()[INPUT_DEVICE_INDEX]['name']}")
            save_audio_config()
            return True
        else:
            print("❌ Invalid choice")
            return False
    except:
        return False

def record_audio_stream(interactive_mode=False):
    """Record audio using sounddevice"""
    q = queue.Queue()

    def callback(indata, frames, time, status):
        """This is called (from a separate thread) for each audio block."""
        if status:
            print(status, file=sys.stderr)
        q.put(indata.copy())

    # Interactive mode helpers
    if interactive_mode:
        countdown_thread = threading.Thread(target=countdown_timer)
        countdown_thread.daemon = True
        countdown_thread.start()
        
        input_thread = threading.Thread(target=check_for_stop_key)
        input_thread.daemon = True
        input_thread.start()
        
        print("Recording... Press Space to stop")
    
    frames = []
    with sd.InputStream(samplerate=RATE, channels=CHANNELS, callback=callback, device=INPUT_DEVICE_INDEX):
        while not stop_recording.is_set():
            frames.append(q.get())

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
    """Get single character"""
    try:
        import termios, tty
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
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
            elif ch.lower() == 'q':
                break
        except KeyboardInterrupt:
            break

if __name__ == "__main__":
    main()
