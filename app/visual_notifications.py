#!/usr/bin/env python3
"""
Visual Notifications Module
A reusable module for creating visual notifications across different display environments.
Supports Wayland, X11, and terminal-based notifications with fallback options.

Features:
- Cross-platform visual overlays (tkinter, zenity)
- Terminal-based colored notifications
- Automatic display environment detection
- Persistent and timed notifications
- Clean process management
"""

import os
import sys
import time
import threading
import subprocess
import logging

# Setup logger for this module
logger = logging.getLogger(__name__)

# Check for tkinter availability
try:
    import tkinter as tk
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False
    logger.debug("tkinter not available, falling back to other notification methods")


class VisualNotification:
    """
    Enhanced visual notification system with cross-platform support.
    
    Automatically detects the display environment and available tools,
    then uses the best available method for showing notifications.
    
    Supports:
    - Tkinter-based overlays (cross-platform)
    - Zenity dialogs (Linux)
    - Terminal-based colored notifications
    """
    
    def __init__(self, app_name="Application", enable_logging=True):
        """
        Initialize the visual notification system.
        
        Args:
            app_name (str): Name of the application for notification titles
            enable_logging (bool): Whether to enable debug logging
        """
        self.app_name = app_name
        self.active = False
        self.overlay_processes = []
        self.display_env = self._detect_display_environment()
        self.available_tools = self._detect_available_tools()
        
        if enable_logging:
            logger.info(f"Display environment: {self.display_env}")
            logger.info(f"Available tools: {self.available_tools}")
    
    def _detect_display_environment(self):
        """Detect the current display environment."""
        if os.environ.get('WAYLAND_DISPLAY'):
            return 'wayland'
        elif os.environ.get('DISPLAY'):
            return 'x11'
        else:
            return 'terminal'
    
    def _detect_available_tools(self):
        """Detect available system notification tools."""
        tools = []
        for tool in ['zenity', 'yad', 'kdialog', 'xmessage']:
            try:
                subprocess.run(['which', tool], capture_output=True, check=True)
                tools.append(tool)
            except:
                pass
        return tools
    
    def show_notification(self, text, color="#0066cc", persistent=False, emoji="ℹ️"):
        """
        Show a notification with the given text and color.
        
        Args:
            text (str): Text to display
            color (str): Background color (hex format)
            persistent (bool): Whether notification should stay until manually closed
            emoji (str): Emoji to prefix the text with
        """
        display_text = f"{emoji} {text}"
        if self.active and persistent:
            return
        
        if persistent:
            self.active = True
        
        self._create_overlay(display_text, color, persistent)
        self._show_terminal_notification(display_text)
    
    def show_recording(self, text="RECORDING"):
        """Show a recording notification."""
        if self.active:
            return
        self.active = True
        self._create_overlay("🔴 RECORDING", "#ff4444", persistent=True)
        self._show_terminal_notification(f"🔴 {text} - Recording in progress")
    
    def show_processing(self, text="PROCESSING"):
        """Show a processing notification."""
        self._cleanup_overlays()
        self._create_overlay("⚡ PROCESSING", "#ffaa00", persistent=True)
        self._show_terminal_notification(f"⚡ {text}...")
    
    def show_completed(self, text="COMPLETED"):
        """Show a completion notification."""
        self._cleanup_overlays()
        self._create_overlay("✅ COMPLETED", "#00aaff", persistent=False)
        self._show_terminal_notification(f"✅ {text}")
        threading.Timer(2.0, self.hide_notification).start()
    
    def show_error(self, text="ERROR"):
        """Show an error notification."""
        self._cleanup_overlays()
        self._create_overlay("❌ ERROR", "#ff0000", persistent=False)
        self._show_terminal_notification(f"❌ {text}")
        threading.Timer(3.0, self.hide_notification).start()
    
    def show_warning(self, text="WARNING"):
        """Show a warning notification."""
        self._cleanup_overlays()
        self._create_overlay("⚠️ WARNING", "#ff8800", persistent=False)
        self._show_terminal_notification(f"⚠️ {text}")
        threading.Timer(3.0, self.hide_notification).start()
    
    def _create_overlay(self, text, color, persistent=False):
        """Create a visual overlay using the best available method."""
        if TKINTER_AVAILABLE:
            try:
                self._create_tkinter_overlay(text, color, persistent)
                return
            except Exception as e:
                logger.debug(f"Tkinter overlay failed: {e}")
        
        if 'zenity' in self.available_tools:
            try:
                self._create_zenity_notification(text, persistent)
                return
            except Exception as e:
                logger.debug(f"Zenity overlay failed: {e}")
    
    def _create_tkinter_overlay(self, text, color, persistent):
        """Create a tkinter-based overlay window."""
        overlay_script = f'''
import tkinter as tk
import time
import sys

def create_overlay():
    try:
        root = tk.Tk()
        root.title("{self.app_name}")
        root.overrideredirect(True)
        root.attributes('-topmost', True)
        root.attributes('-alpha', 0.95)
        root.configure(bg='{color}')
        
        # Center the window
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        window_width = 500
        window_height = 120
        x = (screen_width - window_width) // 2
        y = max(100, (screen_height - window_height) // 4)
        
        root.geometry(f"{{window_width}}x{{window_height}}+{{x}}+{{y}}")
        
        # Create border frame
        border_frame = tk.Frame(root, bg='black', bd=3)
        border_frame.pack(fill='both', expand=True, padx=3, pady=3)
        
        # Create inner frame
        inner_frame = tk.Frame(border_frame, bg='{color}')
        inner_frame.pack(fill='both', expand=True, padx=2, pady=2)
        
        # Create label
        label = tk.Label(
            inner_frame,
            text="{text}",
            bg='{color}',
            fg='white' if '{color}' in ['#ff4444', '#ff0000'] else 'black',
            font=('Arial', 18, 'bold'),
            pady=20,
            wraplength=450
        )
        label.pack(expand=True)
        
        # Handle persistence
        if {'True' if persistent else 'False'}:
            root.mainloop()
        else:
            root.after(3000, root.quit)
            root.mainloop()
            
    except Exception as e:
        print(f"Overlay error: {{e}}")
        sys.exit(1)

if __name__ == "__main__":
    create_overlay()
'''
        
        # Create temporary script file
        overlay_file = f'/tmp/{self.app_name.lower().replace(" ", "_")}_overlay_{int(time.time())}.py'
        with open(overlay_file, 'w') as f:
            f.write(overlay_script)
        
        # Launch overlay process
        process = subprocess.Popen(['python3', overlay_file], 
                                 stderr=subprocess.DEVNULL, 
                                 stdout=subprocess.DEVNULL)
        self.overlay_processes.append(process)
        
        # Schedule cleanup of temporary file
        threading.Timer(10.0, lambda: self._cleanup_temp_file(overlay_file)).start()
    
    def _create_zenity_notification(self, text, persistent):
        """Create a zenity-based notification."""
        cmd = [
            'zenity', '--info',
            f'--title={self.app_name}',
            f'--text=<span font="16" weight="bold">{text}</span>',
            '--width=400', '--height=150'
        ]
        if not persistent:
            cmd.append('--timeout=3')
        
        process = subprocess.Popen(cmd, stderr=subprocess.DEVNULL)
        self.overlay_processes.append(process)
    
    def _cleanup_temp_file(self, filepath):
        """Clean up temporary overlay script files."""
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception as e:
            logger.debug(f"Failed to cleanup temp file {filepath}: {e}")
    
    def _cleanup_overlays(self):
        """Clean up all active overlay processes."""
        for process in self.overlay_processes:
            try:
                process.terminate()
                process.wait(timeout=1)
            except:
                try:
                    process.kill()
                except:
                    pass
        self.overlay_processes = []
    
    def _show_terminal_notification(self, text):
        """Show a colorful terminal notification."""
        try:
            # Clear screen and move cursor to top
            print(f"\033[2J\033[H", end='')
            
            # Choose colors based on text content
            if "RECORDING" in text.upper():
                color_code = "\033[91m"  # Red
                box_char = "█"
            elif "PROCESSING" in text.upper() or "TRANSCRIBING" in text.upper():
                color_code = "\033[93m"  # Yellow
                box_char = "█"
            elif "COMPLETED" in text.upper() or "TYPED" in text.upper():
                color_code = "\033[94m"  # Blue
                box_char = "█"
            elif "ERROR" in text.upper():
                color_code = "\033[95m"  # Magenta
                box_char = "█"
            elif "WARNING" in text.upper():
                color_code = "\033[96m"  # Cyan
                box_char = "█"
            else:
                color_code = "\033[92m"  # Green
                box_char = "█"
            
            # Create notification box
            box_width = 70
            print(color_code + box_char * box_width)
            print(box_char + " " * (box_width - 2) + box_char)
            
            # Center the main text
            main_text = text[:box_width - 4]  # Ensure text fits
            print(box_char + f"{main_text}".center(box_width - 2) + box_char)
            
            # Add app name if there's space
            if len(text) < box_width - 10:
                app_text = f"({self.app_name})"
                print(box_char + f"{app_text}".center(box_width - 2) + box_char)
            
            print(box_char + " " * (box_width - 2) + box_char)
            print(box_char * box_width + "\033[0m")
            
        except Exception as e:
            logger.debug(f"Terminal notification failed: {e}")
            # Fallback to simple print
            print(f"\n{text}\n")
    
    def hide_notification(self):
        """Hide all active notifications."""
        if not self.active:
            return
        
        self.active = False
        self._cleanup_overlays()
        
        try:
            print(f"\033[2J\033[H", end='')
            print(f"🎤 {self.app_name} Ready")
        except:
            pass
    
    def cleanup(self):
        """Clean up all resources and processes."""
        self.hide_notification()
        self._cleanup_overlays()


