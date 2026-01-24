#!/usr/bin/env python3
# Optimized script to record audio and transcribe it with minimal latency
# Updated with browser-like audio capture and AGC from t3.py

import sys
import os
import pyperclip
import threading
import time
import wave
import numpy as np
import pyaudio
import queue
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

# Audio configuration - Browser-like settings for better quality
CHUNK = 256  # Smaller buffer like browsers use (128-256 samples)
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 48000  # Default to 48kHz like browsers prefer
RECORD_SECONDS = 20
INPUT_DEVICE_INDEX = None
CONFIG_FILE = get_data_dir() / 'audio_device_config.json'
# Prioritize 48kHz like browsers, then fallback
FALLBACK_RATES = [48000, 44100, 22050, 16000, 8000]

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
# We don't use the queue for streaming frames anymore, but we'll return frames directly

def load_audio_config():
    """Load audio device configuration from local file"""
    global INPUT_DEVICE_INDEX
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r') as f:
                config = json.loads(f.read())
                INPUT_DEVICE_INDEX = config.get('input_device_index')
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
        return False
    
    print("=" * 60)
    print("Enter the number (0-{}) of the device you want to use, or 'c' to cancel:".format(len(input_devices)-1))
    
    try:
        choice = input("> ").strip().lower()
        if choice == 'c': return False
        
        device_idx = int(choice)
        if 0 <= device_idx < len(input_devices):
            device_id, device_info = input_devices[device_idx]
            INPUT_DEVICE_INDEX = device_id
            print(f"✅ Selected: {device_info['name']}")
            save_audio_config()
            return True
        else:
            print("❌ Invalid choice")
            return False
    except:
        return False

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
    
    # Get device info for optimal settings
    if INPUT_DEVICE_INDEX is not None:
        try:
            device_info = p.get_device_info_by_index(INPUT_DEVICE_INDEX)
        except:
            device_info = p.get_default_input_device_info()
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
    working_chunk = CHUNK
    
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
                
                print(f"Audio setup: {rate} Hz, {chunk_size} samples ({buffer_time_ms:.1f}ms)")
                break
                
            except Exception as e:
                continue
        
        if stream is not None:
            break
    
    if stream is None:
        print("ERROR: Could not open audio stream")
        p.terminate()
        return []
    
    # Update global RATE
    RATE = working_rate
    
    frames = []
    max_amplitude = 0
    total_chunks = 0
    
    # Interactive mode helpers
    if interactive_mode:
        countdown_thread = threading.Thread(target=countdown_timer)
        countdown_thread.daemon = True
        countdown_thread.start()
        
        input_thread = threading.Thread(target=check_for_stop_key)
        input_thread.daemon = True
        input_thread.start()
        
        print("Recording... Press Space to stop")
    
    # Record loop
    # If not interactive, we rely on external stop_recording event
    while not stop_recording.is_set():
        try:
            data = stream.read(working_chunk, exception_on_overflow=False)
            frames.append(data)
            total_chunks += 1
            
            # Monitor audio levels
            audio_data = np.frombuffer(data, dtype=np.int16)
            amplitude = np.max(np.abs(audio_data))
            max_amplitude = max(max_amplitude, amplitude)
            
            # Show level in interactive mode
            if interactive_mode and total_chunks % 20 == 0 and amplitude > 100:
                level_bars = int(amplitude / 1000)
                print(f"Audio level: {'█' * min(level_bars, 20)} ({amplitude})", end='\r')
                
        except Exception as e:
            print(f"Recording error: {e}")
            break
            
        # Limit recording time if in interactive/fixed mode
        if interactive_mode and total_chunks * working_chunk / working_rate > RECORD_SECONDS:
            break
    
    if interactive_mode:
        print(f"\nFinished recording - Max audio: {max_amplitude}")
    
    if max_amplitude < 500:
        print("⚠️  WARNING: Very low audio levels detected!")
    
    stream.stop_stream()
    stream.close()
    p.terminate()
    
    # Apply Browser-like AGC (Automatic Gain Control)
    # Boost quiet audio before returning
    if frames:
        full_audio = b''.join(frames)
        audio_array = np.frombuffer(full_audio, dtype=np.int16)
        current_max = np.max(np.abs(audio_array))
        
        if current_max > 0 and current_max < 8000:
            boost_factor = min(8000 / current_max, 3.0)
            boosted_audio = (audio_array * boost_factor).astype(np.int16)
            print(f"Applied AGC: Boosted audio {boost_factor:.1f}x")
            
            # Convert back to frames
            # This is a bit inefficient but keeps compatibility with frame-based return
            new_bytes = boosted_audio.tobytes()
            frames = [new_bytes[i:i+working_chunk*2] for i in range(0, len(new_bytes), working_chunk*2)]
            
    return frames

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

def process_audio_stream(audio_frames=None):
    """Process audio frames. If None, it's expected to be passed in."""
    # Note: original t2.py pulled from queue. New version expects frames passed in.
    # To maintain backward compatibility if something calls it without args (unlikely in new flow):
    if audio_frames is None:
        return "", 0
        
    model = get_model(device=DEVICE)
    
    if not audio_frames:
        return "", 0
    
    audio_data = b''.join(audio_frames)
    
    transcribe_start_time = time.time()
    
    temp_dir = get_temp_dir()
    temp_file = temp_dir / "temp_output.wav"
    with wave.open(str(temp_file), 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(audio_data)
        
    # Transcribe
    stderr_fd = os.dup(2)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull_fd, 2)
    try:
        result = transcribe_audio(audio_path=str(temp_file), device=DEVICE)
    finally:
        os.dup2(stderr_fd, 2)
        os.close(devnull_fd)
        os.close(stderr_fd)
        
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
    
    # Record synchronously (since we moved away from real-time stream processing)
    # This aligns with t3.py's robust approach
    frames = record_audio_stream(interactive_mode=True)
    
    # Transcribe
    result, transcribe_time = process_audio_stream(frames)
    
    transcription = result.strip()
    
    try:
        pyperclip.copy(transcription)
        print("Transcription copied to clipboard")
        sound_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sounds/pop.mp3')
        subprocess.Popen(['mpg123', '-q', sound_path], stderr=subprocess.DEVNULL)
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

# ok now we should focus on automating the process of transcribing the audio

# we have rec.py to record the audio and save it as a wav file
# we have transcribe.py to transcribe the audio

# now we need to combine these two files

# first we need to record the audio
# import rec

# # then we need to transcribe the audio
# import transcribe
# import pyperclip

# # Write transcription to a temporary file
# with open('/tmp/transcription.txt', 'w') as f:
#     f.write(transcribe.result["text"].strip())


# import subprocess

# # Copy transcription to clipboard
# subprocess.run(['xclip', '-selection', 'clipboard'], input=transcribe.result["text"].strip().encode())

# Execute vim to read and copy the transcription
# subprocess.run(['vim', '-c', 'normal! ggdG', '-c', ':r /tmp/transcription.txt', '-c', 'normal! ggVG"*y', '-c', 'q!', '/tmp/transcription.txt'])

# the above command is working sort of, it copies but 

# 
# Hello, test12.
#
# it has extra return before and after 
# so we need to remove the extra return







