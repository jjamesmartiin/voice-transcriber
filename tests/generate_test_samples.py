#!/usr/bin/env python3
"""
Generate and curate standardized dictation test samples (5s, 15s, 30s, 45s)
using standard LibriSpeech and Harvard sentence benchmarks with exact ground-truth markdown.
"""

import os
import sys
import numpy as np
import soundfile as sf
from gtts import gTTS
from pathlib import Path

def setup_samples():
    test_dir = Path(__file__).resolve().parent / "test_transcribe"
    test_dir.mkdir(parents=True, exist_ok=True)
    
    samples = [
        {
            "id": "1",
            "name": "Pride & Prejudice (LibriSpeech)",
            "text": "Lydia was Lydia still; untamed, unabashed, wild, noisy, and fearless."
        },
        {
            "id": "2",
            "name": "Standard Harvard Benchmark Sentences",
            "text": "The birch canoe slid on the smooth planks. Glue the sheet to the dark blue background. Four hours of steady work faced us."
        },
        {
            "id": "3",
            "name": "Technical Dictation Sample",
            "text": "The voice recognition pipeline processes acoustic features in real time, converting speech spectrograms into formatted text for the user clipboard."
        },
        {
            "id": "4",
            "name": "Long Paragraph Dictation (30 seconds)",
            "text": "Artificial intelligence and neural network speech recognition have evolved significantly over the past decade. Modern end-to-end models combine conformer acoustic encoders with autoregressive transformer decoders to produce highly accurate transcriptions even in noisy environments."
        }
    ]
    
    print("=" * 80)
    print("🎙️ GENERATING & VERIFYING STANDARDIZED DICTATION BENCHMARK SAMPLES")
    print("=" * 80)
    
    for s in samples:
        mp3_file = test_dir / f"{s['id']}.mp3"
        md_file = test_dir / f"{s['id']}.md"
        
        # Save ground truth text
        md_file.write_text(s['text'].strip(), encoding='utf-8')
        
        if not mp3_file.exists():
            print(f"Generating audio for Sample {s['id']} ({s['name']})...")
            tts = gTTS(text=s['text'], lang='en', tld='com', slow=False)
            tts.save(str(mp3_file))
        
        # Check audio length
        data, sr = sf.read(str(mp3_file))
        duration = len(data) / sr
        print(f"  • Sample {s['id']}: {duration:5.1f}s | {s['name']} ➔ {mp3_file.name}")
    
    print("=" * 80)
    print("All benchmark audio samples ready in tests/test_transcribe/")

if __name__ == "__main__":
    setup_samples()
