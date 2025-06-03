#!/usr/bin/env python3
"""
Simple Voice Transcriber with Alt+Shift+K shortcut
Enhanced with multiple keyboard backends for better terminal compatibility
"""
import logging
import threading
import subprocess
import time
import os
import sys
import select

# Import transcription functionality
from t2 import record_and_transcribe, preload_model, get_model, DEVICE, record_audio_stream, process_audio_stream, stop_recording

# Try to import tkinter for visual notifications
try:
    import tkinter as tk
    VISUAL_NOTIFICATIONS_AVAILABLE = True
except ImportError:
    VISUAL_NOTIFICATIONS_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class VisualNotification:
    """Wayland/X11 compatible visual notification system"""
    def __init__(self):
        self.active = False
        self.notification_process = None
        self.overlay_processes = []
        self.display_env = self._detect_display_environment()
        self.available_tools = self._detect_available_tools()
        logger.info(f"🖥️ Display environment: {self.display_env}")
        logger.info(f"🔧 Available tools: {', '.join(self.available_tools)}")
        
    def _detect_display_environment(self):
        """Detect if we're running on Wayland or X11"""
        if os.environ.get('WAYLAND_DISPLAY'):
            return 'wayland'
        elif os.environ.get('DISPLAY'):
            return 'x11'
        else:
            return 'terminal'
    
    def _detect_available_tools(self):
        """Detect available system tools for creating overlays"""
        tools = []
        
        # Check for various GUI tools (excluding notify-send)
        for tool in ['zenity', 'yad', 'kdialog', 'xmessage', 'gxmessage']:
            try:
                subprocess.run(['which', tool], capture_output=True, check=True)
                tools.append(tool)
            except:
                pass
        
        # Check for Wayland-specific tools
        if self.display_env == 'wayland':
            for tool in ['wlr-randr', 'swaymsg', 'hyprctl']:
                try:
                    subprocess.run(['which', tool], capture_output=True, check=True)
                    tools.append(tool)
                except:
                    pass
        
        # Check for X11-specific tools
        if self.display_env == 'x11':
            for tool in ['xwininfo', 'xdotool', 'xprop']:
                try:
                    subprocess.run(['which', tool], capture_output=True, check=True)
                    tools.append(tool)
                except:
                    pass
        
        return tools
    
    def show_recording_border(self):
        """Show recording indicator"""
        if self.active:
            return
            
        self.active = True
        
        # Try multiple approaches for maximum compatibility
        self._create_system_overlay("🔴 RECORDING", "#00ff41", persistent=True)
        self._show_terminal_notification("🔴 RECORDING IN PROGRESS")
    
    def show_processing_border(self):
        """Show processing/transcribing indicator"""
        self._cleanup_overlays()
        self._create_system_overlay("⚡ TRANSCRIBING", "#ffaa00", persistent=True)
        self._show_terminal_notification("⚡ TRANSCRIBING AUDIO")
    
    def show_completed_border(self):
        """Show completion indicator briefly"""
        self._cleanup_overlays()
        self._create_system_overlay("✅ COPIED", "#00aaff", persistent=False)
        self._show_terminal_notification("✅ TRANSCRIPTION COPIED TO CLIPBOARD")
        
        # Auto-hide after 2 seconds
        threading.Timer(2.0, self.hide_notification).start()
    
    def _create_system_overlay(self, text, color, persistent=False):
        """Create system-level overlay using available tools"""
        
        # Method 1: Try creating a custom tkinter overlay first (most reliable)
        if VISUAL_NOTIFICATIONS_AVAILABLE:
            try:
                self._create_tkinter_overlay(text, color, persistent)
                logger.debug(f"Created tkinter overlay: {text}")
                return  # If successful, don't try other methods
            except Exception as e:
                logger.debug(f"Tkinter overlay failed: {e}")
        
        # Method 2: Try zenity with better formatting
        if 'zenity' in self.available_tools:
            try:
                # Make the text more visible with formatting
                formatted_text = f"<span font='20' weight='bold'>{text}</span>"
                cmd = [
                    'zenity', '--info',
                    '--title=Voice Transcriber',
                    f'--text={formatted_text}',
                    '--width=400',
                    '--height=150',
                    '--no-wrap'
                ]
                if not persistent:
                    cmd.append('--timeout=3')
                
                process = subprocess.Popen(cmd, stderr=subprocess.DEVNULL)
                self.overlay_processes.append(process)
                logger.debug(f"Created zenity overlay: {text}")
                return
            except Exception as e:
                logger.debug(f"Zenity overlay failed: {e}")
        
        # Method 3: Try yad with better styling
        elif 'yad' in self.available_tools:
            try:
                cmd = [
                    'yad', '--info',
                    '--title=Voice Transcriber',
                    f'--text=<span font="20" weight="bold">{text}</span>',
                    '--width=400',
                    '--height=150',
                    '--center',
                    '--on-top',
                    '--skip-taskbar',
                    '--borders=20',
                    '--button=gtk-ok:0' if not persistent else '--no-buttons'
                ]
                if not persistent:
                    cmd.extend(['--timeout=3', '--timeout-indicator=bottom'])
                
                process = subprocess.Popen(cmd, stderr=subprocess.DEVNULL)
                self.overlay_processes.append(process)
                logger.debug(f"Created yad overlay: {text}")
                return
            except Exception as e:
                logger.debug(f"Yad overlay failed: {e}")
        
        # Method 4: Try xmessage with better formatting
        elif 'xmessage' in self.available_tools and self.display_env == 'x11':
            try:
                # Create a more visible message
                message = f"\n\n    {text}    \n\n"
                cmd = ['xmessage', '-center', '-buttons', 'OK:0', message]
                if not persistent:
                    cmd = ['timeout', '3'] + cmd
                
                process = subprocess.Popen(cmd, stderr=subprocess.DEVNULL)
                self.overlay_processes.append(process)
                logger.debug(f"Created xmessage overlay: {text}")
                return
            except Exception as e:
                logger.debug(f"Xmessage overlay failed: {e}")
        
        # Fallback: Enhanced terminal notification
        logger.debug(f"Using terminal fallback for: {text}")
    
    def _create_tkinter_overlay(self, text, color, persistent):
        """Create a tkinter-based overlay window"""
        overlay_script = f'''
import tkinter as tk
import sys
import time

def create_overlay():
    try:
        root = tk.Tk()
        root.title("Voice Transcriber")
        root.overrideredirect(True)
        root.attributes('-topmost', True)
        root.attributes('-alpha', 0.95)
        root.configure(bg='{color}')
        
        # Position at top center of screen
        root.update_idletasks()
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        
        window_width = 500
        window_height = 120
        x = (screen_width - window_width) // 2
        y = 100  # Top of screen with margin
        
        root.geometry(f"{{window_width}}x{{window_height}}+{{x}}+{{y}}")
        
        # Add a border frame
        border_frame = tk.Frame(root, bg='black', bd=3)
        border_frame.pack(fill='both', expand=True, padx=3, pady=3)
        
        inner_frame = tk.Frame(border_frame, bg='{color}')
        inner_frame.pack(fill='both', expand=True, padx=2, pady=2)
        
        # Create label with large, bold text
        label = tk.Label(
            inner_frame,
            text="{text}",
            bg='{color}',
            fg='black',
            font=('Arial', 24, 'bold'),
            pady=20
        )
        label.pack(expand=True)
        
        # Add a subtle animation effect
        def pulse():
            try:
                current_alpha = root.attributes('-alpha')
                new_alpha = 0.85 if current_alpha > 0.9 else 0.95
                root.attributes('-alpha', new_alpha)
                if {str(persistent).lower()}:
                    root.after(1000, pulse)
            except:
                pass
        
        # Start pulse animation for recording
        if "{text}".startswith('🔴'):
            pulse()
        
        # Handle persistence
        if {str(persistent).lower()}:
            # Keep open until manually closed
            root.mainloop()
        else:
            # Auto-close after 3 seconds
            root.after(3000, root.quit)
            root.mainloop()
            
    except Exception as e:
        print(f"Overlay error: {{e}}", file=sys.stderr)

if __name__ == "__main__":
    create_overlay()
'''
        
        # Write and execute the overlay script
        overlay_file = f'/tmp/voice_overlay_{int(time.time())}.py'
        with open(overlay_file, 'w') as f:
            f.write(overlay_script)
        
        process = subprocess.Popen([
            'python3', overlay_file
        ], stderr=subprocess.DEVNULL)
        self.overlay_processes.append(process)
        
        # Clean up the temp file after a delay
        threading.Timer(10.0, lambda: self._cleanup_temp_file(overlay_file)).start()
    
    def _cleanup_temp_file(self, filepath):
        """Clean up temporary overlay script file"""
        try:
            os.remove(filepath)
        except:
            pass
    
    def _cleanup_overlays(self):
        """Clean up any existing overlay processes"""
        for process in self.overlay_processes:
            try:
                process.terminate()
            except:
                pass
        self.overlay_processes = []
    
    def _show_terminal_notification(self, text):
        """Show terminal-based notification"""
        try:
            # Clear screen and show notification
            print(f"\033[2J\033[H", end='')  # Clear screen, move cursor to top
            
            if "RECORDING" in text:
                print("\033[92m" + "█" * 70)  # Green blocks
                print("█" + " " * 68 + "█")
                print("█" + f"🔴 RECORDING IN PROGRESS".center(68) + "█")
                print("█" + f"Hold Alt+Shift+K, release to stop".center(68) + "█")
                print("█" + " " * 68 + "█")
                print("█" * 70 + "\033[0m")
            elif "TRANSCRIBING" in text:
                print("\033[93m" + "█" * 70)  # Yellow blocks
                print("█" + " " * 68 + "█")
                print("█" + f"⚡ TRANSCRIBING AUDIO".center(68) + "█")
                print("█" + f"Processing speech to text...".center(68) + "█")
                print("█" + " " * 68 + "█")
                print("█" * 70 + "\033[0m")
            elif "COPIED" in text:
                print("\033[94m" + "█" * 70)  # Blue blocks
                print("█" + " " * 68 + "█")
                print("█" + f"✅ TEXT COPIED TO CLIPBOARD!".center(68) + "█")
                print("█" + f"Transcription complete and ready to paste".center(68) + "█")
                print("█" + " " * 68 + "█")
                print("█" * 70 + "\033[0m")
                
        except Exception as e:
            logger.debug(f"Terminal notification failed: {e}")
    
    def hide_notification(self):
        """Hide the notification"""
        if not self.active:
            return
            
        self.active = False
        
        # Clean up all overlay processes
        self._cleanup_overlays()
        
        # Clear terminal
        try:
            print(f"\033[2J\033[H", end='')  # Clear screen
            print("🎤 Voice Transcriber Ready")
            print("Hold Alt+Shift+K to record")
        except:
            pass

