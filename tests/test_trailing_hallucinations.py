#!/usr/bin/env python3
"""
Targeted Test Suite for Trailing Hallucinations ('you', 'Bye') vs Legitimate Word Retention.
Uses local standardized benchmark audio files + synthetic trailing silence.
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

def run_tests():
    print("=" * 80)
    print("🧪 RUNNING TARGETED TRAILING HALLUCINATION & RETENTION TESTS")
    print("=" * 80)
    
    os.environ["VT_MODEL_BACKEND"] = "cohere"
    transcribe2._backend = None
    transcribe2._current_backend_name = "cohere"
    
    test_dir = Path(__file__).resolve().parent / "test_transcribe"
    
    # Warmup
    print("Pre-loading Cohere model...")
    import transcribe_cohere
    _ = transcribe_cohere.get_model()
    print("Warmup complete.\n")
    
    test_cases = [
        {
            "name": "Sample 1: Pride & Prejudice (Natural End)",
            "file": test_dir / "1.mp3",
            "trailing_silence": 0.0,
            "must_not_contain": ["you", "bye", "watching"]
        },
        {
            "name": "Sample 6: Meeting Action Items (+500ms Dead Air / Silence)",
            "file": test_dir / "6.mp3",
            "trailing_silence": 0.5,
            "must_contain": "month",
            "must_not_contain": ["you", "bye", "watching"]
        },
        {
            "name": "Sample 8: CI/CD Pipeline (+600ms Dead Air / Silence)",
            "file": test_dir / "8.mp3",
            "trailing_silence": 0.6,
            "must_contain": "pipeline",
            "must_not_contain": ["you", "bye", "watching"]
        }
    ]
    
    passed_all = True
    for tc in test_cases:
        print(f"\n--- Testing: {tc['name']} ---")
        data, sr = sf.read(str(tc["file"]))
        if sr != 16000:
            import librosa
            data = librosa.resample(data, orig_sr=sr, target_sr=16000)
            sr = 16000
            
        flat = data.astype(np.float32)
        if tc["trailing_silence"] > 0:
            silence = np.zeros(int(tc["trailing_silence"] * sr), dtype=np.float32)
            flat = np.concatenate([flat, silence])
            
        # Test through StreamingMicroBatcher
        batcher = StreamingMicroBatcher(sample_rate=sr)
        batcher.start()
        
        block_size = int(0.1 * sr)
        for i in range(0, len(flat), block_size):
            batcher.feed_audio(flat[i:i+block_size])
            time.sleep(0.005)
            
        transcription = batcher.finish_and_get_text()
        norm_trans = normalize_text(transcription)
        print(f"  • Model Output: \"{transcription}\"")
        
        passed = True
        if tc.get("must_contain"):
            if tc["must_contain"] not in norm_trans.split():
                print(f"  ❌ FAILED: Missing required word '{tc['must_contain']}'")
                passed = False
                passed_all = False
                
        for forbidden in tc.get("must_not_contain", []):
            if forbidden in norm_trans.split()[-3:]: # check trailing 3 words
                print(f"  ❌ FAILED: Hallucinated forbidden trailing word '{forbidden}'")
                passed = False
                passed_all = False
                
        if passed:
            print("  ✅ PASSED: Clean output without trailing hallucinations!")
            
    print("\n" + "=" * 80)
    if passed_all:
        print("🎉 ALL TEST CASES PASSED PERFECTLY!")
    else:
        print("⚠️ SOME TEST CASES FAILED.")
    print("=" * 80)

if __name__ == "__main__":
    run_tests()
