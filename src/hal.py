"""Stable façade for the Hardware/OS Abstraction Layer (HAL).

The HAL physically lives in ``src/platform/`` as required by the convergence
plan. Python's standard library also has a :mod:`platform` module, and because
``src/`` is on ``sys.path`` at runtime, importing that directory with the plain
name ``platform`` would shadow the stdlib module and break ``numpy``/``torch``
and friends.

To avoid that, this module loads the package under the private module name
``vt_platform`` using an explicit file-location loader. Callers import from
:mod:`hal`::

    import hal

    platform = hal.detect_platform()
    sink = hal.get_clipboard_sink()
    cues = hal.get_audio_cue_player()

The public surface here mirrors ``src/platform/__init__.py`` exactly.
"""
from __future__ import annotations

import importlib.util
import os
import sys

_HAL_MODULE_NAME = "vt_platform"
_HAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "platform")


def _load_hal():
    """Load (once) and return the ``src/platform`` package as ``vt_platform``."""
    existing = sys.modules.get(_HAL_MODULE_NAME)
    if existing is not None:
        return existing

    init_path = os.path.join(_HAL_DIR, "__init__.py")
    spec = importlib.util.spec_from_file_location(
        _HAL_MODULE_NAME,
        init_path,
        submodule_search_locations=[_HAL_DIR],
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"Unable to load the HAL package from {_HAL_DIR}")

    module = importlib.util.module_from_spec(spec)
    # Register before executing so intra-package relative imports resolve.
    sys.modules[_HAL_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(_HAL_MODULE_NAME, None)
        raise
    return module


_hal = _load_hal()

# Re-export the public HAL surface -----------------------------------------
LINUX = _hal.LINUX
WSL = _hal.WSL
WINDOWS = _hal.WINDOWS
VALID_PLATFORMS = _hal.VALID_PLATFORMS

detect_platform = _hal.detect_platform
get_clipboard_sink = _hal.get_clipboard_sink
get_audio_cue_player = _hal.get_audio_cue_player
create_hotkey_manager = _hal.create_hotkey_manager
get_visual_notification = _hal.get_visual_notification


def load_backend(platform: str, name: str):
    """Import a concrete backend module from the loaded HAL package.

    Example: ``load_backend("windows", "notifications")`` returns
    ``vt_platform.windows.notifications``. Used by :mod:`hotkeys` for
    backward-compatible class aliases.
    """
    import importlib

    return importlib.import_module(f"{_HAL_MODULE_NAME}.{platform}.{name}")


__all__ = [
    "LINUX",
    "WSL",
    "WINDOWS",
    "VALID_PLATFORMS",
    "detect_platform",
    "get_clipboard_sink",
    "get_audio_cue_player",
    "create_hotkey_manager",
    "get_visual_notification",
    "load_backend",
]
