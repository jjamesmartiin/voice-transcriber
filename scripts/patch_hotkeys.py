import re
with open('/home/jamesm/gitprojects/voice-transcriber/src/hotkeys.py', 'r') as f:
    content = f.read()

# Update __init__
content = content.replace(
    "def __init__(self, callback_start, callback_stop, callback_config=None):",
    "def __init__(self, callback_toggle, callback_config=None):"
)
content = content.replace(
    "self.callback_start = callback_start\n        self.callback_stop = callback_stop",
    "self.callback_toggle = callback_toggle"
)

# Add SPACE_KEY
content = content.replace(
    "self.SHIFT_KEYS = [42, 54]  # KEY_LEFTSHIFT, KEY_RIGHTSHIFT",
    "self.SHIFT_KEYS = [42, 54]  # KEY_LEFTSHIFT, KEY_RIGHTSHIFT\n        self.SPACE_KEY = [57]       # KEY_SPACE"
)

# Update is_hotkey_pressed
content = content.replace(
    """    def is_hotkey_pressed(self):
        \"\"\"Check if our hotkey combination (Alt+Shift) is currently pressed\"\"\"
        alt_pressed = any(self.key_states.get(key, False) for key in self.ALT_KEYS)
        shift_pressed = any(self.key_states.get(key, False) for key in self.SHIFT_KEYS)
        
        return alt_pressed and shift_pressed""",
    """    def is_hotkey_pressed(self):
        \"\"\"Check if our hotkey combination (Alt+Shift+Space) is currently pressed\"\"\"
        alt_pressed = any(self.key_states.get(key, False) for key in self.ALT_KEYS)
        shift_pressed = any(self.key_states.get(key, False) for key in self.SHIFT_KEYS)
        space_pressed = any(self.key_states.get(key, False) for key in self.SPACE_KEY)
        
        return alt_pressed and shift_pressed and space_pressed"""
)

# Update handle_key_event
content = content.replace(
    """        # Check for hotkey activation
        if self.is_hotkey_pressed() and not self.hotkey_active:
            logger.debug("Hotkey activated - starting recording")
            self.hotkey_active = True
            # Store if Ctrl was pressed when hotkey was activated
            self.copy_to_clipboard_mode = self.is_ctrl_pressed()
            self.callback_start()
        elif self.hotkey_active and self.is_hotkey_released():
            logger.debug("⏹️ Hotkey released - stopping recording")
            self.hotkey_active = False
            # Pass the mode to callback_stop
            self.callback_stop(copy_to_clipboard=self.copy_to_clipboard_mode)""",
    """        # Check for hotkey activation
        if self.is_hotkey_pressed() and not self.hotkey_active:
            logger.debug("Hotkey activated - toggling recording")
            self.hotkey_active = True
            self.copy_to_clipboard_mode = self.is_ctrl_pressed()
            self.callback_toggle(copy_to_clipboard=self.copy_to_clipboard_mode)
            
        elif self.hotkey_active and not self.is_hotkey_pressed():
            logger.debug("⏹️ Hotkey released")
            self.hotkey_active = False"""
)

# Update WSLGlobalHotkeys __init__
content = content.replace(
    "class WSLGlobalHotkeys:\n",
    "class WSLGlobalHotkeys:\n"
)
content = re.sub(
    r"def __init__\(self, callback_start, callback_stop, callback_config=None\):\n\s+self.callback_start = callback_start\n\s+self.callback_stop = callback_stop",
    "def __init__(self, callback_toggle, callback_config=None):\n        self.callback_toggle = callback_toggle",
    content
)

# Update WSL _reader_loop
content = content.replace(
    """                if event == "HOTKEY_DOWN":
                    self.hotkey_active = True
                    threading.Thread(target=self.callback_start, daemon=True).start()
                elif event == "HOTKEY_UP":
                    self.hotkey_active = False
                    threading.Thread(target=self.callback_stop, kwargs={"copy_to_clipboard": True}, daemon=True).start()""",
    """                if event == "HOTKEY_DOWN":
                    self.hotkey_active = True
                    threading.Thread(target=self.callback_toggle, kwargs={"copy_to_clipboard": False}, daemon=True).start()
                elif event == "HOTKEY_UP":
                    self.hotkey_active = False"""
)

# Update create_global_hotkeys
content = content.replace(
    "def create_global_hotkeys(callback_start, callback_stop, callback_config=None):",
    "def create_global_hotkeys(callback_toggle, callback_config=None):"
)
content = content.replace(
    "_current_hotkey_instance = WSLGlobalHotkeys(callback_start, callback_stop, callback_config)",
    "_current_hotkey_instance = WSLGlobalHotkeys(callback_toggle, callback_config)"
)
content = content.replace(
    "_current_hotkey_instance = WaylandGlobalHotkeys(callback_start, callback_stop, callback_config)",
    "_current_hotkey_instance = WaylandGlobalHotkeys(callback_toggle, callback_config)"
)

with open('/home/jamesm/gitprojects/voice-transcriber/src/hotkeys.py', 'w') as f:
    f.write(content)

