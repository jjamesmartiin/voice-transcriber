#!/usr/bin/env python3
# Optimized script to record audio and transcribe it with minimal latency
# Updated with sounddevice for robust audio capture

import os
import sys

from voice_transcriber import keybinds

_current_t2 = sys.modules.get(__name__)
if _current_t2 is not None:
    sys.modules.setdefault("t2", _current_t2)
    sys.modules.setdefault("voice_transcriber.t2", _current_t2)

# Auto-configure WSL2 audio passthrough via WSLg PulseAudio socket if running in WSL
if "PULSE_SERVER" not in os.environ:
    if os.path.exists("/mnt/wslg/runtime-dir/pulse/native"):
        os.environ["PULSE_SERVER"] = "unix:/mnt/wslg/runtime-dir/pulse/native"
    elif os.path.exists("/mnt/wslg/PulseServer"):
        os.environ["PULSE_SERVER"] = "/mnt/wslg/PulseServer"

def _is_wsl() -> bool:
    if sys.platform == "win32":
        return False
    if "microsoft" in os.environ.get("WSL_DISTRO_NAME", "").lower():
        return True
    if os.path.exists("/mnt/wslg") or "WSL_INTEROP" in os.environ:
        return True
    if hasattr(os, "uname"):
        return "microsoft" in os.uname().release.lower()
    return False

# In WSL, ALSA needs to be told to use PulseAudio explicitly, otherwise PortAudio finds 0 devices
if _is_wsl():
    alsa_conf_path = "/tmp/vt-alsa-pulse.conf"
    if not os.path.exists(alsa_conf_path):
        with open(alsa_conf_path, "w") as f:
            f.write("pcm.!default { type pulse }\nctl.!default { type pulse }\n")
    os.environ["ALSA_CONFIG_PATH"] = alsa_conf_path

import queue
import threading
import atexit
import time
import numpy as np
import sounddevice as sd
import logging
logger = logging.getLogger(__name__)
from transcribe2 import transcribe_audio, get_model
import transcribe2
import json
import re
import tempfile
import contextlib
import subprocess
from pathlib import Path

# Tracking the most recent model preload thread
# This allows the main app to wait for it before recording
active_preload_thread = None

def preload_model(device="cpu"):
    """Wrapper for preloading models that tracks the thread"""
    global active_preload_thread
    active_preload_thread = transcribe2.preload_model(device=device)
    return active_preload_thread

# Suppress ALSA/PortAudio error spam
@contextlib.contextmanager
def silence_stderr():
    """Context manager to silence stderr at the OS level (hides C library errors)"""
    new_target = os.open(os.devnull, os.O_WRONLY)
    old_target = os.dup(sys.stderr.fileno())
    try:
        os.dup2(new_target, sys.stderr.fileno())
        yield
    finally:
        os.dup2(old_target, sys.stderr.fileno())
        os.close(new_target)
        os.close(old_target)

# Get appropriate directories for file storage
def get_data_dir():
    """Get appropriate data directory for config and temporary files"""
    if 'XDG_DATA_HOME' in os.environ:
        data_dir = Path(os.environ['XDG_DATA_HOME']) / 'vt'
    elif os.name == 'posix':
        data_dir = Path.home() / '.local' / 'share' / 'vt'
    else:
        data_dir = Path.home() / 'AppData' / 'Local' / 'vt'

    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return data_dir

def get_temp_dir():
    """Get temporary directory for audio files"""
    if 'XDG_RUNTIME_DIR' in os.environ:
        temp_dir = Path(os.environ['XDG_RUNTIME_DIR']) / 'vt'
        try:
            temp_dir.mkdir(parents=True, exist_ok=True)
            return temp_dir
        except OSError:
            pass
    return Path(tempfile.gettempdir())

# Audio configuration
CHANNELS = 1
RATE = 16000
RECORD_SECONDS = 20
INPUT_DEVICE_INDEX = None
PRIMARY_DEVICE_NAME = None
SECONDARY_DEVICE_NAME = None
LAST_USED_DEVICE_NAME = "Unknown"
ACTUAL_RATE = RATE
OVERRIDE_MODE = 'auto' # 'auto', 'primary', or 'secondary'
MODEL_BACKEND = "cohere"  # Cohere Transcribe is the only ASR backend
COPY_TO_CLIPBOARD = True
AUTO_TYPE = False
OUTPUT_MODES = ["clipboard", "type", "type_fast"]
OUTPUT_MODE = "clipboard"
AUTO_TYPE_TRAILING_SPACE = True
AUTO_TYPE_AUTO_PUNCTUATE = True
IS_MUTED = True
NUMBER_MODE = "auto"  # Spoken-number formatting: "auto" | "digits" | "words"
NUMBER_DIGITS = True  # Back-compat mirror: False only when NUMBER_MODE == "words"
SERIAL_COLLAPSE = True  # Serial numbers / codes / NATO strings collapse to one token ("A B C 1 2 3" -> "ABC123")
SPELL_COMMAND = True  # Verbal "spell C A T" -> "CAT" command processing
MIDDLE_CLICK_ENABLED = False  # Push-to-talk by holding middle mouse button (>= 0.25s)
HOTKEY_BINDS = keybinds.parse_binds(None)  # Push-to-talk chords; default is hold Alt+Shift
KEEP_BLUETOOTH_HANDSFREE = True  # Prevent WirePlumber/PipeWire from auto-reverting to headphone profile (pausing media)
SOUND_THEME = "proximity"
UI_THEME = "auto"
PUNCTUATION_MODE = "full"
PUNCTUATION_MODES = ["full", "no_terminal_period", "no_punctuation", "aesthetic_lowercase", "gen_z"]
#: Structured output (spoken lists -> bullets). "off" is the shipped default.
STRUCTURE_MODE = "off"
STRUCTURE_MODES = ["off", "inline", "blocks"]
LANGUAGE = "en"
WAIT_FOR_MODEL_ON_STARTUP = True
ENABLE_SLM = False
TYPING_WPM = 40

# Shipped defaults for every user-tunable setting. Applied by
# reset_to_defaults() (settings modal -> "Reset to Defaults"), and the target
# new installs converge on. Chosen to be the most intuitive out-of-the-box
# setup rather than an arbitrary factory state: phone-style formatting, numbers
# as written, fast auto-type, muted until asked, middle-click PTT opt-in.
# Microphone selection and the custom dictionary are deliberately absent: a
# reset must never lose the chosen device or the user's own vocabulary.
DEFAULT_SETTINGS = {
    'MODEL_BACKEND': "cohere",
    'OVERRIDE_MODE': 'auto',
    'OUTPUT_MODE': "type_fast",
    'AUTO_TYPE': True,
    'COPY_TO_CLIPBOARD': False,
    'AUTO_TYPE_TRAILING_SPACE': True,
    'AUTO_TYPE_AUTO_PUNCTUATE': False,
    'IS_MUTED': True,
    'NUMBER_MODE': "auto",
    'NUMBER_DIGITS': True,
    'SERIAL_COLLAPSE': True,
    'SPELL_COMMAND': True,
    'MIDDLE_CLICK_ENABLED': False,
    'KEEP_BLUETOOTH_HANDSFREE': True,
    'SOUND_THEME': "proximity",
    'UI_THEME': "red",
    'PUNCTUATION_MODE': "no_punctuation",
    'STRUCTURE_MODE': "off",
    'LANGUAGE': "en",
    'WAIT_FOR_MODEL_ON_STARTUP': True,
    'ENABLE_SLM': False,
    'TYPING_WPM': 40,
    # Binds, not config-shaped mappings: reset_to_defaults assigns this value
    # straight onto the HOTKEY_BINDS global, and test_settings_menu asserts the
    # global equals the default, so the two must be the same kind of object.
    'HOTKEY_BINDS': list(keybinds.DEFAULT_BINDS),
}


def _load_hotkey_binds(raw):
    """Parse the configured push-to-talk binds, falling back to the default.

    A hand-edited config with a typo must not take the app down, so an unusable
    entry logs and keeps the shipped chord -- and, because falling back
    silently could leave someone pressing a hotkey that no longer exists, the
    warning names both the error and the chord that is in force.
    """
    try:
        return keybinds.parse_binds(raw)
    except keybinds.KeybindError as e:
        logger.warning(
            f"⚠️  Ignoring invalid hotkeys config ({e}); "
            f"using {keybinds.format_chord(keybinds.DEFAULT_CHORD)}"
        )
        return list(keybinds.DEFAULT_BINDS)


def _load_model_backend(raw):
    """Resolve the configured ASR backend, falling back to the shipped one.

    A hand-edited config with a typo must not take the app down, so an unknown
    name logs and keeps the default -- and, because falling back silently could
    leave someone believing they had switched models, the warning names both the
    error and the backend that is actually in force.
    """
    try:
        return transcribe2.set_backend(raw)
    except transcribe2.UnknownBackendError as e:
        logger.warning(
            f"⚠️  Ignoring invalid model_backend config ({e}); "
            f"using {transcribe2.DEFAULT_BACKEND}"
        )
        return transcribe2.set_backend(transcribe2.DEFAULT_BACKEND)


GLOBAL_CONFIG_FILE = get_data_dir() / 'audio_device_config.json'

