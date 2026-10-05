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


def _read_text(path: str) -> Optional[str]:
    """Whole-file read that never raises; ``None`` when the file is not there."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception as e:  # missing file, permissions, not Linux at all
        logger.debug(f"Cannot read {path}: {e}")
        return None


def alsa_capture_cards() -> Dict[str, str]:
    """Card name -> card id for every ALSA card that can capture.

    PortAudio names a card's devices ``"<card name>: <pcm> (hw:N,M)"``, using
    the name from ``snd_ctl_card_info_get_name`` — which is the trailing field
    of a ``/proc/asound/cards`` line::

         4 [Snowball       ]: USB-Audio - Blue Snowball

    so matching a card against the device list is a substring test. Only cards
    with a capture PCM (``cardN/pcmNc``) matter for dictation. Returns ``{}``
    where there is no ``/proc/asound`` (native Windows, macOS).
    """
    cards: Dict[str, str] = {}
    listing = _read_text(f"{_PROC_ASOUND}/cards")
    if listing is None:
        return cards
    for line in listing.splitlines():
        match = re.match(r"\s*(\d+)\s+\[(\S+)\s*\]\s*:\s*\S+\s+-\s+(.+?)\s*$", line)
        if not match:
            continue
        index, card_id, name = match.group(1), match.group(2), match.group(3)
        try:
            captures = Path(f"{_PROC_ASOUND}/card{index}").glob("pcm*c")
            has_capture = any(captures)
        except Exception:
            has_capture = False
        if has_capture:
            cards[name] = card_id
    return cards


def active_source_holders() -> Dict[str, str]:
    """Source name -> the client capturing from it, from ``wpctl status``.

    A source another process is recording from is held open by PipeWire, and
    that is exactly what hides it from PortAudio's device enumeration. wpctl
    prints the holders in its *Streams* section: a client line followed by one
    line per stream, where an input stream reads
    ``input_MONO < Blue Snowball:capture_MONO [active]``. Only the capture
    direction (``<``) is recorded, and the name left of the port is the
    *source*, so callers match on it. Returns ``{}`` when wpctl is unavailable
    or reports nothing.
    """
    res = _wpctl("status")
    if res is None or res.returncode != 0:
        return {}

    holders: Dict[str, str] = {}
    section = ""
    client: Optional[str] = None
    for line in (res.stdout or "").splitlines():
        # Strip the tree glyphs wpctl draws the hierarchy with, then read what
        # is left: a bare word ("Audio", "Streams:") opens a section, a
        # "<id>. <name>" line is either a client or one of its streams.
        head = re.sub(r"^[\s\u2500-\u257f]+", "", line).strip()
        if not head:
            continue
        if re.match(r"^[A-Za-z][A-Za-z /]*:?$", head):
            section = head.rstrip(":").strip().lower()
            client = None
            continue
        if section != "streams":
            continue
        # A stream line is one that points somewhere ("<" is capture, ">" is
        # playback); only the capture side names a source that we care about, and
        # either side must be consumed so its "<id>. <port>" is not mistaken for
        # a client name. The node naming the source can contain spaces, so take
        # everything up to the port that follows it.
        if "<" in line or ">" in line:
            stream = re.search(r"<\s*(.+?)\s*(?:\[[^\]]*\])?\s*$", line)
            if stream and client:
                source = stream.group(1).rsplit(":", 1)[0].strip()
                if source:
                    holders.setdefault(source, client)
            continue
        node = re.match(r"\s*\d+\.\s+(\S.*?)\s*$", line)
        if node:
            client = node.group(1)
    return holders


def missing_input_devices(device_names: Iterable[str]) -> List[Dict[str, Any]]:
    """Inputs the system has that PortAudio left out, and who is holding them.

    ``device_names`` is what ``sounddevice.query_devices()`` reports. Any card
    with a capture PCM in ``/proc/asound`` that no reported device name mentions
    is one PortAudio could not open while building its list — almost always
    because something else already had it (``holder`` names that app when wpctl
    can tell, else ``None``). These are the devices a re-scan can bring back, so
    naming them is the actionable part of a failed re-scan. Empty on hosts whose
    cards cannot be enumerated at all.
    """
    reported = [str(name).lower() for name in device_names]
    holders = active_source_holders()
    missing: List[Dict[str, Any]] = []
    for name, card_id in alsa_capture_cards().items():
        if any(name.lower() in device for device in reported):
            continue
        holder = next(
            (client for source, client in holders.items() if name.lower() in source.lower()),
            None,
        )
        missing.append({"name": name, "card_id": card_id, "holder": holder})
    return missing
