"""Native Windows global hotkey backend (pynput + keyboard).

Ported from ``main-windows:src/hotkeys_windows.py``. Third-party imports are
performed lazily inside ``__init__`` so importing this module on Linux/WSL
never requires ``pynput`` or the ``keyboard`` library.
"""
from __future__ import annotations

import logging
import time

from ..base import BaseHotkeyManager

logger = logging.getLogger(__name__)


class WindowsHotkeyManager(BaseHotkeyManager):
    """Windows push-to-talk hotkeys using ``pynput`` (+ ``keyboard`` for menu)."""

    def __init__(self, callback_start, callback_stop, callback_config=None):
        super().__init__(callback_start, callback_stop, callback_config)
        self.listener = None
        self.pressed_keys = set()
        self._kb_lib = None
        self._Key = None
        self._KeyCode = None
        self._pynput_keyboard = None
        self.ALT_KEYS = set()
        self.SHIFT_KEYS = set()
        self.CTRL_KEYS = set()
        self._available = False

        self._start_listener()

    # -- setup -------------------------------------------------------------
    def _start_listener(self):
        """Start the keyboard listener, degrading gracefully if deps are absent."""
        try:
            from pynput import keyboard as pynput_keyboard
            from pynput.keyboard import Key, KeyCode
        except ImportError as e:
            logger.error(f"pynput not installed. Run: pip install pynput ({e})")
            self.devices = []
            return
        try:
            import keyboard as kb_lib
        except ImportError as e:
            logger.error(f"keyboard not installed. Run: pip install keyboard ({e})")
            self.devices = []
            return

        self._pynput_keyboard = pynput_keyboard
        self._Key = Key
        self._KeyCode = KeyCode
        self._kb_lib = kb_lib

        self.ALT_KEYS = {Key.alt_l, Key.alt_r}
        self.SHIFT_KEYS = {Key.shift_l, Key.shift_r}
        self.CTRL_KEYS = {Key.ctrl_l, Key.ctrl_r}

        try:
            self.listener = pynput_keyboard.Listener(
                on_press=self._on_press,
                on_release=self._on_release,
            )
            self.listener.start()
            kb_lib.on_press(self._on_config_press, suppress=False)
            self.devices = [True]
            self._available = True
            logger.info("Windows hotkey listener started")
        except Exception as e:
            logger.error(f"Failed to start keyboard listener: {e}")
            self.devices = []

    # -- handlers ----------------------------------------------------------
    def _on_config_press(self, e):
        """Handle Ctrl+Alt+I for the config menu using the keyboard library."""
        try:
            key_name = e.name
        except AttributeError:
            return
        kb_lib = self._kb_lib
        if key_name == "i" and (
            kb_lib.is_pressed("ctrl")
            or kb_lib.is_pressed("left ctrl")
            or kb_lib.is_pressed("right ctrl")
        ) and (
            kb_lib.is_pressed("alt")
            or kb_lib.is_pressed("left alt")
            or kb_lib.is_pressed("right alt")
        ):
            logger.info("⚙️  Config hotkey (Ctrl+Alt+I) activated")
            if self.callback_config:
                self.callback_config()

    def _on_press(self, key):
        try:
            key_val = key if isinstance(key, self._KeyCode) else key
            self.pressed_keys.add(key_val)

            if self._is_main_hotkey_pressed() and not self.hotkey_active:
                logger.info("🎤 Starting recording...")
                self.hotkey_active = True
                self.copy_to_clipboard_mode = (
                    self._Key.ctrl_l in self.pressed_keys
                    or self._Key.ctrl_r in self.pressed_keys
                )
                if self.callback_start:
                    self.callback_start()
        except Exception as e:
            logger.error(f"Error in key press handler: {e}")

    def _on_release(self, key):
        try:
            key_val = key if isinstance(key, self._KeyCode) else key
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
        alt_pressed = bool(self.pressed_keys & self.ALT_KEYS)
        shift_pressed = bool(self.pressed_keys & self.SHIFT_KEYS)
        return alt_pressed and shift_pressed

    # -- state queries -----------------------------------------------------
    def are_modifiers_pressed(self):
        return bool(self.pressed_keys & (self.ALT_KEYS | self.SHIFT_KEYS | self.CTRL_KEYS))

    def is_hotkey_pressed(self):
        return self.hotkey_active

    # -- output ------------------------------------------------------------
    def type_text(self, text):
        """Type text using the keyboard library."""
        if not self._kb_lib:
            return False
        try:
            self._kb_lib.write(text)
            return True
        except Exception as e:
            logger.error(f"Error typing text: {e}")
            return False

    def run(self):
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
        self.running = False
        if self.listener:
            try:
                self.listener.stop()
            except Exception:
                pass
        self.listener = None
        self.pressed_keys.clear()
        if self._kb_lib:
            try:
                self._kb_lib.unhook_all()
            except Exception:
                pass


# Backwards-compatible alias used by the original ``hotkeys_windows.py``.
WindowsGlobalHotkeys = WindowsHotkeyManager
