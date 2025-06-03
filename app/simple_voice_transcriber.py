#!/usr/bin/env python3
"""
Simple Voice Transcriber with Alt+Shift+K shortcut
Enhanced with Wayland-compatible global hotkeys using evdev+uinput
"""
import logging
import threading
import subprocess
import time
import os
import sys
import select
import json

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

class WaylandGlobalHotkeys:
    """Wayland-compatible global hotkey system using evdev + uinput"""
    
    def __init__(self, callback_start, callback_stop):
        self.callback_start = callback_start
        self.callback_stop = callback_stop
        self.running = False
        self.devices = []
        self.virtual_keyboard = None
        self.key_states = {}
        self.hotkey_active = False
        
        # Key codes for our hotkey combination (Alt+Shift+K)
        self.ALT_KEYS = [56, 100]  # KEY_LEFTALT, KEY_RIGHTALT
        self.SHIFT_KEYS = [42, 54]  # KEY_LEFTSHIFT, KEY_RIGHTSHIFT  
        self.K_KEY = 37  # KEY_K
        
        self.init_devices()
    
    def init_devices(self):
        """Initialize evdev devices and uinput virtual keyboard"""
        try:
            import evdev
            import uinput
            self.evdev = evdev
            self.uinput = uinput
        except ImportError as e:
            logger.error(f"Missing dependencies: {e}")
            logger.error("Install with: pip install evdev python-uinput")
            return False
        
        # Find keyboard devices
        try:
            # Try to get devices from evdev.list_devices() first
            device_paths = evdev.list_devices()
            
            # Also try to manually check common event devices
            import glob
            all_event_paths = glob.glob('/dev/input/event*')
            for path in all_event_paths:
                if path not in device_paths:
                    device_paths.append(path)
            
            logger.debug(f"Checking {len(device_paths)} input device paths...")
            
            devices = []
            keyboards = []
            
            for path in device_paths:
                try:
                    device = evdev.InputDevice(path)
                    devices.append(device)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Cannot access {path}: {e}")
                    continue
            
            logger.debug(f"Successfully opened {len(devices)} input devices, checking for keyboards...")
            
            for device in devices:
                caps = device.capabilities()
                if evdev.ecodes.EV_KEY in caps:
                    key_caps = caps[evdev.ecodes.EV_KEY]
                    
                    # More flexible keyboard detection - look for common keyboard keys
                    has_letters = any(key in key_caps for key in [
                        evdev.ecodes.KEY_A, evdev.ecodes.KEY_B, evdev.ecodes.KEY_C,
                        evdev.ecodes.KEY_Q, evdev.ecodes.KEY_W, evdev.ecodes.KEY_E
                    ])
                    has_modifiers = any(key in key_caps for key in [
                        evdev.ecodes.KEY_LEFTALT, evdev.ecodes.KEY_RIGHTALT,
                        evdev.ecodes.KEY_LEFTSHIFT, evdev.ecodes.KEY_RIGHTSHIFT,
                        evdev.ecodes.KEY_LEFTCTRL, evdev.ecodes.KEY_RIGHTCTRL
                    ])
                    has_space_enter = any(key in key_caps for key in [
                        evdev.ecodes.KEY_SPACE, evdev.ecodes.KEY_ENTER
                    ])
                    
                    # Check if it has our specific hotkey keys
                    has_alt = any(key in key_caps for key in [evdev.ecodes.KEY_LEFTALT, evdev.ecodes.KEY_RIGHTALT])
                    has_shift = any(key in key_caps for key in [evdev.ecodes.KEY_LEFTSHIFT, evdev.ecodes.KEY_RIGHTSHIFT])
                    has_k = evdev.ecodes.KEY_K in key_caps
                    
                    # Accept device if it looks like a keyboard and has our hotkey keys
                    if (has_letters or has_modifiers or has_space_enter) and has_alt and has_shift and has_k:
                        keyboards.append(device)
                        logger.info(f"Found keyboard: {device.name} at {device.path}")
                        logger.debug(f"  Device capabilities: letters={has_letters}, modifiers={has_modifiers}, space/enter={has_space_enter}")
                    else:
                        logger.debug(f"Skipping device: {device.name} (missing required keys)")
                        logger.debug(f"  Has letters: {has_letters}, modifiers: {has_modifiers}, space/enter: {has_space_enter}")
                        logger.debug(f"  Has Alt: {has_alt}, Shift: {has_shift}, K: {has_k}")
            
            if not keyboards:
                logger.error("No suitable keyboard devices found")
                logger.error("Available input devices:")
                for device in devices:
                    caps = device.capabilities()
                    has_keys = evdev.ecodes.EV_KEY in caps
                    key_count = len(caps.get(evdev.ecodes.EV_KEY, [])) if has_keys else 0
                    logger.error(f"  - {device.name} at {device.path} (has {key_count} keys)")
                
                logger.error("Inaccessible devices (permission denied):")
                for path in device_paths:
                    if not any(d.path == path for d in devices):
                        logger.error(f"  - {path}")
                
                return False
                
            self.devices = keyboards
            
            # Create virtual keyboard for sending events
            try:
                # Define the keys we might need to send
                events = (
                    uinput.KEY_LEFTALT, uinput.KEY_RIGHTALT,
                    uinput.KEY_LEFTSHIFT, uinput.KEY_RIGHTSHIFT,
                    uinput.KEY_K, uinput.KEY_A, uinput.KEY_SPACE,
                    # Add more keys as needed
                )
                self.virtual_keyboard = uinput.Device(events)
                logger.info("Created virtual keyboard device")
            except Exception as e:
                logger.warning(f"Could not create virtual keyboard: {e}")
                # Continue without virtual keyboard - we can still detect hotkeys
            
            return True
            
        except PermissionError:
            logger.error("Permission denied accessing input devices")
            logger.error("Run as root or add user to input group: sudo usermod -a -G input $USER")
            logger.error("Then log out and back in")
            return False
        except Exception as e:
            logger.error(f"Error initializing devices: {e}")
            return False
    
    def is_hotkey_pressed(self):
        """Check if our hotkey combination (Alt+Shift+K) is currently pressed"""
        alt_pressed = any(self.key_states.get(key, False) for key in self.ALT_KEYS)
        shift_pressed = any(self.key_states.get(key, False) for key in self.SHIFT_KEYS)
        k_pressed = self.key_states.get(self.K_KEY, False)
        
        return alt_pressed and shift_pressed and k_pressed
    
    def is_hotkey_released(self):
        """Check if hotkey combination is no longer fully pressed"""
        alt_pressed = any(self.key_states.get(key, False) for key in self.ALT_KEYS)
        shift_pressed = any(self.key_states.get(key, False) for key in self.SHIFT_KEYS)
        k_pressed = self.key_states.get(self.K_KEY, False)
        
        return not (alt_pressed and shift_pressed and k_pressed)
    
    def handle_key_event(self, event):
        """Handle a key event and check for hotkey activation"""
        if event.type != self.evdev.ecodes.EV_KEY:
            return
        
        key_code = event.code
        key_state = event.value  # 1 = press, 0 = release, 2 = repeat
        
        # Update key state tracking
        if key_state in [0, 1]:  # Only track press/release, ignore repeat
            self.key_states[key_code] = (key_state == 1)
        
        # Check for hotkey activation
        if self.is_hotkey_pressed() and not self.hotkey_active:
            logger.debug("🎙️ Hotkey activated - starting recording")
            self.hotkey_active = True
            self.callback_start()
        elif self.hotkey_active and self.is_hotkey_released():
            logger.debug("⏹️ Hotkey released - stopping recording")
            self.hotkey_active = False
            self.callback_stop()
    
    def run(self):
        """Main event loop for monitoring keyboard events"""
        if not self.devices:
            logger.error("No devices available for monitoring")
            return False
        
        self.running = True
        logger.info(f"Monitoring {len(self.devices)} keyboard device(s) for Alt+Shift+K")
        
        while self.running:
            try:
                # Use select to monitor multiple devices
                devices_map = {dev.fd: dev for dev in self.devices if dev.fd is not None}
                
                if not devices_map:
                    logger.error("All devices disconnected")
                    break
                
                r, w, x = select.select(devices_map, [], [], 1.0)
                
                for fd in r:
                    device = devices_map[fd]
                    try:
                        for event in device.read():
                            self.handle_key_event(event)
                    except OSError as e:
                        logger.warning(f"Device {device.path} error: {e}")
                        # Remove disconnected device
                        if device in self.devices:
                            self.devices.remove(device)
                        continue
                        
            except Exception as e:
                logger.error(f"Error in event loop: {e}")
                time.sleep(1)
        
        return True
    
    def stop(self):
        """Stop the hotkey monitoring"""
        self.running = False
        if self.virtual_keyboard:
            self.virtual_keyboard.destroy()

