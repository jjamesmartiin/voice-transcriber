"""Microphone capture-state guard: see a mute, never leak one.

A muted PipeWire/PulseAudio source is invisible to every other check in this
codebase. The device opens fine, reports the right channel count and sample
rate, and then hands back a stream of exact zeros — so `doctor`, `sounddevice`,
and the app's own device list all report a healthy microphone while the user
hears nothing and Discord says *"no audio input detected"*. That is exactly how
a muted Blue Snowball hid here: the only way to see it was to record and measure
the samples.

This module therefore answers four questions instead of one:

* **Is the default input muted right now?** — :func:`get_default_source_mute`
* **How does the user fix it?** — :data:`SOURCE_MUTE_FIX` / :func:`unmute_default_source`
* **If app code ever mutes it, who unmutes it?** — :func:`preserve_default_source_mute`
* **Which inputs can PortAudio not see, and who is holding them?** —
  :func:`missing_input_devices` (a device another app is capturing from is
  held open by PipeWire, so PortAudio omits it from its list entirely — the
  Blue Snowball vanished from the picker that way)

Voice Transcriber never mutes a source today. The guard exists so that stays
true by construction: any future audio test, level calibration, or settings
toggle that touches the mute wraps its work in ``preserve_default_source_mute()``,
which snapshots the previous state and restores it in a ``finally`` — on success,
on exception, and on early return. A mute must not outlive the code that set it.

Detection is read-only and cheap. Repair is always explicit (``doctor --fix``):
a mute can be deliberate, so nothing here unmutes on its own.

Implementation notes
--------------------
* ``wpctl`` (WirePlumber's CLI) is the interface, capability-probed with
  ``shutil.which`` — the same shape as the Bluetooth hands-free policy in
  ``t2.py``. Hosts without it (native Windows, macOS, a machine without
  PipeWire) report ``supported: False`` and every call is a no-op, so the HAL's
  ban on branching on the OS is respected: this is a capability test, not a
  ``sys.platform`` test.
* Every subprocess call is bounded and every failure is swallowed. A broken or
  missing audio stack must never raise into the app — the worst case here is
  "state unknown", which is reported as unknown rather than as healthy.
"""
from __future__ import annotations

import contextlib
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

#: wpctl's alias for "whatever the system default input is right now", so a
#: mute is tracked across device changes instead of pinning one node id.
DEFAULT_SOURCE = "@DEFAULT_AUDIO_SOURCE@"

#: Copy-pasteable repair, quoted verbatim in `doctor` output and app warnings.
SOURCE_MUTE_FIX = "wpctl set-mute @DEFAULT_AUDIO_SOURCE@ 0"

_WPCTL_TIMEOUT = 2.0

#: Where the kernel lists sound cards and their PCMs. Absent on native Windows
#: and macOS, which is how the ALSA-specific diagnostics probe for support.
_PROC_ASOUND = "/proc/asound"


def wpctl_available() -> bool:
    """True when WirePlumber's CLI is on PATH (i.e. this host has PipeWire)."""
    try:
        return shutil.which("wpctl") is not None
    except Exception:
        return False


def _wpctl(*args: str) -> Optional[subprocess.CompletedProcess]:
    """Run ``wpctl <args>``; return None when it cannot be run or read at all.

    ``subprocess.run`` is looked up on the module (not imported by name) so tests
    can monkeypatch it without touching any real audio hardware.
    """
    if not wpctl_available():
        return None
    try:
        return subprocess.run(
            ["wpctl", *args],
            capture_output=True,
            text=True,
            timeout=_WPCTL_TIMEOUT,
        )
    except Exception as e:  # missing binary, timeout, sandbox, permission
        logger.debug(f"wpctl {' '.join(args)} unavailable: {e}")
        return None


def get_default_source_mute() -> Optional[bool]:
    """Mute state of the default input, or ``None`` when it cannot be determined.

    ``None`` means *unknown* (no wpctl, no default source, timeout, sandbox) —
    callers must not read it as "unmuted". ``wpctl get-volume`` prints
    ``Volume: 0.44 [MUTED]`` for a muted node and ``Volume: 0.44`` otherwise.
    """
    res = _wpctl("get-volume", DEFAULT_SOURCE)
    if res is None or res.returncode != 0:
        return None
    return "[MUTED]" in (res.stdout or "")


def get_default_source_name() -> Optional[str]:
    """Human name of the default input (``node.description``), else ``None``.

    ``wpctl inspect`` marks the requested node with a leading ``*``:
    ``  * node.description = "Blue Snowball Mono"``.
    """
    res = _wpctl("inspect", DEFAULT_SOURCE)
    if res is None or res.returncode != 0:
        return None
    for line in (res.stdout or "").splitlines():
        stripped = line.strip().lstrip("*").strip()
        if stripped.startswith("node.description ="):
            value = stripped.split("=", 1)[1].strip().strip('"')
            if value:
                return value
    return None


def set_default_source_mute(muted: bool) -> bool:
    """Set the default input's mute state. True when wpctl accepted it."""
    res = _wpctl("set-mute", DEFAULT_SOURCE, "1" if muted else "0")
    return bool(res is not None and res.returncode == 0)


def unmute_default_source() -> bool:
    """Unmute the default input, and confirm it took effect.

    Returns True when the input is unmuted afterwards. Already-unmuted is a
    success and issues no command; unknown state is a failure (we cannot claim
    to have fixed something we could not read).
    """
    if get_default_source_mute() is False:
        return True
    if not set_default_source_mute(False):
        return False
    return get_default_source_mute() is False


def describe_state() -> Dict[str, Any]:
    """Read-only snapshot for diagnostics.

    ``muted`` is True / False / ``None`` (unknown); ``supported`` says whether
    this host exposes the state at all, so callers on Windows/macOS can skip
    reporting it instead of printing a permanent "unknown".
    """
    supported = wpctl_available()
    if not supported:
        return {
            "supported": False,
            "muted": None,
            "name": None,
            "fix_command": SOURCE_MUTE_FIX,
        }
    return {
        "supported": True,
        "muted": get_default_source_mute(),
        "name": get_default_source_name(),
        "fix_command": SOURCE_MUTE_FIX,
    }


@contextlib.contextmanager
def preserve_default_source_mute():
    """Restore the default input's mute state when the block exits.

    Yields the state observed on entry (True/False/None). Anything that mutes
    inside the block is undone on the way out, so a mute cannot leak into the
    user's system and cannot silently persist into their next call:

        with preserve_default_source_mute():
            set_default_source_mute(True)     # e.g. an audio self-test
            ...                               # mic is restored even if this raises

    Best-effort by design: a failure to read or restore is logged and swallowed.
    Failing to clean up an audio test must never crash dictation.
    """
    before = get_default_source_mute()
    try:
        yield before
    finally:
        try:
            if before is None:
                return
            if get_default_source_mute() != before:
                restored = set_default_source_mute(before)
                logger.info(
                    "Restored default microphone mute state to %s%s",
                    "muted" if before else "unmuted",
                    "" if restored else " (wpctl reported failure)",
                )
        except Exception as e:
            logger.debug(f"Could not restore microphone mute state: {e}")


