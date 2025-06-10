#!/usr/bin/env python3
"""
Live Microphone Transcription using whisper-streaming

This shows how to use whisper-streaming for real-time transcription
from your system microphone.
"""

import time
import threading
import numpy as np
import pyaudio
import queue
import sys
import os
from whisper_streaming.whisper_online import FasterWhisperASR, OnlineASRProcessor

class LiveMicrophoneTranscriber:
    """Real-time microphone transcriber using whisper-streaming"""
    
    def __init__(self, language="en", model="small", device="cpu"):
        self.language = language
        self.model = model
        self.device = device
        
        # Audio configuration
        self.sample_rate = 16000  # Whisper expects 16kHz
        self.chunk_size = 1024    # Small chunks for low latency
        self.channels = 1         # Mono audio
        self.format = pyaudio.paInt16
        
        # Initialize audio
        self.audio = pyaudio.PyAudio()
        self.audio_queue = queue.Queue()
        self.stream = None
        self.recording = False
        
        # Volume tracking
        self.current_volume = 0.0
        self.volume_history = []
        
        # Initialize the streaming components
        print(f"Initializing streaming transcriber (model: {model}, device: {device})")
        self.asr = FasterWhisperASR(
            language, 
            model, 
            device=device, 
            compute_type="int8" if device == "cpu" else "float16"
        )
        
        # Optional: Enable voice activity detection
        # self.asr.use_vad()
        
        self.online_processor = OnlineASRProcessor(self.asr)
        self.is_processing = False
        
        # Threading
        self.audio_thread = None
        self.processing_thread = None
        
    def clear_screen(self):
        """Clear the terminal screen"""
        os.system('clear' if os.name == 'posix' else 'cls')
        
    def list_audio_devices(self):
        """List available audio input devices"""
        print("Available audio input devices:")
        for i in range(self.audio.get_device_count()):
            info = self.audio.get_device_info_by_index(i)
            if info['maxInputChannels'] > 0:
                print(f"  {i}: {info['name']} (channels: {info['maxInputChannels']})")
        
    def calculate_volume(self, audio_data):
        """Calculate volume level from audio data"""
        if len(audio_data) == 0:
            return 0.0
        # Calculate RMS (Root Mean Square) for volume
        rms = np.sqrt(np.mean(audio_data**2))
        # Convert to a more readable scale (0-100)
        volume = min(100, rms * 1000)
        return volume
        
    def get_volume_bar(self, volume, width=20):
        """Create a visual volume bar"""
        filled = int((volume / 100.0) * width)
        bar = '█' * filled + '░' * (width - filled)
        return f"[{bar}] {volume:5.1f}%"
        
    def audio_callback(self, in_data, frame_count, time_info, status):
        """Callback for audio stream"""
        if self.recording:
            # Convert bytes to numpy array
            audio_data = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
            
            # Calculate volume
            self.current_volume = self.calculate_volume(audio_data)
            self.volume_history.append(self.current_volume)
            if len(self.volume_history) > 10:  # Keep last 10 samples
                self.volume_history.pop(0)
            
            self.audio_queue.put(audio_data)
        return (None, pyaudio.paContinue)
        
    def start_recording(self, device_index=None):
        """Start recording from microphone"""
        try:
            self.stream = self.audio.open(
                format=self.format,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=self.chunk_size,
                stream_callback=self.audio_callback
            )
            
            self.recording = True
            self.is_processing = True
            self.stream.start_stream()
            
            print(f"✅ Recording started from microphone (device: {device_index if device_index else 'default'})")
            print(f"   Sample rate: {self.sample_rate}Hz, Chunk size: {self.chunk_size}")
            
            return True
            
        except Exception as e:
            print(f"❌ Error starting recording: {e}")
            return False
            
    def stop_recording(self):
        """Stop recording"""
        self.recording = False
        self.is_processing = False
        
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            
        print("\n🛑 Recording stopped")
        
    def process_audio_stream(self):
        """Process audio chunks as they arrive"""
        self.clear_screen()
        print("🎙️  Live Microphone Transcription")
        print("=" * 60)
        print("🎤 Listening... (Press Ctrl+C to stop)")
        print()
        
        current_text = ""
        last_display_time = time.time()
        
        try:
            while self.is_processing:
                try:
                    # Get audio chunk with timeout
                    audio_chunk = self.audio_queue.get(timeout=0.1)
                    
                    # Process with whisper-streaming
                    self.online_processor.insert_audio_chunk(audio_chunk)
                    result = self.online_processor.process_iter()
                    
                    # Update display every 100ms to avoid flickering
                    current_time = time.time()
                    if current_time - last_display_time > 0.1:
                        self.update_display(result, current_text)
                        current_text = result if result else current_text
                        last_display_time = current_time
                        
                except queue.Empty:
                    # Still update display to show volume indicator
                    current_time = time.time()
                    if current_time - last_display_time > 0.1:
                        self.update_display(current_text, current_text)
                        last_display_time = current_time
                    continue
                except KeyboardInterrupt:
                    break
                    
        except KeyboardInterrupt:
            pass
            
        # Get final result
        try:
            final_result = self.online_processor.finish()
            if final_result:
                print(f"\n\n📝 Final result: {final_result}")
            else:
                print(f"\n\n📝 Final result: {current_text}")
        except:
            print(f"\n\n📝 Final result: {current_text}")
            
        return current_text
        
    def update_display(self, new_text, current_text):
        """Update the display with volume and transcription"""
        # Move cursor to top
        print("\033[H", end="")
        
        print("🎙️  Live Microphone Transcription")
        print("=" * 60)
        print("🎤 Listening... (Press Ctrl+C to stop)")
        print()
        
        # Volume indicator
        volume_bar = self.get_volume_bar(self.current_volume)
        avg_volume = np.mean(self.volume_history) if self.volume_history else 0
        print(f"🔊 Volume: {volume_bar}")
        print(f"📊 Avg:    {self.get_volume_bar(avg_volume)}")
        print()
        
        # Audio status
        if self.current_volume > 1.0:
            status = "🟢 DETECTING AUDIO"
        elif self.current_volume > 0.1:
            status = "🟡 Low audio"
        else:
            status = "🔴 No audio detected"
        print(f"Status: {status}")
        print()
        
        # Transcription
        print("📝 Transcription:")
        print("-" * 40)
        if new_text and new_text != current_text:
            print(f"{new_text}")
        elif current_text:
            print(f"{current_text}")
        else:
            print("(waiting for speech...)")
        print()
        
        # Instructions
        print("💡 Tips:")
        print("  - Speak clearly into your microphone")
        print("  - Check volume indicator above")
        print("  - If no audio: try different --mic-device")
        
        # Clear rest of screen
        print("\033[J", end="", flush=True)
        
    def start_live_transcription(self, device_index=None):
        """Start live transcription from microphone"""
        
        # List available devices
        self.list_audio_devices()
        print()
        
        # Start recording
        if not self.start_recording(device_index):
            return
            
        try:
            # Process audio stream
            self.process_audio_stream()
        finally:
            self.stop_recording()
            self.cleanup()
            
    def cleanup(self):
        """Clean up resources"""
        if self.audio:
            self.audio.terminate()