class SimpleVoiceTranscriber:
    def __init__(self):
        self.recording = False
        self.record_thread = None
        self.process_thread = None
        self.hotkey_system = None
        self.running = False
        
        # Initialize visual notification
        self.visual_notification = VisualNotification()
        
        # Preload model in background
        logger.info("Loading transcription model...")
        self.preload_thread = preload_model(device=DEVICE)
        
        # Initialize global hotkey system
        self.init_hotkeys()
        
    def init_hotkeys(self):
        """Initialize the global hotkey system"""
        try:
            self.hotkey_system = WaylandGlobalHotkeys(
                callback_start=self.start_recording,
                callback_stop=self.stop_recording
            )
            
            if self.hotkey_system.devices:
                logger.info("✅ Global hotkey system initialized")
                return True
            else:
                logger.error("❌ Failed to initialize global hotkey system")
                return False
                
        except Exception as e:
            logger.error(f"Error initializing hotkeys: {e}")
            return False

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
                try:
                    self.visual_notification.hide_notification()
                except Exception as e:
                    logger.warning(f"Visual notification error: {e}")
                        
                logger.info("❌ No speech detected")
                
        except Exception as e:
            # Hide processing notification on error
            try:
                self.visual_notification.hide_notification()
            except Exception as e2:
                logger.warning(f"Visual notification error: {e2}")
            logger.error(f"Transcription error: {e}")

    def run(self):
        """Run the voice transcriber"""
        if not self.hotkey_system or not self.hotkey_system.devices:
            logger.error("❌ No global hotkey system available")
            logger.error("💡 Make sure you're running as root or in the input group")
            logger.error("💡 Install dependencies: pip install evdev python-uinput")
            return False
        
        logger.info("🎤 Voice Transcriber started!")
        logger.info(f"📱 Using device: {DEVICE}")
        logger.info("🔥 Hold Alt+Shift+K to record, release to transcribe")
        logger.info("🔄 Use Ctrl+C to exit")
        logger.info("💡 Global hotkeys work even when Alacritty is in focus!")
        
        self.running = True
        
        try:
            # Run the hotkey monitoring system
            return self.hotkey_system.run()
        except KeyboardInterrupt:
            logger.info("👋 Shutting down...")
            self.running = False
            if self.hotkey_system:
                self.hotkey_system.stop()
            return True
        except Exception as e:
            logger.error(f"Error running hotkey system: {e}")
            return False

