#!/usr/bin/env python3
"""
Generate and curate expanded standardized dictation test samples (Samples 1 to 8)
covering various domains: Literature, Harvard Phonetic, Technical, Paragraphs,
Legal/Business, Conversational, and Numerical/Dates.
"""

import os
import sys
import numpy as np
import soundfile as sf
from gtts import gTTS
from pathlib import Path

def setup_expanded_samples():
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
            "name": "Long Paragraph Dictation (20s)",
            "text": "Artificial intelligence and neural network speech recognition have evolved significantly over the past decade. Modern end to end models combine conformer acoustic encoders with autoregressive transformer decoders to produce highly accurate transcriptions even in noisy environments."
        },
        {
            "id": "5",
            "name": "Legal & Business Contract Dictation (15s)",
            "text": "The parties hereby agree that all confidential information disclosed under this agreement shall remain the exclusive property of the disclosing party for a period of five years."
        },
        {
            "id": "6",
            "name": "Meeting Action Items & Notes (18s)",
            "text": "During our quarterly review, the engineering team decided to migrate the primary database cluster to a multi-region deployment by the end of next month."
        },
        {
            "id": "7",
            "name": "Medical & Clinical Notes (16s)",
            "text": "The patient presented with mild respiratory symptoms and normal vital signs. We recommended standard hydration, rest, and follow-up monitoring in two weeks."
        },
        {
            "id": "8",
            "name": "Full Length 40-Second Dictation Passage",
            "text": "Good morning team. Please ensure that all pull requests are reviewed and merged before the Friday release window. We need to verify that automated integration tests pass across all environments, including local Windows development and the remote continuous integration pipeline."
        }
    ]
    
    print("=" * 85)
    print("🎙️ GENERATING & EXPANDING STANDARDIZED DICTATION BENCHMARK SUITE")
    print("=" * 85)
    
    for s in samples:
        mp3_file = test_dir / f"{s['id']}.mp3"
        md_file = test_dir / f"{s['id']}.md"
        
        # Save ground truth text
        md_file.write_text(s['text'].strip(), encoding='utf-8')
        
        if not mp3_file.exists() or s['id'] in ["4", "5", "6", "7", "8"]:
            print(f"Generating audio for Sample {s['id']} ({s['name']})...")
            tts = gTTS(text=s['text'], lang='en', tld='com', slow=False)
            tts.save(str(mp3_file))
        
        # Check audio length
        data, sr = sf.read(str(mp3_file))
        duration = len(data) / sr
        print(f"  • Sample {s['id']}: {duration:5.1f}s | {s['name']} ➔ {mp3_file.name}")
    
    print("=" * 85)
    print("All 8 benchmark audio samples ready in tests/test_transcribe/")

if __name__ == "__main__":
    setup_expanded_samples()
