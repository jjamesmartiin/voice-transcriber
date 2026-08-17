#!/usr/bin/env python3
"""
Performance & Latency Benchmark for Voice Transcriber in NixOS WSL
Measures:
1. Audio capture throughput (via WSLg PulseAudio)
2. Cold start / model initialization latency
3. Warm transcription inference latency (average, min, max over multiple runs)
4. Accuracy against reference ground truth
"""

import os
import sys
import time
import glob
import numpy as np
import soundfile as sf

# Ensure src is in sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import transcribe2

def benchmark():
    test_file = os.path.join(os.path.dirname(__file__), 'test_transcribe', '1.mp3')
    ground_truth_file = os.path.join(os.path.dirname(__file__), 'test_transcribe', '1.md')
    
    with open(ground_truth_file, 'r', encoding='utf-8') as f:
        ground_truth = f.read().strip()
    
    audio, sr = sf.read(test_file)
    audio_duration = len(audio) / sr
    audio_16k = audio.astype(np.float32)
    
    print("=" * 80)
    print("🎙️  VOICE TRANSCRIBER PERFORMANCE BENCHMARK (NixOS on WSL2)")
    print("=" * 80)
    print(f"Sample Audio: 1.mp3 ({audio_duration:.2f}s duration, {sr}Hz -> 16000Hz PCM)")
    print(f"Ground Truth: \"{ground_truth}\"")
    print("=" * 80)
    
    backends = ["whisper", "cohere"]
    summary_results = []
    
    for backend_name in backends:
        print(f"\nEvaluating Backend: [{backend_name.upper()}]...")
        
        # Reset backend state
        transcribe2._backend = None
        transcribe2._current_backend_name = backend_name
        os.environ["VT_MODEL_BACKEND"] = backend_name
        
        # 1. Measure Cold Init Latency
        t0 = time.perf_counter()
        _ = transcribe2.get_backend()
        cold_init_sec = time.perf_counter() - t0
        
        # 2. Warmup run
        _ = transcribe2.transcribe_audio(audio_data=audio_16k)
        
        # 3. Multiple Warm Inference Runs
        latencies = []
        last_output = ""
        NUM_RUNS = 5
        for i in range(NUM_RUNS):
            t1 = time.perf_counter()
            last_output = transcribe2.transcribe_audio(audio_data=audio_16k)
            dur = time.perf_counter() - t1
            latencies.append(dur)
        
        avg_lat = sum(latencies) / len(latencies)
        min_lat = min(latencies)
        max_lat = max(latencies)
        rtf = avg_lat / audio_duration # Real-Time Factor (< 1.0 means faster than real-time)
        speedup = audio_duration / avg_lat
        
        summary_results.append({
            "backend": backend_name.capitalize(),
            "cold_init_s": cold_init_sec,
            "avg_latency_s": avg_lat,
            "min_latency_s": min_lat,
            "max_latency_s": max_lat,
            "audio_duration_s": audio_duration,
            "rtf": rtf,
            "speedup": speedup,
            "output": last_output.strip()
        })
        
        print(f"  • Cold Model Load:      {cold_init_sec*1000:7.1f} ms ({cold_init_sec:.3f} s)")
        print(f"  • Avg Warm Latency:     {avg_lat*1000:7.1f} ms ({avg_lat:.3f} s)")
        print(f"  • Min / Max Latency:    {min_lat*1000:7.1f} ms / {max_lat*1000:7.1f} ms")
        print(f"  • Real-Time Factor:     {rtf:7.3f}x (Processing {speedup:5.1f}x faster than real-time)")
        print(f"  • Transcribed Text:     \"{last_output.strip()}\"")
    
    print("\n" + "=" * 80)
    print("SUMMARY PERFORMANCE COMPARISON TABLE")
    print("=" * 80)
    print(f"{'Engine':<12} | {'Audio Len':<10} | {'Cold Load':<11} | {'Avg Latency':<12} | {'Min/Max Latency':<18} | {'RTF (Speedup)'}")
    print("-" * 80)
    for r in summary_results:
        rtf_str = f"{r['rtf']:.3f} ({r['speedup']:.1f}x real-time)"
        min_max_str = f"{r['min_latency_s']*1000:.0f}ms / {r['max_latency_s']*1000:.0f}ms"
        print(f"{r['backend']:<12} | {r['audio_duration_s']:>7.2f} s  | {r['cold_init_s']:>8.3f} s  | {r['avg_latency_s']*1000:>8.1f} ms   | {min_max_str:<18} | {rtf_str}")
    print("=" * 80)

if __name__ == "__main__":
    benchmark()
