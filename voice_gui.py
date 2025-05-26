#!/usr/bin/env python3
"""
GUI Voice Transcriber with keyboard shortcuts
Simple tkinter app with reliable F12 key binding
"""

import tkinter as tk
from tkinter import ttk
import threading
import time
import subprocess
import os
import sys
import queue

# Import our transcription functionality
from t2 import preload_model, get_model, DEVICE

class VoiceTranscriberGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Voice Transcriber")
        self.root.geometry("450x350")
        
        # State variables
        self.recording = False
        self.model_loaded = False
        self.audio_queue = queue.Queue()
        self.stop_recording_flag = threading.Event()
        
        # Setup GUI
        self.setup_gui()
        
        # Setup key bindings
        self.setup_key_bindings()
        
        # Start model loading
        self.status_label.config(text="Loading model...")
        self.load_model_thread = threading.Thread(target=self.load_model)
        self.load_model_thread.daemon = True
        self.load_model_thread.start()
        
        # Keep window on top and minimize to tray behavior
        self.root.attributes('-topmost', True)
        self.root.protocol("WM_DELETE_WINDOW", self.minimize_to_tray)
        
        # Make sure window can receive focus
        self.root.focus_set()
        
    def setup_gui(self):
        """Setup the GUI elements"""
        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Title
        title_label = ttk.Label(main_frame, text="Voice Transcriber", 
                               font=("Arial", 16, "bold"))
        title_label.grid(row=0, column=0, columnspan=2, pady=(0, 20))
        
        # Status
        self.status_label = ttk.Label(main_frame, text="Initializing...")
        self.status_label.grid(row=1, column=0, columnspan=2, pady=(0, 10))
        
        # Record button
        self.record_button = ttk.Button(main_frame, text="Click to Record", 
                                       command=self.toggle_recording, state="disabled")
        self.record_button.grid(row=2, column=0, columnspan=2, pady=10, sticky=(tk.W, tk.E))
        
        # Hotkey info
        hotkey_frame = ttk.LabelFrame(main_frame, text="Keyboard Shortcuts", padding="5")
        hotkey_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(10, 0))
        
        hotkey_info = ttk.Label(hotkey_frame, 
                               text="F12 - Toggle recording\nSpace - Toggle recording\nCtrl+R - Toggle recording\nEsc - Minimize window",
                               font=("Arial", 9))
        hotkey_info.grid(row=0, column=0, sticky=(tk.W))
        
        # Instructions
        instructions = ttk.Label(main_frame, 
                                text="Keep this window focused to use keyboard shortcuts\nOr click the button above",
                                font=("Arial", 9), foreground="gray")
        instructions.grid(row=4, column=0, columnspan=2, pady=(5, 10))
        
        # Progress bar
        self.progress = ttk.Progressbar(main_frame, mode='indeterminate')
        self.progress.grid(row=5, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(10, 0))
        
        # Result text area
        result_frame = ttk.LabelFrame(main_frame, text="Last Transcription", padding="5")
        result_frame.grid(row=6, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(10, 0))
        
        self.result_text = tk.Text(result_frame, height=6, wrap=tk.WORD)
        scrollbar = ttk.Scrollbar(result_frame, orient="vertical", command=self.result_text.yview)
        self.result_text.configure(yscrollcommand=scrollbar.set)
        
        self.result_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        
        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(6, weight=1)
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(0, weight=1)
        
        # Minimize/Close buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=7, column=0, columnspan=2, pady=(10, 0))
        
        minimize_btn = ttk.Button(button_frame, text="Minimize to Background", 
                                 command=self.minimize_to_tray)
        minimize_btn.grid(row=0, column=0, padx=(0, 5))
        
        focus_btn = ttk.Button(button_frame, text="Keep on Top", 
                              command=self.toggle_always_on_top)
        focus_btn.grid(row=0, column=1, padx=(0, 5))
        
        quit_btn = ttk.Button(button_frame, text="Quit", command=self.quit_app)
        quit_btn.grid(row=0, column=2)
        
    def setup_key_bindings(self):
        """Setup keyboard shortcuts"""
        # Bind multiple keys for recording
        self.root.bind('<F12>', lambda e: self.toggle_recording())
        self.root.bind('<space>', lambda e: self.toggle_recording())
        self.root.bind('<Control-r>', lambda e: self.toggle_recording())
        self.root.bind('<Control-R>', lambda e: self.toggle_recording())
        
        # Bind escape to minimize
        self.root.bind('<Escape>', lambda e: self.minimize_to_tray())
        
        # Make sure the window can receive key events
        self.root.focus_set()
        
        # Bind focus events to ensure we can receive keys
        self.root.bind('<FocusIn>', self.on_focus_in)
        self.root.bind('<Button-1>', lambda e: self.root.focus_set())
        
    def on_focus_in(self, event):
        """Handle window focus"""
        self.root.focus_set()
        
    def toggle_always_on_top(self):
        """Toggle always on top"""
        current = self.root.attributes('-topmost')
        self.root.attributes('-topmost', not current)
        
    def load_model(self):
        """Load the transcription model in background"""
        try:
            self.preload_thread = preload_model(device=DEVICE)
            if self.preload_thread.is_alive():
                self.preload_thread.join()
            
            self.model = get_model(device=DEVICE)
            self.model_loaded = True
            
            # Update GUI on main thread
            self.root.after(0, self.on_model_loaded)
        except Exception as e:
            self.root.after(0, lambda: self.update_status(f"Error loading model: {e}"))
    
    def on_model_loaded(self):
        """Called when model is loaded"""
        self.update_status("Ready! Use F12, Space, or Ctrl+R to record")
        self.record_button.config(state="normal")
    
    def toggle_recording(self):
        """Toggle recording state"""
        if not self.model_loaded:
            self.show_notification("Voice Transcriber", "Model still loading, please wait...")
            return
            
        if self.recording:
            self.stop_recording()
        else:
            self.start_recording()
    
    def start_recording(self):
        """Start recording audio"""
        if self.recording:
            return
            
        self.recording = True
        self.stop_recording_flag.clear()
        
        self.update_status("Recording... Press F12/Space/Ctrl+R or button to stop")
        self.record_button.config(text="Stop Recording")
        self.progress.start()
        
        self.show_notification("Voice Transcriber", "Recording started...")
        
        # Start recording in background
        record_thread = threading.Thread(target=self.record_audio)
        record_thread.daemon = True
        record_thread.start()
    
    def stop_recording(self):
        """Stop recording and process"""
        if not self.recording:
            return
            
        self.recording = False
        self.stop_recording_flag.set()
        
        self.update_status("Processing...")
        self.record_button.config(text="Processing...", state="disabled")
        
        # Process in background
        process_thread = threading.Thread(target=self.process_audio)
        process_thread.daemon = True
        process_thread.start()
    
    def record_audio(self):
        """Record audio using pyaudio"""
        try:
            import pyaudio
            import wave
            
            # Audio settings
            CHUNK = 1024
            FORMAT = pyaudio.paInt16
            CHANNELS = 1
            RATE = 16000
            
            # Setup audio
            p = pyaudio.PyAudio()
            stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, 
                           input=True, frames_per_buffer=CHUNK)
            
            frames = []
            
            # Record until stopped
            while not self.stop_recording_flag.is_set():
                data = stream.read(CHUNK, exception_on_overflow=False)
                frames.append(data)
            
            # Cleanup
            stream.stop_stream()
            stream.close()
            p.terminate()
            
            # Save audio file
            temp_file = "temp_recording.wav"
            with wave.open(temp_file, 'wb') as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(p.get_sample_size(FORMAT))
                wf.setframerate(RATE)
                wf.writeframes(b''.join(frames))
            
            # Put filename in queue for processing
            self.audio_queue.put(temp_file)
            
        except Exception as e:
            self.root.after(0, lambda: self.update_status(f"Recording error: {e}"))
    
    def process_audio(self):
        """Process recorded audio"""
        try:
            # Get audio file
            audio_file = self.audio_queue.get(timeout=5)
            
            # Transcribe
            from transcribe2 import transcribe_audio
            result = transcribe_audio(audio_path=audio_file, device=DEVICE)
            transcription = result.strip()
            
            # Clean up temp file
            try:
                os.remove(audio_file)
            except:
                pass
            
            # Update GUI
            self.root.after(0, lambda: self.on_transcription_complete(transcription))
            
        except Exception as e:
            self.root.after(0, lambda: self.update_status(f"Transcription error: {e}"))
    
    def on_transcription_complete(self, transcription):
        """Handle completed transcription"""
        if transcription:
            # Copy to clipboard
            try:
                import pyperclip
                pyperclip.copy(transcription)
                
                # Play sound
                try:
                    subprocess.Popen(['mpg123', '-q', 'sounds/pop.mp3'], 
                                   stderr=subprocess.DEVNULL)
                except:
                    pass
                
                # Update GUI
                self.result_text.delete(1.0, tk.END)
                self.result_text.insert(1.0, transcription)
                
                self.update_status("Transcription complete! Copied to clipboard")
                self.show_notification("Voice Transcriber", f"Transcribed: {transcription[:50]}...")
                
            except Exception as e:
                self.update_status(f"Error copying to clipboard: {e}")
        else:
            self.update_status("No speech detected")
            self.show_notification("Voice Transcriber", "No speech detected")
        
        # Reset button
        self.record_button.config(text="Click to Record", state="normal")
        self.progress.stop()
    
    def update_status(self, message):
        """Update status label"""
        self.status_label.config(text=message)
    
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
    
    def minimize_to_tray(self):
        """Minimize window (hide it)"""
        self.root.withdraw()
        self.show_notification("Voice Transcriber", "Minimized to background. Click taskbar icon to restore.")
        
        # Create a simple way to restore the window
        self.create_restore_mechanism()
    
    def create_restore_mechanism(self):
        """Create a way to restore the window when minimized"""
        # Create a small restore window
        restore_window = tk.Toplevel()
        restore_window.title("Voice Transcriber - Minimized")
        restore_window.geometry("250x100")
        restore_window.attributes('-topmost', True)
        
        frame = ttk.Frame(restore_window, padding="10")
        frame.pack(fill=tk.BOTH, expand=True)
        
        label = ttk.Label(frame, text="Voice Transcriber is running\nin the background")
        label.pack(pady=(0, 10))
        
        restore_btn = ttk.Button(frame, text="Restore Window", 
                                command=lambda: self.restore_window(restore_window))
        restore_btn.pack()
        
        # Auto-close this window after 5 seconds
        restore_window.after(5000, restore_window.destroy)
    
    def restore_window(self, restore_window=None):
        """Restore the main window"""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_set()
        if restore_window:
            restore_window.destroy()
    
    def quit_app(self):
        """Quit the application"""
        self.root.quit()
        self.root.destroy()
        sys.exit(0)
    
    def run(self):
        """Run the GUI application"""
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self.quit_app()

def main():
    app = VoiceTranscriberGUI()
    app.run()

if __name__ == "__main__":
    main() 