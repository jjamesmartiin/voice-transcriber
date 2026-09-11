"""Abstract base classes for the Hardware/OS Abstraction Layer (HAL).

These interfaces define the contract every platform backend must satisfy.
Keeping them dependency-free is deliberate: importing this module must never
pull in ``evdev``, ``pynput``, ``winsound`` or any other OS-specific library,
so it can be loaded on every host (Linux, WSL, Windows).

See :mod:`src.platform` for the concrete factories.
"""
from __future__ import annotations


class BaseClipboardSink:
    """Unified clipboard + typing-injection sink."""

    platform = "unknown"

    def copy_text(self, text: str) -> bool:
        """Copy ``text`` to the system clipboard. Return ``True`` on success."""
        raise NotImplementedError

    def type_text(self, text: str) -> bool:
        """Inject ``text`` as synthetic keystrokes into the active window.

        The default implementation degrades to the clipboard so callers always
        have a functional output path.
        """
        return self.copy_text(text)


class BaseAudioCuePlayer:
    """Unified start/stop/complete earcon player."""

    platform = "unknown"

    def play_cue(self, name: str) -> None:
        """Play the earcon identified by ``name`` (``start``/``stop``/``complete``)."""
        raise NotImplementedError

    # Convenience wrappers -------------------------------------------------
    def play_start(self) -> None:
        self.play_cue("start")

    def play_stop(self) -> None:
        self.play_cue("stop")

    def play_complete(self) -> None:
        self.play_cue("complete")

    def set_sound_theme(self, theme: str) -> None:
        """Optionally switch the active sound theme (no-op by default)."""


class BaseHotkeyManager:
    """Unified global push-to-talk hotkey manager.

    Concrete backends drive the ``callback_start`` / ``callback_stop`` /
    ``callback_config`` callbacks in response to the Alt+Shift hotkey (and the
    Space hands-free latch) and are responsible for typing text into the active
    window when asked.
    """

    def __init__(
        self,
        callback_start=None,
        callback_stop=None,
        callback_config=None,
    ) -> None:
        self.callback_start = callback_start
        self.callback_stop = callback_stop
        self.callback_config = callback_config

        self.running = False
        self.hotkey_active = False
        self.latch_release = False
        self.copy_to_clipboard_mode = False

        # Non-empty when the backend successfully initialised; ``main.py`` uses
        # this to decide whether global hotkeys are available.
        self.devices: list = []

    # Lifecycle ------------------------------------------------------------
    def run(self) -> bool:
        """Block and process hotkey events until :meth:`stop` is called."""
        raise NotImplementedError

    def stop(self) -> None:
        """Stop the event loop and release resources."""

    def cleanup(self) -> None:
        """Full teardown; defaults to :meth:`stop`."""
        self.stop()

    # State queries --------------------------------------------------------
    def are_modifiers_pressed(self) -> bool:
        """Return ``True`` while any hotkey modifier is still physically held."""
        return False

    def is_hotkey_pressed(self) -> bool:
        """Return ``True`` while the push-to-talk session is active."""
        return self.hotkey_active

    # Output helpers -------------------------------------------------------
    def type_text(self, text: str) -> bool:
        """Inject ``text`` into the active window. Optional backend feature."""
        return False

    def play_done_sound(self) -> bool:
        """Play the completion earcon natively (WSL bridge). Optional."""
        return False

    def set_sound_theme(self, theme: str) -> bool:
        """Propagate a sound theme to the backend (WSL bridge). Optional."""
        return False
