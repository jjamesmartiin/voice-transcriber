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
        old_env = os.environ.get("VT_ENABLE_SLM")
        try:
            os.environ["VT_ENABLE_SLM"] = "0"
            # Trailing mutterings (oops, whoops)
            self.assertEqual(clean_speech_transcription("This is important oops"), "This is important.")
            self.assertEqual(clean_speech_transcription("Hello world whoops"), "Hello world")
        
        # Verbal retractions & speech self-corrections
            self.assertEqual(clean_speech_transcription("Let's meet at 5 PM... actually 6 PM"), "Let's meet at 6 PM.")
            self.assertEqual(clean_speech_transcription("Send the report to John... I mean Alice"), "Send the report to Alice.")
            self.assertEqual(clean_speech_transcription("We should deploy on Tuesday... no wait Wednesday"), "We should deploy on Wednesday.")
            self.assertEqual(clean_speech_transcription("Add the class definition... scratch that"), "Add the class definition.")
            
            # Repeated stutters
            self.assertEqual(clean_speech_transcription("about. about this project"), "about this project.")
            self.assertEqual(clean_speech_transcription("the the repository"), "the repository")
            
            # Subordinating conjunctions after period
            self.assertEqual(clean_speech_transcription("specifically. because we might need"), "specifically because we might need.")
            
            # Conjunction after period
            self.assertEqual(clean_speech_transcription("commit code. and push"), "commit code, and push.")
            
            # Discourse markers
            self.assertEqual(clean_speech_transcription("So. I think we should proceed"), "So, I think we should proceed.")

            # Plural acronyms keep their casing mid-sentence ("LLMs", "VMs", "GPUs", ...)
            self.assertEqual(clean_speech_transcription("We use LLMs every day"), "We use LLMs every day.")
            self.assertEqual(clean_speech_transcription("We spin up VMs in the cloud"), "We spin up VMs in the cloud.")
            self.assertEqual(clean_speech_transcription("I have GPUs and SSDs in my rig"), "I have GPUs and SSDs in my rig.")
            self.assertEqual(clean_speech_transcription("The APIs and CLIs work great"), "The APIs and CLIs work great.")

            # ...while over-capitalized common words still get decapitalized
            self.assertEqual(clean_speech_transcription("This Is important"), "This is important.")
            self.assertEqual(clean_speech_transcription("He Asks about it"), "He asks about it.")

            # ASR mis-hearings of the "AI" acronym recover to "AI"
            self.assertEqual(clean_speech_transcription("Hey, if a shoe company can make a eyes, so can a plane company"), "Hey, if a shoe company can make AI, so can a plane company.")
            self.assertEqual(clean_speech_transcription("The agent used an eyes model"), "The agent used AI model.")
            # ...but valid "an eye" / "a eye" / bare "eyes" are untouched
            self.assertEqual(clean_speech_transcription("He has an eye for design"), "He has an eye for design.")
            self.assertEqual(clean_speech_transcription("She has a eye for detail"), "She has a eye for detail.")
            self.assertEqual(clean_speech_transcription("Her eyes are blue"), "Her eyes are blue.")

            # ASR mis-hearings of "addressing" ("t needs a. dressing." -> "needs addressing.")
            self.assertEqual(clean_speech_transcription("t needs a. dressing."), "needs addressing.")
            self.assertEqual(clean_speech_transcription("this issue needs a dressing"), "this issue needs addressing.")
            self.assertEqual(clean_speech_transcription("I like salad with a dressing"), "I like salad with a dressing.")

            # Comma spacing normalization (missing space after comma, spaces before comma)
            self.assertEqual(clean_speech_transcription("whatever we're doing for that,we can do for DF"), "whatever we're doing for that, we can do for DF.")
            self.assertEqual(clean_speech_transcription("works for JK though ,so like whatever"), "works for JK though, so like whatever.")

            # Filler word space preservation & double-space cleanup
            self.assertEqual(clean_speech_transcription("building it right now for myself um and I'll let you know"), "building it right now for myself and I'll let you know.")
            self.assertEqual(clean_speech_transcription("how it goes.  -- this one"), "how it goes. -- this one.")
        finally:
            if old_env is None:
                os.environ.pop("VT_ENABLE_SLM", None)
            else:
                os.environ["VT_ENABLE_SLM"] = old_env

    def test_process_slm_llm_rewrite_fallback(self):
        """Test vLLM SLM pass gracefully falls back when vLLM port is unavailable"""
        from post_processor import process_slm_llm_rewrite
        os.environ["VT_ENABLE_SLM"] = "1"
        os.environ["VT_VLLM_URL"] = "http://localhost:59999/v1/chat/completions" # Unreachable port
        result = process_slm_llm_rewrite("Hello world", timeout_sec=0.05)
        self.assertEqual(result, "Hello world")
        os.environ["VT_ENABLE_SLM"] = "0"

    def test_live_vllm_slm_integration(self):
        """Test live local vLLM REST API server integration on port 8000 (Idempotency & 100% Accuracy)"""
        import urllib.request
        from post_processor import process_slm_llm_rewrite, clean_speech_transcription

        vllm_url = os.environ.get("VT_VLLM_URL", "http://localhost:8000/v1/chat/completions")
        models_url = vllm_url.replace("/chat/completions", "/models")
        
        # Check if local vLLM service is active
        try:
            req = urllib.request.Request(models_url)
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status != 200:
                    self.skipTest("vLLM service not responding with 200 OK")
        except Exception:
            self.skipTest("Local vLLM server is not running on port 8000")

        # Live test against active vLLM service
        old_env = os.environ.get("VT_ENABLE_SLM")
        try:
            os.environ["VT_ENABLE_SLM"] = "1"
            os.environ["VT_VLLM_URL"] = vllm_url
            raw_input = "i went to the store um and bought some apples... actually oranges"
            expected_target = "I went to the store and bought oranges."
            
            # 1. Test direct process_slm_llm_rewrite (temperature=0.0 deterministic output)
            slm_result1 = process_slm_llm_rewrite(raw_input, timeout_sec=2.0)
            self.assertTrue(len(slm_result1) > 0)
            
            # 2. Test full hybrid pipeline accuracy
            full_result1 = clean_speech_transcription(raw_input)
            
            # Semantic & Accuracy Assertions
            self.assertEqual(full_result1.rstrip('.'), expected_target.rstrip('.'), f"Expected '{expected_target}', got '{full_result1}'")
            self.assertNotIn("apples", full_result1.lower(), "Retracted word 'apples' should be removed")
            self.assertNotIn("um", full_result1.lower(), "Hesitation filler 'um' should be removed")
            self.assertIn("oranges", full_result1.lower(), "Retraction target 'oranges' must be present")
            
            # 3. Test Idempotency (Repeatability across 3 consecutive runs)
            full_result2 = clean_speech_transcription(raw_input)
            full_result3 = clean_speech_transcription(raw_input)
            
            self.assertEqual(full_result1, full_result2, "vLLM SLM pass must be idempotent (run 1 vs run 2 mismatch)")
            self.assertEqual(full_result2, full_result3, "vLLM SLM pass must be idempotent (run 2 vs run 3 mismatch)")
        finally:
            if old_env is None:
                os.environ.pop("VT_ENABLE_SLM", None)
            else:
                os.environ["VT_ENABLE_SLM"] = old_env

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

    def test_number_words_to_digits(self):
        """Test spoken number words convert to actual digits."""
        from post_processor import convert_number_words_to_digits as conv
        old_env = os.environ.get("VT_NUMBER_DIGITS")
        os.environ["VT_NUMBER_DIGITS"] = "1"
        try:
            # Phone-number / digit strings (continuous digits)
            self.assertEqual(conv("Six seven zero six seven zero six nine nine six"), "6706706996")
            self.assertEqual(conv("seven six oh six seven oh six nine nine six"), "7606706996")
            self.assertEqual(conv("double oh seven"), "007")
            self.assertEqual(conv("my number is one two three four five"), "my number is 12345")

            # Cardinals
            self.assertEqual(conv("twenty five people"), "25 people")
            self.assertEqual(conv("one hundred and fifty"), "150")
            self.assertEqual(conv("two thousand twenty four"), "2024")
            self.assertEqual(conv("a period of five years"), "a period of 5 years")

            # Ordinals
            self.assertEqual(conv("the fifth item"), "the 5th item")
            self.assertEqual(conv("twenty first birthday"), "21st birthday")
            self.assertEqual(conv("one hundred and first"), "101st")

            # Decimals, percent, times, years
            self.assertEqual(conv("three point one four"), "3.14")
            self.assertEqual(conv("fifty percent"), "50%")
            self.assertEqual(conv("five pm"), "5 PM")
            self.assertEqual(conv("meet at nine thirty am"), "meet at 9:30 AM")
            self.assertEqual(conv("nineteen eighty five"), "1985")
            self.assertEqual(conv("twenty twenty four"), "2024")

            # Protections: pronouns/idioms stay as words
            self.assertEqual(conv("no one is here"), "no one is here")
            self.assertEqual(conv("the one thing i need"), "the one thing i need")
            self.assertEqual(conv("wait a second"), "wait a second")
            self.assertEqual(conv("everyone is welcome"), "everyone is welcome")
        finally:
            if old_env is None:
                os.environ.pop("VT_NUMBER_DIGITS", None)
            else:
                os.environ["VT_NUMBER_DIGITS"] = old_env

    def test_number_words_disabled(self):
        """Test VT_NUMBER_DIGITS=0 disables number conversion."""
        from post_processor import convert_number_words_to_digits as conv
        os.environ["VT_NUMBER_DIGITS"] = "0"
        self.assertEqual(conv("twenty five people"), "twenty five people")
        os.environ["VT_NUMBER_DIGITS"] = "1"

if __name__ == "__main__":
    unittest.main(argv=['first-arg'], exit=False)
