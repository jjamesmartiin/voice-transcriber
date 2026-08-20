#!/usr/bin/env python3
"""
Comprehensive Dual-Path Benchmark Suite:
Compares 'disabled' (Stop-and-Wait), 'always' (Fixed Micro-Batch), and 'auto' (Dynamic Dual-Path)
across audio durations (1.5s, 3.0s, 5.0s, 10.0s, 20.0s, 30.0s).
Measures: Post-Release Latency, Word Accuracy, and Speedup.
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
    text_clean = re.sub(r'[-_]', ' ', text.lower())
    return re.sub(r'[^\w\s]', '', text_clean).strip()

def calculate_word_accuracy(expected, actual):
    norm_exp = normalize_text(expected).split()
    norm_act = normalize_text(actual).split()
    if not norm_exp:
        return 0.0
    overlap = set(norm_exp) & set(norm_act)
    return len(overlap) / len(set(norm_exp))

def run_benchmark():
    print("=" * 105)
    print("🚀 DUAL-PATH BENCHMARK MATRIX: STOP-&-WAIT vs FIXED MICRO-BATCH vs DYNAMIC HYBRID (AUTO)")
    print("=" * 105)
    
    os.environ["VT_MODEL_BACKEND"] = "cohere"
    transcribe2._backend = None
    transcribe2._current_backend_name = "cohere"
    
    # Warmup
    print("Pre-loading Cohere model...")
    import transcribe_cohere
    _ = transcribe_cohere.get_model()
    print("Warmup complete.\n")
    
    test_dir = Path(__file__).resolve().parent / "test_transcribe"
    
    # Selected test clips from our standardized suite:
    # 1.5s: Trimmed phrase from Sample 1
    # 5.0s: Sample 1 (Pride & Prejudice)
    # 10.4s: Sample 3 (Technical Dictation)
    # 20.4s: Sample 4 (Neural Network Paragraph)
    
    raw_samples = [
        {"name": "1.8s Short Command", "file": test_dir / "1.mp3", "max_s": 1.8, "expected": "Lydia was Lydia still"},
        {"name": "5.0s Literature", "file": test_dir / "1.mp3", "max_s": None, "expected": "Lydia was Lydia still; untamed, unabashed, wild, noisy, and fearless."},
        {"name": "10.4s Tech Dictation", "file": test_dir / "3.mp3", "max_s": None, "expected": "The voice recognition pipeline processes acoustic features in real time, converting speech spectrograms into formatted text for the user clipboard."},
        {"name": "20.4s Long Paragraph", "file": test_dir / "4.mp3", "max_s": None, "expected": "Artificial intelligence and neural network speech recognition have evolved significantly over the past decade. Modern end to end models combine conformer acoustic encoders with autoregressive transformer decoders to produce highly accurate transcriptions even in noisy environments."}
    ]
    
    modes = [
        ("disabled", "Stop-and-Wait (Batch)"),
        ("always", "Fixed Micro-Batch (3.0s chunks)"),
        ("auto", "Dynamic Hybrid (Direct <4.5s, Stream >=4.5s)")
    ]
    
    all_results = []
    
    for s_info in raw_samples:
        data, sr = sf.read(str(s_info["file"]))
        if sr != 16000:
            import librosa
            data = librosa.resample(data, orig_sr=sr, target_sr=16000)
            sr = 16000
            
        if s_info["max_s"] is not None:
            data = data[:int(s_info["max_s"] * sr)]
            
        dur = len(data) / sr
        print(f"\n==================== Testing: {s_info['name']} ({dur:.1f}s Audio) ====================")
        
        sample_metrics = {"name": s_info["name"], "duration": dur, "modes": {}}
        
        for mode_key, mode_label in modes:
            batcher = StreamingMicroBatcher(sample_rate=sr, mode=mode_key)
            batcher.start()
            
            block_size = int(0.1 * sr) # 100ms blocks
            for i in range(0, len(data), block_size):
                batcher.feed_audio(data[i:i+block_size])
                time.sleep(0.005) # Simulated fast stream
                
            t_release = time.perf_counter()
            text = batcher.finish_and_get_text()
            post_release_latency = time.perf_counter() - t_release
            acc = calculate_word_accuracy(s_info["expected"], text)
            
            sample_metrics["modes"][mode_key] = {
                "post_release": post_release_latency,
                "accuracy": acc,
                "text": text
            }
            print(f"  • {mode_label:<42}: Post-Release Wait = {post_release_latency:5.2f}s | Word Accuracy = {acc*100:5.1f}%")
            
        all_results.append(sample_metrics)
        
    print("\n" + "=" * 105)
    print(f"{'Audio Clip & Length':<25} | {'Stop-&-Wait':<18} | {'Fixed Micro-Batch':<18} | {'Dynamic Hybrid (Auto)':<22} | {'Auto Accuracy'}")
    print("-" * 105)
    for r in all_results:
        dis_lat = f"{r['modes']['disabled']['post_release']:.2f}s"
        alw_lat = f"{r['modes']['always']['post_release']:.2f}s"
        aut_lat = f"{r['modes']['auto']['post_release']:.2f}s"
        acc = f"{r['modes']['auto']['accuracy']*100:.1f}%"
        print(f"{r['name']:<25} | {dis_lat:>16}   | {alw_lat:>16}   | {aut_lat:>20}   | {acc:>10}")
    print("=" * 105)

if __name__ == "__main__":
    run_benchmark()
