#!/usr/bin/env python3
"""
Windows Global Hotkeys Module
Uses pynput for Alt+Shift recording, keyboard library for Ctrl+Alt+I menu
"""
import logging
import threading
import time
import sys

logger = logging.getLogger(__name__)

try:
    from pynput import keyboard as pynput_keyboard
    from pynput.keyboard import Key, KeyCode
except ImportError:
    logger.error("pynput not installed. Run: pip install pynput")
    raise

try:
    import keyboard as kb_lib
except ImportError:
    logger.error("keyboard not installed. Run: pip install keyboard")
    raise

class WindowsGlobalHotkeys:
    """Windows-compatible global hotkey system using pynput + keyboard library"""
    
    def __init__(self, callback_start, callback_stop, callback_config=None):
        self.callback_start = callback_start
        self.callback_stop = callback_stop
        self.callback_config = callback_config
        self.running = False
        self.hotkey_active = False
        self.copy_to_clipboard_mode = False
        
        self.pressed_keys = set()
        
        self.ALT_KEYS = {Key.alt_l, Key.alt_r}
        self.SHIFT_KEYS = {Key.shift_l, Key.shift_r}
        self.CTRL_KEYS = {Key.ctrl_l, Key.ctrl_r}
        
        self.devices = [True]
        
        self._start_listener()
    
    def _start_listener(self):
        """Start the keyboard listener"""
        try:
            self.listener = pynput_keyboard.Listener(
                on_press=self._on_press,
                on_release=self._on_release
            )
            self.listener.start()
            
            kb_lib.on_press(self._on_config_press, suppress=False)
            
            logger.info("Windows hotkey listener started")
        except Exception as e:
            logger.error(f"Failed to start keyboard listener: {e}")
            self.devices = []
    
    def _on_config_press(self, e):
        """Handle Ctrl+Alt+I for config menu using keyboard library"""
        key_name = e.name
        if key_name == 'i' and (kb_lib.is_pressed('ctrl') or kb_lib.is_pressed('left ctrl') or kb_lib.is_pressed('right ctrl')) and (kb_lib.is_pressed('alt') or kb_lib.is_pressed('left alt') or kb_lib.is_pressed('right alt')):
            logger.info("⚙️  Config hotkey (Ctrl+Alt+I) activated")
            if self.callback_config:
                self.callback_config()
    
    def _on_press(self, key):
        """Handle key press using pynput"""
        try:
            key_val = key if isinstance(key, KeyCode) else key
            self.pressed_keys.add(key_val)
            
            if self._is_main_hotkey_pressed() and not self.hotkey_active:
                logger.info("🎤 Starting recording...")
                self.hotkey_active = True
                self.copy_to_clipboard_mode = Key.ctrl_l in self.pressed_keys or Key.ctrl_r in self.pressed_keys
                if self.callback_start:
                    self.callback_start()
                    
        except Exception as e:
            logger.error(f"Error in key press handler: {e}")
    
    def _on_release(self, key):
        """Handle key release using pynput"""
        try:
            key_val = key if isinstance(key, KeyCode) else key
            if key_val in self.pressed_keys:
                self.pressed_keys.remove(key_val)
            
            if self.hotkey_active and not self._is_main_hotkey_pressed():
                logger.info("🛑 Stopping recording...")
                self.hotkey_active = False
                if self.callback_stop:
                    self.callback_stop(copy_to_clipboard=self.copy_to_clipboard_mode)
                    
        except Exception as e:
            logger.error(f"Error in key release handler: {e}")
    
    def _is_main_hotkey_pressed(self):
        """Check if Alt+Shift is pressed"""
        alt_pressed = bool(self.pressed_keys & self.ALT_KEYS)
        shift_pressed = bool(self.pressed_keys & self.SHIFT_KEYS)
        return alt_pressed and shift_pressed
    
    def are_modifiers_pressed(self):
        """Check if any modifier keys are still pressed"""
        return bool(self.pressed_keys & (self.ALT_KEYS | self.SHIFT_KEYS | self.CTRL_KEYS))
    
    def type_text(self, text):
        """Type text using keyboard library"""
        try:
            kb_lib.write(text)
            return True
        except Exception as e:
            logger.error(f"Error typing text: {e}")
            return False
    
    def run(self):
        """Main run loop"""
        self.running = True
        logger.info("Started Windows hotkey monitor")
        
        try:
            while self.running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
        
        return True
    
    def stop(self):
        """Stop the hotkey monitoring"""
        self.running = False
        if self.listener:
            try:
                self.listener.stop()
            except:
                pass
        self.listener = None
        self.pressed_keys.clear()
        try:
            kb_lib.unhook_all()
        except:
            pass

WaylandGlobalHotkeys = WindowsGlobalHotkeys