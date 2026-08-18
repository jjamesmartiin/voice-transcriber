#!/usr/bin/env python3
"""
Test and Benchmark Micro-Batching Streaming vs Stop-and-Wait
Evaluates accuracy across all curated dictation test audio files (5s, 9s, 10.4s, 20.4s).
"""

import os
import sys
import time
import re
import numpy as np
import soundfile as sf
from pathlib import Path

# Ensure src is in sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import transcribe2
from micro_batcher import StreamingMicroBatcher

def normalize_text(text):
    return re.sub(r'[^\w\s]', '', text.lower()).strip()

def calculate_word_accuracy(expected, actual):
    norm_exp = normalize_text(expected).split()
    norm_act = normalize_text(actual).split()
    if not norm_exp:
        return 0.0
    
    # Calculate word overlap
    overlap = set(norm_exp) & set(norm_act)
    return len(overlap) / len(set(norm_exp))

def run_micro_batching_tests():
    test_dir = Path(__file__).resolve().parent / "test_transcribe"
    samples = sorted(test_dir.glob("*.mp3"))
    
    print("=" * 95)
    print("🚀 MICRO-BATCHING ACCURACY & LATENCY BENCHMARK SUITE")
    print("=" * 95)
    
    os.environ["VT_MODEL_BACKEND"] = "cohere"
    transcribe2._backend = None
    transcribe2._current_backend_name = "cohere"
    
    # Pre-warm backend
    print("Warming up Cohere model...")
    _ = transcribe2.transcribe_audio(audio_data=np.zeros(16000, dtype=np.float32))
    print("Warmup ready!\n")
    
    results = []
    
    for sample_path in samples:
        md_path = sample_path.with_suffix(".md")
        if not md_path.exists():
            continue
            
        ground_truth = md_path.read_text(encoding='utf-8').strip()
        data, sr = sf.read(str(sample_path))
        if sr != 16000:
            import librosa
            data = librosa.resample(data, orig_sr=sr, target_sr=16000)
            sr = 16000
        audio_duration = len(data) / sr
        
        print(f"\n--- Testing Sample {sample_path.stem} ({audio_duration:.1f}s audio) ---")
        print(f"Ground Truth: \"{ground_truth}\"")
        
        # 1. Baseline Stop-and-Wait
        t0 = time.perf_counter()
        baseline_text = transcribe2.transcribe_audio(audio_data=data, sample_rate=sr)
        baseline_latency = time.perf_counter() - t0
        baseline_acc = calculate_word_accuracy(ground_truth, baseline_text)
        
        # 2. Micro-Batching Streaming Simulation
        # Simulate live streaming by feeding in 100ms blocks
        block_size = int(0.1 * sr) # 100ms
        batcher = StreamingMicroBatcher(sample_rate=sr)
        batcher.start()
        
        # Stream chunks with simulated real-time delay (or fast streaming)
        for i in range(0, len(data), block_size):
            chunk = data[i:i+block_size]
            batcher.feed_audio(chunk)
            time.sleep(0.01) # Accelerated live feed
            
        # Measure post-release latency (time after user stops speaking / releases key)
        t_release = time.perf_counter()
        mb_text = batcher.finish_and_get_text()
        mb_post_release_latency = time.perf_counter() - t_release
        mb_acc = calculate_word_accuracy(ground_truth, mb_text)
        
        print(f"  • Baseline:       Latency = {baseline_latency:5.2f}s | Word Accuracy = {baseline_acc*100:5.1f}%")
        print(f"  • Micro-Batching: Post-Release Wait = {mb_post_release_latency:5.2f}s | Word Accuracy = {mb_acc*100:5.1f}%")
        print(f"  • Transcribed:    \"{mb_text}\"")
        
        speedup = baseline_latency / max(0.01, mb_post_release_latency)
        results.append({
            "sample": sample_path.stem,
            "duration": audio_duration,
            "baseline_lat": baseline_latency,
            "mb_lat": mb_post_release_latency,
            "accuracy": mb_acc,
            "speedup": speedup
        })
        
    print("\n" + "=" * 95)
    print(f"{'Sample':<8} | {'Audio Len':<10} | {'Stop-&-Wait Wait':<18} | {'Micro-Batch Post-Release':<24} | {'Accuracy':<10} | {'Latency Speedup'}")
    print("-" * 95)
    for r in results:
        print(f"{r['sample']:<8} | {r['duration']:>7.1f} s  | {r['baseline_lat']:>14.2f} s   | {r['mb_lat']:>20.2f} s   | {r['accuracy']*100:>7.1f} %  | {r['speedup']:.1f}x faster post-release")
    print("=" * 95)

if __name__ == "__main__":
    run_micro_batching_tests()
