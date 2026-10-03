"""Persistent, lifetime dictation statistics.

The engine keeps *per-session* counters in memory (``session_time_saved_sec``
et al. in :mod:`main`). Those die with the process, so the number a user
actually cares about — "how much typing has this saved me, ever" — is
accumulated here and written to a small JSON file in the data directory.

Design constraints:

* **Incremental persistence.** Every dictation appends and writes, so a crash,
  a ``Ctrl+C`` or a ``SIGKILL`` loses at most the in-flight recording.
* **Atomic writes.** The record is written to a temp file and ``os.replace``\\ d
  into place, so a kill mid-write can never truncate the real file.
* **Fail-soft.** Stats are a nicety, never a dependency. A missing, unreadable
  or corrupt file degrades to zeros and a failed write is logged and swallowed.
  Nothing in this module raises into the recording path.
* **Numeric only.** Durations stay floats here; each frontend formats them with
  its own ``format_duration`` so the terminal and the ratatui UI stay identical
  in behaviour.
* **Single-writer assumption.** The read-modify-write below is guarded by a
  process-local lock, so threads cannot interleave. Two *engines* sharing one
  file (possible via ``VT_CONTROL_SOCKET`` isolation) can still lose an
  increment, since a cross-process lock is not worth the complexity for a vanity
  counter. The file itself can never be corrupted or truncated: every write is an
  atomic ``os.replace``.

Set ``VT_STATS_FILE`` to override the location (tests use this).
"""

from __future__ import annotations

import glob
import json
import logging
import math
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

STATS_FILE_ENV = "VT_STATS_FILE"
STATS_FILE_NAME = "stats.json"
SCHEMA_VERSION = 1

# The record is a fixed set of scalars: a written file is ~200 bytes and grows
# only with the number of digits, so it never becomes "large" no matter how long
# the app is used. This cap only exists for files that were *not* written by us --
# a bad VT_STATS_FILE, a symlink to something huge, or hand-edited corruption --
# so a read can never balloon memory or stall the recording path. Anything over
# the cap reads as zeros and is rewritten small on the next update.
MAX_STATS_BYTES = 64 * 1024

# Leftover <name>.tmp<pid> files from a crashed engine are only removed once they
# are this old, so a *concurrent* engine's in-flight temp file is never pulled out
# from under its os.replace().
STALE_TEMP_AGE_SEC = 24 * 60 * 60

_EMPTY = {
    "time_saved_sec": 0.0,
    "words": 0,
    "transcriptions": 0,
    "sessions": 0,
    "first_used": None,
    "last_used": None,
}

# Guards read-modify-write so two transcriptions can never interleave.
_lock = threading.RLock()

# Set once the one-per-process stale temp-file sweep has run (see below).
_cleaned_temp_files = False