def main():
    """Main function with command line options"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Live Microphone Transcription with Whisper Streaming")
    parser.add_argument("--language", default="en", help="Source language (default: en)")
    parser.add_argument("--model", default="small", help="Whisper model size (default: small)")
    parser.add_argument("--device", default="cpu", help="Processing device (cpu/cuda, default: cpu)")
    parser.add_argument("--mic-device", type=int, help="Microphone device index (use --list-devices to see options)")
    parser.add_argument("--list-devices", action="store_true", help="List available audio devices and exit")
    
    args = parser.parse_args()
    
    # Create transcriber
    transcriber = LiveMicrophoneTranscriber(
        language=args.language,
        model=args.model,
        device=args.device
    )
    
    # List devices and exit if requested
    if args.list_devices:
        transcriber.list_audio_devices()
        transcriber.cleanup()
        return
    
    print("🎙️  Live Microphone Transcription")
    print("=" * 50)
    print(f"Language: {args.language}")
    print(f"Model: {args.model}")
    print(f"Device: {args.device}")
    print(f"Microphone: {args.mic_device if args.mic_device else 'default'}")
    print()
    print("Press Ctrl+C to stop transcription")
    print()
    
    try:
        # Start live transcription
        transcriber.start_live_transcription(args.mic_device)
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
    finally:
        transcriber.cleanup()

if __name__ == "__main__":
    main() 