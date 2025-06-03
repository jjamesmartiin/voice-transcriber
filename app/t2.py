#!/usr/bin/env python3
# Optimized script to record audio and transcribe it with minimal latency

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

# Suppress ONNX warnings
warnings.filterwarnings("ignore", message=".*Init provider bridge failed.*")
warnings.filterwarnings("ignore", category=UserWarning)

# Audio configuration
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1  # Mono for faster processing
RATE = 44100  # Use standard sample rate supported by most hardware
RECORD_SECONDS = 20  # Default recording time
INPUT_DEVICE_INDEX = None  # None = use system default, or specify device index
CONFIG_FILE = 'audio_device_config.json'  # Local config file for device settings

# Fallback sample rates to try if the primary one fails
FALLBACK_RATES = [44100, 48000, 22050, 16000, 8000]

# Determine device at startup
def get_device():
    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        device = "cpu"
    return device

# Global device variable
DEVICE = get_device()

# Preload model at startup in background thread
preload_thread = preload_model(device=DEVICE)

# Audio buffer for streaming processing
audio_buffer = queue.Queue()
stop_recording = threading.Event()

def load_audio_config():
    """Load audio device configuration from local file"""
    global INPUT_DEVICE_INDEX
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                config = json.loads(f.read())
                INPUT_DEVICE_INDEX = config.get('input_device_index')
                print(f"Loaded saved audio device: {INPUT_DEVICE_INDEX}")
    except Exception as e:
        print(f"Could not load audio config: {e}")

def save_audio_config():
    """Save audio device configuration to local file"""
    try:
        config = {
            'input_device_index': INPUT_DEVICE_INDEX
        }
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
        
        if choice == 'c':
            print("Device selection cancelled.")
            return False
        
        device_idx = int(choice)
        if 0 <= device_idx < len(input_devices):
            device_id, device_info = input_devices[device_idx]
            INPUT_DEVICE_INDEX = device_id
            print(f"✅ Selected: {device_info['name']}")
            save_audio_config()
            return True
        else:
            print(f"❌ Invalid choice. Please enter 0-{len(input_devices)-1}")
            return False
            
    except (ValueError, KeyboardInterrupt):
        print("❌ Invalid input or cancelled.")
        return False