if __name__ == "__main__":
    def check_permissions():
        """Check if user has proper permissions for input device access"""
        import grp
        import pwd
        
        # Check if running as root
        if os.geteuid() == 0:
            logger.info("✅ Running as root - full input device access available")
            return True
        
        # Check if user is in input group
        try:
            current_user = pwd.getpwuid(os.getuid()).pw_name
            
            # Get current groups using os.getgroups() which reflects actual active groups
            current_gids = os.getgroups()
            
            # Get input group info
            input_group = grp.getgrnam('input')
            
            # Check if user is in input group (by GID)
            if input_group.gr_gid in current_gids:
                logger.info("✅ User is in input group - input device access available")
                return True
            else:
                # Get group names for display
                group_names = []
                for gid in current_gids:
                    try:
                        group_names.append(grp.getgrgid(gid).gr_name)
                    except:
                        group_names.append(str(gid))
                
                logger.error("❌ Permission issue detected!")
                logger.error(f"👤 Current user: {current_user}")
                logger.error(f"👥 Active groups: {', '.join(group_names)}")
                logger.error(f"🔢 Input group GID: {input_group.gr_gid} (not in active groups)")
                logger.error("")
                logger.error("🔧 To fix this issue, choose one of these options:")
                logger.error("")
                logger.error("   Option 1 (Recommended): Add user to input group")
                logger.error("   sudo usermod -a -G input $USER")
                logger.error("   Then log out and back in (or reboot)")
                logger.error("")
                logger.error("   Option 2: Run as root (less secure)")
                logger.error("   sudo nix-shell --run 'python3 app/simple_voice_transcriber.py'")
                logger.error("")
                logger.error("🔍 Why this is needed:")
                logger.error("   Global hotkeys on Wayland require direct access to input devices")
                logger.error("   This bypasses application-level input restrictions")
                logger.error("   Works even when Alacritty or other apps are focused")
                logger.error("")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error checking permissions: {e}")
            logger.error("💡 Try running as root: sudo nix-shell --run 'python3 app/simple_voice_transcriber.py'")
            return False
    
    def check_input_devices():
        """Check if input devices are accessible"""
        try:
            import evdev
            devices = evdev.list_devices()
            if not devices:
                logger.error("❌ No input devices found")
                return False
            
            # Try to access at least one device
            for device_path in devices:
                try:
                    device = evdev.InputDevice(device_path)
                    device.close()
                    logger.info(f"✅ Input devices accessible ({len(devices)} found)")
                    return True
                except PermissionError:
                    continue
            
            logger.error("❌ Input devices found but not accessible due to permissions")
            return False
            
        except ImportError:
            logger.error("❌ evdev not available")
            return False
        except Exception as e:
            logger.error(f"❌ Error checking input devices: {e}")
            return False
    
    # Perform comprehensive checks
    logger.info("🔍 Checking system requirements...")
    
    permissions_ok = check_permissions()
    devices_ok = check_input_devices() if permissions_ok else False
    
    if not permissions_ok:
        logger.error("")
        logger.error("⚠️  Cannot start voice transcriber due to permission issues")
        logger.error("📖 Follow the instructions above to fix permissions")
        sys.exit(1)
    
    if not devices_ok:
        logger.error("")
        logger.error("⚠️  Cannot access input devices")
        logger.error("💡 Make sure input devices are connected and accessible")
        sys.exit(1)
    
    logger.info("✅ All system checks passed!")
    logger.info("")
    
    transcriber = SimpleVoiceTranscriber()
    transcriber.run() 
