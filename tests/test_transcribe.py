#!/usr/bin/env python3
"""
Test transcription accuracy against expected results.
Run: python tests/test_transcribe.py
"""
import os
import sys
import re
import glob

test_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_transcribe")
src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, src_dir)

import numpy as np
import soundfile as sf


def load_audio(audio_path):
    audio, sr = sf.read(audio_path)
    if sr != 16000:
        import librosa
        audio = librosa.resample(audio, orig_sr=sr, target_sr=16000)
    return audio.astype(np.float32)


def transcribe(backend_module, audio):
    """Use transcribe2 like the app does"""
    import transcribe2
    return transcribe2.transcribe_audio(audio_data=audio)


def score_transcription(expected, actual):
    if not actual:
        return 0, "FAIL"
    
    normal_expected = re.sub(r'[^\w\s]', '', expected.lower()).split()
    normal_actual = re.sub(r'[^\w\s]', '', actual.lower()).split()
    
    expected_words = set(normal_expected)
    actual_words = set(normal_actual)
    overlap = expected_words & actual_words
    
    if not normal_expected:
        return 0, "FAIL"
    
    match_ratio = len(overlap) / len(expected_words)
    
    if expected.lower().strip() == actual.lower().strip():
        return match_ratio, "PASS"
    elif match_ratio >= 0.7:
        return match_ratio, "PASS"
    elif match_ratio >= 0.4:
        return match_ratio, "PARTIAL"
    else:
        return match_ratio, "FAIL"


def main():
    import transcribe2
    import time
    
    test_files = sorted(glob.glob(os.path.join(test_dir, "*.mp3")))
    
    if not test_files:
        print(f"No test files found in {test_dir}")
        return 1
    
    # Set backends explicitly
    os.environ["VT_MODEL_BACKEND"] = "whisper"
    whisper_result = transcribe2.get_backend()
    os.environ["VT_MODEL_BACKEND"] = "cohere"
    cohere_result = transcribe2.get_backend()
    
    backends = [
        ("Whisper", "whisper"),
        ("Cohere", "cohere")
    ]
    
    print("VT Transcription Test")
    print("="*100)
    
    all_results = []
    
    for test_file in test_files:
        test_num = os.path.basename(test_file).replace(".mp3", "")
        md_file = test_file.replace(".mp3", ".md")
        
        if not os.path.exists(md_file):
            print(f"Skipping {test_num}: no .md file")
            continue
        
        with open(md_file, "r") as f:
            expected = f.read().strip()
        
        print(f"\nTest {test_num}: {expected[:60]}...")
        
        audio = load_audio(test_file)
        
        for backend_name, backend_id in backends:
            os.environ["VT_MODEL_BACKEND"] = backend_id
            # Reset and reload backend
            transcribe2._backend = None
            transcribe2._current_backend_name = backend_id
            
            start_load = time.time()
            backend = transcribe2.get_backend()
            load_time = time.time() - start_load
            
            start_transcribe = time.time()
            result = transcribe2.transcribe_audio(audio_data=audio)
            transcribe_time = time.time() - start_transcribe
            
            total_time = load_time + transcribe_time
            
            score, status = score_transcription(expected, result)
            all_results.append((test_num, backend_name, score, status, result, load_time, transcribe_time, total_time))
    
    print(f"\n{'='*100}")
    print("RESULTS TABLE")
    print("="*100)
    print(f"{'Test':<6} {'Model':<10} {'Load':<10} {'Transcribe':<12} {'Total':<10} {'Score':<8} {'Status':<8} {'Output'}")
    print(f"{'-'*6} {'-'*10} {'-'*10} {'-'*12} {'-'*10} {'-'*8} {'-'*8} {'-'*40}")
    
    for test_num, backend_name, score, status, result, load_t, trans_t, total_t in all_results:
        result_str = result if result else "None"
        print(f"{test_num:<6} {backend_name:<10} {load_t:>9.2f}s {trans_t:>11.2f}s {total_t:>9.2f}s {score*100:>7.1f}% {status:<8} {result_str}")
    
    print("="*80)
    
    pass_count = sum(1 for _, _, _, s, _ in all_results if s == "PASS")
    total_count = len(all_results)
    
    print(f"\nTotal: {pass_count}/{total_count} tests passed")
    
    return 0 if pass_count == total_count else 1


if __name__ == "__main__":
    sys.exit(main())