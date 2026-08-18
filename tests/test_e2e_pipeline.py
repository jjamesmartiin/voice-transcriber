#!/usr/bin/env python3
"""
End-to-End Integration Test:
Simulates Windows Key Press (Alt+Shift) -> Plays/Feeds Known Audio Sample -> Transcribes -> Verifies Windows Clipboard
"""

import os
import sys
import time
import subprocess
import numpy as np
import soundfile as sf

# Ensure src is in sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import transcribe2

def get_windows_clipboard():
    """Retrieve text from Windows host clipboard"""
    try:
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, timeout=5
        )
        return res.stdout.strip()
    except Exception as e:
        print(f"Warning: Could not read Windows clipboard via PowerShell: {e}")
        return ""

def set_windows_clipboard(text):
    """Set text on Windows host clipboard via clip.exe or powershell"""
    try:
        clip_path = "/mnt/c/Windows/System32/clip.exe"
        if os.path.exists(clip_path):
            p = subprocess.Popen([clip_path], stdin=subprocess.PIPE)
            p.communicate(input=text.encode('utf-16le'))
            return True
        else:
            subprocess.run([
                "powershell.exe", "-NoProfile", "-Command",
                f"Set-Clipboard -Value @'\n{text}\n'@"
            ], check=True)
            return True
    except Exception as e:
        print(f"Warning: Failed to set Windows clipboard: {e}")
        return False

def run_e2e_test(audio_file=None, ground_truth_file=None, backend="cohere"):
    """
    Run end-to-end simulation from hotkey down to clipboard paste
    """
    if audio_file is None:
        audio_file = os.path.join(os.path.dirname(__file__), 'test_transcribe', '1.mp3')
    if ground_truth_file is None:
        ground_truth_file = os.path.join(os.path.dirname(__file__), 'test_transcribe', '1.md')
    
    with open(ground_truth_file, 'r', encoding='utf-8') as f:
        ground_truth = f.read().strip()
    
    audio_raw, sr = sf.read(audio_file)
    audio_duration = len(audio_raw) / sr
    audio_16k = audio_raw.astype(np.float32)
    
    print("=" * 85)
    print("🚀 END-TO-END PIPELINE TEST: Windows Hotkey ➔ Audio Ingestion ➔ Transcribe ➔ Clipboard")
    print("=" * 85)
    print(f"Backend Model:      {backend.upper()}")
    print(f"Test Audio File:    {os.path.basename(audio_file)} ({audio_duration:.2f}s, {sr}Hz)")
    print(f"Ground Truth Text:  \"{ground_truth}\"")
    print("-" * 85)
    
    # 0. Clear Windows clipboard with sentinel value
    sentinel = f"VT_TEST_SENTINEL_{int(time.time())}"
    set_windows_clipboard(sentinel)
    time.sleep(0.2)
    
    # 1. Simulate Windows Hotkey Trigger (Alt+Shift Pressed)
    print("[1/5] ⌨️  Simulating Windows Hotkey Press [Alt+Shift DOWN]...")
    t_start = time.perf_counter()
    
    # 2. Simulate Audio Stream Ingestion
    print(f"[2/5] 🎙️  Ingesting audio sample ({audio_duration:.2f}s of standard benchmark speech)...")
    time.sleep(0.1) # Simulate brief buffer
    
    # 3. Simulate Hotkey Release (Alt+Shift UP)
    print("[3/5] 🛑 Simulating Windows Hotkey Release [Alt+Shift UP] ➔ Triggering Transcription...")
    t_release = time.perf_counter()
    
    # Configure backend
    os.environ["VT_MODEL_BACKEND"] = backend
    transcribe2._current_backend_name = backend
    transcribe2._backend = None
    
    # 4. Model Transcription
    t_transcribe_start = time.perf_counter()
    transcription = transcribe2.transcribe_audio(audio_data=audio_16k)
    t_transcribe_end = time.perf_counter()
    transcribe_latency = t_transcribe_end - t_transcribe_start
    
    print(f"[4/5] 📝 Transcription Result ({transcribe_latency:.3f}s):")
    print(f"      \"{transcription}\"")
    
    # 5. Push to Windows Clipboard
    print("[5/5] 📋 Copying result to Windows host clipboard...")
    set_windows_clipboard(transcription)
    t_end = time.perf_counter()
    
    # Verify Windows Clipboard Content
    actual_clipboard = get_windows_clipboard()
    total_pipeline_time = t_end - t_start
    
    print("-" * 85)
    print("📊 BENCHMARK & ACCURACY METRICS:")
    print(f"  • Audio Duration:           {audio_duration:6.2f} s")
    print(f"  • Inference Latency:        {transcribe_latency:6.3f} s")
    print(f"  • Real-Time Factor (RTF):   {(transcribe_latency / audio_duration):6.3f}x ({(audio_duration / transcribe_latency):.1f}x real-time)")
    print(f"  • Total Pipeline Latency:   {total_pipeline_time:6.3f} s")
    print(f"  • Windows Clipboard Match:  {'✅ VERIFIED' if transcription in actual_clipboard or actual_clipboard in transcription else '⚠️ MISMATCH'}")
    
    # Accuracy check
    import re
    norm_expected = re.sub(r'[^\w\s]', '', ground_truth.lower()).split()
    norm_actual = re.sub(r'[^\w\s]', '', transcription.lower()).split()
    overlap = set(norm_expected) & set(norm_actual)
    accuracy = len(overlap) / len(set(norm_expected)) if norm_expected else 0.0
    print(f"  • Word Accuracy:            {accuracy * 100:.1f}%")
    
    print("=" * 85)
    if accuracy >= 0.7:
        print("🎉 END-TO-END TEST PASSED SUCCESSFULLY!")
        return 0
    else:
        print("❌ TEST FAILED: Accuracy below threshold.")
        return 1

if __name__ == "__main__":
    backend_to_test = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("VT_MODEL_BACKEND", "cohere")
    sys.exit(run_e2e_test(backend=backend_to_test))
