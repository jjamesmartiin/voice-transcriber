#!/usr/bin/env python3
"""
Synthetic End-to-End Audio Benchmark & Integration Suite.
Generates synthetic speech audio from text prompts, streams the audio through the
Voice Transcriber pipeline (StreamingMicroBatcher + ASR + Wispr Flow Post-Processor),
and measures latency intervals across Technique A, B, and C configurations.
"""

import os
import sys
import time
import subprocess
import tempfile
import unittest
import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from post_processor import clean_speech_transcription, process_verbal_retractions, process_slm_llm_rewrite
from micro_batcher import StreamingMicroBatcher, trim_trailing_silence
from transcribe_cohere import transcribe_audio as transcribe_cohere_audio

TEST_CASES = [
    {
        "name": "Self-Correction Retraction",
        "raw_text": "I went to the store um and bought some apples... actually oranges",
        "target_text": "I went to the store and bought oranges.",
    },
    {
        "name": "Name Retraction",
        "raw_text": "Send the report to John... I mean Alice",
        "target_text": "Send the report to Alice.",
    },
    {
        "name": "Date Self-Correction",
        "raw_text": "We should deploy on Tuesday... no wait Wednesday",
        "target_text": "We should deploy on Wednesday.",
    },
    {
        "name": "Voice Erasure",
        "raw_text": "Add the class definition... scratch that",
        "target_text": "Add the class definition.",
    },
    {
        "name": "Subordinating Conjunction Fix",
        "raw_text": "Specifically. because we might need to update",
        "target_text": "Specifically because we might need to update.",
    }
]

def synthesize_speech_audio(text: str, target_sr: int = 16000) -> np.ndarray:
    """
    Synthesizes PCM 16kHz speech audio from text using espeak-ng / espeak TTS.
    Returns float32 audio array normalized to [-1.0, 1.0].
    """
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
        tmp_wav = tmp_file.name

    try:
        # Use espeak-ng or espeak to generate WAV
        cmd = ["espeak-ng", "-s", "150", text, "-w", tmp_wav]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode != 0:
            cmd = ["espeak", "-s", "150", text, "-w", tmp_wav]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        data, sr = sf.read(tmp_wav, dtype='float32')
        if len(data.shape) > 1:
            data = data[:, 0]

        # Resample to 16000Hz if needed
        if sr != target_sr:
            from scipy.signal import resample_poly
            from math import gcd
            g = gcd(sr, target_sr)
            data = resample_poly(data, target_sr // g, sr // g).astype(np.float32)

        # Append 300ms cushion of trailing silence
        silence_cushion = np.zeros(int(target_sr * 0.3), dtype=np.float32)
        return np.concatenate([data, silence_cushion])
    finally:
        if os.path.exists(tmp_wav):
            os.remove(tmp_wav)

class TestSyntheticE2EBenchmark(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        print("\n" + "="*80)
        print("  SYNTHETIC E2E AUDIO BENCHMARK SUITE (TTS -> ASR -> WISPR FLOW)")
        print("="*80)

    def test_synthetic_audio_end_to_end_benchmark(self):
        """Runs synthetic audio generation and measures latency breakdown across pipeline modes."""
        results_summary = []

        for case in TEST_CASES:
            case_name = case["name"]
            raw_text = case["raw_text"]
            target_text = case["target_text"]

            # Step 1: Synthesize Audio
            t0_tts = time.time()
            audio_pcm = synthesize_speech_audio(raw_text)
            tts_latency_ms = (time.time() - t0_tts) * 1000
            duration_sec = len(audio_pcm) / 16000.0

            # Step 2: ASR Audio Decoding Latency
            t0_asr = time.time()
            asr_transcript = transcribe_cohere_audio(audio_pcm)
            asr_latency_ms = (time.time() - t0_asr) * 1000

            # Step 3: Technique A (Instant 0ms Pre-pass)
            t0_tech_a = time.time()
            tech_a_output = process_verbal_retractions(asr_transcript)
            tech_a_latency_ms = (time.time() - t0_tech_a) * 1000

            # Step 4: Technique C (Hybrid Pass with vLLM SLM)
            os.environ["VT_ENABLE_SLM"] = "1"
            os.environ["VT_SLM_TIMEOUT"] = "2.0"
            t0_tech_c = time.time()
            tech_c_output = clean_speech_transcription(asr_transcript)
            tech_c_latency_ms = (time.time() - t0_tech_c) * 1000

            # Idempotency Verification (Run 2 & Run 3)
            tech_c_run2 = clean_speech_transcription(asr_transcript)
            tech_c_run3 = clean_speech_transcription(asr_transcript)
            is_idempotent = (tech_c_output == tech_c_run2 == tech_c_run3)

            results_summary.append({
                "name": case_name,
                "duration_sec": duration_sec,
                "tts_ms": tts_latency_ms,
                "asr_ms": asr_latency_ms,
                "tech_a_ms": tech_a_latency_ms,
                "tech_c_ms": tech_c_latency_ms,
                "asr_text": asr_transcript,
                "final_output": tech_c_output,
                "target_text": target_text,
                "idempotent": is_idempotent
            })

            # Assertions
            self.assertTrue(len(tech_c_output) > 0, "Output must not be empty")
            self.assertTrue(is_idempotent, f"vLLM SLM pass must be idempotent for case '{case_name}'")

        # Print Benchmark Table
        print("\n" + "-"*80)
        print(f"{'Benchmark Test Case':<28} | {'Audio Len':<9} | {'ASR Latency':<12} | {'Wispr Flow':<12} | {'Total E2E':<10} | {'Idempotent'}")
        print("-"*80)
        for r in results_summary:
            total_e2e_ms = r["asr_ms"] + r["tech_c_ms"]
            print(f"{r['name']:<28} | {r['duration_sec']:>5.2f}s    | {r['asr_ms']:>7.1f} ms  | {r['tech_c_ms']:>7.1f} ms  | {total_e2e_ms:>6.1f} ms | {'✅ Yes' if r['idempotent'] else '❌ No'}")
            print(f"   ├─ Raw ASR Output:  \"{r['asr_text']}\"")
            print(f"   └─ Final Wispr Flow: \"{r['final_output']}\"")
        print("-"*80 + "\n")

if __name__ == "__main__":
    unittest.main()
