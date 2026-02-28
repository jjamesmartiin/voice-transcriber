#!/usr/bin/env python3
"""
Wayland Global Hotkeys Module
Wayland-compatible global hotkey system using evdev + uinput
"""
import logging
import threading
import time
import select
import sys

logger = logging.getLogger(__name__)

class WaylandGlobalHotkeys:
    """Wayland-compatible global hotkey system using evdev + uinput"""
    
    def __init__(self, callback_start, callback_stop, callback_config=None):
        self.callback_start = callback_start
        self.callback_stop = callback_stop
        self.callback_config = callback_config
        self.running = False
        self.devices = []
        self.virtual_keyboard = None
        self.key_states = {}
        self.hotkey_active = False
        
        # Key codes for our hotkey combination (Alt+Shift)
        self.ALT_KEYS = [56, 100]  # KEY_LEFTALT, KEY_RIGHTALT
        self.SHIFT_KEYS = [42, 54]  # KEY_LEFTSHIFT, KEY_RIGHTSHIFT
        
        # Key codes for config hotkey (Ctrl+Alt+I)
        self.CTRL_KEYS = [29, 97]   # KEY_LEFTCTRL, KEY_RIGHTCTRL
        self.KEY_I = [23]           # KEY_I
        
        self.init_devices()
    
    def init_devices(self):
        """Initialize evdev and uinput dependencies"""
        try:
            import evdev
            import uinput
            self.evdev = evdev
            self.uinput = uinput
        except ImportError as e:
            logger.error(f"Missing dependencies: {e}")
            logger.error("Install with: pip install evdev python-uinput")
            return False
            
        # Create virtual keyboard for sending events
        try:
            # Define the keys we might need to send
            events = (
                uinput.KEY_LEFTALT, uinput.KEY_RIGHTALT,
                uinput.KEY_LEFTSHIFT, uinput.KEY_RIGHTSHIFT,
                uinput.KEY_A, uinput.KEY_SPACE,
                # Add more keys as needed
            )
            self.virtual_keyboard = uinput.Device(events)
            logger.info("Created virtual keyboard device")
        except Exception as e:
            logger.warning(f"Could not create virtual keyboard: {e}")
            # Continue without virtual keyboard - we can still detect hotkeys
            
        # Initial scan
        return self.scan_for_devices()
        
    def _is_keyboard_device(self, device):
        """Check if a device looks like a keyboard we want to monitor"""
        try:
            caps = device.capabilities()
            if self.evdev.ecodes.EV_KEY not in caps:
                return False
                
            key_caps = caps[self.evdev.ecodes.EV_KEY]
            
            # More flexible keyboard detection
            has_letters = any(key in key_caps for key in [
                self.evdev.ecodes.KEY_A, self.evdev.ecodes.KEY_B, self.evdev.ecodes.KEY_C,
                self.evdev.ecodes.KEY_Q, self.evdev.ecodes.KEY_W, self.evdev.ecodes.KEY_E
            ])
            has_modifiers = any(key in key_caps for key in [
                self.evdev.ecodes.KEY_LEFTALT, self.evdev.ecodes.KEY_RIGHTALT,
                self.evdev.ecodes.KEY_LEFTSHIFT, self.evdev.ecodes.KEY_RIGHTSHIFT,
                self.evdev.ecodes.KEY_LEFTCTRL, self.evdev.ecodes.KEY_RIGHTCTRL
            ])
            has_space_enter = any(key in key_caps for key in [
                self.evdev.ecodes.KEY_SPACE, self.evdev.ecodes.KEY_ENTER
            ])
            
            # Check if it has our specific hotkey keys
            has_alt = any(key in key_caps for key in [self.evdev.ecodes.KEY_LEFTALT, self.evdev.ecodes.KEY_RIGHTALT])
            has_shift = any(key in key_caps for key in [self.evdev.ecodes.KEY_LEFTSHIFT, self.evdev.ecodes.KEY_RIGHTSHIFT])
            
            # Accept device if it looks like a keyboard and has our hotkey keys
            return (has_letters or has_modifiers or has_space_enter) and has_alt and has_shift
        except Exception:
            return False

    def scan_for_devices(self):
        """Scan for new keyboard devices"""
        try:
            evdev = self.evdev
            
            # Get current device paths to avoid re-opening
            current_paths = set(d.path for d in self.devices)
            
            # Try to get devices from evdev.list_devices() first
            device_paths = evdev.list_devices()
            
            # Also try to manually check common event devices
            import glob
            all_event_paths = glob.glob('/dev/input/event*')
            for path in all_event_paths:
                if path not in device_paths:
                    device_paths.append(path)
            
            new_devices = []
            
            for path in device_paths:
                if path in current_paths:
                    continue
                    
                try:
                    device = evdev.InputDevice(path)
                    if self._is_keyboard_device(device):
                        new_devices.append(device)
                        logger.info(f"Found new keyboard device: {device.name} at {device.path}")
                    else:
                        device.close()
                except (PermissionError, OSError):
                    continue
            
            if new_devices:
                self.devices.extend(new_devices)
                logger.info(f"Added {len(new_devices)} new device(s). Total: {len(self.devices)}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error scanning for devices: {e}")
            return False
    
    def is_hotkey_pressed(self):
        """Check if our hotkey combination (Alt+Shift) is currently pressed"""
        alt_pressed = any(self.key_states.get(key, False) for key in self.ALT_KEYS)
        shift_pressed = any(self.key_states.get(key, False) for key in self.SHIFT_KEYS)
        
        return alt_pressed and shift_pressed
    
    def is_config_hotkey_pressed(self):
        """Check if config hotkey (Ctrl+Alt+I) is currently pressed"""
        alt_pressed = any(self.key_states.get(key, False) for key in self.ALT_KEYS)
        ctrl_pressed = any(self.key_states.get(key, False) for key in self.CTRL_KEYS)
        i_pressed = any(self.key_states.get(key, False) for key in self.KEY_I)
        
        return alt_pressed and ctrl_pressed and i_pressed
    
    def is_hotkey_released(self):
        """Check if hotkey combination is no longer fully pressed"""
        alt_pressed = any(self.key_states.get(key, False) for key in self.ALT_KEYS)
        shift_pressed = any(self.key_states.get(key, False) for key in self.SHIFT_KEYS)
        
        return not (alt_pressed and shift_pressed)
    
    def handle_key_event(self, event):
        """Handle a key event and check for hotkey activation"""
        if event.type != self.evdev.ecodes.EV_KEY:
            return
        
        key_code = event.code
        key_state = event.value  # 1 = press, 0 = release, 2 = repeat
        
        # Update key state tracking
        if key_state in [0, 1]:  # Only track press/release, ignore repeat
            self.key_states[key_code] = (key_state == 1)
        
        # Check for config hotkey (Ctrl + Alt + I)
        if key_state == 1 and self.is_config_hotkey_pressed() and self.callback_config:
            logger.debug("⚙️ Config hotkey activated")
            self.callback_config()
            self.key_states.clear()
            return

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
        self.running = True
        logger.info("Started hotkey monitor loop")
        
        last_scan_time = 0
        scan_interval = 5.0  # Seconds between scans when no devices found
        
        while self.running:
            try:
                # Periodic device scan
                current_time = time.time()
                if current_time - last_scan_time > scan_interval:
                    # Scan for new devices periodically
                    if self.scan_for_devices():
                         # If we found new devices, we might need to update our select list
                         pass
                    last_scan_time = current_time
                
                if not self.devices:
                    time.sleep(0.5)
                    continue
                        
                # Monitor existing devices
                devices_map = {dev.fd: dev for dev in self.devices if dev.fd is not None}
                
                if not devices_map:
                    # Devices might have been closed/lost
                    if self.devices: # If we had devices but map is empty
                        logger.warning("Devices lost (fd invalid). clearing list.")
                    self.devices = []
                    self.key_states.clear() # Clear potential stuck keys
                    continue
                
                r, w, x = select.select(devices_map, [], [], 1.0)
                
                for fd in r:
                    device = devices_map[fd]
                    try:
                        for event in device.read():
                            self.handle_key_event(event)
                    except OSError as e:
                        # Check for device disconnection (Errno 19: No such device)
                        # extended check because sometimes errno might be missing or different wrapper
                        is_disconnect = (e.errno == 19) or ("No such device" in str(e))
                        
                        if is_disconnect:
                            logger.warning(f"Device disconnected: {device.name}")
                        else:
                            logger.warning(f"Device {device.path} error: {e}")
                        
                        # Remove disconnected device
                        if device in self.devices:
                            self.devices.remove(device)
                            try:
                                device.close()
                            except:
                                pass
                        
                        # If we lost all devices, clear state immediately
                        if not self.devices:
                             self.key_states.clear()
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
