#!/usr/bin/env python3
"""
Simple Voice Transcriber with Alt+Shift+K shortcut
"""
import logging
import threading
import subprocess
import time
from pynput import keyboard
from pynput.keyboard import Key, Listener

# Import transcription functionality
from t2 import record_and_transcribe, preload_model, get_model, DEVICE, record_audio_stream, process_audio_stream, stop_recording

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SimpleVoiceTranscriber:
    def __init__(self):
        self.alt_pressed = False
        self.shift_pressed = False
        self.recording = False
        self.record_thread = None
        self.process_thread = None
        
        # Preload model in background
        logger.info("Loading transcription model...")
        self.preload_thread = preload_model(device=DEVICE)
        
    def on_press(self, key):
        try:
            if key == Key.alt_l or key == Key.alt_r:
                self.alt_pressed = True
                logger.debug("Alt pressed")
            elif key == Key.shift_l or key == Key.shift_r:
                self.shift_pressed = True
                logger.debug("Shift pressed")
            elif hasattr(key, 'char') and key.char and key.char.lower() == 'k':
                logger.debug("K pressed")
                self.check_start_recording()
            
        except AttributeError:
            pass

    def on_release(self, key):
        try:
            if key == Key.alt_l or key == Key.alt_r:
                self.alt_pressed = False
                logger.debug("Alt released")
                self.check_stop_recording()
            elif key == Key.shift_l or key == Key.shift_r:
                self.shift_pressed = False
                logger.debug("Shift released")
                self.check_stop_recording()
            elif hasattr(key, 'char') and key.char and key.char.lower() == 'k':
                logger.debug("K released")
                self.check_stop_recording()
            elif key == Key.esc:
                logger.info("Escape pressed - exiting...")
                return False
                
        except AttributeError:
            pass

    def check_start_recording(self):
        """Check if Alt+Shift+K combination is pressed to start recording"""
        if self.alt_pressed and self.shift_pressed and not self.recording:
            logger.info("🎙️ Alt+Shift+K combination detected - starting recording!")
            self.start_recording()

    def check_stop_recording(self):
        """Check if any key in the combination is released to stop recording"""
        if self.recording and (not self.alt_pressed or not self.shift_pressed):
            logger.info("⏹️ Key combination released - stopping recording...")
            self.stop_recording()

    def start_recording(self):
        """Start recording audio"""
        if self.recording:
            return
            
        self.show_notification("Voice Transcriber", "Recording... Release any key to stop")
        
        # Wait for model to load if still loading
        if hasattr(self, 'preload_thread') and self.preload_thread.is_alive():
            logger.info("Waiting for model to load...")
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
            record_audio_stream()
        except Exception as e:
            logger.error(f"Recording error: {e}")

    def process_and_transcribe(self):
        """Process recorded audio and transcribe"""
        try:
            self.show_notification("Voice Transcriber", "Processing...")
            
            # Process the audio
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
                self.show_notification("Voice Transcriber", "Transcription complete - copied to clipboard")
                logger.info(f"✅ Transcribed: {transcription}")
            else:
                self.show_notification("Voice Transcriber", "No speech detected")
                logger.info("❌ No speech detected")
                
        except Exception as e:
            logger.error(f"Transcription error: {e}")
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
            logger.info(f"{title}: {message}")

    def run(self):
        """Run the voice transcriber"""
        logger.info("🎤 Simple Voice Transcriber started!")
        logger.info(f"📱 Using device: {DEVICE}")
        logger.info("🔥 Hold Alt+Shift+K to record, release any key to transcribe")
        logger.info("🚪 Press Escape to exit")
        
        with Listener(on_press=self.on_press, on_release=self.on_release) as listener:
            listener.join()

if __name__ == "__main__":
    transcriber = SimpleVoiceTranscriber()
    transcriber.run() 