_TOML_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _toml_string(value: str) -> str:
    """Serialize a Python string as a TOML basic string (always quoted)."""
    out = ['"']
    for ch in value:
        if ch == '\\':
            out.append('\\\\')
        elif ch == '"':
            out.append('\\"')
        elif ch == '\n':
            out.append('\\n')
        elif ch == '\r':
            out.append('\\r')
        elif ch == '\t':
            out.append('\\t')
        elif ch == '\b':
            out.append('\\b')
        elif ch == '\f':
            out.append('\\f')
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append('\\u%04X' % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _toml_key(key) -> str:
    """Serialize a key: bare when TOML allows it, quoted otherwise (e.g. 'deep seq')."""
    text = str(key)
    if text and _TOML_BARE_KEY_RE.match(text):
        return text
    return _toml_string(text)


def _toml_value(value):
    """Serialize a scalar/list value; returns None for values TOML cannot express."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        import math
        if math.isnan(value) or math.isinf(value):
            return None
        return repr(value)
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, (list, tuple)):
        parts = [_toml_value(v) for v in value]
        if any(p is None for p in parts):
            return None
        return "[" + ", ".join(parts) + "]"
    return None


def _dump_toml(d: dict) -> str:
    """Dependency-free serializer for the app config to TOML.

    Handles the shape we actually write: a flat table of scalars/lists plus
    nested tables (notably ``[dictionary]``, whose keys may contain spaces and
    therefore must be quoted). Values TOML cannot express (None, NaN, inf,
    unsupported types) are skipped rather than emitting invalid syntax.
    """
    lines = []

    def emit(table: dict, prefix: str) -> None:
        subtables = []
        for key, value in table.items():
            if isinstance(value, dict):
                subtables.append((key, value))
                continue
            literal = _toml_value(value)
            if literal is None:
                continue
            lines.append(f"{_toml_key(key)} = {literal}")
        for key, value in subtables:
            name = f"{prefix}.{_toml_key(key)}" if prefix else _toml_key(key)
            if lines:
                lines.append("")
            lines.append(f"[{name}]")
            emit(value, name)

    emit(d, "")
    return "\n".join(lines) + "\n" if lines else ""

def get_config_file():
    """Find local project config.yaml/config.yml/config.json/config.toml (in root or config/ dir), fallback to global data dir"""
    candidates = [
        Path('config.toml'),
        Path('config/config.toml'),
        Path('config.yaml'),
        Path('config.yml'),
        Path('config/config.yaml'),
        Path('config/config.yml'),
        Path('config.json'),
        Path('config/config.json'),
        Path('audio_device_config.json'),
        Path('config/audio_device_config.json'),
        GLOBAL_CONFIG_FILE
    ]
    for loc in candidates:
        if loc.exists():
            return loc.resolve()
    return GLOBAL_CONFIG_FILE

CONFIG_FILE = get_config_file()


def set_default_input_device(index):
    """Set sounddevice default input device while strictly preserving the default output device."""
    try:
        current = sd.default.device
        out_dev = current[1] if isinstance(current, (list, tuple)) and len(current) > 1 else None
        sd.default.device = (index, out_dev)
    except Exception:
        try:
            sd.default.device = (index, None)
        except Exception:
            pass


def get_input_devices():
    """Return a list of available input audio devices with metadata, prioritizing system defaults."""
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        server_devices = []
        hardware_devices = []
        default_in = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else None

        for i, d in enumerate(devices):
            if d.get('max_input_channels', 0) > 0:
                name = d.get('name', f'Device {i}')
                lower = name.lower()
                is_cur = False
                if PRIMARY_DEVICE_NAME and PRIMARY_DEVICE_NAME.lower() in lower:
                    is_cur = True
                elif INPUT_DEVICE_INDEX is not None and i == INPUT_DEVICE_INDEX:
                    is_cur = True
                elif INPUT_DEVICE_INDEX is None and (i == default_in or lower == 'default'):
                    is_cur = True

                display_name = name
                if lower == 'default':
                    display_name = 'System Default (Recommended)'
                elif lower == 'pipewire':
                    display_name = 'PipeWire Sound Server'
                elif lower == 'pulse':
                    display_name = 'PulseAudio Sound Server'

                item = {
                    'index': i,
                    'name': name,
                    'display_name': display_name,
                    'channels': d.get('max_input_channels', 1),
                    'is_default': (i == default_in or lower == 'default'),
                    'is_active': is_cur,
                }
                if lower in ('default', 'pipewire', 'pulse'):
                    server_devices.append(item)
                else:
                    hardware_devices.append(item)

        # System default first, then PipeWire, then hardware devices
        server_devices.sort(key=lambda x: 0 if x['name'].lower() == 'default' else 1)
        return server_devices + hardware_devices
    except Exception as e:
        logger.debug(f"Failed to query input devices: {e}")
        return []


def _reselect_input_device(devices=None):
    """Re-point the configured mic at its index in a freshly enumerated list.

    A device index is only a position in PortAudio's list, so it shifts the
    moment a device appears or disappears (on the maintainer's machine
    ``default`` moved 9 -> 10 when the Blue Snowball came back). The configured
    *name* is the stable identity, so the selection is re-resolved from it. An
    index that no longer exists is dropped rather than kept, so the auto logic
    picks a working device at record time instead of failing on a stale one.
    """
    global INPUT_DEVICE_INDEX
    if devices is None:
        devices = get_input_devices()

    if OVERRIDE_MODE == 'secondary' and SECONDARY_DEVICE_NAME:
        wanted = SECONDARY_DEVICE_NAME
    else:
        wanted = PRIMARY_DEVICE_NAME

    before = INPUT_DEVICE_INDEX
    if wanted:
        idx = find_device_index(wanted)
        if idx is not None:
            INPUT_DEVICE_INDEX = idx
            set_default_input_device(idx)
    elif INPUT_DEVICE_INDEX is not None and INPUT_DEVICE_INDEX >= len(devices):
        INPUT_DEVICE_INDEX = None

    if INPUT_DEVICE_INDEX != before:
        logger.info(f"Microphone selection re-resolved: index {before} -> {INPUT_DEVICE_INDEX}")
        save_audio_config()
    return wanted or None


def _mic_rescan_notice(summary):
    """One line naming why an input is still unlisted, for the modal and TUI."""
    parts = []
    for dev in summary.get("missing") or []:
        if dev.get("holder"):
            parts.append(f"{dev['name']} is being used by {dev['holder']}")
        else:
            parts.append(f"{dev['name']} is busy or unusable")
    return "; ".join(parts)


def rescan_audio_devices():
    """Re-enumerate audio devices, then re-point the selected mic at its index.

    PortAudio builds its device list once, inside ``Pa_Initialize()``, and caches
    it for the life of the process. A device it cannot open *at that moment* is
    left out of the list entirely rather than marked unavailable, so a mic that
    something else held when the app started (PipeWire routing the default
    source, another app's level meter, a browser) stays invisible to the picker
    until the process restarts. Re-initialising is the only way to ask PortAudio
    to look again.

    Precondition: nothing may be capturing. ``Pa_Terminate()`` closes every open
    stream (``CloseOpenStreams`` in pa_front.c), so the warm input-stream cache
    is dropped here first and callers must be idle.

    Returns a summary: ``ok``, ``count``, ``devices`` (names), ``device`` (the
    re-resolved selection), ``missing`` (cards PortAudio still does not list,
    each with whoever holds it — see ``audio_state.missing_input_devices``) and a
    human-readable ``message``.
    """
    summary = {
        "ok": False,
        "count": 0,
        "devices": [],
        "device": None,
        "missing": [],
        "notice": "",
        "message": "",
    }
    try:
        _close_cached_input_streams()
        with silence_stderr():
            sd._terminate()
            sd._initialize()
    except Exception as e:
        logger.warning(f"Audio device re-scan failed: {e}")
        summary["message"] = f"Audio device re-scan failed: {e}"
        return summary

    devices = get_input_devices()
    summary["devices"] = [d.get("name") for d in devices if d.get("name")]
    summary["count"] = len(devices)
    summary["device"] = _reselect_input_device(devices)
    summary["ok"] = True

    try:
        import audio_state
        summary["missing"] = audio_state.missing_input_devices(summary["devices"])
    except Exception as e:
        logger.debug(f"Could not check for unlisted inputs: {e}")

    plural = "" if summary["count"] == 1 else "s"
    message = f"{summary['count']} input device{plural} found"
    notice = _mic_rescan_notice(summary)
    summary["notice"] = notice
    summary["message"] = f"{message} · still missing: {notice}" if notice else message

    try:
        prewarm_input_stream()
    except Exception as e:
        logger.debug(f"Could not re-prewarm the input stream: {e}")
    return summary


def get_wireplumber_bt_autoswitch():
    """Check if WirePlumber autoswitch to headset profile is enabled."""
    import shutil
    if not shutil.which('wpctl'):
        return None
    try:
        res = subprocess.run(['wpctl', 'settings', 'bluetooth.autoswitch-to-headset-profile'],
                             capture_output=True, text=True, timeout=2)
        if 'Value: true' in res.stdout:
            return True
        elif 'Value: false' in res.stdout:
            return False
    except Exception:
        pass
    return None


def set_wireplumber_bt_autoswitch(enable_autoswitch: bool):
    """Enable or disable WirePlumber autoswitch to headset profile."""
    import shutil
    if not shutil.which('wpctl'):
        return False
    val_str = 'true' if enable_autoswitch else 'false'
    try:
        res = subprocess.run(['wpctl', 'settings', '-s', 'bluetooth.autoswitch-to-headset-profile', val_str],
                             capture_output=True, text=True, timeout=2)
        if res.returncode == 0:
            return True
        res = subprocess.run(['wpctl', 'settings', 'bluetooth.autoswitch-to-headset-profile', val_str],
                             capture_output=True, text=True, timeout=2)
        return res.returncode == 0
    except Exception:
        return False


# Snapshot of WirePlumber's Bluetooth headset-profile autoswitch *before* this
# process changed it, so quitting restores the system instead of leaving it
# reconfigured. None = not read yet (and "unreadable" means "do not touch").
_bt_autoswitch_before = None
_bt_restore_registered = False


def _register_exit_hook(fn):
    """Indirection over atexit.register so tests can observe the hook."""
    atexit.register(fn)


def restore_bluetooth_autoswitch():
    """Put WirePlumber's Bluetooth autoswitch setting back the way we found it.

    Registered at exit by :func:`apply_bluetooth_handsfree_policy`, so the
    setting is a loan, not a permanent edit. Safe to call more than once and
    safe to call when nothing was ever changed.
    """
    global _bt_autoswitch_before, _bt_restore_registered
    before = _bt_autoswitch_before
    _bt_autoswitch_before = None
    _bt_restore_registered = False
    if before is None:
        return False
    if get_wireplumber_bt_autoswitch() == before:
        return True
    restored = set_wireplumber_bt_autoswitch(before)
    if restored:
        logger.info(
            "Restored Bluetooth autoswitch-to-headset-profile to %s on exit",
            "on" if before else "off",
        )
    return restored


def apply_bluetooth_handsfree_policy(keep_handsfree: bool = True):
    """Apply the policy to keep Bluetooth devices in hands-free mode (prevents media pause on record end).

    This setting is global and persisted by WirePlumber, so the previous value is
    snapshotted before the first change and put back on exit — the app must not
    leave the user's system reconfigured after it quits (the same rule as the
    microphone mute in ``audio_state``). If the current value cannot be read
    there is nothing to restore, so the policy is skipped rather than applied
    blind.
    """
    global _bt_autoswitch_before, _bt_restore_registered
    current = get_wireplumber_bt_autoswitch()
    if current is None:
        logger.debug("wpctl could not report the Bluetooth autoswitch setting; leaving it untouched")
        return False
    if _bt_autoswitch_before is None:
        _bt_autoswitch_before = current
    if not _bt_restore_registered:
        _register_exit_hook(restore_bluetooth_autoswitch)
        _bt_restore_registered = True
    desired = not keep_handsfree
    if current == desired:
        return True
    return set_wireplumber_bt_autoswitch(desired)


def find_device_index(name):
    """Find device index by name substring match"""
    if not name:
        return None
    try:
        with silence_stderr():
            devices = sd.query_devices()
        for i, d in enumerate(devices):
            if d['max_input_channels'] > 0 and name.lower() in d['name'].lower():
                return i
    except Exception:
        pass
    return None

def get_active_device_name(include_model=True):
    """Return the name of the device that will be used or was last used"""
    global LAST_USED_DEVICE_NAME

    prefix = f"{MODEL_BACKEND.capitalize()}: " if include_model else ""

    if OVERRIDE_MODE == 'primary' and PRIMARY_DEVICE_NAME:
        return f"{prefix}Primary: {PRIMARY_DEVICE_NAME}"
    elif OVERRIDE_MODE == 'secondary' and SECONDARY_DEVICE_NAME:
        return f"{prefix}Secondary: {SECONDARY_DEVICE_NAME}"

    # In auto mode, try to find what would be used
    if PRIMARY_DEVICE_NAME:
        idx = find_device_index(PRIMARY_DEVICE_NAME)
        if idx is not None:
            return f"{prefix}Primary: {PRIMARY_DEVICE_NAME}"

    try:
        if INPUT_DEVICE_INDEX is not None:
            with silence_stderr():
                d = sd.query_devices(INPUT_DEVICE_INDEX)
                return f"{prefix}{d['name']}"
        default_in = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else sd.default.device
        if default_in is not None and default_in >= 0:
            with silence_stderr():
                d = sd.query_devices(default_in)
                return f"{prefix}{d['name']} (Default)"
    except Exception:
        pass

    return f"{prefix}{LAST_USED_DEVICE_NAME}"

def check_microphone_health():
    """
    Checks if an active microphone source is available.
    Returns: (is_healthy: bool, issues: list of str)
    """
    issues = []

    # 1. PulseAudio source verification (WSL / Linux)
    if os.path.exists("/mnt/wslg") or os.environ.get("WSL_DISTRO_NAME"):
        try:
            import subprocess
            res = subprocess.run(["pactl", "list", "sources", "short"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0:
                lines = [line for line in res.stdout.strip().split("\n") if line]
                sources = [line.split()[1] for line in lines if len(line.split()) >= 2]
                real_mics = [s for s in sources if not s.endswith(".monitor")]
                if not real_mics:
                    issues.append("WSLg audio bridge has no active microphone source (only speaker monitor was found).")
        except Exception:
            pass

    # 2. SoundDevice device query
    try:
        devs = sd.query_devices()
        input_devs = [d for d in devs if d.get('max_input_channels', 0) > 0]
        if not input_devs:
            issues.append("No audio input devices detected by PortAudio.")
    except Exception as e:
        issues.append(f"PortAudio error: {e}")

    # 3. Muted default source. A mute is invisible to every other check above:
    #    the device opens fine, reports sane channels/rates, and then records pure
    #    silence, so PortAudio reports a perfectly healthy microphone while the
    #    user hears nothing and Discord says "no audio input detected".
    try:
        import audio_state
        state = audio_state.describe_state()
        if state.get("muted"):
            label = state.get("name") or "the default input"
            issues.append(
                f"Default microphone '{label}' is MUTED — recording will capture silence "
                f"(Discord reports \"no audio input detected\"). Unmute with: {state['fix_command']}"
            )
    except Exception as e:
        logger.debug(f"Muted-source check skipped: {e}")

    return (len(issues) == 0), issues

def reset_terminal():
    """Reset terminal settings and clipboard processes if they become wonky"""
    try:
        import os
        import subprocess
        # Only run external 'reset' command when NOT running inside vt-tui (which owns raw mode)
        if sys.platform != "win32" and not os.environ.get("VT_TUI_SOCKET") and not os.environ.get("VT_TUI_BIN"):
            os.system('reset')

        # Kill stuck clipboard processes (Wayland)
        if sys.platform != "win32":
            try:
                subprocess.run(['pkill', 'wl-copy'], stderr=subprocess.DEVNULL)
                subprocess.run(['pkill', 'wl-paste'], stderr=subprocess.DEVNULL)
            except Exception:
                pass

        # Also re-initialize termios just in case (only if not in vt-tui)
        if sys.platform != "win32" and not os.environ.get("VT_TUI_SOCKET") and not os.environ.get("VT_TUI_BIN"):
            try:
                import termios
                fd = sys.stdin.fileno()
                termios.tcgetattr(fd)
            except Exception:
                pass
    except Exception:
        pass

# Global device variable
def get_device():
    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        device = "cpu"
    return device

DEVICE = get_device()

# Audio buffering
stop_recording = threading.Event()


def _normalize_number_mode(value) -> str:
    """Coerce bool / "1"/"0" / mode string into "auto" | "digits" | "words"."""
    try:
        from post_processor import normalize_number_mode
        return normalize_number_mode(value)
    except Exception:
        if isinstance(value, bool):
            return "digits" if value else "words"
        mode = str(value).strip().lower() if value is not None else "auto"
        if mode in ("digits", "1", "true", "yes", "on"):
            return "digits"
        if mode in ("words", "0", "false", "no", "off"):
            return "words"
        return "auto"


def _normalize_bool(value, default: bool = False) -> bool:
    """Coerce common truthy/falsey config spellings into a bool."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "on", "enabled", "enable")


def load_audio_config(file_path=None):
    """Load audio device configuration from local file with fallback"""
    global INPUT_DEVICE_INDEX, PRIMARY_DEVICE_NAME, SECONDARY_DEVICE_NAME, OVERRIDE_MODE, MODEL_BACKEND, COPY_TO_CLIPBOARD, IS_MUTED, AUTO_TYPE, OUTPUT_MODE, AUTO_TYPE_TRAILING_SPACE, AUTO_TYPE_AUTO_PUNCTUATE, NUMBER_DIGITS, NUMBER_MODE, SERIAL_COLLAPSE, SPELL_COMMAND, MIDDLE_CLICK_ENABLED, HOTKEY_BINDS, KEEP_BLUETOOTH_HANDSFREE, LANGUAGE, WAIT_FOR_MODEL_ON_STARTUP, ENABLE_SLM, SOUND_THEME, UI_THEME, PUNCTUATION_MODE, STRUCTURE_MODE, TYPING_WPM, CONFIG_FILE
    if file_path is not None:
        CONFIG_FILE = Path(file_path)
    else:
        # Always re-resolve (honors monkeypatched get_config_file in tests, and
        # picks up any new config file created since import)
        CONFIG_FILE = get_config_file()
    try:
        config = {}
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r') as f:
                content = f.read()
            if CONFIG_FILE.suffix == '.toml':
                try:
                    import tomllib
                    config = tomllib.loads(content)
                except Exception as e:
                    logger.warning(f"⚠️  Could not parse {CONFIG_FILE} as TOML ({e}); using defaults (file left untouched).")
                    config = {}
            elif CONFIG_FILE.suffix in ['.yaml', '.yml']:
                try:
                    import yaml
                    config = yaml.safe_load(content) or {}
                except Exception:
                    config = json.loads(content) if content.strip() else {}
            else:
                config = json.loads(content) if content.strip() else {}

            PRIMARY_DEVICE_NAME = config.get('primary_device_name')
            SECONDARY_DEVICE_NAME = config.get('secondary_device_name')
            OVERRIDE_MODE = config.get('override_mode', 'auto')
            IS_MUTED = config.get('is_muted', True)
            raw_out_mode = config.get('output_mode')
            if raw_out_mode in OUTPUT_MODES:
                OUTPUT_MODE = raw_out_mode
            elif raw_out_mode in ("paste", "paste_terminal"):
                OUTPUT_MODE = "type_fast"
            elif config.get('auto_type', False):
                OUTPUT_MODE = "type"
            else:
                OUTPUT_MODE = "clipboard"
            AUTO_TYPE = (OUTPUT_MODE in ("type", "type_fast"))
            COPY_TO_CLIPBOARD = (OUTPUT_MODE not in ("type", "type_fast"))
            NUMBER_MODE = _normalize_number_mode(config.get('number_digits', config.get('number_mode', 'auto')))
            NUMBER_DIGITS = NUMBER_MODE != "words"
            SERIAL_COLLAPSE = _normalize_bool(config.get('serial_collapse', True), default=True)
            SPELL_COMMAND = _normalize_bool(config.get('spell_command', True), default=True)
            MIDDLE_CLICK_ENABLED = config.get('middle_click_enabled', False)
            HOTKEY_BINDS = _load_hotkey_binds(config.get('hotkeys'))
            MODEL_BACKEND = _load_model_backend(config.get('model_backend'))
            KEEP_BLUETOOTH_HANDSFREE = config.get('keep_bluetooth_handsfree', True)

            raw_punct = config.get('preset') or config.get('mode_preset') or config.get('punctuation_mode') or config.get('formatting_level') or 'full'
            PUNCTUATION_MODE = get_canonical_preset_name(raw_punct)

            env_punct = os.environ.get("VT_PRESET", "").strip().lower() or os.environ.get("VT_PUNCTUATION_MODE", "").strip().lower()
            if env_punct:
                PUNCTUATION_MODE = get_canonical_preset_name(env_punct)

            STRUCTURE_MODE = normalize_structure_mode(config.get('structure_mode', 'off'))
            env_structure = os.environ.get("VT_STRUCTURE_MODE", "").strip().lower()
            if env_structure:
                STRUCTURE_MODE = normalize_structure_mode(env_structure)

            LANGUAGE = config.get('language', 'en')
            env_lang = os.environ.get("VT_LANGUAGE", "").strip().lower()
            if env_lang:
                LANGUAGE = env_lang
            # The acoustic backend reads the language from the environment, so
            # publish the resolved value (config or env), the same way the SLM
            # flag is published below.
            os.environ["VT_LANGUAGE"] = LANGUAGE

            WAIT_FOR_MODEL_ON_STARTUP = config.get('wait_for_model_on_startup', True)
            env_wait = os.environ.get("VT_WAIT_FOR_MODEL_ON_STARTUP", "").strip().lower()
            if env_wait in ["1", "true", "yes"]:
                WAIT_FOR_MODEL_ON_STARTUP = True
            elif env_wait in ["0", "false", "no"]:
                WAIT_FOR_MODEL_ON_STARTUP = False

            ENABLE_SLM = config.get('enable_slm', False)
            env_slm = os.environ.get("VT_ENABLE_SLM", "").strip().lower()
            if env_slm in ["1", "true", "yes"]:
                ENABLE_SLM = True
            elif env_slm in ["0", "false", "no"]:
                ENABLE_SLM = False
            os.environ["VT_ENABLE_SLM"] = "1" if ENABLE_SLM else "0"

            try:
                TYPING_WPM = int(config.get('typing_wpm', 40))
                if TYPING_WPM <= 0:
                    TYPING_WPM = 40
            except (ValueError, TypeError):
                TYPING_WPM = 40

            AUTO_TYPE_TRAILING_SPACE = config.get('auto_type_trailing_space', True)
            env_trailing_space = os.environ.get("VT_AUTO_TYPE_TRAILING_SPACE", "").strip().lower()
            if env_trailing_space in ["1", "true", "yes"]:
                AUTO_TYPE_TRAILING_SPACE = True
            elif env_trailing_space in ["0", "false", "no"]:
                AUTO_TYPE_TRAILING_SPACE = False

            AUTO_TYPE_AUTO_PUNCTUATE = config.get('auto_type_auto_punctuate', True)
            env_auto_punctuate = os.environ.get("VT_AUTO_TYPE_AUTO_PUNCTUATE", "").strip().lower()
            if env_auto_punctuate in ["1", "true", "yes"]:
                AUTO_TYPE_AUTO_PUNCTUATE = True
            elif env_auto_punctuate in ["0", "false", "no"]:
                AUTO_TYPE_AUTO_PUNCTUATE = False

            env_middle_click = os.environ.get("VT_MIDDLE_CLICK_ENABLED", "").strip().lower()
            if env_middle_click in ["1", "true", "yes"]:
                MIDDLE_CLICK_ENABLED = True
            elif env_middle_click in ["0", "false", "no"]:
                MIDDLE_CLICK_ENABLED = False

            env_bt_handsfree = os.environ.get("VT_KEEP_BLUETOOTH_HANDSFREE", "").strip().lower()
            if env_bt_handsfree in ["1", "true", "yes"]:
                KEEP_BLUETOOTH_HANDSFREE = True
            elif env_bt_handsfree in ["0", "false", "no"]:
                KEEP_BLUETOOTH_HANDSFREE = False

            env_muted = os.environ.get("VT_IS_MUTED", "").strip().lower()
            if env_muted in ["1", "true", "yes"]:
                IS_MUTED = True
            elif env_muted in ["0", "false", "no"]:
                IS_MUTED = False

            env_auto_type = os.environ.get("VT_AUTO_TYPE", "").strip().lower()
            if env_auto_type in ["1", "true", "yes"]:
                OUTPUT_MODE = "type"
            elif env_auto_type in ["0", "false", "no"] and OUTPUT_MODE in ("type", "type_fast"):
                OUTPUT_MODE = "clipboard"

            env_out_mode = os.environ.get("VT_OUTPUT_MODE", "").strip().lower()
            if env_out_mode in OUTPUT_MODES:
                OUTPUT_MODE = env_out_mode
            elif env_out_mode in ("paste", "paste_terminal"):
                OUTPUT_MODE = "type_fast"

            AUTO_TYPE = (OUTPUT_MODE in ("type", "type_fast"))
            COPY_TO_CLIPBOARD = (OUTPUT_MODE not in ("type", "type_fast"))

            env_sound = os.environ.get("VT_SOUND_THEME", "").strip()
            SOUND_THEME = env_sound or config.get('sound_theme', 'proximity')
            if SOUND_THEME.lower() in ["silent", "muted", "none"]:
                IS_MUTED = True

            COPY_TO_CLIPBOARD = config.get('copy_to_clipboard', True)

            env_theme = os.environ.get("VT_UI_THEME", "").strip().lower()
            UI_THEME = env_theme or config.get('ui_theme', 'auto')

            # If we have an override, try that first
            if OVERRIDE_MODE == 'primary' and PRIMARY_DEVICE_NAME:
                idx = find_device_index(PRIMARY_DEVICE_NAME)
                if idx is not None:
                    INPUT_DEVICE_INDEX = idx
                    logger.info(f"[Override] Using primary device: {PRIMARY_DEVICE_NAME} (index {idx})")
                else:
                    logger.warning(f"[Override] Primary device not found: {PRIMARY_DEVICE_NAME}")
            elif OVERRIDE_MODE == 'secondary' and SECONDARY_DEVICE_NAME:
                idx = find_device_index(SECONDARY_DEVICE_NAME)
                if idx is not None:
                    INPUT_DEVICE_INDEX = idx
                    logger.info(f"[Override] Using secondary device: {SECONDARY_DEVICE_NAME} (index {idx})")
                else:
                    logger.warning(f"[Override] Secondary device not found: {SECONDARY_DEVICE_NAME}")

            # If no override or override failed, try the standard auto logic
            if INPUT_DEVICE_INDEX is None:
                # Attempt to find primary
                idx = find_device_index(PRIMARY_DEVICE_NAME)
                if idx is not None:
                    INPUT_DEVICE_INDEX = idx
                    logger.info(f"Using primary audio device: {PRIMARY_DEVICE_NAME} (index {idx})")
                else:
                    # Attempt to find secondary
                    idx = find_device_index(SECONDARY_DEVICE_NAME)
                    if idx is not None:
                        INPUT_DEVICE_INDEX = idx
                        logger.info(f"Using secondary audio device: {SECONDARY_DEVICE_NAME} (index {idx})")
                    else:
                        # Fallback to index if names fail (for backward compatibility or if names are not set)
                        INPUT_DEVICE_INDEX = config.get('input_device_index')
                        if INPUT_DEVICE_INDEX is not None:
                            try:
                                d = sd.query_devices(INPUT_DEVICE_INDEX)
                                if d.get('max_input_channels', 0) > 0:
                                    logger.info(f"Falling back to saved device index {INPUT_DEVICE_INDEX}: {d['name']}")
                                else:
                                    INPUT_DEVICE_INDEX = None
                            except Exception:
                                INPUT_DEVICE_INDEX = None

            # Double check that the selected INPUT_DEVICE_INDEX actually exists and is valid
            if INPUT_DEVICE_INDEX is not None:
                try:
                    with silence_stderr():
                        d = sd.query_devices(INPUT_DEVICE_INDEX)
                        if d.get('max_input_channels', 0) <= 0:
                            INPUT_DEVICE_INDEX = None
                except Exception:
                    INPUT_DEVICE_INDEX = None

            if INPUT_DEVICE_INDEX is not None:
                set_default_input_device(INPUT_DEVICE_INDEX)
                # Print secondary device info
                if SECONDARY_DEVICE_NAME:
                    sec_idx = find_device_index(SECONDARY_DEVICE_NAME)
                    if sec_idx is not None:
                        logger.info(f"Secondary audio device: {SECONDARY_DEVICE_NAME} (index {sec_idx})")
            else:
                logger.info("No configured audio devices found. Using system default.")

        # Apply Bluetooth hands-free policy on Linux / PipeWire
        apply_bluetooth_handsfree_policy(KEEP_BLUETOOTH_HANDSFREE)

        # Environment overrides always win, even when no config file exists yet
        # (a fresh install with only VT_* vars exported).
        env_number_digits = os.environ.get("VT_NUMBER_DIGITS", "").strip()
        if env_number_digits:
            NUMBER_MODE = _normalize_number_mode(env_number_digits)
            NUMBER_DIGITS = NUMBER_MODE != "words"

        env_serial_collapse = os.environ.get("VT_SERIAL_COLLAPSE", "").strip().lower()
        if env_serial_collapse in ["1", "true", "yes", "on", "collapsed", "collapse"]:
            SERIAL_COLLAPSE = True
        elif env_serial_collapse in ["0", "false", "no", "off", "spaced", "space"]:
            SERIAL_COLLAPSE = False

        env_spell_command = os.environ.get("VT_SPELL_COMMAND", "").strip().lower()
        if env_spell_command in ["1", "true", "yes", "on"]:
            SPELL_COMMAND = True
        elif env_spell_command in ["0", "false", "no", "off"]:
            SPELL_COMMAND = False

        env_wpm = os.environ.get("VT_TYPING_WPM", "").strip()
        if env_wpm:
            try:
                parsed_wpm = int(env_wpm)
                if parsed_wpm > 0:
                    TYPING_WPM = parsed_wpm
            except (ValueError, TypeError):
                pass

        # Keep the post-processor's runtime number mode in sync with the loaded config
        set_number_digits(NUMBER_MODE)

        # Keep serial/NATO collapsing and the verbal spell command in sync
        set_serial_collapse(SERIAL_COLLAPSE)
        set_spell_command(SPELL_COMMAND)

        # Keep post-processor punctuation mode in sync
        set_punctuation_mode(PUNCTUATION_MODE)

        # Publish the effective structured-output mode (never saves: this is a load)
        _push_structure_mode()

        # Sync custom word/phrase dictionary if configured in config or external file
        dict_setting = config.get('dictionary')
        dict_file = config.get('dictionary_file')
        if dict_file:
            from post_processor import load_custom_dictionary_from_file
            load_custom_dictionary_from_file(dict_file)
        elif dict_setting and isinstance(dict_setting, dict):
            from post_processor import set_custom_dictionary
            set_custom_dictionary(dict_setting)
    except Exception as e:
        logger.warning(f"Could not load audio config: {e}")

def save_audio_config(file_path=None):
    """Save audio device configuration to local file preserving existing keys and format"""
    global CONFIG_FILE, AUTO_TYPE, OUTPUT_MODE, COPY_TO_CLIPBOARD, AUTO_TYPE_TRAILING_SPACE, AUTO_TYPE_AUTO_PUNCTUATE, SERIAL_COLLAPSE, SPELL_COMMAND, TYPING_WPM
    if file_path is not None:
        CONFIG_FILE = Path(file_path)
    elif CONFIG_FILE is None:
        CONFIG_FILE = get_config_file()
    try:
        existing_config = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r') as f:
                    content = f.read()
                if CONFIG_FILE.suffix == '.toml':
                    try:
                        import tomllib
                        existing_config = tomllib.loads(content)
                    except Exception:
                        existing_config = {}
                elif CONFIG_FILE.suffix in ['.yaml', '.yml']:
                    try:
                        import yaml
                        existing_config = yaml.safe_load(content) or {}
                    except Exception:
                        existing_config = json.loads(content) if content.strip() else {}
                else:
                    existing_config = json.loads(content) if content.strip() else {}
            except Exception:
                existing_config = {}

        if not isinstance(existing_config, dict):
            existing_config = {}

        # Keep AUTO_TYPE and OUTPUT_MODE in sync if updated directly
        if AUTO_TYPE and OUTPUT_MODE == "clipboard":
            OUTPUT_MODE = "type"
        elif not AUTO_TYPE and OUTPUT_MODE in ("type", "type_fast"):
            OUTPUT_MODE = "clipboard"
        AUTO_TYPE = (OUTPUT_MODE in ("type", "type_fast"))
        COPY_TO_CLIPBOARD = (OUTPUT_MODE not in ("type", "type_fast"))

        existing_config.update({
            'input_device_index': INPUT_DEVICE_INDEX,
            'primary_device_name': PRIMARY_DEVICE_NAME,
            'secondary_device_name': SECONDARY_DEVICE_NAME,
            'override_mode': OVERRIDE_MODE,
            'is_muted': IS_MUTED,
            'output_mode': OUTPUT_MODE,
            'auto_type': AUTO_TYPE,
            'auto_type_trailing_space': AUTO_TYPE_TRAILING_SPACE,
            'auto_type_auto_punctuate': AUTO_TYPE_AUTO_PUNCTUATE,
            'sound_theme': SOUND_THEME,
            'copy_to_clipboard': COPY_TO_CLIPBOARD,
            'ui_theme': UI_THEME,
            'number_digits': NUMBER_MODE,
            'serial_collapse': SERIAL_COLLAPSE,
            'spell_command': SPELL_COMMAND,
            'middle_click_enabled': MIDDLE_CLICK_ENABLED,
            'keep_bluetooth_handsfree': KEEP_BLUETOOTH_HANDSFREE,
            'punctuation_mode': PUNCTUATION_MODE,
            'preset': PUNCTUATION_MODE,
            'structure_mode': STRUCTURE_MODE,
            'language': LANGUAGE,
            'enable_slm': ENABLE_SLM,
            'wait_for_model_on_startup': WAIT_FOR_MODEL_ON_STARTUP,
            'typing_wpm': TYPING_WPM,
            'hotkeys': [b.to_config() for b in keybinds.parse_binds(HOTKEY_BINDS)],
            'model_backend': MODEL_BACKEND,
        })

        if CONFIG_FILE.suffix == '.toml':
            out_content = _dump_toml(existing_config)
        elif CONFIG_FILE.suffix in ['.yaml', '.yml']:
            try:
                import yaml
                out_content = yaml.dump(existing_config, default_flow_style=False, sort_keys=False)
            except Exception:
                out_content = json.dumps(existing_config, indent=2)
        else:
            out_content = json.dumps(existing_config, indent=2)

        with open(CONFIG_FILE, 'w') as f:
            f.write(out_content)
        logger.debug(f"Saved audio device config to {CONFIG_FILE}")
    except Exception as e:
        logger.error(f"Could not save audio config: {e}")


def reset_to_defaults() -> dict:
    """Restore every user-tunable setting to its shipped default and persist it.

    Applies :data:`DEFAULT_SETTINGS` to the live module globals, then writes the
    result through :func:`save_audio_config`. Microphone selection and the
    custom dictionary are intentionally untouched, and unknown keys already
    present in the config file are preserved.
    """
    global NUMBER_DIGITS
    for name, value in DEFAULT_SETTINGS.items():
        globals()[name] = value
    NUMBER_DIGITS = (NUMBER_MODE != "words")
    set_number_digits(NUMBER_MODE)
    _push_structure_mode()
    try:
        import hotkeys

        hotkeys.set_global_binds(HOTKEY_BINDS)
    except Exception:
        pass
    save_audio_config()
    logger.info("All settings restored to shipped defaults")
    return dict(DEFAULT_SETTINGS)


def cycle_output_mode() -> str:
    """Cycle output mode: clipboard -> type -> type_fast."""
    global OUTPUT_MODE, AUTO_TYPE, COPY_TO_CLIPBOARD
    idx = (OUTPUT_MODES.index(OUTPUT_MODE) + 1) % len(OUTPUT_MODES)
    OUTPUT_MODE = OUTPUT_MODES[idx]
    AUTO_TYPE = (OUTPUT_MODE in ("type", "type_fast"))
    COPY_TO_CLIPBOARD = (OUTPUT_MODE not in ("type", "type_fast"))
    if AUTO_TYPE and AUTO_TYPE_AUTO_PUNCTUATE:
        set_punctuation_mode("full")
    _push_structure_mode()
    return OUTPUT_MODE


def set_output_mode(mode: str) -> str:
    """Set output mode: clipboard, type, or type_fast."""
    global OUTPUT_MODE, AUTO_TYPE, COPY_TO_CLIPBOARD
    clean = str(mode).strip().lower()
    if clean in OUTPUT_MODES:
        OUTPUT_MODE = clean
        AUTO_TYPE = (OUTPUT_MODE in ("type", "type_fast"))
        COPY_TO_CLIPBOARD = (OUTPUT_MODE not in ("type", "type_fast"))
    elif clean in ("paste", "paste_terminal"):
        OUTPUT_MODE = "type_fast"
        AUTO_TYPE = True
        COPY_TO_CLIPBOARD = False
    if AUTO_TYPE and AUTO_TYPE_AUTO_PUNCTUATE:
        set_punctuation_mode("full")
    _push_structure_mode()
    return OUTPUT_MODE


def get_output_mode() -> str:
    return OUTPUT_MODE


def toggle_auto_type_trailing_space() -> bool:
    global AUTO_TYPE_TRAILING_SPACE
    AUTO_TYPE_TRAILING_SPACE = not AUTO_TYPE_TRAILING_SPACE
    save_audio_config()
    return AUTO_TYPE_TRAILING_SPACE


def set_auto_type_trailing_space(enabled: bool) -> bool:
    global AUTO_TYPE_TRAILING_SPACE
    AUTO_TYPE_TRAILING_SPACE = bool(enabled)
    save_audio_config()
    return AUTO_TYPE_TRAILING_SPACE


def get_auto_type_trailing_space() -> bool:
    return AUTO_TYPE_TRAILING_SPACE


def toggle_auto_type_auto_punctuate() -> bool:
    global AUTO_TYPE_AUTO_PUNCTUATE
    AUTO_TYPE_AUTO_PUNCTUATE = not AUTO_TYPE_AUTO_PUNCTUATE
    save_audio_config()
    return AUTO_TYPE_AUTO_PUNCTUATE


def set_auto_type_auto_punctuate(enabled: bool) -> bool:
    global AUTO_TYPE_AUTO_PUNCTUATE
    AUTO_TYPE_AUTO_PUNCTUATE = bool(enabled)
    save_audio_config()
    return AUTO_TYPE_AUTO_PUNCTUATE


def get_auto_type_auto_punctuate() -> bool:
    return AUTO_TYPE_AUTO_PUNCTUATE


def set_typing_wpm(wpm: int) -> int:
    """Set average typing WPM and persist to config."""
    global TYPING_WPM
    try:
        val = int(wpm)
        if val > 0:
            TYPING_WPM = val
            save_audio_config()
    except (ValueError, TypeError):
        pass
    return TYPING_WPM


def get_typing_wpm() -> int:
    """Get current average typing WPM setting."""
    return TYPING_WPM


WPM_PRESETS = (30, 40, 50, 60, 70, 80, 100)


def cycle_typing_wpm() -> int:
    """Cycle through WPM presets (30, 40, 50, 60, 70, 80, 100) and persist."""
    global TYPING_WPM
    try:
        cur = int(TYPING_WPM)
    except (ValueError, TypeError):
        cur = 40
    if cur in WPM_PRESETS:
        idx = WPM_PRESETS.index(cur)
        next_wpm = WPM_PRESETS[(idx + 1) % len(WPM_PRESETS)]
    else:
        next_candidates = [w for w in WPM_PRESETS if w > cur]
        next_wpm = next_candidates[0] if next_candidates else WPM_PRESETS[0]
    set_typing_wpm(next_wpm)
    return TYPING_WPM


def calculate_time_saved(text: str, actual_duration_sec: float, wpm: float | None = None) -> float:
    """Calculate estimated time saved in seconds compared to typing.

    Args:
        text: Transcribed text string.
        actual_duration_sec: Actual time taken for speech + processing.
        wpm: Typing words per minute (defaults to TYPING_WPM).

    Returns:
        Time saved in seconds (float >= 0.0). If word count is 0, returns 0.0.
    """
    if not text:
        return 0.0
    words = len(text.strip().split())
    if words == 0:
        return 0.0

    if wpm is None:
        wpm = TYPING_WPM
    try:
        wpm = float(wpm)
    except (ValueError, TypeError):
        wpm = 40.0
    if wpm <= 0:
        wpm = 40.0

    typing_time = (words / wpm) * 60.0
    return max(0.0, typing_time - actual_duration_sec)


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable string.

    Rounds half *up* (``2.5 -> "3s"``), matching ``format_duration`` in
    ``tui-rs/src/ui.rs``. Python's builtin ``round`` is banker's rounding
    (``round(2.5) == 2``), which would make the Rich and ratatui frontends
    disagree by a second on exactly-.5 values.

    Examples:
        format_duration(14) -> "14s"
        format_duration(135) -> "2m 15s"
        format_duration(3900) -> "1h 05m"
    """
    total_sec = max(0, int(seconds + 0.5))
    if total_sec < 60:
        return f"{total_sec}s"
    if total_sec < 3600:
        mins = total_sec // 60
        secs = total_sec % 60
        return f"{mins}m {secs:02d}s"
    hours = total_sec // 3600
    mins = (total_sec % 3600) // 60
    return f"{hours}h {mins:02d}m"


def get_canonical_preset_name(name: str) -> str:
    """Normalize aliases to canonical preset id."""
    clean = str(name or "full").strip().lower().replace("-", "_")
    if clean in ("default", "full", "standard"):
        return "full"
    elif clean in ("casual", "no_terminal_period", "semi_formal", "no_period", "no_ending_period"):
        return "no_terminal_period"
    elif clean in ("autocorrect", "no_punctuation", "phone", "none", "no_punct"):
        return "no_punctuation"
    elif clean in ("aesthetic_lowercase", "aesthetic", "lowercase_punct", "lower_punct"):
        return "aesthetic_lowercase"
    elif clean in ("gen_z", "genz", "pure_gen_z", "lowercase_no_punctuation", "lowercase_no_punct"):
        return "gen_z"
    return "full"


def get_preset_display_name(name: str) -> str:
    canon = get_canonical_preset_name(name)
    return {
        "full": "Default (Standard)",
        "no_terminal_period": "Casual (No Ending Period)",
        "no_punctuation": "Autocorrect (Phone Style)",
        "aesthetic_lowercase": "Aesthetic Lowercase",
        "gen_z": "Pure Gen Z (No Caps/Punct)",
    }.get(canon, "Default (Standard)")


# ---------------------------------------------------------------------------
# Mode preset presentations
# ---------------------------------------------------------------------------
# Spec: docs/mode_presets.md (enforced by tests/shared/test_mode_presets.py).
#
# Every preset is demonstrated with the *same* sample sentence so the
# differences are directly comparable: capitalisation, internal punctuation and
# the trailing period. The previews below are exactly what
# post_processor.apply_punctuation_mode() produces for PRESET_SAMPLE, and the
# ratatui settings modal shows the same strings.
PRESET_SAMPLE = "Hey, how are you? I'm good."

# Switcher ids <-> canonical mode ids. Keep both directions here so the
# switcher and the settings menu can never disagree about what a preset is.
PRESET_SWITCHER_ID_BY_CANON = {
    "full": "default",
    "no_terminal_period": "casual",
    "no_punctuation": "autocorrect",
    "aesthetic_lowercase": "aesthetic_lowercase",
    "gen_z": "gen_z",
}
PRESET_CANON_BY_SWITCHER_ID = {v: k for k, v in PRESET_SWITCHER_ID_BY_CANON.items()}

# (switcher id, display name, badge, description, preview, color)
PRESET_PRESENTATIONS = (
    (
        "default",
        "Default (Standard)",
        "[DEFAULT]",
        "Full punctuation, standard capitalization, and grammar rules",
        "Hey, how are you? I'm good.",
        "green",
    ),
    (
        "casual",
        "Casual (No Ending Period)",
        "[CASUAL]",
        "Standard capitalization and commas, but no period at the end",
        "Hey, how are you? I'm good",
        "yellow",
    ),
    (
        "autocorrect",
        "Autocorrect (Phone Style)",
        "[PHONE]",
        "Capitalizes sentence starts and 'I', but strips punctuation",
        "Hey how are you I'm good",
        "blue",
    ),
    (
        "aesthetic_lowercase",
        "Aesthetic Lowercase",
        "[AESTH]",
        "Keeps commas and questions, but all lowercase (even 'i')",
        "hey, how are you? i'm good",
        "magenta",
    ),
    (
        "gen_z",
        "Pure Gen Z",
        "[GEN Z]",
        "All lowercase, zero punctuation, zero grammar enforcement",
        "hey how are you i'm good",
        "cyan",
    ),
)


def get_preset_presentation(mode=None):
    """Presentation for a mode preset: ``(name, badge, "quoted preview", color)``.

    The single lookup every Python surface uses, so the settings menu and the
    preset switcher cannot drift from each other — or from the previews in
    ``docs/mode_presets.md``.
    """
    canon = get_canonical_preset_name(mode if mode else PUNCTUATION_MODE)
    want = PRESET_SWITCHER_ID_BY_CANON.get(canon, "default")
    for pid, name, badge, _desc, preview, color in PRESET_PRESENTATIONS:
        if pid == want:
            return name, badge, f'"{preview}"', color
    return "Default (Standard)", "[DEFAULT]", f'"{PRESET_SAMPLE}"', "green"


def set_punctuation_mode(mode: str) -> None:
    """Runtime setter for punctuation formatting mode; keeps post-processor in sync."""
    global PUNCTUATION_MODE
    clean = get_canonical_preset_name(mode)
    PUNCTUATION_MODE = clean
    try:
        from post_processor import set_punctuation_mode as post_set_punct
        post_set_punct(PUNCTUATION_MODE)
    except Exception:
        pass


def cycle_punctuation_mode() -> str:
    """Cycle through the 5 preset modes and save."""
    global PUNCTUATION_MODE
    canon = get_canonical_preset_name(PUNCTUATION_MODE)
    idx = PUNCTUATION_MODES.index(canon) if canon in PUNCTUATION_MODES else 0
    next_mode = PUNCTUATION_MODES[(idx + 1) % len(PUNCTUATION_MODES)]
    set_punctuation_mode(next_mode)
    return next_mode


def normalize_structure_mode(value) -> str:
    """Canonicalise a structure mode; anything unrecognised means "off".

    The post-processor owns the behaviour and the alias table, so this delegates
    to it rather than keeping a second copy that could drift.
    """
    try:
        from post_processor import normalize_structure_mode as post_normalize
        return post_normalize(value)
    except Exception:
        return "off"


def get_structure_mode() -> str:
    """The configured structure mode ("off"/"inline"/"blocks")."""
    return STRUCTURE_MODE


def get_effective_structure_mode() -> str:
    """The structure mode that is safe for the current output path.

    "blocks" emits real line breaks, and a newline is an Enter keypress in
    whatever window has focus — Slack sends the message, a terminal executes it.
    "inline" emits markers only, so typing downgrades to it. Clipboard and paste
    output gets the real breaks.
    """
    if STRUCTURE_MODE == "blocks" and OUTPUT_MODE in ("type", "type_fast"):
        return "inline"
    return STRUCTURE_MODE


def _push_structure_mode() -> str:
    """Publish the effective structure mode to the post-processor; never saves."""
    effective = get_effective_structure_mode()
    try:
        from post_processor import set_structure_mode as post_set_structure
        post_set_structure(effective)
    except Exception:
        pass
    return effective


def set_structure_mode(mode) -> str:
    """Set and persist the structure mode; keeps the post-processor in sync."""
    global STRUCTURE_MODE
    STRUCTURE_MODE = normalize_structure_mode(mode)
    _push_structure_mode()
    save_audio_config()
    return STRUCTURE_MODE


def toggle_structure_mode() -> str:
    """Cycle off -> inline -> blocks -> off and persist."""
    global STRUCTURE_MODE
    index = STRUCTURE_MODES.index(STRUCTURE_MODE) if STRUCTURE_MODE in STRUCTURE_MODES else 0
    STRUCTURE_MODE = STRUCTURE_MODES[(index + 1) % len(STRUCTURE_MODES)]
    _push_structure_mode()
    save_audio_config()
    return STRUCTURE_MODE


def set_number_digits(value):
    """Set number formatting mode; keeps post-processor in sync.

    Accepts a mode string ("auto"/"digits"/"words") or a backwards-compatible
    boolean (True -> "digits", False -> "words").
    """
    global NUMBER_DIGITS, NUMBER_MODE
    NUMBER_MODE = _normalize_number_mode(value)
    NUMBER_DIGITS = NUMBER_MODE != "words"
    if "VT_NUMBER_DIGITS" in os.environ:
        os.environ["VT_NUMBER_DIGITS"] = NUMBER_MODE
    try:
        from post_processor import set_number_digits_mode
        set_number_digits_mode(NUMBER_MODE)
    except Exception:
        pass


def set_serial_collapse(enabled):
    """Set whether serial numbers / codes / NATO strings collapse into one token."""
    global SERIAL_COLLAPSE
    if isinstance(enabled, str):
        SERIAL_COLLAPSE = enabled.strip().lower() in ("1", "true", "yes", "on", "collapsed", "collapse")
    else:
        SERIAL_COLLAPSE = bool(enabled)
    try:
        from post_processor import set_serial_collapse as post_set_serial_collapse
        post_set_serial_collapse(SERIAL_COLLAPSE)
    except Exception:
        pass
    return SERIAL_COLLAPSE


def toggle_serial_collapse() -> bool:
    """Flip serial collapsing on/off; returns the new state."""
    return set_serial_collapse(not SERIAL_COLLAPSE)


def set_spell_command(enabled):
    """Enable/disable the verbal 'spell' command."""
    global SPELL_COMMAND
    if isinstance(enabled, str):
        SPELL_COMMAND = enabled.strip().lower() in ("1", "true", "yes", "on")
    else:
        SPELL_COMMAND = bool(enabled)
    try:
        from post_processor import set_spell_command as post_set_spell_command
        post_set_spell_command(SPELL_COMMAND)
    except Exception:
        pass
    return SPELL_COMMAND


def toggle_spell_command() -> bool:
    """Flip the verbal spell command on/off; returns the new state."""
    return set_spell_command(not SPELL_COMMAND)


def cycle_number_mode() -> str:
    """Cycle AUTO -> DIGITS -> WORDS -> AUTO; returns the new mode."""
    global NUMBER_MODE
    order = ("auto", "digits", "words")
    idx = order.index(NUMBER_MODE) if NUMBER_MODE in order else 0
    set_number_digits(order[(idx + 1) % len(order)])
    return NUMBER_MODE


def set_middle_click_enabled(enabled):
    """Runtime toggle for middle click hold push-to-talk mode."""
    global MIDDLE_CLICK_ENABLED
    MIDDLE_CLICK_ENABLED = bool(enabled)
    try:
        import hotkeys
        hotkeys.set_global_middle_click_enabled(MIDDLE_CLICK_ENABLED)
    except Exception:
        pass


def set_hotkey_binds(binds):
    """Replace the push-to-talk binds at runtime (loads + live manager).

    ``binds`` is anything :func:`keybinds.parse_binds` accepts: a chord string
    (``"ctrl+shift"``), a list of them, or ``{"keys": [...], "action": ...}``
    mappings. Raises :class:`keybinds.KeybindError` for an unusable chord so the
    control API and the settings prompt can report a real message rather than
    silently ignoring the request. Persisting is the caller's job, matching
    every other setter here.
    """
    global HOTKEY_BINDS
    HOTKEY_BINDS = keybinds.parse_binds(binds)
    try:
        import hotkeys
        hotkeys.set_global_binds(HOTKEY_BINDS)
    except Exception:
        pass
    return HOTKEY_BINDS


def set_dictionary(mapping):
    """Set custom word/phrase replacement dictionary at runtime."""
    try:
        from post_processor import set_custom_dictionary
        set_custom_dictionary(mapping)
    except Exception:
        pass


def get_dictionary():
    """Get currently active custom word/phrase replacement dictionary."""
    try:
        from post_processor import get_custom_dictionary
        return get_custom_dictionary()
    except Exception:
        return {}


COLOR_PALETTES = ["auto", "green", "cyan", "blue", "magenta", "yellow", "red", "white"]


def _fuzzy_subsequence(pattern: str, target: str) -> int | None:
    p_idx = 0
    dist = 0
    p_len = len(pattern)
    for i, c in enumerate(target):
        if p_idx < p_len and c == pattern[p_idx]:
            p_idx += 1
            dist += i
    if p_idx == p_len:
        return dist
    return None


def _score_setting(query: str, title: str, keywords: str = "") -> int | None:
    if not query:
        return 0
    q = query.strip().lower()
    if not q:
        return 0
    t = title.lower()
    kw = keywords.lower()

    if t == q:
        return 1000
    if t.startswith(q):
        return 800 - len(q)
    if q in t:
        return 600 - t.find(q) * 10
    if kw and q in kw:
        return 400 - kw.find(q) * 5

    dist = _fuzzy_subsequence(q, t)
    if dist is not None:
        return 200 - dist

    if kw:
        dist_kw = _fuzzy_subsequence(q, kw)
        if dist_kw is not None:
            return 100 - dist_kw

    return None


def _read_key():
    if sys.platform == "win32":
        try:
            import msvcrt
            ch = msvcrt.getwch()
            if ch in ('\x00', '\xe0'):
                ch2 = msvcrt.getwch()
                if ch2 == 'H':
                    return 'UP'
                elif ch2 == 'P':
                    return 'DOWN'
                elif ch2 == 'M':
                    return 'RIGHT'
                elif ch2 == 'K':
                    return 'LEFT'
                elif ch2 == '\x0f':
                    return 'UP'  # Shift+Tab
                return ''
            elif ch == '\x1b':
                return 'ESC'
            elif ch in ('\r', '\n'):
                return 'ENTER'
            elif ch in ('\x7f', '\x08'):
                return 'BACKSPACE'
            elif ch == '\x03':
                return 'CTRL_C'
            elif ch == '\t':
                return 'DOWN'
            return ch
        except Exception:
            return sys.stdin.read(1)

    import termios
    import tty
    import select
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == '\x1b':
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                ch2 = sys.stdin.read(1)
                if ch2 == '[':
                    ch3 = sys.stdin.read(1)
                    if ch3 == 'A':
                        return 'UP'
                    elif ch3 == 'B':
                        return 'DOWN'
                    elif ch3 == 'C':
                        return 'RIGHT'
                    elif ch3 == 'D':
                        return 'LEFT'
                    elif ch3 == 'Z':
                        return 'UP'  # Shift+Tab
            return 'ESC'
        elif ch in ('\r', '\n'):
            return 'ENTER'
        elif ch in ('\x7f', '\x08'):
            return 'BACKSPACE'
        elif ch == '\x03':
            return 'CTRL_C'
        elif ch == '\t':
            return 'DOWN'
        return ch
    except Exception:
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def select_theme_picker(current_theme=None):
    """Interactive theme picker with fuzzy search and arrow key navigation"""
    global UI_THEME
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich import box

    palettes = [
        ("auto", "Auto", "Detect from system terminal accent color"),
        ("green", "Green", "Classic emerald / forest green"),
        ("cyan", "Cyan", "Cyber electric cyan / aqua"),
        ("blue", "Blue", "Ocean royal blue / cobalt"),
        ("magenta", "Magenta", "Neon magenta / purple / violet"),
        ("yellow", "Yellow", "Solar amber / gold / warm yellow"),
        ("red", "Red", "Crimson / coral / bold red"),
        ("white", "White", "Monochrome / crisp clean white"),
    ]

    active = (current_theme or UI_THEME or "auto").lower()
    selected_idx = 0
    for i, (name, _, _) in enumerate(palettes):
        if name == active:
            selected_idx = i
            break

    query = ""
    console = Console()

    def filter_palettes(q):
        if not q.strip():
            return list(enumerate(palettes))
        q = q.strip().lower()
        scored = []
        for i, (name, label, desc) in enumerate(palettes):
            score = 0
            if name == q:
                score = 1000
            elif name.startswith(q):
                score = 800 - len(q)
            elif label.lower().startswith(q):
                score = 700 - len(q)
            elif q in name:
                score = 600 - name.find(q) * 10
            elif q in desc.lower():
                score = 400 - desc.lower().find(q) * 5
            else:
                idx = 0
                for c in name:
                    if idx < len(q) and c == q[idx]:
                        idx += 1
                if idx == len(q):
                    score = 200
            if score > 0:
                scored.append((score, i, palettes[i]))
        scored.sort(key=lambda x: -x[0])
        return [(i, p) for _, i, p in scored]

    while True:
        console.clear()
        matches = filter_palettes(query)
        if selected_idx >= len(matches):
            selected_idx = max(0, len(matches) - 1)

        preview_color = matches[selected_idx][1][0] if matches else active
        if preview_color == "auto":
            preview_color = "green"

        table = Table(box=None, padding=(0, 1), show_header=False)
        table.add_column("Ind", justify="right", width=2)
        table.add_column("Dot", justify="center", width=2)
        table.add_column("Name", width=10)
        table.add_column("Description", width=42)
        table.add_column("Action", width=12)

        for row_i, (_orig_i, (name, label, desc)) in enumerate(matches):
            is_sel = (row_i == selected_idx)
            is_active = (name == active)
            dot_color = "green" if name == "auto" else name

            ind = "❯" if is_sel else " "
            ind_style = f"bold {dot_color}" if is_sel else "dim white"

            name_style = f"bold {dot_color}" if is_sel else ("bold white" if is_active else "white")
            desc_style = "bold white" if is_sel else "dim white"

            status = "↵ Select" if is_sel else ("[Active]" if is_active else "")
            status_style = f"bold black on {dot_color}" if is_sel else f"dim {dot_color}"

            table.add_row(
                Text(ind, style=ind_style),
                Text("●", style=dot_color),
                Text(label, style=name_style),
                Text(desc, style=desc_style),
                Text(status, style=status_style),
            )

        if not matches:
            table.add_row("", "", "No match", f"No themes match '{query}'", "")

        q_disp = query if query else "[dim]type to search (e.g. cyan, magenta)...[/dim]"
        search_panel = Panel(
            Text.from_markup(f"🔍 [bold]Filter:[/bold] [yellow]{q_disp}[/yellow]"),
            border_style=preview_color,
            box=box.ROUNDED,
            padding=(0, 1)
        )

        footer = Text.from_markup("  [cyan][↑/↓][/cyan] Navigate   [cyan][Type][/cyan] Search   [green][Enter][/green] Apply   [red][Esc][/red] Cancel")
        main_panel = Panel(
            table,
            title="🎨  Select UI Color Theme",
            subtitle=footer,
            border_style=preview_color,
            box=box.ROUNDED,
            padding=(0, 1)
        )

        console.print(search_panel)
        console.print(main_panel)

        key = _read_key()
        if key in ('ESC', 'CTRL_C'):
            console.clear()
            return None
        elif key == 'ENTER':
            if matches:
                chosen = matches[selected_idx][1][0]
                UI_THEME = chosen
                save_audio_config()
                console.clear()
                return chosen
            console.clear()
            return None
        elif key == 'UP':
            if matches:
                selected_idx = (selected_idx - 1) % len(matches)
        elif key in ('DOWN', '\t'):
            if matches:
                selected_idx = (selected_idx + 1) % len(matches)
        elif key == 'BACKSPACE':
            query = query[:-1]
            selected_idx = 0
        elif len(key) == 1 and key.isprintable():
            query += key
            selected_idx = 0


def select_microphone_picker():
    """Interactive microphone picker with fuzzy search and arrow key navigation"""
    global INPUT_DEVICE_INDEX, PRIMARY_DEVICE_NAME, UI_THEME
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich import box

    console = Console()
    devices = get_input_devices()
    if not devices:
        return None

    selected_idx = 0
    for i, dev in enumerate(devices):
        if dev.get('is_active'):
            selected_idx = i
            break

    query = ""

    def filter_devs(q):
        if not q.strip():
            return list(enumerate(devices))
        q = q.strip().lower()
        scored = []
        for i, dev in enumerate(devices):
            score = _score_setting(q, dev['name'])
            if score is not None:
                scored.append((score, i, dev))
        scored.sort(key=lambda x: -x[0])
        return [(i, d) for _, i, d in scored]

    while True:
        console.clear()
        matches = filter_devs(query)
        if selected_idx >= len(matches):
            selected_idx = max(0, len(matches) - 1)

        theme_color = UI_THEME.lower() if UI_THEME and UI_THEME.lower() in COLOR_PALETTES and UI_THEME.lower() != "auto" else "green"

        table = Table(box=None, padding=(0, 1), show_header=False, expand=True)
        table.add_column("Ind", justify="right", width=2)
        table.add_column("Dot", justify="center", width=2)
        table.add_column("Name", ratio=1)
        table.add_column("Badges", justify="right", width=22)

        for row_i, (_orig_i, dev) in enumerate(matches):
            is_sel = (row_i == selected_idx)
            is_active = dev.get('is_active', False)
            is_default = dev.get('is_default', False)

            ind = "❯ " if is_sel else "  "
            ind_style = f"bold {theme_color}" if is_sel else "dim white"

            dot = "●" if is_active else "○"
            dot_style = "bold green" if is_active else "dim white"

            name_style = f"bold {theme_color}" if is_sel else ("bold white" if is_active else "white")

            badges = Text()
            if is_default:
                badges.append("[DEFAULT] ", style="bold cyan")
            if is_active:
                badges.append("[ACTIVE]", style="bold green")

            table.add_row(
                Text(ind, style=ind_style),
                Text(dot, style=dot_style),
                Text(dev['name'], style=name_style),
                badges,
            )

        if not matches:
            table.add_row("", "", f"No devices match '{query}'. Press Backspace or Esc.", "")

        q_disp = query if query else "[dim]type to search (e.g. default, usb, quadcast)...[/dim]"
        search_panel = Panel(
            Text.from_markup(f"🔍 [bold]Filter:[/bold] [yellow]{q_disp}[/yellow]"),
            border_style=theme_color,
            box=box.ROUNDED,
            padding=(0, 1),
        )

        tip = Text.from_markup("  [yellow]🎙 Live Monitor:[/yellow] [dim]Speak or hold Alt+Shift to preview audio levels in real time[/dim]\n")

        footer = Text.from_markup(
            "  [cyan][↑/↓][/cyan] Navigate   [cyan][Type][/cyan] Search   [green][Enter][/green] Select   [red][Esc][/red] Cancel"
        )

        main_content = Table.grid(expand=True)
        main_content.add_row(tip)
        main_content.add_row(table)

        main_panel = Panel(
            main_content,
            title="🎤  Microphone Input Device",
            subtitle=footer,
            border_style=theme_color,
            box=box.ROUNDED,
            padding=(0, 1),
        )

        console.print(search_panel)
        console.print(main_panel)

        key = _read_key()
        if key in ('ESC', 'CTRL_C'):
            console.clear()
            return None
        elif key == 'ENTER' or (key == ' ' and not query):
            if matches:
                chosen_dev = matches[selected_idx][1]
                PRIMARY_DEVICE_NAME = chosen_dev['name']
                INPUT_DEVICE_INDEX = chosen_dev['index']
                set_default_input_device(INPUT_DEVICE_INDEX)
                save_audio_config()
                console.clear()
                return PRIMARY_DEVICE_NAME
            console.clear()
            return None
        elif key == 'UP':
            if matches:
                selected_idx = (selected_idx - 1) % len(matches)
        elif key in ('DOWN', '\t'):
            if matches:
                selected_idx = (selected_idx + 1) % len(matches)
        elif key == 'BACKSPACE':
            query = query[:-1]
            selected_idx = 0
        elif len(key) == 1 and key.isprintable():
            query += key
            selected_idx = 0


def select_preset_picker(current_preset=None):
    """Interactive mode preset picker modal with live preview and fuzzy search."""
    global PUNCTUATION_MODE
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich import box

    presets = list(PRESET_PRESENTATIONS)

    active = get_canonical_preset_name(current_preset or PUNCTUATION_MODE)
    active_pid = {
        "full": "default",
        "no_terminal_period": "casual",
        "no_punctuation": "autocorrect",
        "aesthetic_lowercase": "aesthetic_lowercase",
        "gen_z": "gen_z",
    }.get(active, "default")

    selected_idx = 0
    for i, (pid, _, _, _, _, _) in enumerate(presets):
        if pid == active_pid:
            selected_idx = i
            break

    query = ""
    console = Console()

    def filter_presets(q):
        if not q.strip():
            return list(enumerate(presets))
        q = q.strip().lower()
        scored = []
        for i, (pid, name, _badge, desc, _preview, _color) in enumerate(presets):
            score = 0
            if pid == q or q in pid:
                score += 500
            if q in name.lower():
                score += 300
            if q in desc.lower():
                score += 200
            if score > 0:
                scored.append((score, i, presets[i]))
        scored.sort(key=lambda x: -x[0])
        return [(i, p) for _, i, p in scored]

    while True:
        console.clear()
        matches = filter_presets(query)
        if selected_idx >= len(matches):
            selected_idx = max(0, len(matches) - 1)

        eff_theme = UI_THEME.lower() if UI_THEME and UI_THEME.lower() in COLOR_PALETTES and UI_THEME.lower() != "auto" else "green"

        table = Table(box=None, padding=(0, 1), show_header=False, expand=True)
        table.add_column("Indicator", justify="center", width=3)
        table.add_column("Name", width=28)
        table.add_column("Badge", justify="center", width=14)
        table.add_column("Preview / Description")

        for row_idx, (_orig_idx, (pid, name, badge, desc, preview, color)) in enumerate(matches):
            is_cursor = (row_idx == selected_idx)
            is_active = (pid == active_pid)

            if is_active:
                indicator = Text("●", style=f"bold {color}")
            else:
                indicator = Text("○", style="dim white")

            name_style = f"bold {color}" if is_cursor else ("bold white" if is_active else "white")
            if is_cursor:
                name_text = Text(f"> {name}", style=name_style)
            else:
                name_text = Text(f"  {name}", style=name_style)

            badge_text = Text(badge, style=f"bold {color}" if (is_cursor or is_active) else "dim white")

            detail = Text()
            detail.append(f'"{preview}"\n', style=f"italic {color}" if is_cursor else "italic white")
            detail.append(f"  {desc}", style="dim white")

            table.add_row(indicator, name_text, badge_text, detail)

        search_text = Text("  Search: ", style="bold white")
        search_text.append(query if query else "(type to search presets)", style="cyan" if query else "dim white")
        search_panel = Panel(search_text, box=box.ROUNDED, border_style=eff_theme, padding=(0, 1))

        main_panel = Panel(
            table,
            title="[bold white]✨ Mode Preset Switcher[/bold white]",
            subtitle="[dim white]↑/↓ Navigate · Enter Select · Esc Cancel[/dim white]",
            border_style=eff_theme,
            box=box.ROUNDED,
            padding=(0, 1),
        )

        console.print(search_panel)
        console.print(main_panel)

        key = _read_key()
        if key in ("ESC", "CTRL_C", "q", "Q"):
            console.clear()
            return None
        elif key == "ENTER":
            if matches:
                chosen = matches[selected_idx][1][0]
                canon = PRESET_CANON_BY_SWITCHER_ID.get(chosen, "full")
                set_punctuation_mode(canon)
                save_audio_config()
                console.clear()
                return canon
            console.clear()
            return None
        elif key == "UP":
            if matches:
                selected_idx = (selected_idx - 1) % len(matches)
        elif key in ("DOWN", "\t"):
            if matches:
                selected_idx = (selected_idx + 1) % len(matches)
        elif key == "BACKSPACE":
            query = query[:-1]
            selected_idx = 0
        elif len(key) == 1 and key.isprintable():
            query += key
            selected_idx = 0


def select_settings_picker():
    """Interactive settings picker modal with live in-place toggling and fuzzy search."""
    global INPUT_DEVICE_INDEX, PRIMARY_DEVICE_NAME, SECONDARY_DEVICE_NAME, OVERRIDE_MODE
    global MODEL_BACKEND, COPY_TO_CLIPBOARD, IS_MUTED, SOUND_THEME, AUTO_TYPE
    global UI_THEME, NUMBER_DIGITS, NUMBER_MODE, MIDDLE_CLICK_ENABLED, KEEP_BLUETOOTH_HANDSFREE
    global AUTO_TYPE_TRAILING_SPACE, AUTO_TYPE_AUTO_PUNCTUATE, OUTPUT_MODE, PUNCTUATION_MODE

    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich import box

    console = Console()
    reset_done = False
    defaults_done = False
    mic_rescan = None
    confirm_defaults = False
    query = ""
    selected_idx = 0

    settings_defs = [
        {
            "id": "preset",
            "icon": "✨ ",
            "title": "Mode Preset",
            "keywords": "mode preset switcher formatting gen z casual autocorrect aesthetic default punctuation capitalization grammar",
        },
        {
            "id": "structure",
            "icon": "☰ ",
            "title": "List Formatting",
            "keywords": "structure list bullet bullets enumeration spoken lists formatting newline paragraph break clipboard inline",
        },
        {
            "id": "output_mode",
            "icon": "🚀 ",
            "title": "Output Delivery",
            "keywords": "output mode delivery clipboard type typing paste speed fast slow safe",
        },
        {
            "id": "trailing_space",
            "icon": "␣ ",
            "title": "Trailing Space",
            "keywords": "trailing space auto type whitespace append space",
        },
        {
            "id": "number_digits",
            "icon": "🔢 ",
            "title": "Number Conversion",
            "keywords": "numbers digits words spelled format numeric conversion",
        },
        {
            "id": "serial_collapse",
            "icon": "🔤 ",
            "title": "Serial/Codes",
            "keywords": "serial code alphanumeric nato phonetic spelled collapse spacing identifier model vin license plate",
        },
        {
            "id": "spell_command",
            "icon": "✍️ ",
            "title": "Spell Command",
            "keywords": "spell spelled verbal command letters c a t acronym dictation",
        },
        {
            "id": "typing_wpm",
            "icon": "⚡ ",
            "title": "Typing Speed",
            "keywords": "typing speed wpm words per minute time saved benchmark calculation stats",
        },
        {
            "id": "middle_click",
            "icon": "🖱️ ",
            "title": "Mouse Hotkey",
            "keywords": "middle click mouse hotkey push to talk button hold",
        },
        {
            "id": "sound_mute",
            "icon": "🔔 ",
            "title": "Sound Effects",
            "keywords": "sound effects mute volume chimes audio audio cues notify",
        },
        {
            "id": "theme",
            "icon": "🎨 ",
            "title": "UI Color Theme",
            "keywords": "theme color ui palette cyan magenta green blue yellow red white",
        },
        {
            "id": "microphone",
            "icon": "🎙️ ",
            "title": "Audio Device (Mic)",
            "keywords": "mic microphone audio device input hardware primary secondary",
        },
        {
            "id": "rescan_mics",
            "icon": "♻ ",
            "title": "Reset Microphones",
            "keywords": "reset rescan refresh microphones mics devices audio input list enumerate plugged busy stale missing",
        },
        {
            "id": "reset_terminal",
            "icon": "🔄 ",
            "title": "Reset Terminal",
            "keywords": "reset terminal clipboard bridge clear state fix",
        },
        {
            "id": "reset_defaults",
            "icon": "↩️ ",
            "title": "Reset to Defaults",
            "keywords": "reset defaults factory restore revert shipped initial config settings default everything",
        },
    ]

    def get_setting_state(item_id):
        eff_theme = UI_THEME.lower() if UI_THEME and UI_THEME.lower() in COLOR_PALETTES and UI_THEME.lower() != "auto" else "green"
        if item_id in ("preset", "punctuation_mode"):
            # Same sample sentence the ratatui settings modal shows, so the
            # style of each preset is visible at a glance.
            _name, badge, preview, color = get_preset_presentation(PUNCTUATION_MODE)
            return preview, badge, color
        elif item_id == "structure":
            # The configured mode and the effective one can differ: typing
            # downgrades "blocks" to "inline" because a newline is an Enter
            # keypress in whatever window has focus.
            effective = get_effective_structure_mode()
            if STRUCTURE_MODE == "off":
                return "Flat prose (no list formatting)", "[OFF]", "dim white"
            elif STRUCTURE_MODE == "inline":
                return "Bullets on one line: - one. - two.", "[INLINE]", "cyan"
            elif effective != STRUCTURE_MODE:
                return "Bullets on their own lines (typed text stays inline)", "[PASTE ONLY]", "yellow"
            else:
                return "Bullets on their own lines", "[BLOCKS]", "green"
        elif item_id == "trailing_space":
            if AUTO_TYPE_TRAILING_SPACE:
                return "Enabled (appends ' ')", "[ON]", "green"
            else:
                return "Disabled (exact text)", "[OFF]", "dim white"
        elif item_id == "number_digits":
            if NUMBER_MODE == "digits":
                return "All Digits (1, 2, 3)", "[DIGITS]", "green"
            elif NUMBER_MODE == "words":
                return "Words Only (one, two, three)", "[WORDS]", "dim white"
            else:
                return "Auto (Consecutive Numbers)", "[AUTO]", "cyan"
        elif item_id == "serial_collapse":
            if SERIAL_COLLAPSE:
                return "Collapsed (ABC123)", "[COLLAPSE]", "cyan"
            else:
                return "Spaced (A B C 1 2 3)", "[SPACED]", "dim white"
        elif item_id == "spell_command":
            if SPELL_COMMAND:
                return "Enabled (say 'spell C A T')", "[ON]", "green"
            else:
                return "Disabled", "[OFF]", "dim white"
        elif item_id == "typing_wpm":
            return f"{TYPING_WPM} WPM (time saved baseline)", f"[{TYPING_WPM} WPM]", "cyan"
        elif item_id == "middle_click":
            if MIDDLE_CLICK_ENABLED:
                return "Enabled (hold middle click)", "[ON]", "green"
            else:
                return "Disabled", "[OFF]", "dim white"
        elif item_id == "sound_mute":
            if not IS_MUTED:
                return "Enabled (sound chimes on)", "[ON]", "green"
            else:
                return "Muted (silent)", "[MUTED]", "red"
        elif item_id == "output_mode":
            mode = OUTPUT_MODE
            if mode == "type_fast":
                return "Auto-Type (Fast / Optimized)", "[FAST]", "cyan"
            elif mode == "type":
                return "Auto-Type (Slow / Safe)", "[SLOW]", "cyan"
            else:
                return "Clipboard Only", "[CLIP]", "cyan"
        elif item_id == "theme":
            name = (UI_THEME or "auto").capitalize()
            return f"{name} palette", "[PICKER]", eff_theme
        elif item_id == "microphone":
            dev = PRIMARY_DEVICE_NAME or "Default Microphone"
            if len(dev) > 30:
                dev = dev[:27] + "..."
            return dev, "[SELECT]", "yellow"
        elif item_id == "rescan_mics":
            if mic_rescan is None:
                return "Re-scan for microphones", "[RUN]", "yellow"
            if mic_rescan.get("missing"):
                return f"{mic_rescan['count']} found · {len(mic_rescan['missing'])} still in use", "[HELD]", "yellow"
            return f"{mic_rescan['count']} input devices found", "[DONE]", "green"
        elif item_id == "reset_terminal":
            if reset_done:
                return "Terminal & clipboard bridge reset", "[DONE]", "green"
            else:
                return "Reset terminal state & clipboard bridge", "[RUN]", "yellow"
        elif item_id == "reset_defaults":
            if defaults_done:
                return "All settings restored to defaults", "[DONE]", "green"
            elif confirm_defaults:
                return "Press Enter again to confirm factory reset", "[SURE?]", "red"
            else:
                return "Restore all settings to shipped defaults", "[RESET]", "red"
        return "", "", "white"

    def filter_settings(q):
        if not q.strip():
            return list(enumerate(settings_defs))
        q = q.strip().lower()
        scored = []
        for i, item in enumerate(settings_defs):
            score = _score_setting(q, item["title"], item["keywords"])
            if score is not None:
                scored.append((score, i, item))
        scored.sort(key=lambda x: -x[0])
        return [(i, item) for _, i, item in scored]

    while True:
        console.clear()
        matches = filter_settings(query)
        if selected_idx >= len(matches):
            selected_idx = max(0, len(matches) - 1)

        eff_theme = UI_THEME.lower() if UI_THEME and UI_THEME.lower() in COLOR_PALETTES and UI_THEME.lower() != "auto" else "green"

        table = Table(box=None, padding=(0, 1), show_header=False, expand=True)
        table.add_column("Ind", justify="right", width=2)
        table.add_column("Icon", justify="center", width=3)
        table.add_column("Title", width=20)
        table.add_column("Description", ratio=1)
        table.add_column("Badge", justify="right", width=12)

        for row_i, (_orig_i, item) in enumerate(matches):
            is_sel = (row_i == selected_idx)
            val_str, badge, badge_color = get_setting_state(item["id"])

            ind = "❯ " if is_sel else "  "
            ind_style = f"bold {eff_theme}" if is_sel else "dim white"

            title_style = f"bold {eff_theme}" if is_sel else "bold white"
            desc_style = "bold white" if is_sel else "dim white"
            badge_style = f"bold {badge_color}"

            table.add_row(
                Text(ind, style=ind_style),
                Text(item["icon"], style="default"),
                Text(item["title"], style=title_style),
                Text(val_str, style=desc_style),
                Text(badge, style=badge_style),
            )

        if not matches:
            table.add_row("", "", f"No settings match '{query}'. Press Backspace or Esc.", "", "")

        q_disp = query if query else "[dim]type to search (e.g. space, num, mouse, punc, mode)...[/dim]"
        search_panel = Panel(
            Text.from_markup(f"🔍 [bold]Filter:[/bold] [yellow]{q_disp}[/yellow]"),
            border_style=eff_theme,
            box=box.ROUNDED,
            padding=(0, 1),
        )

        footer = Text.from_markup(
            "  [cyan][↑/↓][/cyan] Navigate   [cyan][Type][/cyan] Search   [green][Enter/Space][/green] Toggle   [red][Esc][/red] Close"
        )
        main_panel = Panel(
            table,
            title="⚙️  Settings & Configuration",
            subtitle=footer,
            border_style=eff_theme,
            box=box.ROUNDED,
            padding=(0, 1),
        )

        console.print(search_panel)
        console.print(main_panel)

        # A mic that is still absent needs saying out loud, on every redraw: it is
        # the one row state the user cannot act on from inside this modal.
        _notice = _mic_rescan_notice(mic_rescan) if mic_rescan else ""
        if _notice:
            console.print(Text.from_markup(f"  [yellow]⚠ {_notice}. Close it and pick this again.[/yellow]"))

        key = _read_key()
        if key != 'ENTER':
            confirm_defaults = False
        if key in ('ESC', 'CTRL_C'):
            save_audio_config()
            console.clear()
            return True
        elif key in ('q', 'Q') and not query:
            save_audio_config()
            console.clear()
            return True
        elif key == 'ENTER' or (key == ' ' and not query):
            if matches:
                item_id = matches[selected_idx][1]["id"]
                if item_id in ("preset", "punctuation_mode"):
                    if key == 'ENTER':
                        select_preset_picker()
                    else:
                        cycle_punctuation_mode()
                    save_audio_config()
                elif item_id == "structure":
                    toggle_structure_mode()
                elif item_id == "output_mode":
                    cycle_output_mode()
                    save_audio_config()
                elif item_id == "trailing_space":
                    toggle_auto_type_trailing_space()
                elif item_id == "number_digits":
                    cycle_number_mode()
                    save_audio_config()
                elif item_id == "serial_collapse":
                    toggle_serial_collapse()
                    save_audio_config()
                elif item_id == "spell_command":
                    toggle_spell_command()
                    save_audio_config()
                elif item_id == "typing_wpm":
                    if key == 'ENTER':
                        console.clear()
                        console.print()
                        console.print("[bold cyan]⚡ Typing Speed Configuration[/bold cyan]")
                        console.print(f"[dim white]Current speed: {TYPING_WPM} words/minute[/dim white]\n")
                        console.print("Enter your average typing speed in words per minute (e.g. 40, 65, 80):")
                        console.print("[dim white](Press Enter without typing to keep current)[/dim white]")
                        try:
                            val_str = input("❯ ").strip()
                            if val_str:
                                val = int(val_str)
                                if 0 < val <= 500:
                                    set_typing_wpm(val)
                        except (ValueError, EOFError, KeyboardInterrupt):
                            pass
                        console.clear()
                    else:
                        cycle_typing_wpm()
                    save_audio_config()
                elif item_id == "middle_click":
                    set_middle_click_enabled(not MIDDLE_CLICK_ENABLED)
                    save_audio_config()
                elif item_id == "sound_mute":
                    IS_MUTED = not IS_MUTED
                    save_audio_config()
                elif item_id == "theme":
                    select_theme_picker()
                elif item_id == "microphone":
                    select_microphone_picker()
                elif item_id == "rescan_mics":
                    mic_rescan = rescan_audio_devices()
                elif item_id == "reset_terminal":
                    reset_terminal()
                    reset_done = True
                elif item_id == "reset_defaults":
                    if confirm_defaults:
                        reset_to_defaults()
                        defaults_done = True
                        confirm_defaults = False
                    else:
                        confirm_defaults = True
        elif key == 'UP':
            if matches:
                selected_idx = (selected_idx - 1) % len(matches)
        elif key in ('DOWN', '\t'):
            if matches:
                selected_idx = (selected_idx + 1) % len(matches)
        elif key == 'BACKSPACE':
            query = query[:-1]
            selected_idx = 0
        elif len(key) == 1 and key.isprintable():
            query += key
            selected_idx = 0


def select_audio_device():
    """Interactive settings and configuration picker (matches Linux ratatui frontend)."""
    return select_settings_picker()

# ---------------------------------------------------------------------------
# Warm input-stream cache (Windows/WASAPI)
# ---------------------------------------------------------------------------
# Opening a PortAudio input stream costs ~50-300ms on Windows/WASAPI, which
# clipped the first syllable of push-to-talk dictation. We keep the constructed
# stream alive (stopped between recordings) so the next press starts capturing
# instantly. No audio is captured while the stream is stopped. Disable with
# VT_WARM_MIC=0. Other platforms keep the original per-recording open.
_stream_cache = {}
_stream_cache_lock = threading.Lock()
_active_warm_sink = {"queue": None, "lock": threading.Lock()}


def _warm_mic_enabled() -> bool:
    if not sys.platform.startswith("win"):
        return False
    return os.environ.get("VT_WARM_MIC", "1").strip().lower() not in ("0", "false", "no", "off")


def _cached_stream_callback(indata, frames, time_info, status):
    if status:
        print(status, file=sys.stderr)
    with _active_warm_sink["lock"]:
        q = _active_warm_sink["queue"]
    if q is not None:
        q.put(indata.copy())


def _get_cached_input_stream(device_idx, rate):
    """Return a constructed (possibly stopped) stream for this device/rate.

    Opens it once, then reuses it across recordings so the device-open cost is
    paid a single time instead of on every push-to-talk press.
    """
    key = (device_idx, rate, CHANNELS)
    stale = []
    with _stream_cache_lock:
        for k in list(_stream_cache):
            if k != key:
                stale.append(_stream_cache.pop(k))
        stream = _stream_cache.get(key)
        if stream is None:
            stream = sd.InputStream(
                samplerate=rate,
                channels=CHANNELS,
                callback=_cached_stream_callback,
                device=device_idx,
                blocksize=1024,
                latency="low",
            )
            _stream_cache[key] = stream
    for s in stale:
        try:
            s.stop()
            s.close()
        except Exception:
            pass
    return stream


def _drop_cached_input_stream(device_idx, rate):
    with _stream_cache_lock:
        stream = _stream_cache.pop((device_idx, rate, CHANNELS), None)
    if stream is not None:
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass


def _close_cached_input_streams():
    with _stream_cache_lock:
        streams = list(_stream_cache.values())
        _stream_cache.clear()
    for s in streams:
        try:
            s.stop()
            s.close()
        except Exception:
            pass


atexit.register(_close_cached_input_streams)


def prewarm_input_stream():
    """Construct (but do not start) the cached input stream at startup.

    Pays the one-time PortAudio device-open cost before the first
    push-to-talk press so the first dictation is not clipped. No audio is
    captured while the stream is stopped. Windows-only; a no-op elsewhere or
    with ``VT_WARM_MIC=0``.
    """
    if not _warm_mic_enabled():
        return
    try:
        device_idx = INPUT_DEVICE_INDEX
        if device_idx is None:
            device_idx = sd.default.device[0]
        if device_idx is not None and int(device_idx) >= 0:
            _get_cached_input_stream(device_idx, RATE)
    except Exception as e:
        logger.debug(f"Input stream prewarm skipped: {e}")


def record_audio_stream(interactive_mode=False, stream_callback=None):
    """Record audio using sounddevice with fallback and auto-recovery support"""
    global INPUT_DEVICE_INDEX, ACTUAL_RATE, LAST_USED_DEVICE_NAME

    is_wsl = _is_wsl()

    if is_wsl:
        # In WSL, we always rely on the single default ALSA-Pulse audio bridge
        INPUT_DEVICE_INDEX = None
        set_default_input_device(None)

    q = queue.Queue()

    def callback(indata, frames, time, status):
        """This is called (from a separate thread) for each audio block."""
        if status:
            print(status, file=sys.stderr)
        q.put(indata.copy())

    def perform_recording(device_idx, rate):
        nonlocal q
        frames = []
        try:
            import scipy.signal
        except ImportError:
            scipy = None

        def _consume():
            while not stop_recording.is_set():
                try:
                    # Responsive 10ms timeout for instant stop_recording detection
                    frame = q.get(timeout=0.01)
                    if rate != 16000 and scipy is not None:
                        frame_flat = frame.flatten().astype(np.float32)
                        frame_processed = scipy.signal.resample_poly(frame_flat, 16000, rate).astype(np.float32)
                    else:
                        frame_processed = frame.ravel() if frame.ndim > 1 else frame

                    frames.append(frame_processed)
                    if stream_callback:
                        stream_callback(frame_processed)
                except queue.Empty:
                    continue

            # Drain any remaining frames in the queue
            while not q.empty():
                try:
                    frame = q.get_nowait()
                    if rate != 16000 and scipy is not None:
                        frame_flat = frame.flatten().astype(np.float32)
                        frame_processed = scipy.signal.resample_poly(frame_flat, 16000, rate).astype(np.float32)
                    else:
                        frame_processed = frame.ravel() if frame.ndim > 1 else frame

                    frames.append(frame_processed)
                    if stream_callback:
                        stream_callback(frame_processed)
                except queue.Empty:
                    break

        try:
            # Suppress ALSA/PortAudio errors at OS level
            with silence_stderr():
                # Fast path: reuse a warm stream so capture starts instantly
                # (no device-open gap on the first syllable).
                if _warm_mic_enabled():
                    try:
                        stream = _get_cached_input_stream(device_idx, rate)
                        with _active_warm_sink["lock"]:
                            _active_warm_sink["queue"] = q
                        stream.start()
                        try:
                            _consume()
                        finally:
                            try:
                                stream.stop()
                            except Exception:
                                pass
                            with _active_warm_sink["lock"]:
                                _active_warm_sink["queue"] = None
                        return frames
                    except Exception as e:
                        logger.debug(f"Warm input stream failed ({e}); falling back to a fresh open")
                        _drop_cached_input_stream(device_idx, rate)

                # Cold path: open a fresh stream for this recording
                # Use blocksize=1024 and latency='low' for instant audio capture response
                stream = None
                try:
                    stream = sd.InputStream(
                        samplerate=rate,
                        channels=CHANNELS,
                        callback=callback,
                        device=device_idx,
                        blocksize=1024,
                        latency="low",
                    )
                except Exception:
                    stream = sd.InputStream(
                        samplerate=rate,
                        channels=CHANNELS,
                        callback=callback,
                        device=device_idx,
                    )
                with stream:
                    _consume()
            return frames
        except Exception:
            # If it's specifically a sample rate error, we'll try a fallback in the parent
            return None

    # Interactive mode helpers
    if interactive_mode:
        countdown_thread = threading.Thread(target=countdown_timer)
        countdown_thread.daemon = True
        countdown_thread.start()

        input_thread = threading.Thread(target=check_for_stop_key)
        input_thread.daemon = True
        input_thread.start()

        print("Recording... Press Space to stop")

    # Fast path: If INPUT_DEVICE_INDEX is already known, immediately record without
    # scanning all audio endpoints across all host APIs on every push-to-talk press.
    frames = None
    if not is_wsl and INPUT_DEVICE_INDEX is not None:
        frames = perform_recording(INPUT_DEVICE_INDEX, RATE)
        if frames is not None:
            ACTUAL_RATE = 16000
            return frames

    # Fallback / Auto-Recovery Path: triggered only if INPUT_DEVICE_INDEX was None or recording failed
    if not is_wsl:
        # Manual Override Logic for Native Windows/Linux
        if OVERRIDE_MODE == 'primary' and PRIMARY_DEVICE_NAME:
            primary_idx = find_device_index(PRIMARY_DEVICE_NAME)
            if primary_idx is not None:
                if INPUT_DEVICE_INDEX != primary_idx:
                    print(f"[Override] Using primary device: {PRIMARY_DEVICE_NAME}")
                    INPUT_DEVICE_INDEX = primary_idx
                    set_default_input_device(INPUT_DEVICE_INDEX)
            else:
                print(f"[Override] Primary device not found: {PRIMARY_DEVICE_NAME}")
        elif OVERRIDE_MODE == 'secondary' and SECONDARY_DEVICE_NAME:
            secondary_idx = find_device_index(SECONDARY_DEVICE_NAME)
            if secondary_idx is not None:
                if INPUT_DEVICE_INDEX != secondary_idx:
                    print(f"[Override] Using secondary device: {SECONDARY_DEVICE_NAME}")
                    INPUT_DEVICE_INDEX = secondary_idx
                    set_default_input_device(INPUT_DEVICE_INDEX)
            else:
                print(f"[Override] Secondary device not found: {SECONDARY_DEVICE_NAME}")
        elif PRIMARY_DEVICE_NAME:
            # Auto-recovery: Always try to see if the primary device has returned before starting
            primary_idx = find_device_index(PRIMARY_DEVICE_NAME)
            if primary_idx is not None:
                if INPUT_DEVICE_INDEX != primary_idx:
                    print(f"Switching to primary device: {PRIMARY_DEVICE_NAME}")
                    INPUT_DEVICE_INDEX = primary_idx
                    set_default_input_device(INPUT_DEVICE_INDEX)
            elif SECONDARY_DEVICE_NAME:
                # If primary is gone, ensure we at least use the secondary if it's available
                secondary_idx = find_device_index(SECONDARY_DEVICE_NAME)
                if secondary_idx is not None and INPUT_DEVICE_INDEX != secondary_idx:
                    print(f"Using secondary device: {SECONDARY_DEVICE_NAME}")
                    INPUT_DEVICE_INDEX = secondary_idx
                    set_default_input_device(INPUT_DEVICE_INDEX)

        # Verify INPUT_DEVICE_INDEX is still valid
        if INPUT_DEVICE_INDEX is not None:
            try:
                with silence_stderr():
                    d = sd.query_devices(INPUT_DEVICE_INDEX)
                    if d.get('max_input_channels', 0) <= 0:
                        INPUT_DEVICE_INDEX = None
            except Exception:
                INPUT_DEVICE_INDEX = None

    # Try primary/current device
    frames = perform_recording(INPUT_DEVICE_INDEX, RATE)
    ACTUAL_RATE = 16000

    # If it failed, try current device with its default sample rate
    if frames is None:
        try:
            with silence_stderr():
                device_info = sd.query_devices(INPUT_DEVICE_INDEX)
            default_rate = int(device_info['default_samplerate'])
            if default_rate != RATE:
                print(f"{RATE}Hz failed on '{device_info['name']}', trying default {default_rate}Hz (resampling to 16000Hz)...")
                frames = perform_recording(INPUT_DEVICE_INDEX, default_rate)
                if frames is not None:
                    ACTUAL_RATE = 16000
        except Exception:
            pass

    # If it still failed, try to find a fallback (only if not in manual override mode)
    if frames is None and OVERRIDE_MODE == 'auto':
        print("Attempting fallback to secondary device...")
        fallback_idx = find_device_index(SECONDARY_DEVICE_NAME)
        if fallback_idx is not None and fallback_idx != INPUT_DEVICE_INDEX:
            print(f"Primary device failed. Trying secondary: {SECONDARY_DEVICE_NAME} (index {fallback_idx})")

            # Try secondary at standard rate
            frames = perform_recording(fallback_idx, RATE)
            ACTUAL_RATE = RATE

            # If secondary standard rate fails, try its default rate
            if frames is None:
                try:
                    with silence_stderr():
                        device_info = sd.query_devices(fallback_idx)
                    default_rate = int(device_info['default_samplerate'])
                    if default_rate != RATE:
                        print(f"{RATE}Hz failed on secondary, trying default {default_rate}Hz...")
                        frames = perform_recording(fallback_idx, default_rate)
                        if frames is not None:
                            ACTUAL_RATE = default_rate
                except Exception:
                    pass

            if frames is not None:
                # Update current device if fallback succeeded
                INPUT_DEVICE_INDEX = fallback_idx
                set_default_input_device(INPUT_DEVICE_INDEX)
                print("Fallback successful!")
        else:
            print("No valid secondary device found or secondary device is the same as failed device.")

    # Ultimate fallback: try system default audio device if primary/secondary failed
    if frames is None:
        try:
            with silence_stderr():
                default_idx = find_device_index('default') or find_device_index('pipewire')
            if default_idx is not None and default_idx != INPUT_DEVICE_INDEX:
                print(f"Device failed. Attempting fallback to system default (index {default_idx})...")
                frames = perform_recording(default_idx, RATE)
                if frames is None:
                    try:
                        with silence_stderr():
                            d_info = sd.query_devices(default_idx)
                        d_rate = int(d_info['default_samplerate'])
                        frames = perform_recording(default_idx, d_rate)
                    except Exception:
                        pass
                if frames is not None:
                    INPUT_DEVICE_INDEX = default_idx
                    set_default_input_device(default_idx)
                    print("System default fallback successful!")
        except Exception:
            pass

    elif frames is None:
        print(f"Recording failed on {OVERRIDE_MODE} device.")

    # Update the last used device name for reporting
    try:
        if INPUT_DEVICE_INDEX is not None:
            with silence_stderr():
                name = sd.query_devices(INPUT_DEVICE_INDEX)['name']
            LAST_USED_DEVICE_NAME = name
    except Exception:
        pass

    if not frames:
        return np.array([], dtype=np.float32)
    if len(frames) == 1:
        f0 = frames[0]
        return f0.ravel() if f0.ndim > 1 else f0
    concatenated = np.concatenate(frames, axis=0)
    return concatenated.ravel() if concatenated.ndim > 1 else concatenated



def countdown_timer():
    """Display countdown timer"""
    for i in range(RECORD_SECONDS, 0, -1):
        if stop_recording.is_set():
            break
        print(f'Recording: {i}s... (press space to stop)', end='\r')
        if stop_recording.wait(timeout=1.0):
            break

def check_for_stop_key():
    """Check for space key"""
    if sys.platform == "win32":
        try:
            import msvcrt
            while not stop_recording.is_set():
                if msvcrt.kbhit():
                    c = msvcrt.getwch()
                    if c == ' ':
                        stop_recording.set()
                        break
                time.sleep(0.05)
        except Exception:
            pass
        return

    import select
    try:
        import termios
        import tty
        old_settings = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin.fileno())
        while not stop_recording.is_set():
            rlist, _, _ = select.select([sys.stdin], [], [], 0.05)
            if rlist:
                c = sys.stdin.read(1)
                if c == ' ':
                    stop_recording.set()
                    break
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
    except Exception:
        pass

def process_audio_stream(audio_data=None):
    """Process audio frames. If None, it's expected to be passed in."""
    if audio_data is None or len(audio_data) == 0:
        return "", 0

    duration = len(audio_data) / ACTUAL_RATE
    if duration < 0.15: # Support short single-word utterances (0.2s - 0.5s)
        return "", 0

    get_model(device=DEVICE)

    transcribe_start_time = time.time()

    # Transcribe directly from numpy array (zero-copy flattening)
    try:
        if isinstance(audio_data, np.ndarray) and audio_data.dtype == np.float32 and audio_data.ndim == 1:
            flat_audio = audio_data
        else:
            flat_audio = np.asarray(audio_data, dtype=np.float32).ravel()
        result = transcribe_audio(audio_data=flat_audio, sample_rate=ACTUAL_RATE, device=DEVICE)
    except Exception as e:
        print(f"Processing error: {e}")
        result = ""

    transcribe_end_time = time.time()

    return result, transcribe_end_time - transcribe_start_time

def copy_text_to_clipboard(text):
    """Copy ``text`` using the platform HAL, with a pyperclip fallback."""
    try:
        import hal

        if hal.get_clipboard_sink().copy_text(text):
            return True
    except Exception:
        pass
    try:
        import pyperclip

        pyperclip.copy(text)
        return True
    except Exception:
        return False


def record_and_transcribe():
    """Record audio and transcribe it"""
    process_start_time = time.time()
    stop_recording.clear()

    frames = record_audio_stream(interactive_mode=True)

    # Transcribe using the optimized process_audio_stream
    result, transcribe_time = process_audio_stream(frames)

    transcription = result.strip()

    if transcription:
        # Copy to clipboard with retry mechanism
        max_retries = 3

        for attempt in range(max_retries):
            try:
                if copy_text_to_clipboard(transcription):
                    print("Transcription copied to clipboard")
                    break
                raise RuntimeError("clipboard backend returned failure")
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"Clipboard copy failed (attempt {attempt+1}), retrying...")
                    time.sleep(0.5)
                else:
                    print(f"Failed to copy to clipboard after {max_retries} attempts: {e}")

    print(f"Total time: {time.time() - process_start_time:.2f}s (Transcribe: {transcribe_time:.2f}s)")
    print(f"\nTranscription: {transcription}")
    return transcription

def getch():
    """Get single character with echo"""
    if sys.platform == "win32":
        try:
            import msvcrt
            return msvcrt.getwche()
        except Exception:
            return sys.stdin.read(1)

    try:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            ch = sys.stdin.read(1)
            # Echo the character manually to be sure it shows up
            sys.stdout.write(ch)
            sys.stdout.flush()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return ch
    except Exception:
        return sys.stdin.read(1)

def main():
    print("T2 Transcription Tool (Optimized)")
    print(f"Using device: {DEVICE}")
    load_audio_config()

    # Preload model at startup
    preload_thread = preload_model(device=DEVICE)

    if preload_thread.is_alive():
        print("Waiting for model...")
        preload_thread.join()
        print("Model ready!")

    while True:
        try:
            print("> ", end="", flush=True)
            ch = getch()
            if ch in [' ', '\r', '\n']:
                print()
                record_and_transcribe()
            elif ch in ['M', 'i', 'I']:
                print()
                select_audio_device()
            elif ch.lower() == 'r':
                print("\nResetting terminal and clipboard...")
                reset_terminal()
            elif ch.lower() == 'q':
                break
        except KeyboardInterrupt:
            break

if __name__ == "__main__":
    main()
