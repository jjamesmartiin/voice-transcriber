#!/usr/bin/env python3
"""
Windows Host Global Hotkey Bridge
Runs on Windows to capture physical global hotkeys (Alt+Shift) across any active Windows application,
and notifies the NixOS WSL Transcription Daemon.
"""
import sys
import os
import json
import time
import socket
import logging

try:
    from pynput import keyboard as pynput_keyboard
    from pynput.keyboard import Key, KeyCode
except ImportError:
    print("[ERROR] pynput not installed on Windows Python. Run: pip install pynput")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - [Win-Bridge] %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BRIDGE_HOST = '127.0.0.1'
BRIDGE_PORT = 50555

def send_bridge_command(action, **kwargs):
    """Send command to WSL Daemon over local TCP socket"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect((BRIDGE_HOST, BRIDGE_PORT))
        payload = {"action": action, **kwargs}
        s.sendall(json.dumps(payload).encode('utf-8'))
        resp = s.recv(1024).decode('utf-8')
        s.close()
        return json.loads(resp)
    except Exception as e:
        logger.warning(f"Could not communicate with WSL daemon on {BRIDGE_HOST}:{BRIDGE_PORT}: {e}")
        return None

class WindowsBridgeListener:
    def __init__(self):
        self.pressed_keys = set()
        self.hotkey_active = False
        self.ALT_KEYS = {Key.alt_l, Key.alt_r, Key.alt}
        self.SHIFT_KEYS = {Key.shift_l, Key.shift_r, Key.shift}
        
    def _is_hotkey_pressed(self):
        has_alt = bool(self.pressed_keys & self.ALT_KEYS)
        has_shift = bool(self.pressed_keys & self.SHIFT_KEYS)
        return has_alt and has_shift

    def on_press(self, key):
        try:
            key_val = key if isinstance(key, KeyCode) else key
            self.pressed_keys.add(key_val)
            
            if self._is_hotkey_pressed() and not self.hotkey_active:
                self.hotkey_active = True
                logger.info("🎤 Alt+Shift pressed -> Starting recording in WSL...")
                send_bridge_command("start")
        except Exception as e:
            logger.error(f"Error in on_press: {e}")

    def on_release(self, key):
        try:
            key_val = key if isinstance(key, KeyCode) else key
            if key_val in self.pressed_keys:
                self.pressed_keys.remove(key_val)
                
            if self.hotkey_active and not self._is_hotkey_pressed():
                self.hotkey_active = False
                logger.info("🛑 Alt+Shift released -> Stopping & Transcribing in WSL...")
                send_bridge_command("stop", copy_clipboard=True)
        except Exception as e:
            logger.error(f"Error in on_release: {e}")

    def start(self):
        logger.info("=" * 60)
        logger.info("Windows Global Hotkey Bridge Active")
        logger.info("Hold Alt+Shift anywhere on Windows to record voice into NixOS WSL.")
        logger.info("=" * 60)
        with pynput_keyboard.Listener(on_press=self.on_press, on_release=self.on_release) as listener:
            listener.join()

if __name__ == "__main__":
    listener = WindowsBridgeListener()
    listener.start()
