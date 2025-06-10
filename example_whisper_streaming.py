#!/usr/bin/env python3
"""
Example usage of whisper_streaming package

This demonstrates how to use the whisper_streaming package from 
https://github.com/ufal/whisper_streaming for real-time transcription.
"""

import sys
import numpy as np
import argparse

# Import from our newly installed whisper_streaming package
from whisper_streaming.whisper_online import *

def main():
    parser = argparse.ArgumentParser(description="Whisper Streaming Example")
    parser.add_argument("--language", default="en", help="Source language (default: en)")
    parser.add_argument("--model", default="small", help="Whisper model size (default: small)")
    parser.add_argument("--backend", default="faster-whisper", help="Backend to use (default: faster-whisper)")
    parser.add_argument("--audio-file", help="Audio file to process (for testing)")
    
    args = parser.parse_args()
    
    print(f"Initializing Whisper Streaming with:")
    print(f"  Language: {args.language}")
    print(f"  Model: {args.model}")
    print(f"  Backend: {args.backend}")
    
    try:
        # Initialize the ASR processor based on backend
        if args.backend == "faster-whisper":
            # Configure device and compute type for CPU usage
            asr = FasterWhisperASR(args.language, args.model, device="cpu", compute_type="int8")
        else:
            print(f"Backend {args.backend} not implemented in this example")
            return 1
            
        # You can configure additional options:
        # asr.use_vad()  # Use Voice Activity Detection
        
        # Create the online processor
        online = OnlineASRProcessor(asr)
        
        print("Whisper Streaming initialized successfully!")
        print("Available methods:")
        print(f"  - online.insert_audio_chunk(audio_data)")
        print(f"  - online.process_iter()")
        print(f"  - online.finish()")
        
        if args.audio_file:
            print(f"\nProcessing audio file: {args.audio_file}")
            # This would require additional audio loading code
            print("Audio file processing not implemented in this simple example")
            print("See whisper_online.py in the original repo for full implementation")
        else:
            print("\nTo use with real audio:")
            print("1. Load audio data (16kHz, mono)")
            print("2. Call online.insert_audio_chunk(audio_chunk)")
            print("3. Call online.process_iter() to get partial results")
            print("4. Call online.finish() when done")
            
        return 0
        
    except Exception as e:
        print(f"Error initializing Whisper Streaming: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main()) 