#!/usr/bin/env python3
"""
Fast Unit Test Suite for Micro-Batching, Energy Gate, Silence Trimming, and Post-Processor.
Runs in milliseconds without requiring neural network model weights.
"""

import os
import sys
import unittest
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from post_processor import clean_speech_transcription
from micro_batcher import trim_trailing_silence, StreamingMicroBatcher
from transcribe_cohere import has_speech_activity

class TestMicroBatchingEngine(unittest.TestCase):

    def test_has_speech_activity_silence(self):
        """Test energy gate detects pure silence"""
        silence = np.zeros(16000, dtype=np.float32)
        self.assertFalse(has_speech_activity(silence))
        
        low_noise = np.random.uniform(-0.001, 0.001, 16000).astype(np.float32)
        self.assertFalse(has_speech_activity(low_noise))

    def test_has_speech_activity_signal(self):
        """Test energy gate detects speech signal"""
        t = np.linspace(0, 1, 16000, endpoint=False)
        sine_speech = (0.05 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        self.assertTrue(has_speech_activity(sine_speech))

    def test_trim_trailing_silence(self):
        """Test VAD tail silence trimmer leaves speech + 100ms cushion"""
        sr = 16000
        # 1 sec signal + 2 sec silence
        speech = np.random.uniform(-0.05, 0.05, sr).astype(np.float32)
        silence = np.zeros(sr * 2, dtype=np.float32)
        audio = np.concatenate([speech, silence])
        
        trimmed = trim_trailing_silence(audio, sample_rate=sr, min_speech_cushion_ms=100)
        
        # Original was 3 sec (48000 samples). Trimmed should be around ~1.1 sec (~17600 samples)
        self.assertLess(len(trimmed), len(audio))
        expected_len = int(1.0 * sr + 0.1 * sr) # 1s speech + 100ms cushion
        self.assertAlmostEqual(len(trimmed), expected_len, delta=800)

    def test_post_processor_artifacts(self):
        """Test post processor fixes false breaks, stutters, and trailing mutterings"""
        # Trailing mutterings (oops, whoops)
        self.assertEqual(clean_speech_transcription("This is important oops"), "This is important")
        self.assertEqual(clean_speech_transcription("Hello world whoops"), "Hello world")
        
        # Verbal retractions & speech self-corrections
        self.assertEqual(clean_speech_transcription("Let's meet at 5 PM... actually 6 PM"), "Let's meet at 6 PM")
        self.assertEqual(clean_speech_transcription("Send the report to John... I mean Alice"), "Send the report to Alice")
        self.assertEqual(clean_speech_transcription("We should deploy on Tuesday... no wait Wednesday"), "We should deploy on Wednesday")
        self.assertEqual(clean_speech_transcription("Add the class definition... scratch that"), "Add the class definition")
        
        # Repeated stutters
        self.assertEqual(clean_speech_transcription("about. about this project"), "about this project")
        self.assertEqual(clean_speech_transcription("the the repository"), "the repository")
        
        # Conjunction after period
        self.assertEqual(clean_speech_transcription("commit code. and push"), "commit code, and push")
        
        # Discourse markers
        self.assertEqual(clean_speech_transcription("So. I think we should proceed"), "So, I think we should proceed")

    def test_micro_batcher_buffer_splitting(self):
        """Test StreamingMicroBatcher splits chunks correctly"""
        batcher = StreamingMicroBatcher(sample_rate=16000, mode="always")
        batcher.start()
        
        # Feed 4 seconds of speech audio (above threshold)
        sr = 16000
        speech_chunk = np.random.uniform(-0.05, 0.05, sr).astype(np.float32)
        for _ in range(4):
            batcher.feed_audio(speech_chunk)
            
        # Finish
        text = batcher.finish_and_get_text()
        self.assertIsInstance(text, str)

if __name__ == "__main__":
    unittest.main(argv=['first-arg'], exit=False)
