#!/usr/bin/env python3
"""
Audio Processor Module
Handles audio processing and transcription coordination.
"""

import os
import time
import wave
import logging
from audio_recorder import CHANNELS, RATE

logger = logging.getLogger(__name__)

def get_device():
    """Detect available compute device (CUDA/CPU)"""
    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        device = "cpu"
    return device

def process_audio_stream(audio_frames, device="cpu"):
    """Process the audio frames directly and transcribe them"""
    # Import transcription functionality
    from transcribe import transcribe_audio, get_model
    
    # Get preloaded model
    model = get_model(device=device)
    
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
    result = transcribe_audio(audio_path=temp_file, device=device)
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