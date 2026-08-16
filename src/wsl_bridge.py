#!/usr/bin/env python3
"""
WSL Host-Guest Bridge for Voice Transcriber
Allows Windows host global hotkeys to trigger transcription inside NixOS WSL.
"""
import os
import sys
import json
import time
import socket
import threading
import subprocess
import logging

# Ensure src is in sys.path
src_dir = os.path.dirname(os.path.abspath(__file__))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

import t2
from t2 import (
    preload_model, DEVICE, record_audio_stream, process_audio_stream,
    stop_recording, load_audio_config, get_active_device_name
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - [WSL-Bridge] %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BRIDGE_HOST = '127.0.0.1'
BRIDGE_PORT = 50555

def copy_to_windows_clipboard(text):
    """Copy text to Windows clipboard from inside WSL"""
    try:
        # Try clip.exe first (fastest and most reliable built-in tool)
        clip_path = "/mnt/c/Windows/System32/clip.exe"
        if os.path.exists(clip_path):
            p = subprocess.Popen([clip_path], stdin=subprocess.PIPE, close_fds=True)
            p.communicate(input=text.encode('utf-16le'))
            logger.info("Copied transcription to Windows clipboard via clip.exe")
            return True
        else:
            # Fallback to PowerShell
            subprocess.run([
                "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
                "-NoProfile", "-Command", f"Set-Clipboard -Value @'\n{text}\n'@"
            ], check=True)
            logger.info("Copied transcription to Windows clipboard via PowerShell")
            return True
    except Exception as e:
        logger.warning(f"Failed to copy to Windows clipboard: {e}")
        return False

class WSLTranscriptionDaemon:
    def __init__(self):
        self.recording = False
        self.record_thread = None
        self.process_thread = None
        load_audio_config()
        
        logger.info(f"Active audio input device: {get_active_device_name()}")
        logger.info(f"PULSE_SERVER: {os.environ.get('PULSE_SERVER', 'default')}")
        logger.info("Preloading transcription model in background...")
        preload_model(device=DEVICE)

    def start_recording(self):
        if self.recording:
            return {"status": "already_recording"}
        
        self.recording = True
        stop_recording.clear()
        
        self.record_thread = threading.Thread(target=record_audio_stream, daemon=True)
        self.record_thread.start()
        logger.info("🎤 Recording started...")
        return {"status": "recording_started"}

    def stop_recording(self, copy_clipboard=True):
        if not self.recording:
            return {"status": "not_recording"}
        
        self.recording = False
        stop_recording.set()
        logger.info("🛑 Recording stopped. Transcribing...")
        
        # Process transcription
        def _transcribe():
            result = process_audio_stream()
            if result:
                logger.info(f"📝 Transcription: {result}")
                if copy_clipboard:
                    copy_to_windows_clipboard(result)
            return result

        t = threading.Thread(target=_transcribe, daemon=True)
        t.start()
        return {"status": "transcription_in_progress"}

def run_server():
    daemon = WSLTranscriptionDaemon()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((BRIDGE_HOST, BRIDGE_PORT))
    server.listen(5)
    
    logger.info(f"🚀 WSL Transcription Bridge listening on {BRIDGE_HOST}:{BRIDGE_PORT}")
    
    while True:
        try:
            conn, addr = server.accept()
            data = conn.recv(1024).decode('utf-8').strip()
            if not data:
                conn.close()
                continue
            
            try:
                cmd = json.loads(data)
                action = cmd.get("action")
                if action == "start":
                    resp = daemon.start_recording()
                elif action == "stop":
                    resp = daemon.stop_recording(copy_clipboard=cmd.get("copy_clipboard", True))
                elif action == "status":
                    resp = {"status": "recording" if daemon.recording else "idle"}
                else:
                    resp = {"error": f"Unknown action: {action}"}
            except Exception as e:
                resp = {"error": str(e)}
            
            conn.sendall(json.dumps(resp).encode('utf-8'))
            conn.close()
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Error handling connection: {e}")

if __name__ == "__main__":
    run_server()
