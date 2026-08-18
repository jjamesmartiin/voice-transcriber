#!/usr/bin/env python3
"""
Benchmark latency scaling across different audio clip lengths (5s, 10s, 15s, 30s)
"""

import os
import sys
import time
import numpy as np
import soundfile as sf
from pathlib import Path

# Ensure src is in sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import transcribe2

def benchmark_durations():
    root = Path(__file__).resolve().parent.parent
    sample_file = root / "tests" / "test_transcribe" / "1.mp3"
    audio_base, sr = sf.read(str(sample_file))
    
    print("=" * 80)
    print("⏱️  COHERE ASR LATENCY SCALING BENCHMARK ACROSS CLIP DURATIONS")
    print("=" * 80)
    
    os.environ["VT_MODEL_BACKEND"] = "cohere"
    transcribe2._backend = None
    transcribe2._current_backend_name = "cohere"
    
    # Warmup
    print("Warming up model...")
    _ = transcribe2.transcribe_audio(audio_data=audio_base.astype(np.float32)[:sr])
    print("Warmup complete.\n")
    
    # Test lengths: 1x (5s), 2x (10s), 3x (15s), 6x (30s), 9x (45s), 12x (60s / 1 min)
    durations = [1, 2, 3, 6, 9, 12]
    results = []
    
    for multiplier in durations:
        # Tile audio to simulate longer continuous speech
        audio_long = np.tile(audio_base, multiplier).astype(np.float32)
        dur_s = len(audio_long) / sr
        
        t0 = time.perf_counter()
        text = transcribe2.transcribe_audio(audio_data=audio_long)
        elapsed = time.perf_counter() - t0
        rtf = elapsed / dur_s
        
        results.append({
            "duration_s": dur_s,
            "latency_s": elapsed,
            "rtf": rtf,
            "words": len(text.split()),
            "text": text[:80] + "..." if len(text) > 80 else text
        })
        
        print(f"  • Clip Duration: {dur_s:5.1f}s | Latency: {elapsed:6.2f}s | RTF: {rtf:5.2f}x | Words: {len(text.split()):3d}")
    
    print("\n" + "=" * 80)
    print(f"{'Audio Duration':<16} | {'Inference Latency':<18} | {'Real-Time Factor':<18} | {'Output Words'}")
    print("-" * 80)
    for r in results:
        print(f"{r['duration_s']:>14.1f} s | {r['latency_s']:>16.2f} s | {r['rtf']:>16.2f} x | {r['words']}")
    print("=" * 80)

if __name__ == "__main__":
    benchmark_durations()