class SimpleVoiceTranscriber:
    def __init__(self):
        self.alt_pressed = False
        self.shift_pressed = False
        self.recording = False
        self.record_thread = None
        self.process_thread = None
        self.backend = None
        self.running = False
        
        # Initialize visual notification
        self.visual_notification = VisualNotification() if VISUAL_NOTIFICATIONS_AVAILABLE else None
        
        # Preload model in background
        logger.info("Loading transcription model...")
        self.preload_thread = preload_model(device=DEVICE)
        
        # Try to initialize the best available backend
        self.init_backend()
        
    def init_backend(self):
        """Initialize the best available keyboard monitoring backend"""
        # Try backends in order of preference
        backends = [
            ('evdev', self.init_evdev_backend),
            ('pynput', self.init_pynput_backend)
        ]
        
        for backend_name, init_func in backends:
            try:
                if init_func():
                    self.backend = backend_name
                    logger.info(f"Using {backend_name} backend for keyboard monitoring")
                    return True
            except Exception as e:
                logger.warning(f"Failed to initialize {backend_name} backend: {e}")
                continue
        
        logger.error("No keyboard monitoring backend available")
        return False
    
    def init_evdev_backend(self):
        """Initialize evdev backend (best for terminals and Wayland)"""
        try:
            import evdev
            from evdev import InputDevice, categorize, ecodes
            
            # Find keyboard devices
            devices = [InputDevice(path) for path in evdev.list_devices()]
            keyboards = []
            
            for device in devices:
                caps = device.capabilities()
                # Check if device has keyboard capabilities (look for common keys)
                if ecodes.EV_KEY in caps:
                    key_caps = caps[ecodes.EV_KEY]
                    # Check for essential keyboard keys
                    if (ecodes.KEY_A in key_caps and ecodes.KEY_SPACE in key_caps and 
                        (ecodes.KEY_LEFTALT in key_caps or ecodes.KEY_RIGHTALT in key_caps)):
                        keyboards.append(device)
                        logger.info(f"Found keyboard device: {device.name} at {device.path}")
            
            if not keyboards:
                raise Exception("No keyboard devices found")
            
            self.keyboards = keyboards
            self.evdev = evdev
            self.ecodes = ecodes
            logger.info(f"Found {len(keyboards)} keyboard device(s)")
            return True
            
        except ImportError:
            raise Exception("evdev not available - install with: pip install evdev")
        except PermissionError:
            raise Exception("Permission denied - add user to input group: sudo usermod -a -G input $USER")
    
    def init_pynput_backend(self):
        """Initialize pynput backend (fallback)"""
        try:
            from pynput import keyboard
            from pynput.keyboard import Key, Listener
            
            # Test if pynput can create a listener
            test_listener = Listener(on_press=lambda key: None, on_release=lambda key: None)
            test_listener.start()
            test_listener.stop()
            
            self.pynput_keyboard = keyboard
            self.pynput_Key = Key
            self.pynput_Listener = Listener
            return True
            
        except ImportError:
            raise Exception("pynput not available - install with: pip install pynput")
        except Exception as e:
            raise Exception(f"pynput initialization failed: {e}")

    def run_evdev_backend(self):
        """Run keyboard monitoring using evdev"""
        logger.info("Starting evdev keyboard monitoring...")
        
        while self.running:
            try:
                # Use select to monitor multiple devices
                devices_map = {dev.fd: dev for dev in self.keyboards}
                r, w, x = select.select(devices_map, [], [], 1.0)
                
                for fd in r:
                    device = devices_map[fd]
                    try:
                        for event in device.read():
                            if event.type == self.ecodes.EV_KEY:
                                # Check for Alt keys
                                if event.code in [self.ecodes.KEY_LEFTALT, self.ecodes.KEY_RIGHTALT]:
                                    if event.value == 1:  # Key press
                                        self.alt_pressed = True
                                        logger.debug("Alt pressed")
                                    elif event.value == 0:  # Key release
                                        self.alt_pressed = False
                                        logger.debug("Alt released")
                                        self.check_stop_recording()
                                
                                # Check for Shift keys
                                elif event.code in [self.ecodes.KEY_LEFTSHIFT, self.ecodes.KEY_RIGHTSHIFT]:
                                    if event.value == 1:  # Key press
                                        self.shift_pressed = True
                                        logger.debug("Shift pressed")
                                    elif event.value == 0:  # Key release
                                        self.shift_pressed = False
                                        logger.debug("Shift released")
                                        self.check_stop_recording()
                                
                                # Check for K key
                                elif event.code == self.ecodes.KEY_K:
                                    if event.value == 1:  # Key press
                                        logger.debug("K pressed")
                                        self.check_start_recording()
                                
                    except OSError:
                        # Device disconnected, remove it
                        logger.warning(f"Device {device.path} disconnected")
                        self.keyboards.remove(device)
                        if not self.keyboards:
                            logger.error("All keyboard devices disconnected")
                            self.running = False
                            return
                            
            except Exception as e:
                logger.error(f"Error in evdev monitoring: {e}")
                time.sleep(1)

    def run_pynput_backend(self):
        """Run keyboard monitoring using pynput"""
        logger.info("Starting pynput keyboard monitoring...")
        
        def on_press(key):
            try:
                if key == self.pynput_Key.alt_l or key == self.pynput_Key.alt_r:
                    self.alt_pressed = True
                    logger.debug("Alt pressed")
                elif key == self.pynput_Key.shift_l or key == self.pynput_Key.shift_r:
                    self.shift_pressed = True
                    logger.debug("Shift pressed")
                elif hasattr(key, 'char') and key.char and key.char.lower() == 'k':
                    logger.debug("K pressed")
                    self.check_start_recording()
            except AttributeError:
                pass
        
        def on_release(key):
            try:
                if key == self.pynput_Key.alt_l or key == self.pynput_Key.alt_r:
                    self.alt_pressed = False
                    logger.debug("Alt released")
                    self.check_stop_recording()
                elif key == self.pynput_Key.shift_l or key == self.pynput_Key.shift_r:
                    self.shift_pressed = False
                    logger.debug("Shift released")
                    self.check_stop_recording()
                elif hasattr(key, 'char') and key.char and key.char.lower() == 'k':
                    logger.debug("K released")
            except AttributeError:
                pass
        
        # Keep restarting the listener until we're told to stop
        while self.running:
            try:
                with self.pynput_Listener(on_press=on_press, on_release=on_release) as listener:
                    listener.join()  # This will block until the listener stops
            except Exception as e:
                logger.error(f"Pynput listener error: {e}")
                if self.running:
                    logger.info("Restarting listener in 1 second...")
                    time.sleep(1)

    def check_start_recording(self):
        """Check if Alt+Shift+K combination is pressed to start recording"""
        logger.debug(f"Checking start: alt={self.alt_pressed}, shift={self.shift_pressed}, recording={self.recording}")
        if self.alt_pressed and self.shift_pressed and not self.recording:
            logger.info("🎙️ Alt+Shift+K combination detected - starting recording!")
            self.start_recording()

    def check_stop_recording(self):
        """Check if any key in the combination is released to stop recording"""
        logger.debug(f"Checking stop: alt={self.alt_pressed}, shift={self.shift_pressed}, recording={self.recording}")
        if self.recording and (not self.alt_pressed or not self.shift_pressed):
            logger.info("⏹️ Key combination released - stopping recording...")
            self.stop_recording()

    def start_recording(self):
        """Start recording audio"""
        if self.recording:
            return
            
        # Wait for model to load if still loading
        if hasattr(self, 'preload_thread') and self.preload_thread.is_alive():
            logger.info("Waiting for model to load...")
            self.preload_thread.join()
        
        self.recording = True
        stop_recording.clear()
        
        # Show visual notification
        if self.visual_notification:
            try:
                self.visual_notification.show_recording_border()
            except Exception as e:
                logger.warning(f"Visual notification error: {e}")
        
        # Play start recording sound
        try:
            subprocess.Popen(['mpg123', '-q', 'app/sounds/pop2.mp3'], 
                           stderr=subprocess.DEVNULL)
        except:
            pass
        
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
        
        # Show processing notification
        if self.visual_notification:
            try:
                self.visual_notification.show_processing_border()
            except Exception as e:
                logger.warning(f"Visual notification error: {e}")
        
        # Play stop recording sound
        try:
            subprocess.Popen(['mpg123', '-q', 'app/sounds/pop2.mp3'], 
                           stderr=subprocess.DEVNULL)
        except:
            pass
        
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
            # Process the audio
            result, transcribe_time = process_audio_stream()
            
            # Clean up result
            transcription = result.strip()
            
            if transcription:
                # Copy to clipboard
                import pyperclip
                pyperclip.copy(transcription)
                
                # Show completion notification
                if self.visual_notification:
                    try:
                        self.visual_notification.show_completed_border()
                    except Exception as e:
                        logger.warning(f"Visual notification error: {e}")
                
                # Play sound
                try:
                    subprocess.Popen(['mpg123', '-q', 'app/sounds/pop.mp3'], 
                                   stderr=subprocess.DEVNULL)
                except:
                    pass
                
                logger.info(f"✅ Transcribed: {transcription}")
            else:
                # Hide processing notification
                if self.visual_notification:
                    try:
                        self.visual_notification.hide_notification()
                    except Exception as e:
                        logger.warning(f"Visual notification error: {e}")
                        
                logger.info("❌ No speech detected")
                
        except Exception as e:
            # Hide processing notification on error
            if self.visual_notification:
                try:
                    self.visual_notification.hide_notification()
                except Exception as e2:
                    logger.warning(f"Visual notification error: {e2}")
            logger.error(f"Transcription error: {e}")

    def run(self):
        """Run the voice transcriber"""
        if not self.backend:
            logger.error("No keyboard monitoring backend available")
            return False
        
        logger.info("🎤 Simple Voice Transcriber started!")
        logger.info(f"📱 Using device: {DEVICE}")
        logger.info(f"🔧 Backend: {self.backend}")
        logger.info("🔥 Hold Alt+Shift+K to record, release any key to transcribe")
        logger.info("🔄 Use Ctrl+C to exit")
        
        if self.backend == 'evdev':
            logger.info("💡 Using evdev backend - works great in terminals like Alacritty!")
        
        self.running = True
        
        try:
            if self.backend == 'evdev':
                self.run_evdev_backend()
            elif self.backend == 'pynput':
                self.run_pynput_backend()
        except Exception as e:
            logger.error(f"Error running {self.backend} backend: {e}")
            return False
        
        return True

if __name__ == "__main__":
    transcriber = SimpleVoiceTranscriber()
    transcriber.run() 