# Convenience functions for quick usage
def show_recording_notification(app_name="App", text="RECORDING"):
    """Quick function to show a recording notification."""
    notifier = VisualNotification(app_name, enable_logging=False)
    notifier.show_recording(text)
    return notifier

def show_processing_notification(app_name="App", text="PROCESSING"):
    """Quick function to show a processing notification."""
    notifier = VisualNotification(app_name, enable_logging=False)
    notifier.show_processing(text)
    return notifier

def show_completed_notification(app_name="App", text="COMPLETED"):
    """Quick function to show a completion notification."""
    notifier = VisualNotification(app_name, enable_logging=False)
    notifier.show_completed(text)
    return notifier

def show_error_notification(app_name="App", text="ERROR"):
    """Quick function to show an error notification."""
    notifier = VisualNotification(app_name, enable_logging=False)
    notifier.show_error(text)
    return notifier


# Example usage and testing
if __name__ == "__main__":
    # Demo of the notification system
    print("Visual Notifications Demo")
    print("=" * 40)
    
    # Create notification instance
    notifier = VisualNotification("Demo App")
    
    # Test different notification types
    print("Testing recording notification...")
    notifier.show_recording("Recording Audio")
    time.sleep(3)
    
    print("Testing processing notification...")
    notifier.show_processing("Processing Data")
    time.sleep(3)
    
    print("Testing completion notification...")
    notifier.show_completed("Task Finished")
    time.sleep(3)
    
    print("Testing custom notification...")
    notifier.show_notification("Custom Message", "#00ff00", False, "🎉")
    time.sleep(3)
    
    print("Testing error notification...")
    notifier.show_error("Something went wrong")
    time.sleep(3)
    
    # Cleanup
    notifier.cleanup()
    print("Demo completed!") 