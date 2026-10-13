"""What "auto" means, and the theme vocabulary behind it.

Three front ends draw in the same colour (the Rich TUI, the ratatui binary and
this package's settings modals), so the list of themes and the resolution of
``auto`` live here instead of being re-derived per front end -- the same reason
:mod:`keybinds` owns the chord vocabulary for every hotkey backend.

``auto`` resolves through this chain, first hit wins:

1. ``VT_UI_THEME`` -- the documented per-session override (concrete names only)
2. ``ACCENT_COLOR`` -- an accent set by a terminal profile or a wrapper
3. the desktop's own accent (see :func:`detect_desktop_accent`)
4. ``TERM_PROGRAM`` -- the terminal heuristic that predates the probe
5. ``green``

The ratatui binary cannot run the desktop probe, so the engine resolves the
chain once and hands the result to it as ``ACCENT_COLOR`` -- step 2 of the
binary's own identical chain (see ``RatatuiTui._child_env``).
"""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache

#: Every theme a front end can render. ``auto`` is the only non-colour, and it is
#: never configured as a theme name: it is resolved by :func:`resolve`.
COLOR_PALETTES = ["auto", "green", "cyan", "blue", "magenta", "yellow", "red", "white"]

#: GNOME 47's accent palette, as ``gsettings`` reports it, mapped onto the
#: nearest colour above. Orange/purple/pink have no exact entry, so they take the
#: neighbour that reads the same way (amber, violet, magenta).
ACCENT_TO_THEME = {
    "blue": "blue",
    "teal": "cyan",
    "green": "green",
    "yellow": "yellow",
    "orange": "yellow",
    "red": "red",
    "pink": "magenta",
    "purple": "magenta",
    "slate": "white",
}


@lru_cache(maxsize=1)
def detect_desktop_accent() -> str | None:
    """The desktop's accent colour as a theme name, or ``None``.

    Asked of the desktop with its own tool rather than guessed from the OS: no
    GNOME, a GNOME older than 47 (no such key), no session bus and
    Windows/macOS/WSL all end at ``None``, and the caller falls through. KDE
    stores an RGB triplet instead of a name, so it is not covered -- see
    ``docs/TODO.md``.

    Cached: the answer cannot change while the app runs, and the call spawns a
    process. The timeout is there because ``gsettings`` talks to a session bus
    that can be wedged.
    """
    try:
        proc = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "accent-color"],
            capture_output=True, text=True, timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    # ``gsettings`` prints GVariant text: a quoted string, e.g. 'red'.
    return ACCENT_TO_THEME.get(proc.stdout.strip().strip("'").lower())


def _explicit(var: str) -> str | None:
    """A concrete theme named by an environment variable, if it names one."""
    value = os.environ.get(var, "").strip().lower()
    if value in COLOR_PALETTES and value != "auto":
        return value
    return None


def resolve(configured: str | None) -> str:
    """The concrete colour to paint with, for a configured theme name."""
    theme = str(configured or "auto").strip().lower()
    if theme in COLOR_PALETTES and theme != "auto":
        return theme

    prog = os.environ.get("TERM_PROGRAM", "").lower()
    terminal = next((t for needle, t in (
        ("vscode", "cyan"), ("kitty", "magenta"),
        ("apple_terminal", "blue"), ("alacritty", "yellow"),
    ) if needle in prog), None)

    return (
        _explicit("VT_UI_THEME")
        or _explicit("ACCENT_COLOR")
        or detect_desktop_accent()
        or terminal
        or "green"
    )
