#!/usr/bin/env python3
"""
Audio Recording Module
Handles audio recording, device management, and audio configuration.
"""

import os
import sys
import json
import wave
import time
import select
import threading
import numpy as np
import pyaudio
import logging

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

# Global control for recording
stop_recording = threading.Event()

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