def stats_path() -> Path:
    """Location of the stats file: ``$VT_STATS_FILE`` or ``<data dir>/stats.json``."""
    override = os.environ.get(STATS_FILE_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    import t2  # imported lazily: honors a monkeypatched get_data_dir in tests

    return t2.get_data_dir() / STATS_FILE_NAME


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _clean_number(value, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number) or number < 0:
        return default
    return number


def _clean_count(value) -> int:
    number = _clean_number(value, 0.0)
    return int(number)


def _coerce(raw) -> dict:
    """Turn anything read from disk into a complete, sane record."""
    data = dict(_EMPTY)
    if not isinstance(raw, dict):
        return data
    data["time_saved_sec"] = _clean_number(raw.get("time_saved_sec"), 0.0)
    for key in ("words", "transcriptions", "sessions"):
        data[key] = _clean_count(raw.get(key))
    for key in ("first_used", "last_used"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            data[key] = value
    return data


def _read_file(path: Path) -> dict:
    try:
        # Regular files only. ``is_file()`` follows symlinks but rejects
        # everything else, which matters for two adversarial cases: ``open()`` on
        # a FIFO blocks *forever* (there is no writer), and a character device
        # like /dev/zero has no meaningful content. A missing file is also not a
        # regular file, so it reads as zeros without being created.
        if not path.is_file():
            return dict(_EMPTY)
        # Cheap early-out before opening: never open a file we know is not ours.
        if path.stat().st_size > MAX_STATS_BYTES:
            logger.warning(
                "Ignoring oversized stats file %s (limit %d bytes); it will be "
                "rewritten on the next dictation",
                path,
                MAX_STATS_BYTES,
            )
            return dict(_EMPTY)
        # Bounded read: even if the file grows between stat() and open(), we read
        # at most one byte past the cap and reject it. Memory is O(64 KiB).
        # encoding is explicit: Windows would otherwise use the ANSI codepage.
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read(MAX_STATS_BYTES + 1)
        if len(raw) > MAX_STATS_BYTES:
            logger.warning("Ignoring oversized stats file %s; rewriting it small", path)
            return dict(_EMPTY)
        return _coerce(json.loads(raw) if raw.strip() else None)
    except Exception as e:  # corrupt JSON, permissions, a directory, a device file, ...
        logger.warning("Could not read stats file %s (%s); starting from zero", path, e)
        return dict(_EMPTY)


def _cleanup_stale_temp_files(path: Path) -> None:
    """Delete ``<name>.tmp<pid>`` files left behind by crashed engines.

    Runs at most once per process — stale temp files can only come from a
    *previous* crash, so there is nothing new to find during a run, and the
    dictation path stays a single small write. Best effort, age-gated and never
    fatal: a young temp file may belong to a concurrently running engine.
    """
    global _cleaned_temp_files
    if _cleaned_temp_files:
        return
    _cleaned_temp_files = True
    try:
        cutoff = time.time() - STALE_TEMP_AGE_SEC
        # glob.escape: a filename containing metacharacters ([, ?, *) must not be
        # treated as a pattern, or the sweep could delete unrelated files that
        # happen to match.
        for stale in path.parent.glob(f"{glob.escape(path.name)}.tmp*"):
            try:
                if stale.stat().st_mtime < cutoff:
                    stale.unlink()
            except OSError:
                continue
    except Exception:
        pass


def _replace_with_retry(tmp: Path, path: Path, attempts: int = 3, delay: float = 0.02) -> None:
    """``os.replace`` with a short retry for Windows sharing violations.

    ``os.replace`` is atomic and overwrites on every platform, which is why it is
    used here rather than ``os.rename``. On Windows, though, it fails with
    ``PermissionError`` (WinError 32) while *any* other process has the file open
    -- an editor, a backup agent, an antivirus scanner. Those readers are
    momentary, so a couple of quick retries ride it out. Deliberately short: the
    dictation path must never be stalled, and losing one stat update is harmless.
    """
    for attempt in range(1, attempts + 1):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == attempts:
                raise
            time.sleep(delay)


def _write_file(path: Path, data: dict) -> bool:
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
        # newline="\n" disables text-mode translation, so the bytes written are
        # identical on Windows (which would otherwise emit CRLF) and elsewhere.
        # encoding is explicit for the same reason: never the Windows ANSI default.
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        # Same-directory replace is atomic, so a kill mid-write cannot truncate
        # the real file. No fsync: a ~200 byte write is cheap and a lost update on
        # power loss is not worth an fsync on the dictation path.
        _replace_with_retry(tmp, path)
        _cleanup_stale_temp_files(path)
        return True
    except Exception as e:
        logger.warning("Could not write stats file %s: %s", path, e)
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass
        return False


def _mutate(apply, now=None) -> dict:
    """Read-modify-write the record under the lock, then persist it."""
    with _lock:
        path = stats_path()
        data = _read_file(path)
        stamp = now or _now()
        if not data.get("first_used"):
            data["first_used"] = stamp
        data["last_used"] = stamp
        apply(data)
        _write_file(path, data)
        return data


def snapshot() -> dict:
    """Return the current lifetime totals (freshly read from disk).

    Deliberately read-only: a missing file reads as zeros and is *not* created.
    Use :func:`ensure` when the file itself must exist.
    """
    with _lock:
        return _read_file(stats_path())


def ensure() -> dict:
    """Guarantee the stats file exists, creating it with zeros if missing.

    The engine calls this at startup, so the file appears on first launch even
    before the user dictates anything. Idempotent; returns the current record.
    """
    with _lock:
        path = stats_path()
        if path.exists():
            return _read_file(path)
        data = dict(_EMPTY)
        _write_file(path, data)
        return data


def record_transcription(words, time_saved_sec, now=None) -> dict:
    """Fold one dictation into the lifetime totals and persist immediately.

    Returns the updated record. ``words`` and ``time_saved_sec`` are sanitized;
    negative/non-finite values contribute nothing.
    """
    words = _clean_count(words)
    saved = _clean_number(time_saved_sec, 0.0)

    def apply(data: dict) -> None:
        data["words"] += words
        data["time_saved_sec"] += saved
        data["transcriptions"] += 1

    return _mutate(apply, now=now)


def record_session_start(now=None) -> dict:
    """Count one engine launch in the lifetime totals and persist.

    Creates the file when it does not exist yet, so a first launch always
    leaves a ``stats.json`` behind — even with zero dictations.
    """
    return _mutate(lambda data: data.__setitem__("sessions", data["sessions"] + 1), now=now)


def reset() -> dict:
    """Wipe every lifetime total. Persists the empty record and returns it."""
    with _lock:
        path = stats_path()
        data = dict(_EMPTY)
        _write_file(path, data)
        return data
