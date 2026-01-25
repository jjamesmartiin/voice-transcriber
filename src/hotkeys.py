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
                    
                    # Accept device if it looks like a keyboard and has our hotkey keys
                    if (has_letters or has_modifiers or has_space_enter) and has_alt and has_shift:
                        keyboards.append(device)
                        logger.info(f"Found keyboard: {device.name} at {device.path}")
                        logger.debug(f"  Device capabilities: letters={has_letters}, modifiers={has_modifiers}, space/enter={has_space_enter}")
                    else:
                        logger.debug(f"Skipping device: {device.name} (missing required keys)")
                        logger.debug(f"  Has letters: {has_letters}, modifiers: {has_modifiers}, space/enter: {has_space_enter}")
                        logger.debug(f"  Has Alt: {has_alt}, Shift: {has_shift}")
            
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
                    uinput.KEY_A, uinput.KEY_SPACE,
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
        # We check this first to allow accessing config even if something else is going on
        if key_state == 1 and self.is_config_hotkey_pressed() and self.callback_config:
            logger.debug("⚙️ Config hotkey activated")
            self.callback_config()
            # Clear key states to prevent stuck keys after menu interaction
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
        if not self.devices:
            logger.error("No devices available for monitoring")
            return False
        
        self.running = True
        logger.info(f"Monitoring {len(self.devices)} keyboard device(s) for Alt+Shift")
        
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
