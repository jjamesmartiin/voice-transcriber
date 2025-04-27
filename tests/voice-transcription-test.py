# to run this test:
# nix-shell --run "python tests/test1.py"

import unittest
import os
import sys
import numpy as np
from scipy.io import wavfile
import tempfile
from gtts import gTTS

# Add parent directory to path so we can import the transcribe module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import transcribe

class TranscriptionTest(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures before each test method"""
        # Create a temporary directory for test files
        self.test_dir = tempfile.mkdtemp()
        self.test_audio_path = os.path.join(self.test_dir, "test_audio.wav")
        
        # The text we'll use for our test audio file
        self.test_text = "This is a test of the voice transcription system. We are making sure to test its capabilities."
        
        # Generate an audio file with known text using gTTS
        tts = gTTS(text=self.test_text, lang='en', slow=False)
        tts.save(self.test_audio_path)
        
    def tearDown(self):
        """Clean up after each test method"""
        # Remove test audio file
        # if we need to test this then comment out the removals and listen to the file
        if os.path.exists(self.test_audio_path):
            os.remove(self.test_audio_path)
        
        # Remove test directory
        if os.path.exists(self.test_dir):
            os.rmdir(self.test_dir)
    
    def test_transcription_accuracy(self):
        """Test that the transcriber correctly transcribes our test audio"""
        # Transcribe the test audio file
        result = transcribe.transcribe_audio(audio_path=self.test_audio_path)
        
        # Get the transcribed text
        transcribed_text = result["text"].strip().lower()
        expected_text = self.test_text.lower()
        
        # Log the results for debugging
        print(f"Expected: '{expected_text}'")
        print(f"Got: '{transcribed_text}'")
        
        # Use a fuzzy match since speech recognition isn't always perfect
        # At minimum, all words should be present (just not necessarily in the exact order)
        for word in expected_text.split():
            self.assertIn(word.lower(), transcribed_text, 
                         f"Word '{word}' not found in transcription")
        
    def test_alternative_method(self):
        """Test transcription using manual audio generation instead of gTTS"""
        # Create a simple sine wave audio file - this is not speech but tests the pipeline
        alt_audio_path = os.path.join(self.test_dir, "sine_wave.wav")
        
        # Generate a simple sine wave
        sample_rate = 44100
        duration = 3  # seconds
        t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
        # Generate a 440 Hz sine wave
        audio_data = (np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
        
        # Save as WAV
        wavfile.write(alt_audio_path, sample_rate, audio_data)
        
        # This should produce empty or minimal transcription, but shouldn't crash
        try:
            result = transcribe.transcribe_audio(audio_path=alt_audio_path)
            # Test succeeded if we get here (no exceptions)
            self.assertIsNotNone(result["text"])
        finally:
            # Clean up
            if os.path.exists(alt_audio_path):
                os.remove(alt_audio_path)

if __name__ == "__main__":
    unittest.main() 
