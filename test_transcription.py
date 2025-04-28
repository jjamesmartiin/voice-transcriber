#!/usr/bin/env python3
"""
Test file to verify preloading of transcription model works properly
and to measure performance improvements.
"""

import time
import sys
from transcribe2 import get_model, transcribe_audio, preload_model

def test_preloading():
    """Test and benchmark preloading vs. on-demand loading"""
    print("Testing transcription performance...")
    
    # Try to determine device
    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"Using device: {device}")
    except ImportError:
        device = "cpu"
        print("Using CPU (torch not available)")
    
    # 1. Test cold start (first load)
    print("\n--- Test 1: Cold Start ---")
    start_time = time.time()
    model = get_model(device=device)  # This will initialize the model
    cold_load_time = time.time() - start_time
    print(f"Cold load time: {cold_load_time:.2f} seconds")
    
    # 2. Test pre-warmed model access
    print("\n--- Test 2: Pre-warmed Model Access ---")
    start_time = time.time()
    model = get_model(device=device)  # This should be instant
    warm_load_time = time.time() - start_time
    print(f"Warm load time: {warm_load_time:.2f} seconds")
    print(f"Performance improvement: {cold_load_time/warm_load_time:.1f}x faster")
    
    # 3. Test transcription with preloaded model
    if len(sys.argv) > 1:
        audio_file = sys.argv[1]
        print(f"\n--- Test 3: Transcription with file: {audio_file} ---")
        result = transcribe_audio(audio_file, device=device)
        print(f"Transcription result: {result}")

if __name__ == "__main__":
    test_preloading() 