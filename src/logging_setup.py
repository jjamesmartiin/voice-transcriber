"""Logging configuration: console plus an optional per-user log file.

Why a file at all: on Windows the app is usually started by double-clicking
``run.bat``, and a crash closes that console before anyone can read it. Keeping
the same output in a per-user log file makes those failures diagnosable after
the fact (see ``platforms/windows/README.md``).

Kept deliberately dependency-free (stdlib only) and importable *before* the rest
of the app so that import-time messages from heavy modules are captured too.

Knobs:
  VT_LOG_FILE     explicit log path (overrides the default location)
  VT_LOG_LEVEL    root log level name, default ``WARNING`` (e.g. ``INFO``)
  VT_LOG_DISABLE  truthy ("1"/"true"/"yes"/"on") disables the file handler
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

DEFAULT_LEVEL = "WARNING"
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def default_log_path() -> Path:
    """Where the on-disk log lives (``$VT_LOG_FILE`` wins if set)."""
    override = os.environ.get("VT_LOG_FILE", "").strip()
    if override:
        return Path(override).expanduser()

    # Mirrors t2.get_data_dir() without importing it: logging must be up before
    # t2 loads, and importing the app to find its own data dir would be circular.
    if os.name == "posix":
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    else:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "vt" / "vt.log"


def configure_logging(level: str | int | None = None, log_file=None, console: bool = True):
    """Install console and file handlers on the root logger.

    Returns the :class:`Path` used for the log file, or ``None`` when the file
    handler is disabled or could not be created. Never raises for a bad/unwritable
    location: logging must not be able to take the app down.
    """
    resolved_level = level if level is not None else os.environ.get("VT_LOG_LEVEL", DEFAULT_LEVEL)
    formatter = logging.Formatter(LOG_FORMAT)
    handlers: list[logging.Handler] = []

    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        handlers.append(stream)

    log_path: Path | None = None
    if not _env_flag("VT_LOG_DISABLE"):
        try:
            target = Path(log_file) if log_file is not None else default_log_path()
            target.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(target, encoding="utf-8")
            file_handler.setFormatter(formatter)
            handlers.append(file_handler)
            log_path = target
        except Exception:
            log_path = None

    # force=True replaces any handlers basicConfig may already have installed.
    logging.basicConfig(level=resolved_level, handlers=handlers, force=True)
    return log_path
