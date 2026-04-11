#!/usr/bin/env python3
"""
Windows Global Hotkeys Module
Uses pynput for cross-platform global hotkey support on Windows
"""
import logging
import threading
import time
import sys

logger = logging.getLogger(__name__)

try:
    from pynput import keyboard
    from pynput.keyboard import Key, KeyCode
except ImportError:
    logger.error("pynput not installed. Run: pip install pynput")
    raise

class WindowsGlobalHotkeys:
    """Windows-compatible global hotkey system using pynput"""
    
    def __init__(self, callback_start, callback_stop, callback_config=None):
        self.callback_start = callback_start
        self.callback_stop = callback_stop
        self.callback_config = callback_config
        self.running = False
        self.listener = None
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
            self.listener = keyboard.Listener(
                on_press=self._on_press,
                on_release=self._on_release
            )
            self.listener.start()
            logger.info("Windows hotkey listener started")
        except Exception as e:
            logger.error(f"Failed to start keyboard listener: {e}")
            self.devices = []
    
    def _on_press(self, key):
        """Handle key press"""
        try:
            key_val = key if isinstance(key, KeyCode) else key
            self.pressed_keys.add(key_val)
            
            if self._is_config_hotkey_pressed():
                if self.callback_config:
                    logger.debug("Config hotkey activated")
                    self.callback_config()
                self.pressed_keys.clear()
                return
            
            if self._is_main_hotkey_pressed() and not self.hotkey_active:
                logger.debug("Hotkey activated - starting recording")
                self.hotkey_active = True
                self.copy_to_clipboard_mode = Key.ctrl_l in self.pressed_keys or Key.ctrl_r in self.pressed_keys
                self.callback_start()
                
        except Exception as e:
            logger.error(f"Error in key press handler: {e}")
    
    def _on_release(self, key):
        """Handle key release"""
        try:
            key_val = key if isinstance(key, KeyCode) else key
            if key_val in self.pressed_keys:
                self.pressed_keys.remove(key_val)
            
            if self.hotkey_active and not self._is_main_hotkey_pressed():
                logger.debug("Hotkey released - stopping recording")
                self.hotkey_active = False
                self.callback_stop(copy_to_clipboard=self.copy_to_clipboard_mode)
                
        except Exception as e:
            logger.error(f"Error in key release handler: {e}")
    
    def _is_main_hotkey_pressed(self):
        """Check if Alt+Shift is pressed"""
        alt_pressed = bool(self.pressed_keys & self.ALT_KEYS)
        shift_pressed = bool(self.pressed_keys & self.SHIFT_KEYS)
        return alt_pressed and shift_pressed
    
    def _is_config_hotkey_pressed(self):
        """Check if Ctrl+Alt+I is pressed"""
        alt_pressed = bool(self.pressed_keys & self.ALT_KEYS)
        ctrl_pressed = bool(self.pressed_keys & self.CTRL_KEYS)
        i_pressed = any(
            (isinstance(k, KeyCode) and getattr(k, 'char', None) == 'i')
            for k in self.pressed_keys
        )
        return alt_pressed and ctrl_pressed and i_pressed
    
    def are_modifiers_pressed(self):
        """Check if any modifier keys are still pressed"""
        return bool(self.pressed_keys & (self.ALT_KEYS | self.SHIFT_KEYS | self.CTRL_KEYS))
    
    def type_text(self, text):
        """Type text using pynput keyboard"""
        try:
            from pynput.keyboard import Controller
            keyboard_controller = Controller()
            
            for char in text:
                try:
                    keyboard_controller.type(char)
                except:
                    pass
                time.sleep(0.01)
            
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

# Alias for compatibility with main.py import
WaylandGlobalHotkeys = WindowsGlobalHotkeys