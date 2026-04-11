#!/usr/bin/env python3
"""
Windows Notifications Module
Uses Windows-native notifications and sound
"""
import logging
import threading
import time
import os
import sys

logger = logging.getLogger(__name__)

try:
    import winsound
except ImportError:
    winsound = None

class VisualNotification:
    """Windows visual notification system"""
    
    def __init__(self, app_name="Voice Transcriber"):
        self.app_name = app_name
        self.notification_window = None
        self.window_visible = False
        self._notification_thread = None
    
    def _play_sound_async(self, sound_type):
        """Play sound asynchronously"""
        def play():
            try:
                if winsound:
                    if sound_type == "start":
                        winsound.PlaySound("SystemExclamation", winsound.SND_ASYNC)
                    elif sound_type == "stop":
                        winsound.PlaySound("SystemAsterisk", winsound.SND_ASYNC)
                    elif sound_type == "complete":
                        winsound.PlaySound("SystemExit", winsound.SND_ASYNC)
            except Exception:
                pass
        
        thread = threading.Thread(target=play, daemon=True)
        thread.start()
    
    def show_recording(self):
        """Show recording notification"""
        logger.info("Recording...")
        self._play_sound_async("start")
        self._show_toast_notification("Voice Transcriber", "Recording... (release Alt+Shift to transcribe)")
    
    def show_processing(self, message="Processing"):
        """Show processing notification"""
        logger.info(message)
        self._show_toast_notification("Voice Transcriber", message)
    
    def show_completed(self, sub_text=None):
        """Show completed notification"""
        message = "Transcription complete!"
        if sub_text:
            if len(sub_text) > 50:
                sub_text = sub_text[:47] + "..."
            message = f"{message}\n{sub_text}"
        
        logger.info(f"Transcription: {sub_text}")
        self._play_sound_async("complete")
        self._show_toast_notification("Voice Transcriber", message)
    
    def hide_notification(self):
        """Hide notification"""
        self.window_visible = False
    
    def set_active_device(self, device_name):
        """Set the active device name for display"""
        self.active_device = device_name
    
    def _show_toast_notification(self, title, message):
        """Show Windows toast notification via PowerShell"""
        try:
            import subprocess
            
            ps_script = f'''
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDom.XmlDocument, Windows.Data.Xml.Dom.XmlDom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

$template = @"
<toast>
    <visual>
        <binding template="ToastText02">
            <text id="1">{title}</text>
            <text id="2">{message}</text>
        </binding>
    </visual>
</toast>
"@

$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("VoiceTranscriber").Show($toast)
'''
            
            temp_script = os.path.join(os.path.dirname(__file__), "_toast_temp.ps1")
            with open(temp_script, "w", encoding="utf-8") as f:
                f.write(ps_script)
            
            subprocess.Popen(
                ["powershell", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", temp_script],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
            )
            
            def cleanup():
                try:
                    time.sleep(2)
                    if os.path.exists(temp_script):
                        os.remove(temp_script)
                except:
                    pass
            
            threading.Thread(target=cleanup, daemon=True).start()
            
        except Exception as e:
            logger.debug(f"Toast notification failed: {e}")
    
    def cleanup(self):
        """Clean up resources"""
        self.hide_notification()

# Alias for compatibility
VisualNotification = VisualNotification