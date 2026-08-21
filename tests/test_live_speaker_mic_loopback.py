#!/usr/bin/env python3
"""
Live Speaker-to-Microphone Acoustic Loopback Integration Test Suite.

Plays reference audio clips through the system speakers while simultaneously recording 
from the system microphone, transcribing the captured audio in real time, and evaluating
accuracy and end-to-end latency.

Supports:
  - 30-second continuous speech test (sample_id='long_30s')
  - Short single-word / phrase pickup tests (sample_id='short_word', 'short_phrase')
  - Standard samples (sample_id='1' through '8')
  - Full suite runner (sample_id='all')
"""

import os
import sys
import time
import glob
import re
import threading
import numpy as np
import soundfile as sf
import sounddevice as sd

src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, src_dir)

from main import SimpleVoiceTranscriber
import t2


def score_transcription(expected, actual):
    if not actual:
        return 0.0, "FAIL"
    
    normal_expected = re.sub(r'[^\w\s]', '', expected.lower()).split()
    normal_actual = re.sub(r'[^\w\s]', '', actual.lower()).split()
    
    if not normal_expected:
        return 0.0, "FAIL"
        
    expected_words = set(normal_expected)
    actual_words = set(normal_actual)
    overlap = expected_words & actual_words
    
    match_ratio = len(overlap) / len(expected_words)
    
    if expected.lower().strip() == actual.lower().strip():
        return match_ratio, "PASS"
    elif match_ratio >= 0.6:
        return match_ratio, "PASS"
    elif match_ratio >= 0.3:
        return match_ratio, "PARTIAL"
    else:
        return match_ratio, "FAIL"


def run_single_test(sample_id, transcriber):
    test_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_transcribe")
    audio_path = os.path.join(test_dir, f"{sample_id}.mp3")
    md_path = os.path.join(test_dir, f"{sample_id}.md")
    
    if not os.path.exists(audio_path) or not os.path.exists(md_path):
        print(f"Error: Sample file '{sample_id}' not found in {test_dir}")
        return None

    with open(md_path, 'r') as f:
        expected_text = f.read().strip()

    # Load playback audio
    data, sr = sf.read(audio_path, dtype='float32')
    duration_sec = len(data) / float(sr)

    print("\n" + "-" * 80)
    print(f"🎙️  TESTING SAMPLE: {sample_id} ({duration_sec:.2f}s audio)")
    print(f"EXPECTED: \"{expected_text}\"")
    print("-" * 80)

    transcriber.start_recording()
    time.sleep(0.3)  # Pre-playback cushion

    sd.play(data, sr)
    sd.wait()
    time.sleep(0.5)  # Acoustic tail cushion

    t0_process = time.time()
    transcriber.stop_recording(copy_to_clipboard=True)
    transcriber.process_recording()
    processing_time = time.time() - t0_process

    actual_text = getattr(transcriber, 'last_transcription', '').strip()
    score, status = score_transcription(expected_text, actual_text)

    print(f"CAPTURED: \"{actual_text}\"")
    print(f"RESULT  : {score * 100:.1f}% [{status}] in {processing_time:.2f}s")
    
    return {
        "sample_id": sample_id,
        "duration": duration_sec,
        "expected": expected_text,
        "actual": actual_text,
        "score": score,
        "status": status,
        "latency": processing_time
    }


def main():
    sample_arg = sys.argv[1] if len(sys.argv) > 1 else "long_30s"
    
    print("\n" + "=" * 80)
    print("🎙️  LIVE SPEAKER-TO-MIC ACOUSTIC LOOPBACK SUITE")
    print("=" * 80)

    # Pre-initialize single transcriber instance
    transcriber = SimpleVoiceTranscriber()

    if sample_arg == "all":
        samples_to_run = ["short_word", "short_phrase", "1", "2", "3", "long_30s"]
    else:
        samples_to_run = [sample_arg]

    results = []
    for sid in samples_to_run:
        res = run_single_test(sid, transcriber)
        if res:
            results.append(res)

    print("\n" + "=" * 80)
    print("SUMMARY RESULTS TABLE")
    print("=" * 80)
    print(f"{'Sample ID':<15} | {'Duration':<10} | {'Score':<8} | {'Status':<8} | {'Pipeline Latency'}")
    print("-" * 80)
    for r in results:
        print(f"{r['sample_id']:<15} | {r['duration']:>6.2f}s    | {r['score']*100:>5.1f}%   | {r['status']:<8} | {r['latency']:>6.2f}s")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
