"""macOS global hotkey backend using pynput.

Hold ``Cmd+Shift`` or ``Alt/Option+Shift`` to record (push-to-talk), tap ``Space``
while holding to latch hands-free. Middle mouse button hold is supported when
enabled. Accessibility permissions are validated on launch.
"""
from __future__ import annotations

import logging
import sys
import threading
import time

from ..base import BaseHotkeyManager

logger = logging.getLogger(__name__)


def check_accessibility_permissions() -> bool:
    """Check whether macOS Accessibility permissions are granted.

    Uses ``AXIsProcessTrusted`` from the ApplicationServices framework via ctypes.
    Returns True if granted or if the check cannot be performed (e.g. non-Darwin).
    """
    try:
        import ctypes
        import ctypes.util

        lib = (
            ctypes.util.find_library("ApplicationServices")
            or "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        app_services = ctypes.cdll.LoadLibrary(lib)
        app_services.AXIsProcessTrusted.restype = ctypes.c_bool
        app_services.AXIsProcessTrusted.argtypes = []
        return bool(app_services.AXIsProcessTrusted())
    except Exception:
        return True


class MacOSHotkeyManager(BaseHotkeyManager):
    """macOS push-to-talk hotkeys using ``pynput``."""

    platform = "macos"

    def __init__(self, callback_start, callback_stop):
        super().__init__(callback_start, callback_stop)
        self.listener = None
        self.mouse_listener = None
        self.pressed_keys = set()
        self._Key = None
        self._KeyCode = None
        self._pynput_keyboard = None
        self._pynput_mouse = None

        self.CMD_KEYS = set()
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
        """Start the keyboard and mouse listeners, checking permissions."""
        if sys.platform == "darwin" and not check_accessibility_permissions():
            logger.warning(
                "macOS Accessibility permission not granted. Global hotkeys require permission. "
                "Please enable Voice Transcriber in: System Settings -> Privacy & Security -> Accessibility."
            )

        try:
            from pynput import keyboard as pynput_keyboard
            from pynput.keyboard import Key, KeyCode
        except ImportError as e:
            logger.error(f"pynput not installed. Run: pip install pynput ({e})")
            self.devices = []
            return

        self._pynput_keyboard = pynput_keyboard
        self._Key = Key
        self._KeyCode = KeyCode

        try:
            from pynput import mouse as pynput_mouse

            self._pynput_mouse = pynput_mouse
        except ImportError:
            self._pynput_mouse = None

        self.CMD_KEYS = set()
        for k in ("cmd", "cmd_l", "cmd_r"):
            if hasattr(Key, k):
                self.CMD_KEYS.add(getattr(Key, k))

        self.ALT_KEYS = set()
        for k in ("alt", "alt_l", "alt_r", "alt_gr"):
            if hasattr(Key, k):
                self.ALT_KEYS.add(getattr(Key, k))

        self.SHIFT_KEYS = set()
        for k in ("shift", "shift_l", "shift_r"):
            if hasattr(Key, k):
                self.SHIFT_KEYS.add(getattr(Key, k))

        self.CTRL_KEYS = set()
        for k in ("ctrl", "ctrl_l", "ctrl_r"):
            if hasattr(Key, k):
                self.CTRL_KEYS.add(getattr(Key, k))

        try:
            self.listener = pynput_keyboard.Listener(
                on_press=self._on_press,
                on_release=self._on_release,
            )
            self.listener.start()

            if self._pynput_mouse:
                try:
                    self.mouse_listener = self._pynput_mouse.Listener(
                        on_click=self._on_mouse_click,
                    )
                    self.mouse_listener.start()
                except Exception as e:
                    logger.debug(f"Could not start macOS mouse listener: {e}")
                    self.mouse_listener = None

            self.devices = [True]
            self._available = True
            logger.info("macOS hotkey listener started")
        except Exception as e:
            logger.error(f"Failed to start macOS hotkey listener: {e}")
            logger.error(
                "If this is a permission error, grant Accessibility permission in "
                "System Settings -> Privacy & Security -> Accessibility."
            )
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
                            if self.latch_release:
                                logger.debug("Space-latched release - continuing recording hands-free")
                                self.latch_release = False
                            elif self.callback_stop:
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
                    self.copy_to_clipboard_mode = bool(self.pressed_keys & self.CTRL_KEYS)
                    if self.callback_start:
                        self.callback_start()
        except Exception as e:
            logger.error(f"Error in middle click timeout handler: {e}")

    def _on_press(self, key):
        try:
            key_val = key if (isinstance(self._KeyCode, type) and isinstance(key, self._KeyCode)) else key
            self.pressed_keys.add(key_val)

            # Space pressed while recording engages hands-free latch
            is_space = (self._Key and key == self._Key.space) or (getattr(key, "char", None) == " ")
            if is_space and self.hotkey_active:
                logger.debug("Space latched - recording will hold after release")
                self.latch_release = True

            if self._is_main_hotkey_pressed() and not self.hotkey_active:
                logger.info("Starting recording...")
                self.hotkey_active = True
                self.copy_to_clipboard_mode = bool(self.pressed_keys & self.CTRL_KEYS)
                if self.callback_start:
                    self.callback_start()
        except Exception as e:
            logger.error(f"Error in key press handler: {e}")

    def _on_release(self, key):
        try:
            key_val = key if (isinstance(self._KeyCode, type) and isinstance(key, self._KeyCode)) else key
            if key_val in self.pressed_keys:
                self.pressed_keys.remove(key_val)

            if self.hotkey_active and not self.middle_click_active and not self._is_main_hotkey_pressed():
                self.hotkey_active = False
                if self.latch_release:
                    logger.debug("Space-latched release - continuing recording hands-free")
                    self.latch_release = False
                else:
                    logger.info("Stopping recording...")
                    if self.callback_stop:
                        self.callback_stop(copy_to_clipboard=self.copy_to_clipboard_mode)
        except Exception as e:
            logger.error(f"Error in key release handler: {e}")

    def _is_main_hotkey_pressed(self):
        mod_pressed = bool(self.pressed_keys & (self.CMD_KEYS | self.ALT_KEYS))
        shift_pressed = bool(self.pressed_keys & self.SHIFT_KEYS)
        return mod_pressed and shift_pressed

    # -- state queries -----------------------------------------------------
    def are_modifiers_pressed(self):
        return bool(self.pressed_keys & (self.CMD_KEYS | self.ALT_KEYS | self.SHIFT_KEYS | self.CTRL_KEYS)) or self.middle_pressed

    def is_hotkey_pressed(self):
        return self.hotkey_active

    # -- output helpers ----------------------------------------------------
    def type_text(self, text, fast: bool = False):
        """Type text into active macOS window."""
        try:
            from .clipboard import MacOSClipboardSink

            sink = MacOSClipboardSink()
            return sink.type_text(text, fast=fast)
        except Exception as e:
            logger.debug(f"MacOSClipboardSink typing failed: {e}")
        return False

    def paste_text(self, terminal: bool = False) -> bool:
        """Emit Cmd+V paste on macOS."""
        import shutil
        import subprocess

        if shutil.which("osascript"):
            try:
                subprocess.run(
                    ["osascript", "-e", 'tell application "System Events" to keystroke "v" using command down'],
                    check=False,
                )
                return True
            except Exception:
                pass

        try:
            from pynput.keyboard import Controller, Key

            keyboard = Controller()
            with keyboard.pressed(Key.cmd):
                keyboard.press("v")
                keyboard.release("v")
            return True
        except Exception as e:
            logger.error(f"Error emitting macOS paste: {e}")
            return False

    def run(self):
        self.running = True
        logger.info("Started macOS hotkey monitor")
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


MacOSGlobalHotkeys = MacOSHotkeyManager
