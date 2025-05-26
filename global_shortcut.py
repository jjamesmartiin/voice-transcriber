#!/usr/bin/env python3
"""
Global shortcut daemon for voice transcriber on GNOME/Wayland
Uses keyboard monitoring to record while Control key is held
"""

import os
import sys
import subprocess
import threading
import time
import signal
from pynput import keyboard
from pynput.keyboard import Key, Listener

# Import our transcription functionality
from t2 import record_and_transcribe, preload_model, get_model, DEVICE, record_audio_stream, process_audio_stream, stop_recording

class GlobalShortcutDaemon:
    def __init__(self):
        self.ctrl_pressed = False
        self.recording = False
        self.record_thread = None
        self.process_thread = None
        
        # Preload model in background
        print("Loading transcription model...")
        self.preload_thread = preload_model(device=DEVICE)
        
    def on_key_press(self, key):
        """Handle key press events"""
        try:
            if key == Key.ctrl_l or key == Key.ctrl_r:
                if not self.ctrl_pressed and not self.recording:
                    self.ctrl_pressed = True
                    self.start_recording()
        except AttributeError:
            # Special keys (like ctrl) might not have char
            pass
    
    def on_key_release(self, key):
        """Handle key release events"""
        try:
            if key == Key.ctrl_l or key == Key.ctrl_r:
                if self.ctrl_pressed and self.recording:
                    self.ctrl_pressed = False
                    self.stop_recording()
            elif key == Key.esc:
                # Exit on Escape
                print("\nExiting daemon...")
                return False
        except AttributeError:
            pass
    
    def start_recording(self):
        """Start recording audio"""
        if self.recording:
            return
            
        print("Control pressed - Starting recording...")
        self.show_notification("Voice Transcriber", "Recording... Release Control to stop")
        
        # Wait for model to load if still loading
        if hasattr(self, 'preload_thread') and self.preload_thread.is_alive():
            print("Waiting for model to load...")
            self.preload_thread.join()
        
        self.recording = True
        stop_recording.clear()
        
        # Start recording in background thread
        self.record_thread = threading.Thread(target=self.record_audio)
        self.record_thread.daemon = True
        self.record_thread.start()
    
    def stop_recording(self):
        """Stop recording and process audio"""
        if not self.recording:
            return
            
        print("Control released - Stopping recording...")
        self.recording = False
        stop_recording.set()
        
        # Wait for recording to finish
        if self.record_thread:
            self.record_thread.join()
        
        # Process the recorded audio
        self.process_thread = threading.Thread(target=self.process_and_transcribe)
        self.process_thread.daemon = True
        self.process_thread.start()
    
    def record_audio(self):
        """Record audio using the existing function"""
        try:
            from t2 import record_audio_stream
            record_audio_stream()
        except Exception as e:
            print(f"Recording error: {e}")
    
    def process_and_transcribe(self):
        """Process recorded audio and transcribe"""
        try:
            self.show_notification("Voice Transcriber", "Processing...")
            
            # Process the audio
            from t2 import process_audio_stream
            result, transcribe_time = process_audio_stream()
            
            # Clean up result
            transcription = result.strip()
            
            if transcription:
                # Copy to clipboard
                import pyperclip
                pyperclip.copy(transcription)
                
                # Play sound
                try:
                    subprocess.Popen(['mpg123', '-q', 'sounds/pop.mp3'], 
                                   stderr=subprocess.DEVNULL)
                except:
                    pass
                
                # Show completion notification
                self.show_notification("Voice Transcriber", 
                                     f"Transcribed: {transcription[:50]}...")
                print(f"Transcribed: {transcription}")
            else:
                self.show_notification("Voice Transcriber", "No speech detected")
                print("No speech detected")
                
        except Exception as e:
            print(f"Transcription error: {e}")
            self.show_notification("Voice Transcriber", f"Error: {e}")
    
    def show_notification(self, title, message):
        """Show desktop notification"""
        try:
            subprocess.run([
                'notify-send', 
                '--app-name=Voice Transcriber',
                '--icon=audio-input-microphone',
                title, message
            ], check=False)
        except:
            print(f"{title}: {message}")
    
    def run_daemon(self):
        """Run the daemon"""
        print("Starting Voice Transcriber global shortcut daemon...")
        print(f"Using device: {DEVICE}")
        print("Hold Control key to record, release to transcribe")
        print("Press Escape to exit")
        
        # Set up signal handler for clean exit
        def signal_handler(sig, frame):
            print("\nShutting down daemon...")
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        try:
            # Start keyboard listener
            with Listener(on_press=self.on_key_press, 
                         on_release=self.on_key_release) as listener:
                print("Daemon running. Hold Control to record.")
                listener.join()
        except Exception as e:
            print(f"Error starting keyboard listener: {e}")
            print("Make sure you have the necessary permissions to monitor keyboard events.")
            return False

def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--transcribe':
        # Direct transcription call (for compatibility)
        print("Direct transcription triggered...")
        try:
            # Quick model load and transcribe
            preload_thread = preload_model(device=get_device())
            if preload_thread.is_alive():
                preload_thread.join()
            
            result = record_and_transcribe()
            if result:
                subprocess.run([
                    'notify-send', 
                    '--app-name=Voice Transcriber',
                    '--icon=audio-input-microphone',
                    'Voice Transcriber', f'Transcribed: {result[:50]}...'
                ], check=False)
        except Exception as e:
            subprocess.run([
                'notify-send', 
                '--app-name=Voice Transcriber',
                '--icon=dialog-error',
                'Voice Transcriber', f'Error: {e}'
            ], check=False)
    else:
        # Run as daemon
        daemon = GlobalShortcutDaemon()
        daemon.run_daemon()

if __name__ == "__main__":
    main() 