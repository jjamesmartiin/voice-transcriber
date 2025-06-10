#!/usr/bin/env python3
"""
Global Hotkey System Module
Handles global hotkey detection using evdev for Wayland/X11 compatibility.
"""

import logging
import threading
import select
import time
import os
import grp
import pwd

logger = logging.getLogger(__name__)

# Enable debug logging for troubleshooting hotkey issues
hotkey_logger = logging.getLogger(__name__ + '.hotkeys')
hotkey_logger.setLevel(logging.DEBUG)

class WaylandGlobalHotkeys:
    """Global hotkey system using evdev"""
    
    def __init__(self, callback_start, callback_stop):
        self.callback_start = callback_start
        self.callback_stop = callback_stop
        self.running = False
        self.devices = []
        self.key_states = {}
        self.hotkey_active = False
        
        self.ALT_KEYS = [56, 100]  # KEY_LEFTALT, KEY_RIGHTALT
        self.SHIFT_KEYS = [42, 54]  # KEY_LEFTSHIFT, KEY_RIGHTSHIFT
        
        self.init_devices()
    
    def init_devices(self):
        try:
            import evdev
            self.evdev = evdev
        except ImportError as e:
            logger.error(f"Missing evdev: {e}")
            return False
        
        try:
            device_paths = evdev.list_devices()
            devices = []
            keyboards = []
            
            for path in device_paths:
                try:
                    device = evdev.InputDevice(path)
                    devices.append(device)
                except (PermissionError, OSError):
                    continue
            
            for device in devices:
                caps = device.capabilities()
                if evdev.ecodes.EV_KEY in caps:
                    key_caps = caps[evdev.ecodes.EV_KEY]
                    
                    has_alt = any(key in key_caps for key in [evdev.ecodes.KEY_LEFTALT, evdev.ecodes.KEY_RIGHTALT])
                    has_shift = any(key in key_caps for key in [evdev.ecodes.KEY_LEFTSHIFT, evdev.ecodes.KEY_RIGHTSHIFT])
                    
                    if has_alt and has_shift:
                        keyboards.append(device)
                        logger.info(f"Found keyboard: {device.name}")
            
            if not keyboards:
                logger.error("No suitable keyboard devices found")
                return False
                
            self.devices = keyboards
            return True
            
        except PermissionError:
            logger.error("Permission denied accessing input devices")
            logger.error("Run as root or add user to input group: sudo usermod -a -G input $USER")
            return False
        except Exception as e:
            logger.error(f"Error initializing devices: {e}")
            return False
    
    def is_hotkey_pressed(self):
        alt_pressed = any(self.key_states.get(key, False) for key in self.ALT_KEYS)
        shift_pressed = any(self.key_states.get(key, False) for key in self.SHIFT_KEYS)
        result = alt_pressed and shift_pressed
        
        # Debug log the key states occasionally
        if hasattr(self, '_debug_counter'):
            self._debug_counter += 1
        else:
            self._debug_counter = 0
            
        if self._debug_counter % 100 == 0:  # Log every 100 calls to avoid spam
            hotkey_logger.debug(f"Key states - Alt: {alt_pressed}, Shift: {shift_pressed}, Hotkey: {result}")
            
        return result
    
    def is_hotkey_released(self):
        alt_pressed = any(self.key_states.get(key, False) for key in self.ALT_KEYS)
        shift_pressed = any(self.key_states.get(key, False) for key in self.SHIFT_KEYS)
        return not (alt_pressed and shift_pressed)
    
    def handle_key_event(self, event):
        if event.type != self.evdev.ecodes.EV_KEY:
            return
        
        key_code = event.code
        key_state = event.value  # 0=release, 1=press, 2=repeat
        
        # Handle press and release (ignore repeat events for now)
        if key_state in [0, 1]:
            self.key_states[key_code] = (key_state == 1)
            
            # Debug key state changes for Alt and Shift keys
            if key_code in self.ALT_KEYS + self.SHIFT_KEYS:
                key_name = "ALT" if key_code in self.ALT_KEYS else "SHIFT"
                state_name = "PRESSED" if key_state == 1 else "RELEASED"
                hotkey_logger.debug(f"{key_name} key {state_name} (code: {key_code})")
        
        # Check if hotkey combination is now active
        hotkey_currently_pressed = self.is_hotkey_pressed()
        
        if hotkey_currently_pressed and not self.hotkey_active:
            hotkey_logger.debug("Hotkey combination activated - starting recording")
            self.hotkey_active = True
            self.callback_start()
        elif not hotkey_currently_pressed and self.hotkey_active:
            hotkey_logger.debug("Hotkey combination released - stopping recording")
            self.hotkey_active = False
            self.callback_stop()
    
    def run(self):
        if not self.devices:
            return False
        
        self.running = True
        logger.info(f"Monitoring {len(self.devices)} keyboard device(s)")
        
        while self.running:
            try:
                devices_map = {dev.fd: dev for dev in self.devices if dev.fd is not None}
                
                if not devices_map:
                    break
                
                r, w, x = select.select(devices_map, [], [], 1.0)
                
                for fd in r:
                    device = devices_map[fd]
                    try:
                        for event in device.read():
                            self.handle_key_event(event)
                    except OSError:
                        if device in self.devices:
                            self.devices.remove(device)
                        continue
                        
            except Exception as e:
                logger.error(f"Error in event loop: {e}")
                time.sleep(1)
        
        return True
    
    def stop(self):
        self.running = False

def check_permissions():
    """Check permissions for input device access"""
    if os.geteuid() == 0:
        logger.info("✅ Running as root")
        return True
    
    try:
        current_user = pwd.getpwuid(os.getuid()).pw_name
        current_gids = os.getgroups()
        input_group = grp.getgrnam('input')
        
        if input_group.gr_gid in current_gids:
            logger.info("✅ User is in input group")
            return True
        else:
            logger.error("❌ Permission issue!")
            logger.error(f"👤 Current user: {current_user}")
            logger.error("🔧 Fix with: sudo usermod -a -G input $USER")
            logger.error("   Then log out and back in")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error checking permissions: {e}")
        return False 