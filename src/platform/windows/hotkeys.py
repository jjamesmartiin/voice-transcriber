"""Native Windows global hotkey backend (pynput + keyboard).

Ported from ``main-windows:src/hotkeys_windows.py``. Third-party imports are
performed lazily inside ``__init__`` so importing this module on Linux/WSL
never requires ``pynput`` or the ``keyboard`` library.
"""
from __future__ import annotations

import logging
import threading
import time

from ..base import BaseHotkeyManager

logger = logging.getLogger(__name__)


class WindowsHotkeyManager(BaseHotkeyManager):
    """Windows push-to-talk hotkeys using ``pynput`` (+ ``keyboard`` for menu)."""

    def __init__(self, callback_start, callback_stop, callback_config=None):
        super().__init__(callback_start, callback_stop, callback_config)
        self.listener = None
        self.mouse_listener = None
        self.pressed_keys = set()
        self._kb_lib = None
        self._Key = None
        self._KeyCode = None
        self._pynput_keyboard = None
        self._pynput_mouse = None
        self.ALT_KEYS = set()
        self.SHIFT_KEYS = set()
        self.CTRL_KEYS = set()
        self._available = False

        self.MIDDLE_CLICK_HOLD_DELAY = 0.25
        self._middle_click_timer = None
        self.middle_click_active = False
        self.middle_pressed = False
        self._lock = threading.Lock()

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

        try:
            from pynput import mouse as pynput_mouse

            self._pynput_mouse = pynput_mouse
        except ImportError:
            self._pynput_mouse = None

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

            if self._pynput_mouse:
                try:
                    self.mouse_listener = self._pynput_mouse.Listener(
                        on_click=self._on_mouse_click,
                    )
                    self.mouse_listener.start()
                except Exception as e:
                    logger.debug(f"Could not start Windows mouse listener: {e}")
                    self.mouse_listener = None

            self.devices = [True]
            self._available = True
            logger.info("Windows hotkey listener started")
        except Exception as e:
            logger.error(f"Failed to start keyboard listener: {e}")
            self.devices = []

    # -- handlers ----------------------------------------------------------
    def set_middle_click_enabled(self, enabled: bool):
        """Toggle middle click push-to-talk mode."""
        with self._lock:
            self.middle_click_enabled = bool(enabled)
            if not self.middle_click_enabled:
                if self._middle_click_timer:
                    self._middle_click_timer.cancel()
                    self._middle_click_timer = None
                if self.middle_click_active:
                    self.middle_click_active = False
                    if not self._is_main_hotkey_pressed():
                        self.hotkey_active = False
                        if self.callback_stop:
                            self.callback_stop(copy_to_clipboard=self.copy_to_clipboard_mode)

    def _on_mouse_click(self, x, y, button, pressed):
        try:
            if not self.middle_click_enabled or not self._pynput_mouse:
                return

            if button != self._pynput_mouse.Button.middle:
                return

            with self._lock:
                self.middle_pressed = pressed
                if pressed:
                    if not self.hotkey_active:
                        if self._middle_click_timer:
                            self._middle_click_timer.cancel()
                        self._middle_click_timer = threading.Timer(
                            self.MIDDLE_CLICK_HOLD_DELAY,
                            self._on_middle_click_hold_timeout,
                        )
                        self._middle_click_timer.daemon = True
                        self._middle_click_timer.start()
                else:
                    if self._middle_click_timer:
                        self._middle_click_timer.cancel()
                        self._middle_click_timer = None
                    if self.middle_click_active:
                        self.middle_click_active = False
                        if not self._is_main_hotkey_pressed():
                            self.hotkey_active = False
                            if self.callback_stop:
                                self.callback_stop(copy_to_clipboard=self.copy_to_clipboard_mode)
        except Exception as e:
            logger.error(f"Error in mouse click handler: {e}")

    def _on_middle_click_hold_timeout(self):
        try:
            with self._lock:
                if self._middle_click_timer is None:
                    return
                if self.middle_pressed and not self.hotkey_active:
                    self.hotkey_active = True
                    self.middle_click_active = True
                    self.copy_to_clipboard_mode = (
                        self._Key is not None
                        and (
                            self._Key.ctrl_l in self.pressed_keys
                            or self._Key.ctrl_r in self.pressed_keys
                        )
                    )
                    if self.callback_start:
                        self.callback_start()
        except Exception as e:
            logger.error(f"Error in middle click timeout handler: {e}")

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

            if self.hotkey_active and not self.middle_click_active and not self._is_main_hotkey_pressed():
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
        return bool(self.pressed_keys & (self.ALT_KEYS | self.SHIFT_KEYS | self.CTRL_KEYS)) or self.middle_pressed

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
        with self._lock:
            if self._middle_click_timer:
                try:
                    self._middle_click_timer.cancel()
                except Exception:
                    pass
                self._middle_click_timer = None
            self.middle_click_active = False
            self.middle_pressed = False
        if self.listener:
            try:
                self.listener.stop()
            except Exception:
                pass
        self.listener = None
        if self.mouse_listener:
            try:
                self.mouse_listener.stop()
            except Exception:
                pass
        self.mouse_listener = None
        self.pressed_keys.clear()
        if self._kb_lib:
            try:
                self._kb_lib.unhook_all()
            except Exception:
                pass


# Backwards-compatible alias used by the original ``hotkeys_windows.py``.
WindowsGlobalHotkeys = WindowsHotkeyManager
