"""Linux (Wayland/X11) global hotkey backend using evdev + uinput.

Extracted from ``src/hotkeys.py`` so the unified entry point can select it
through the HAL. ``WaylandGlobalHotkeys`` is kept as an alias for backwards
compatibility.
"""
from __future__ import annotations

import glob
import logging
import os
import select
import socket
import threading
import time

from ..base import BaseHotkeyManager

logger = logging.getLogger(__name__)


class LinuxHotkeyManager(BaseHotkeyManager):
    """Wayland/X11-compatible global hotkeys via evdev + uinput.

    Hold ``Alt+Shift`` to record (push-to-talk), tap ``Space`` while holding to
    latch hands-free. ``Ctrl`` held at activation switches the output mode to
    clipboard. Settings are opened from the terminal frontend, not from a
    global hotkey.
    """

    def __init__(self, callback_start, callback_stop):
        super().__init__(callback_start, callback_stop)
        self.devices = []
        self.virtual_keyboard = None
        self.key_states = {}
        self.device_key_states = {}
        self.evdev = None
        self.uinput = None

        self.ALT_KEYS = [56, 100]  # KEY_LEFTALT, KEY_RIGHTALT
        self.SHIFT_KEYS = [42, 54]  # KEY_LEFTSHIFT, KEY_RIGHTSHIFT
        # KEY_SPACE (pressed during an Alt+Shift hold = hands-free latch)
        self.SPACE_KEY = [57]

        # Modifier key codes (Ctrl is also the clipboard override at release).
        self.CTRL_KEYS = [29, 97]  # KEY_LEFTCTRL, KEY_RIGHTCTRL

        # Middle mouse button code (BTN_MIDDLE = 274) and hold delay (quarter second)
        self.MIDDLE_MOUSE_KEYS = [274]
        self.MIDDLE_CLICK_HOLD_DELAY = 0.25
        self._middle_click_timer = None
        self.middle_click_active = False

        # A middle tap shorter than this passes through as a normal click; once
        # the window elapses while still held we promote it to push-to-talk.
        self.MIDDLE_CLICK_TAP_WINDOW = 0.1
        self._pending_middle_events = []
        self._pending_middle_forwarder = None
        self._pending_middle_deadline = 0.0
        self._middle_eaten = False

        # Left+right mouse chord -> Enter. While mouse mode is on the pointing
        # device is grabbed (EVIOCGRAB) and every event is re-emitted through a
        # virtual mouse, so a left+right chord can be swallowed entirely rather
        # than leaking through to the OS.
        self.BTN_LEFT = 272
        self.BTN_RIGHT = 273
        self.MOUSE_BUTTON_KEYS = [272, 273]
        self.MOUSE_CHORD_WINDOW = 0.05  # seconds allowed between the two presses
        self.EV_SYN = 0
        self.EV_KEY = 1
        self.EV_REL = 2
        self.EV_ABS = 3
        self._pending_btn = None
        self._pending_events = []
        self._pending_forwarder = None
        self._pending_deadline = 0.0
        self._chord_swallow = False
        self._mouse_forwards = {}  # device.path -> {"device": dev, "virtual": Device}
        self._mouse_grab_failed = set()  # paths we already gave up on
        self._grab_sync_pending = True
        self._lock = threading.Lock()

        self.init_devices()

    def init_devices(self):
        """Initialize evdev and uinput dependencies."""
        try:
            import evdev
            import uinput

            self.evdev = evdev
            self.uinput = uinput
            if hasattr(evdev.ecodes, "BTN_MIDDLE"):
                self.MIDDLE_MOUSE_KEYS = [evdev.ecodes.BTN_MIDDLE]
            self.BTN_LEFT = getattr(evdev.ecodes, "BTN_LEFT", 272)
            self.BTN_RIGHT = getattr(evdev.ecodes, "BTN_RIGHT", 273)
            self.MOUSE_BUTTON_KEYS = [self.BTN_LEFT, self.BTN_RIGHT]
            self.EV_SYN = evdev.ecodes.EV_SYN
            self.EV_KEY = evdev.ecodes.EV_KEY
            self.EV_REL = evdev.ecodes.EV_REL
            self.EV_ABS = evdev.ecodes.EV_ABS
        except ImportError as e:
            logger.error(f"Missing dependencies: {e}")
            logger.error("Install with: pip install evdev python-uinput")
            return False

        # Create virtual keyboard for sending events
        try:
            all_keys = [
                getattr(uinput, name)
                for name in dir(uinput)
                if name.startswith("KEY_")
            ]
            self.virtual_keyboard = uinput.Device(all_keys)
            logger.debug(
                f"Created virtual keyboard device with {len(all_keys)} keys"
            )
        except Exception as e:
            logger.debug(f"Could not create virtual keyboard: {e}")

        return self.scan_for_devices()

    def type_text(self, text, fast: bool = False):
        """Type text using the virtual keyboard device."""
        if not self.virtual_keyboard or not self.uinput:
            logger.warning("Virtual keyboard not available for typing")
            return False

        try:
            uinput = self.uinput

            # In fast/optimized mode, normalize common Unicode punctuation before mapping
            if fast:
                text = (
                    text.replace("“", '"')
                    .replace("”", '"')
                    .replace("‘", "'")
                    .replace("’", "'")
                    .replace("—", "-")
                    .replace("–", "-")
                    .replace("…", "...")
                )

            key_map = {
                "a": (uinput.KEY_A, False), "b": (uinput.KEY_B, False),
                "c": (uinput.KEY_C, False), "d": (uinput.KEY_D, False),
                "e": (uinput.KEY_E, False), "f": (uinput.KEY_F, False),
                "g": (uinput.KEY_G, False), "h": (uinput.KEY_H, False),
                "i": (uinput.KEY_I, False), "j": (uinput.KEY_J, False),
                "k": (uinput.KEY_K, False), "l": (uinput.KEY_L, False),
                "m": (uinput.KEY_M, False), "n": (uinput.KEY_N, False),
                "o": (uinput.KEY_O, False), "p": (uinput.KEY_P, False),
                "q": (uinput.KEY_Q, False), "r": (uinput.KEY_R, False),
                "s": (uinput.KEY_S, False), "t": (uinput.KEY_T, False),
                "u": (uinput.KEY_U, False), "v": (uinput.KEY_V, False),
                "w": (uinput.KEY_W, False), "x": (uinput.KEY_X, False),
                "y": (uinput.KEY_Y, False), "z": (uinput.KEY_Z, False),
                "1": (uinput.KEY_1, False), "2": (uinput.KEY_2, False),
                "3": (uinput.KEY_3, False), "4": (uinput.KEY_4, False),
                "5": (uinput.KEY_5, False), "6": (uinput.KEY_6, False),
                "7": (uinput.KEY_7, False), "8": (uinput.KEY_8, False),
                "9": (uinput.KEY_9, False), "0": (uinput.KEY_0, False),
                " ": (uinput.KEY_SPACE, False),
                ".": (uinput.KEY_DOT, False),
                ",": (uinput.KEY_COMMA, False),
                "!": (uinput.KEY_1, True),
                "@": (uinput.KEY_2, True),
                "#": (uinput.KEY_3, True),
                "$": (uinput.KEY_4, True),
                "%": (uinput.KEY_5, True),
                "^": (uinput.KEY_6, True),
                "&": (uinput.KEY_7, True),
                "*": (uinput.KEY_8, True),
                "(": (uinput.KEY_9, True),
                ")": (uinput.KEY_0, True),
                "?": (uinput.KEY_SLASH, True),
                "/": (uinput.KEY_SLASH, False),
                "\n": (uinput.KEY_ENTER, False),
                "\t": (uinput.KEY_TAB, False),
                "-": (uinput.KEY_MINUS, False),
                "_": (uinput.KEY_MINUS, True),
                "=": (uinput.KEY_EQUAL, False),
                "+": (uinput.KEY_EQUAL, True),
                ":": (uinput.KEY_SEMICOLON, True),
                ";": (uinput.KEY_SEMICOLON, False),
                '"': (uinput.KEY_APOSTROPHE, True),
                "'": (uinput.KEY_APOSTROPHE, False),
                "<": (uinput.KEY_COMMA, True),
                ">": (uinput.KEY_DOT, True),
                "[": (uinput.KEY_LEFTBRACE, False),
                "]": (uinput.KEY_RIGHTBRACE, False),
                "{": (uinput.KEY_LEFTBRACE, True),
                "}": (uinput.KEY_RIGHTBRACE, True),
                "\\": (uinput.KEY_BACKSLASH, False),
                "|": (uinput.KEY_BACKSLASH, True),
                "`": (uinput.KEY_GRAVE, False),
                "~": (uinput.KEY_GRAVE, True),
            }

            for char in text:
                shift = False
                key = None

                char_lower = char.lower()
                if char_lower in key_map:
                    key, shift = key_map[char_lower]
                    if char.isupper():
                        shift = True
                else:
                    continue

                if fast:
                    if shift:
                        self.virtual_keyboard.emit(uinput.KEY_LEFTSHIFT, 1, syn=False)
                        self.virtual_keyboard.emit(key, 1, syn=True)
                        self.virtual_keyboard.emit(key, 0, syn=False)
                        self.virtual_keyboard.emit(uinput.KEY_LEFTSHIFT, 0, syn=True)
                    else:
                        self.virtual_keyboard.emit(key, 1, syn=True)
                        self.virtual_keyboard.emit(key, 0, syn=True)
                    time.sleep(0.001)
                else:
                    if shift:
                        self.virtual_keyboard.emit(uinput.KEY_LEFTSHIFT, 1)

                    self.virtual_keyboard.emit(key, 1)  # Press
                    self.virtual_keyboard.emit(key, 0)  # Release

                    if shift:
                        self.virtual_keyboard.emit(uinput.KEY_LEFTSHIFT, 0)

                    time.sleep(0.01)

            return True
        except Exception as e:
            logger.error(f"Error typing text via uinput: {e}")
            return False

    def paste_text(self, terminal: bool = False) -> bool:
        """Emit Ctrl+V (or Ctrl+Shift+V for terminal) via the virtual keyboard."""
        if not self.virtual_keyboard or not self.uinput:
            logger.warning("Virtual keyboard not available for paste")
            return False

        try:
            uinput = self.uinput
            ctrl = uinput.KEY_LEFTCTRL
            v = uinput.KEY_V
            if terminal:
                shift = uinput.KEY_LEFTSHIFT
                self.virtual_keyboard.emit(ctrl, 1)
                self.virtual_keyboard.emit(shift, 1)
                self.virtual_keyboard.emit(v, 1)
                time.sleep(0.01)
                self.virtual_keyboard.emit(v, 0)
                self.virtual_keyboard.emit(shift, 0)
                self.virtual_keyboard.emit(ctrl, 0)
            else:
                self.virtual_keyboard.emit(ctrl, 1)
                self.virtual_keyboard.emit(v, 1)
                time.sleep(0.01)
                self.virtual_keyboard.emit(v, 0)
                self.virtual_keyboard.emit(ctrl, 0)
            return True
        except Exception as e:
            logger.error(f"Error emitting paste via uinput: {e}")
            return False

    def _is_keyboard_device(self, device):
        """Check if a device looks like a keyboard we want to monitor."""
        try:
            caps = device.capabilities()
            if self.evdev.ecodes.EV_KEY not in caps:
                return False

            key_caps = caps[self.evdev.ecodes.EV_KEY]

            has_letters = any(
                key in key_caps
                for key in [
                    self.evdev.ecodes.KEY_A, self.evdev.ecodes.KEY_B,
                    self.evdev.ecodes.KEY_C, self.evdev.ecodes.KEY_Q,
                    self.evdev.ecodes.KEY_W, self.evdev.ecodes.KEY_E,
                ]
            )
            has_modifiers = any(
                key in key_caps
                for key in [
                    self.evdev.ecodes.KEY_LEFTALT, self.evdev.ecodes.KEY_RIGHTALT,
                    self.evdev.ecodes.KEY_LEFTSHIFT, self.evdev.ecodes.KEY_RIGHTSHIFT,
                    self.evdev.ecodes.KEY_LEFTCTRL, self.evdev.ecodes.KEY_RIGHTCTRL,
                ]
            )
            has_space_enter = any(
                key in key_caps
                for key in [self.evdev.ecodes.KEY_SPACE, self.evdev.ecodes.KEY_ENTER]
            )

            has_alt = any(
                key in key_caps
                for key in [self.evdev.ecodes.KEY_LEFTALT, self.evdev.ecodes.KEY_RIGHTALT]
            )
            has_shift = any(
                key in key_caps
                for key in [self.evdev.ecodes.KEY_LEFTSHIFT, self.evdev.ecodes.KEY_RIGHTSHIFT]
            )

            return (has_letters or has_modifiers or has_space_enter) and has_alt and has_shift
        except Exception:
            return False

    def _is_mouse_device(self, device):
        """Check if a device looks like a pointer with left/right/middle buttons."""
        try:
            name = getattr(device, "name", "").lower()
            # Keyboards, remap daemons, and virtual bridges must NEVER be grabbed as mice
            if any(k in name for k in ("kanata", "kmonad", "keyboard", "kbd")):
                return False

            caps = device.capabilities()
            if self.evdev.ecodes.EV_KEY not in caps:
                return False

            key_caps = caps[self.evdev.ecodes.EV_KEY]

            # If device has standard typing keys (e.g. KEY_A = 30), it is a keyboard
            if getattr(self.evdev.ecodes, "KEY_A", 30) in key_caps:
                return False

            # If it qualifies as a keyboard, it is not a mouse
            if self._is_keyboard_device(device):
                return False

            return any(
                key in key_caps
                for key in (self.MIDDLE_MOUSE_KEYS + self.MOUSE_BUTTON_KEYS)
            )
        except Exception:
            return False

    def _is_monitored_device(self, device):
        """Check if a device should be monitored (keyboard or mouse)."""
        name = getattr(device, "name", "")
        if name == "python-uinput" or name.startswith("vt-"):
            return False
        if not self.middle_click_enabled:
            return self._is_keyboard_device(device)
        return self._is_keyboard_device(device) or self._is_mouse_device(device)

    def scan_for_devices(self):
        """Scan for new keyboard and mouse devices."""
        try:
            evdev = self.evdev

            current_paths = set(d.path for d in self.devices)

            device_paths = evdev.list_devices()

            all_event_paths = glob.glob("/dev/input/event*")
            for path in all_event_paths:
                if path not in device_paths:
                    device_paths.append(path)

            new_devices = []

            for path in device_paths:
                if path in current_paths:
                    continue
                try:
                    device = evdev.InputDevice(path)
                    if self._is_monitored_device(device):
                        new_devices.append(device)
                except (PermissionError, OSError):
                    continue

            if new_devices:
                self.devices.extend(new_devices)
                return True

            return False
        except Exception as e:
            logger.error(f"Error scanning for devices: {e}")
            return False

    # -- hotkey state queries ---------------------------------------------
    def is_key_pressed(self, keys):
        """Check if any of the given keys are pressed on any device."""
        for dev_states in self.device_key_states.values():
            if any(dev_states.get(k, False) for k in keys):
                return True
        return any(self.key_states.get(k, False) for k in keys)

    def is_alt_shift_pressed(self):
        """Check if the hotkey combination (Alt+Shift) is currently pressed."""
        alt_pressed = self.is_key_pressed(self.ALT_KEYS)
        shift_pressed = self.is_key_pressed(self.SHIFT_KEYS)
        return alt_pressed and shift_pressed

    def is_middle_click_pressed(self):
        """Check if the middle mouse button is currently pressed."""
        if not self.middle_click_enabled:
            return False
        return self.is_key_pressed(self.MIDDLE_MOUSE_KEYS)

    def is_hotkey_pressed(self):
        """Check if any push-to-talk trigger is currently pressed."""
        return self.is_alt_shift_pressed() or (self.middle_click_active and self.is_middle_click_pressed())

    def is_ctrl_pressed(self):
        """Check if Ctrl is currently pressed."""
        return self.is_key_pressed(self.CTRL_KEYS)

    def are_modifiers_pressed(self):
        """Check if any modifier keys (Alt, Shift, Ctrl) or middle click are still pressed."""
        alt_pressed = self.is_key_pressed(self.ALT_KEYS)
        shift_pressed = self.is_key_pressed(self.SHIFT_KEYS)
        ctrl_pressed = self.is_key_pressed(self.CTRL_KEYS)
        middle_pressed = self.is_middle_click_pressed()
        return alt_pressed or shift_pressed or ctrl_pressed or middle_pressed

    def is_hotkey_released(self):
        """Check if the hotkey combination is no longer fully pressed."""
        return not self.is_hotkey_pressed()

    def set_middle_click_enabled(self, enabled: bool):
        """Toggle middle click push-to-talk mode."""
        release_mice = False
        with self._lock:
            self.middle_click_enabled = bool(enabled)
            if not self.middle_click_enabled:
                if self._middle_click_timer:
                    self._middle_click_timer.cancel()
                    self._middle_click_timer = None
                if self.middle_click_active:
                    self.middle_click_active = False
                    if not self.is_alt_shift_pressed():
                        self.hotkey_active = False
                        if self.callback_stop:
                            self.callback_stop(copy_to_clipboard=self.copy_to_clipboard_mode)
                release_mice = True
            else:
                # Ask the event loop to grab pointing devices on its next tick.
                self._grab_sync_pending = True

        if release_mice:
            self._release_all_mice()

    def _on_middle_click_hold_timeout(self):
        """Called when middle click has been held for >= 0.25s."""
        with self._lock:
            if self._middle_click_timer is None:
                return
            if self.is_middle_click_pressed() and not self.hotkey_active:
                logger.debug("Middle click held >= 0.25s - starting recording")
                self.hotkey_active = True
                self.middle_click_active = True
                self.copy_to_clipboard_mode = self.is_ctrl_pressed()
                if self.callback_start:
                    self.callback_start()

    def _handle_middle_mouse_event(self, event):
        """Handle middle mouse button event for push-to-talk."""
        if not self.middle_click_enabled:
            return

        key_code = event.code
        key_state = event.value  # 1 = press, 0 = release

        with self._lock:
            if key_state in [0, 1]:
                self.key_states[key_code] = (key_state == 1)

            if key_state == 1:
                if not self.hotkey_active:
                    if self._middle_click_timer:
                        self._middle_click_timer.cancel()
                    self._middle_click_timer = threading.Timer(
                        self.MIDDLE_CLICK_HOLD_DELAY,
                        self._on_middle_click_hold_timeout,
                    )
                    self._middle_click_timer.daemon = True
                    self._middle_click_timer.start()

            elif key_state == 0:
                if self._middle_click_timer:
                    self._middle_click_timer.cancel()
                    self._middle_click_timer = None

                if self.middle_click_active:
                    # Held >= 0.25s: was push-to-talk.
                    self.middle_click_active = False
                    if not self.is_alt_shift_pressed():
                        self.hotkey_active = False
                        if self.latch_release:
                            logger.debug("⏸️ Space-latched release - continuing recording hands-free")
                            self.latch_release = False
                        else:
                            logger.debug("⏹️ Middle click released - stopping recording")
                            if self.callback_stop:
                                self.callback_stop(copy_to_clipboard=self.copy_to_clipboard_mode)

    # -- left+right mouse chord -> Enter -----------------------------------
    def _uinput_specs_for(self, device):
        """Mirror a device's key/rel/abs capabilities for a uinput clone."""
        wanted = (self.EV_KEY, self.EV_REL, self.EV_ABS)
        specs = []
        seen = set()
        for ev_type, entries in device.capabilities().items():
            if ev_type not in wanted:
                continue
            for entry in entries:
                if isinstance(entry, tuple):
                    code, info = entry[0], entry[1]
                    spec = (
                        ev_type,
                        code,
                        getattr(info, "min", 0),
                        getattr(info, "max", 0),
                        getattr(info, "fuzz", 0),
                        getattr(info, "flat", 0),
                    )
                else:
                    spec = (ev_type, entry)
                if spec[:2] in seen:
                    continue
                seen.add(spec[:2])
                specs.append(spec)
        return specs

    def _create_virtual_mouse(self, device):
        """Create a uinput clone of ``device`` so it can be safely grabbed."""
        if not self.uinput:
            return None
        try:
            specs = self._uinput_specs_for(device)
            if not specs:
                return None
            # BUS_USB makes libinput treat the clone as a plain pointer.
            return self.uinput.Device(specs, name="vt-mouse", bustype=0x03)
        except Exception as e:
            logger.warning(f"Could not create virtual mouse for {device.name}: {e}")
            return None

    def _can_grab_mouse(self, device):
        """Only grab real relative pointers; never virtual remapper outputs."""
        try:
            name = getattr(device, "name", "").lower()
            if any(
                k in name
                for k in (
                    "kanata", "kmonad", "remapper", "ydotool", "wtype",
                    "dotool", "virtual", "forwarded",
                )
            ):
                return False
            # Mirroring an absolute pointer would drop its gesture properties.
            return self.EV_ABS not in device.capabilities()
        except Exception:
            return False

    def _grab_mouse(self, device):
        """Grab a mouse and start re-emitting its events (enables suppression)."""
        if device.path in self._mouse_forwards:
            return True
        if device.path in self._mouse_grab_failed:
            return False
        if not self.virtual_keyboard:
            # Without a virtual keyboard we could swallow clicks but never send
            # Enter, so leave the mouse alone.
            logger.warning(
                f"Mouse chord disabled for {device.name}: virtual keyboard unavailable"
            )
            self._mouse_grab_failed.add(device.path)
            return False
        virtual = self._create_virtual_mouse(device)
        if virtual is None:
            logger.warning(
                f"Mouse chord disabled for {device.name}: no virtual mouse available"
            )
            self._mouse_grab_failed.add(device.path)
            return False
        # Give udev/libinput a moment to enumerate the clone before we take the
        # physical device exclusively, otherwise the pointer can stutter.
        time.sleep(0.15)
        try:
            device.grab()
        except Exception as e:
            logger.warning(f"Could not grab {device.name} for mouse chord: {e}")
            try:
                virtual.destroy()
            except Exception:
                pass
            self._mouse_grab_failed.add(device.path)
            return False
        self._mouse_forwards[device.path] = {"device": device, "virtual": virtual}
        self._mouse_grab_failed.discard(device.path)
        logger.info(f"🖱️ Grabbed {device.name}: left+right click now sends Enter")
        return True

    def _release_mouse(self, path):
        self._mouse_grab_failed.discard(path)
        entry = self._mouse_forwards.pop(path, None)
        if not entry:
            return
        try:
            entry["device"].ungrab()
        except Exception:
            pass
        try:
            entry["virtual"].destroy()
        except Exception:
            pass
        self._clear_pending_mouse_gestures()

    def _release_all_mice(self):
        for path in list(self._mouse_forwards):
            self._release_mouse(path)
        self._mouse_grab_failed.clear()

    def _sync_mouse_grabs(self):
        """Grab any newly-seen mice while mouse mode is enabled."""
        if not self.middle_click_enabled or not self.evdev:
            return
        for device in list(self.devices):
            if device.path in self._mouse_forwards:
                continue
            try:
                if self._is_mouse_device(device) and self._can_grab_mouse(device):
                    self._grab_mouse(device)
            except Exception:
                continue

    def _forward_event(self, forwarder, event):
        """Re-emit a raw event on the virtual mouse (grabbed devices only)."""
        if forwarder is None:
            return
        try:
            ev_type = event.type
            if ev_type == self.EV_SYN:
                forwarder.syn()
                return
            if ev_type not in (self.EV_KEY, self.EV_REL, self.EV_ABS):
                return
            forwarder.emit((ev_type, event.code), event.value, syn=False)
        except Exception as e:
            logger.debug(f"Failed to forward mouse event: {e}")

    def _forward_button(self, forwarder, code, value):
        if forwarder is None:
            return
        try:
            forwarder.emit((self.EV_KEY, code), value, syn=True)
        except Exception as e:
            logger.debug(f"Failed to forward mouse button: {e}")

    def _emit_enter(self):
        """Emit a synthetic Enter keystroke for the left+right chord."""
        if not self.virtual_keyboard or not self.uinput:
            logger.warning("Cannot send Enter: virtual keyboard unavailable")
            return False
        try:
            enter = self.uinput.KEY_ENTER
            self.virtual_keyboard.emit(enter, 1)
            self.virtual_keyboard.emit(enter, 0)
            logger.debug("🖱️ Left+right click -> Enter")
            return True
        except Exception as e:
            logger.error(f"Failed to send Enter for mouse chord: {e}")
            return False

    def _handle_grabbed_event(self, device, forwarder, event):
        """Handle an event from a grabbed mouse, forwarding everything else."""
        if event.type == self.EV_KEY:
            if event.code in self.MIDDLE_MOUSE_KEYS:
                if self.middle_click_enabled:
                    self._handle_grabbed_middle_event(forwarder, event)
                    return
                self._forward_event(forwarder, event)
                return
            if event.code in self.MOUSE_BUTTON_KEYS:
                self._handle_mouse_button_event(event, forwarder)
                return
        self._forward_event(forwarder, event)

    def _handle_grabbed_middle_event(self, forwarder, event):
        """Middle click: a sub-100ms tap passes through, a hold records.

        The press is buffered for the tap window so a hold never leaks a
        primary-selection paste; if the window elapses we swallow the click and
        promote it to push-to-talk.
        """
        value = event.value
        # Keep push-to-talk bookkeeping (key state + fallback timer) in sync.
        self._handle_middle_mouse_event(event)
        forwarded = []
        with self._lock:
            if value == 2:
                return
            if self._middle_eaten:
                if value == 0:
                    self._middle_eaten = False
                return
            if value == 1:
                self._pending_middle_events = [1]
                self._pending_middle_forwarder = forwarder
                self._pending_middle_deadline = (
                    time.monotonic() + self.MIDDLE_CLICK_TAP_WINDOW
                )
                return
            if value == 0 and self._pending_middle_events:
                # Released inside the tap window: replay it as a real click.
                self._pending_middle_events.append(0)
                forwarded = self._pending_middle_events
                self._pending_middle_events = []
                self._pending_middle_forwarder = None
                self._pending_middle_deadline = 0.0
        for v in forwarded:
            self._forward_button(forwarder, event.code, v)

    def _expire_pending_middle_click(self, now=None, force=False):
        """Promote a middle press that outlived the tap window to a recording."""
        start_recording = False
        with self._lock:
            if not self._pending_middle_events:
                return
            now = time.monotonic() if now is None else now
            if not force and now < self._pending_middle_deadline:
                return
            self._pending_middle_events = []
            self._pending_middle_forwarder = None
            self._pending_middle_deadline = 0.0
            self._middle_eaten = True
            # Cancel the 0.25s fallback timer; we promote to a hold right now.
            if self._middle_click_timer:
                self._middle_click_timer.cancel()
                self._middle_click_timer = None
            if not self.hotkey_active:
                start_recording = True
                self.hotkey_active = True
                self.middle_click_active = True
                self.copy_to_clipboard_mode = self.is_ctrl_pressed()
        if start_recording:
            logger.debug("Middle click held past tap window - starting recording")
            if self.callback_start:
                self.callback_start()

    def _handle_mouse_button_event(self, event, forwarder):
        """Buffer left/right presses; a near-simultaneous pair becomes Enter."""
        code = event.code
        value = event.value
        forward_later = []
        with self._lock:
            if value in (0, 1):
                self.key_states[code] = (value == 1)

            if self._chord_swallow:
                if value == 0 and not any(
                    self.key_states.get(k, False) for k in self.MOUSE_BUTTON_KEYS
                ):
                    self._chord_swallow = False
                return

            if value == 1:
                if self._pending_btn is None:
                    self._pending_btn = code
                    self._pending_events = [1]
                    self._pending_forwarder = forwarder
                    self._pending_deadline = time.monotonic() + self.MOUSE_CHORD_WINDOW
                    return
                if self._pending_btn == code:
                    self._pending_events.append(1)
                    return
                # A different button arrived inside the window -> chord.
                self._pending_btn = None
                self._pending_events = []
                self._pending_forwarder = None
                self._pending_deadline = 0.0
                self._chord_swallow = True
                self._emit_enter()
                return

            if value == 0:
                if self._pending_btn == code:
                    self._pending_events.append(0)
                    return
                forward_later.append((forwarder, code, value))

        for fwd, c, v in forward_later:
            self._forward_button(fwd, c, v)

    def _expire_pending_mouse_chord(self, now=None, force=False):
        """Flush a buffered press once its chord window has elapsed."""
        with self._lock:
            if self._pending_btn is None:
                return
            now = time.monotonic() if now is None else now
            if not force and now < self._pending_deadline:
                return
            code = self._pending_btn
            events = self._pending_events
            forwarder = self._pending_forwarder
            self._pending_btn = None
            self._pending_events = []
            self._pending_forwarder = None
            self._pending_deadline = 0.0
        for value in events:
            self._forward_button(forwarder, code, value)

    def _clear_pending_mouse_gestures(self):
        with self._lock:
            self._pending_btn = None
            self._pending_events = []
            self._pending_forwarder = None
            self._pending_deadline = 0.0
            self._chord_swallow = False
            self._pending_middle_events = []
            self._pending_middle_forwarder = None
            self._pending_middle_deadline = 0.0
            self._middle_eaten = False

    def handle_key_event(self, event, forwarder=None, fd=None):
        """Handle a key event and check for hotkey activation."""
        if event.type != self.evdev.ecodes.EV_KEY:
            return

        key_code = event.code
        key_state = event.value  # 1 = press, 0 = release, 2 = repeat

        # Delegate middle mouse button events to _handle_middle_mouse_event
        if key_code in self.MIDDLE_MOUSE_KEYS:
            self._handle_middle_mouse_event(event)
            return

        # Left/right only matter for the chord. A non-grabbed mouse cannot have
        # its clicks suppressed, so just keep the button state fresh.
        if key_code in self.MOUSE_BUTTON_KEYS:
            if key_state in [0, 1]:
                with self._lock:
                    self.key_states[key_code] = (key_state == 1)
            return

        with self._lock:
            if key_state in [0, 1]:
                if fd is not None:
                    if fd not in self.device_key_states:
                        self.device_key_states[fd] = {}
                    self.device_key_states[fd][key_code] = (key_state == 1)
                self.key_states[key_code] = (key_state == 1)

            # Space pressed while the hotkey is held = hold the recording hands-free.
            if key_state == 1 and key_code in self.SPACE_KEY and self.hotkey_active:
                logger.debug("Space latched - recording will hold after release")
                self.latch_release = True

            # Push-to-talk: hold Alt+Shift to record.
            if self.is_alt_shift_pressed() and not self.hotkey_active:
                logger.debug("Hotkey activated - starting recording")
                self.hotkey_active = True
                self.copy_to_clipboard_mode = self.is_ctrl_pressed()
                if self.callback_start:
                    self.callback_start()
            elif self.hotkey_active and not self.middle_click_active and not self.is_alt_shift_pressed():
                self.hotkey_active = False
                if self.latch_release:
                    logger.debug("⏸️ Space-latched release - continuing recording hands-free")
                    self.latch_release = False
                else:
                    logger.debug("⏹️ Hotkey released - stopping recording")
                    if self.callback_stop:
                        self.callback_stop(copy_to_clipboard=self.copy_to_clipboard_mode)

    def run(self):
        """Main event loop for monitoring keyboard events."""
        self.running = True
        logger.info("Started hotkey monitor loop")

        last_scan_time = 0
        scan_interval = 5.0  # Seconds between scans when no devices found

        while self.running:
            try:
                current_time = time.time()
                if current_time - last_scan_time > scan_interval:
                    self.scan_for_devices()
                    last_scan_time = current_time
                    self._grab_sync_pending = False
                    self._sync_mouse_grabs()
                elif self._grab_sync_pending:
                    self._grab_sync_pending = False
                    self._sync_mouse_grabs()

                if not self.devices:
                    time.sleep(0.5)
                    continue

                devices_map = {
                    dev.fd: dev for dev in self.devices if dev.fd is not None
                }

                if not devices_map:
                    if self.devices:
                        logger.warning("Devices lost (fd invalid). clearing list.")
                    self.devices = []
                    self.key_states.clear()
                    continue

                timeout = 1.0
                now = time.monotonic()
                if self._pending_btn is not None:
                    timeout = max(0.0, min(timeout, self._pending_deadline - now))
                if self._pending_middle_events:
                    timeout = max(
                        0.0, min(timeout, self._pending_middle_deadline - now)
                    )
                r, w, x = select.select(devices_map, [], [], timeout)

                # Flush any gesture whose decision window elapsed while blocked,
                # so event ordering stays faithful.
                self._expire_pending_mouse_chord()
                self._expire_pending_middle_click()

                for fd in r:
                    device = devices_map.get(fd)
                    if device is None:
                        continue
                    forwarder = self._mouse_forwards.get(device.path, {}).get("virtual")
                    try:
                        for event in device.read():
                            if forwarder is not None:
                                self._handle_grabbed_event(device, forwarder, event)
                            elif event.type == self.evdev.ecodes.EV_KEY:
                                self.handle_key_event(event, fd=device.fd)
                    except OSError as e:
                        is_disconnect = (e.errno == 19) or ("No such device" in str(e))

                        if is_disconnect:
                            logger.warning(f"Device disconnected: {device.name}")
                        else:
                            logger.warning(f"Device {device.path} error: {e}")

                        self.device_key_states.pop(device.fd, None)
                        self._release_mouse(device.path)

                        if device in self.devices:
                            self.devices.remove(device)
                            try:
                                device.close()
                            except Exception:
                                pass

                        if not self.devices:
                            self.key_states.clear()
                            self.device_key_states.clear()
                        continue

            except Exception as e:
                logger.error(f"Error in event loop: {e}")
                time.sleep(1)

        return True

    def stop(self):
        """Stop the hotkey monitoring."""
        self.running = False
        with self._lock:
            if self._middle_click_timer:
                try:
                    self._middle_click_timer.cancel()
                except Exception:
                    pass
                self._middle_click_timer = None
            self.middle_click_active = False
            self.hotkey_active = False

        self._release_all_mice()

        for device in self.devices:
            try:
                device.close()
            except Exception:
                pass
        self.devices = []
        self.key_states.clear()
        self.device_key_states.clear()
        if self.virtual_keyboard:
            try:
                self.virtual_keyboard.destroy()
            except Exception:
                pass
            self.virtual_keyboard = None


# Backwards-compatible alias used by the original ``src/hotkeys.py``.
WaylandGlobalHotkeys = LinuxHotkeyManager
