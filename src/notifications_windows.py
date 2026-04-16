#!/usr/bin/env python3
"""
Windows Notifications Module
Uses Tkinter overlay for visual notifications (no focus stealing)
"""
import logging
import threading
import time
import os
import sys

logger = logging.getLogger(__name__)

class VisualNotification:
    """Windows visual notification system using Tkinter overlay"""
    
    def __init__(self, app_name="Voice Transcriber"):
        self.app_name = app_name
        self.active_overlay = None
    
    def _show_tkinter_overlay(self, text, color="#0066cc"):
        """Show a Tkinter overlay window - does not steal focus"""
        def create_overlay():
            try:
                import tkinter as tk
                
                root = tk.Tk()
                root.title(self.app_name)
                root.overrideredirect(True)
                root.attributes('-topmost', True)
                root.attributes('-alpha', 0.9)
                # Extra flags to avoid stealing focus
                root.attributes('-disabled', True)  # Makes window non-interactive
                root.configure(bg=color)
                
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                window_width = 320
                window_height = 60
                x = (screen_width - window_width) // 2
                y = 100
                
                root.geometry(f"{window_width}x{window_height}+{x}+{y}")
                
                # Make window appear on all virtual desktops (Windows 10+)
                try:
                    # CS_DROPSHADE = 0x00000008, LWA_NOREDIRECT = 0x00000002
                    import ctypes
                    GWL_EXSTYLE = -20
                    WS_EX_NOACTIVATE = 0x08000000
                    WS_EX_TOOLWINDOW = 0x00000080
                    ex_style = ctypes.windll.user32.GetWindowLongW(root.winfo_id(), GWL_EXSTYLE)
                    ctypes.windll.user32.SetWindowLongW(root.winfo_id(), GWL_EXSTYLE, ex_style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
                except:
                    pass
                
                label = tk.Label(
                    root,
                    text=text,
                    bg=color,
                    fg="white",
                    font=("Arial", 12, "bold")
                )
                label.pack(expand=True)
                
                root.after(2500, root.destroy)
                root.mainloop()
            except Exception as e:
                logger.debug(f"Tkinter overlay failed: {e}")
        
        # Kill any existing overlay first
        if self.active_overlay and self.active_overlay.is_alive():
            try:
                # The previous overlay will auto-close after its timer
                pass
            except:
                pass
        
        self.active_overlay = threading.Thread(target=create_overlay, daemon=True)
        self.active_overlay.start()
    
    def _play_sound(self, sound_type):
        """Play system sound"""
        try:
            import t2
            if t2.IS_MUTED:
                return
        except:
            pass
        
        def play():
            try:
                import winsound
                if sound_type == "start":
                    winsound.PlaySound("SystemExclamation", winsound.SND_ASYNC)
                elif sound_type == "stop":
                    winsound.PlaySound("SystemAsterisk", winsound.SND_ASYNC)
                elif sound_type == "complete":
                    winsound.PlaySound("SystemExit", winsound.SND_ASYNC)
            except Exception as e:
                logger.debug(f"Sound failed: {e}")
        
        thread = threading.Thread(target=play, daemon=True)
        thread.start()
    
    def show_recording(self):
        """Show recording notification"""
        logger.info("Recording...")
        self._play_sound("start")
        self._show_tkinter_overlay("● RECORDING", "#ff4444")
    
    def show_processing(self, message="Processing"):
        """Show processing notification"""
        logger.info(message)
        self._show_tkinter_overlay(f"LOADING {message.upper()}", "#ffaa00")
    
    def show_completed(self, sub_text=None):
        """Show completed notification"""
        message = "✓ COMPLETED"
        if sub_text:
            message = f"✓ {sub_text[:30]}..."
        
        logger.info(f"Transcription: {sub_text}")
        self._play_sound("complete")
        self._show_tkinter_overlay(message, "#00aa44")
    
    def hide_notification(self):
        """Hide notification (overlay auto-closes after 2.5s)"""
        pass
    
    def set_active_device(self, device_name):
        """Set active device for display"""
        self.active_device = device_name
    
    def cleanup(self):
        """Clean up resources"""
        pass