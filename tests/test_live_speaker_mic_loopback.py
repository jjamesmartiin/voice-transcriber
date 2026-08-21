#!/usr/bin/env python3
"""
Live Speaker-to-Microphone Acoustic Loopback Integration Test.

Plays reference audio clips through the system speakers while simultaneously recording 
from the system microphone, transcribing the captured audio in real time, and evaluating
accuracy and end-to-end latency.
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


def run_loopback_test(sample_id="1"):
    test_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_transcribe")
    audio_path = os.path.join(test_dir, f"{sample_id}.mp3")
    md_path = os.path.join(test_dir, f"{sample_id}.md")
    
    if not os.path.exists(audio_path) or not os.path.exists(md_path):
        print(f"Error: Sample file {sample_id} not found in {test_dir}")
        return False

    with open(md_path, 'r') as f:
        expected_text = f.read().strip()

    print("\n" + "=" * 80)
    print(f"🎙️  LIVE SPEAKER-TO-MIC ACOUSTIC LOOPBACK TEST (Sample #{sample_id})")
    print("=" * 80)
    print(f"EXPECTED TEXT : \"{expected_text}\"")
    print("-" * 80)

    # Load playback audio
    data, sr = sf.read(audio_path, dtype='float32')
    duration_sec = len(data) / float(sr)

    # Initialize transcriber
    transcriber = SimpleVoiceTranscriber()
    
    print("\n[1/3] Starting microphone recording...")
    transcriber.start_recording()
    time.sleep(0.3)  # Pre-playback audio buffer cushion

    print(f"[2/3] Playing audio sample #{sample_id} over speakers ({duration_sec:.2f}s)...")
    t0_play = time.time()
    sd.play(data, sr)
    sd.wait()
    t_play_done = time.time()
    
    time.sleep(0.5)  # Reverb & room acoustic tail cushion

    print("[3/3] Stopping recording & running ASR transcription pipeline...")
    t0_process = time.time()
    transcriber.stop_recording(copy_to_clipboard=True)
    
    # Process recording synchronously for testing
    transcriber.process_recording()
    processing_time = time.time() - t0_process

    actual_text = getattr(transcriber, 'last_transcription', '').strip()
    score, status = score_transcription(expected_text, actual_text)

    print("\n" + "=" * 80)
    print("ACOUSTIC LOOPBACK RESULTS")
    print("=" * 80)
    print(f"EXPECTED TEXT  : \"{expected_text}\"")
    print(f"CAPTURED TEXT  : \"{actual_text}\"")
    print(f"MATCH ACCURACY : {score * 100:.1f}% [{status}]")
    print(f"PLAYBACK TIME  : {duration_sec:.2f}s")
    print(f"PIPELINE TIME  : {processing_time:.2f}s")
    print("=" * 80 + "\n")

    return status in ["PASS", "PARTIAL"]


if __name__ == "__main__":
    sample_id = sys.argv[1] if len(sys.argv) > 1 else "1"
    run_loopback_test(sample_id)