def record_audio_stream():
    """Record audio directly into memory and stream to the buffer"""
    
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
    print("Available input devices:")
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info['maxInputChannels'] > 0:
            print(f"  Device {i}: {info['name']} - Rate: {info['defaultSampleRate']}")
    
    # Try different sample rates until one works
    stream = None
    working_rate = None
    
    for rate in FALLBACK_RATES:
        try:
            stream = p.open(format=FORMAT, 
                            channels=CHANNELS, 
                            rate=rate, 
                            input=True,
                            input_device_index=INPUT_DEVICE_INDEX,
                            frames_per_buffer=CHUNK)
            working_rate = rate
            device_info = p.get_device_info_by_index(INPUT_DEVICE_INDEX) if INPUT_DEVICE_INDEX else p.get_default_input_device_info()
            print(f"Using sample rate: {rate} Hz")
            print(f"Using input device: {device_info['name']}")
            break
        except Exception as e:
            print(f"Sample rate {rate} Hz failed: {e}")
            continue
    
    if stream is None:
        print("ERROR: Could not open audio stream with any sample rate")
        p.terminate()
        return []
    
    # Update global RATE variable for other functions
    global RATE
    RATE = working_rate
    
    frames = []
    max_amplitude = 0
    
    # Start countdown in a separate thread
    countdown_thread = threading.Thread(target=countdown_timer)
    countdown_thread.daemon = True
    countdown_thread.start()
    
    # Listen for keypress to stop early
    input_thread = threading.Thread(target=check_for_stop_key)
    input_thread.daemon = True
    input_thread.start()
    
    # Calculate max chunks for the recording duration
    max_chunks = int(RATE / CHUNK * RECORD_SECONDS)
    
    print("Recording... Press Space to stop")
    
    # Record audio
    for i in range(max_chunks):
        if stop_recording.is_set():
            break
        try:
            data = stream.read(CHUNK, exception_on_overflow=False)
            frames.append(data)
            audio_buffer.put(data)  # Add to buffer for real-time processing
            
            # Monitor audio levels for debugging
            audio_data = np.frombuffer(data, dtype=np.int16)
            amplitude = np.max(np.abs(audio_data))
            max_amplitude = max(max_amplitude, amplitude)
            
            # Show audio level indicator every 10 chunks
            if i % 10 == 0 and amplitude > 100:
                level_bars = int(amplitude / 1000)
                print(f"Audio level: {'█' * min(level_bars, 20)} ({amplitude})", end='\r')
                
        except Exception as e:
            print(f"Recording error: {e}")
            break
    
    print(f"\nFinished recording - Max audio level: {max_amplitude}")
    
    if max_amplitude < 500:
        print("⚠️  WARNING: Very low audio levels detected!")
        print("   - Check microphone is connected and not muted")
        print("   - Try speaking louder or closer to microphone")
        print("   - Check system audio input settings")
    
    # Add a None to mark the end of the stream
    audio_buffer.put(None)
    
    # Clean up
    stream.stop_stream()
    stream.close()
    p.terminate()
    
    # Save to file for backup and debugging
    try:
        with wave.open('output.wav', 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(p.get_sample_size(FORMAT))
            wf.setframerate(RATE)
            wf.writeframes(b''.join(frames))
        print(f"Audio saved to output.wav for debugging")
    except Exception as e:
        print(f"Warning: Could not save audio file: {e}")
    
    return frames

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
        import msvcrt
        while not stop_recording.is_set():
            if msvcrt.kbhit():
                if msvcrt.getch() == b' ':
                    print("\nStopping recording early...")
                    stop_recording.set()
                    break
            time.sleep(0.1)

def process_audio_stream():
    """Process the audio stream as it's being recorded"""
    # Get preloaded model
    model = get_model(device=DEVICE)
    
    # Collect audio data until we get None (end of stream)
    chunks = []
    while True:
        chunk = audio_buffer.get()
        if chunk is None:
            break
        chunks.append(chunk)
    
    # Convert audio data to proper format for transcription
    audio_data = b''.join(chunks)
    
    # Start transcription immediately
    transcribe_start_time = time.time()
    

    # Process the complete audio
    temp_file = "temp_output.wav"
    with wave.open(temp_file, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(RATE)
        wf.writeframes(audio_data)
    
    # Transcribe with optimized parameters
    # Suppress ONNX warnings during transcription
    # we were getting this warning:
    # 2025-05-01 06:47:52.065986702 [W:onnxruntime:Default, onnxruntime_pybind_state.cc:1983 CreateInferencePybindStateModule] Init provider bridge failed.
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

def record_and_transcribe():
    """Record audio and transcribe it with minimal latency"""
    process_start_time = time.time()
    
    # Reset stop flag
    stop_recording.clear()
    
    # Start recording in a separate thread
    record_thread = threading.Thread(target=record_audio_stream)
    record_thread.start()

    # Start processing in parallel
    result, transcribe_time = process_audio_stream()
    
    # Ensure recording is complete
    record_thread.join()
    
    # Clean up result
    transcription = result.strip()
    
    
    # Copy to clipboard with fast path
    try:
        pyperclip.copy(transcription)
        print("Transcription copied to clipboard")
        import subprocess
        # Use mpg123 for fastest MP3 playback (fire-and-forget)
        playSound = subprocess.Popen(['mpg123', '-q', 'sounds/pop.mp3'], stderr=subprocess.DEVNULL)
    except Exception as e:
        print("Unable to copy to clipboard")
    
    # Calculate timing information
    process_end_time = time.time()
    total_time = process_end_time - process_start_time
    
    print(f"Total time: {total_time:.2f}s")

    # put the text we copied at the end so it's easier to see
    print(f"\nTranscription: {transcription}")
    
    return transcription

def getch():
    """Get a single character from standard input without waiting for enter"""
    try:
        # For Unix/Linux/MacOS
        import termios, tty
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return ch
    except ImportError:
        # For Windows
        import msvcrt
        return msvcrt.getch().decode()

def main():
    print("T2 Transcription Tool (Optimized)")
    print(f"Using device: {DEVICE}")
    
    # Load saved audio device configuration
    load_audio_config()
    
    print("Model loading in background, press Enter or Space to start recording")
    print("Press 'i' to select audio input device, or 'q' to exit")
    
    # Wait for model to fully load before allowing first transcription
    if preload_thread.is_alive():
        print("Waiting for model to fully load...")
        preload_thread.join()
        print("Model loaded and ready!")
    
    while True:
        try:
            print("> ", end="", flush=True)
            ch = getch()
            
            if ch in [' ', '\r', '\n']:  # Space or Enter key
                print()  # Move to next line after keypress
                record_and_transcribe()
            elif ch.lower() in ['i']:  # Input device selection
                print()
                select_audio_device()
            elif ch.lower() in ['q', 'Q']:
                print("\nExiting...")
                break
            
            print("\nReady for next recording (Space/Enter=record, i=input device, q=quit)")
        except KeyboardInterrupt:
            print("\nExiting...")
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







