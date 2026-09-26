#!/usr/bin/env python3
# Optimized script to record audio and transcribe it with minimal latency
# Updated with sounddevice for robust audio capture

import os
import sys

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
import pyperclip
import threading
import atexit
import time
import numpy as np
import sounddevice as sd
import soundfile as sf
import warnings
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
    
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir

def get_temp_dir():
    """Get temporary directory for audio files"""
    if 'XDG_RUNTIME_DIR' in os.environ:
        temp_dir = Path(os.environ['XDG_RUNTIME_DIR']) / 'vt'
        temp_dir.mkdir(parents=True, exist_ok=True)
        return temp_dir
    else:
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
NUMBER_DIGITS = True  # Convert spoken number words to digits ("twenty five" -> 25)
MIDDLE_CLICK_ENABLED = True  # Push-to-talk by holding middle mouse button (>= 0.25s)
KEEP_BLUETOOTH_HANDSFREE = True  # Prevent WirePlumber/PipeWire from auto-reverting to headphone profile (pausing media)
SOUND_THEME = "proximity"
UI_THEME = "auto"
PUNCTUATION_MODE = "full"
PUNCTUATION_MODES = ["full", "no_terminal_period", "no_punctuation", "lowercase_no_punctuation"]
WAIT_FOR_MODEL_ON_STARTUP = True
ENABLE_SLM = False
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


def get_wireplumber_bt_autoswitch():
    """Check if WirePlumber autoswitch to headset profile is enabled."""
    import shutil
    import subprocess
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
    import subprocess
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


def apply_bluetooth_handsfree_policy(keep_handsfree: bool = True):
    """Apply the policy to keep Bluetooth devices in hands-free mode (prevents media pause on record end)."""
    if keep_handsfree:
        set_wireplumber_bt_autoswitch(False)
    else:
        set_wireplumber_bt_autoswitch(True)


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
                lines = [l for l in res.stdout.strip().split("\n") if l]
                sources = [l.split()[1] for l in lines if len(l.split()) >= 2]
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
                import termios, sys
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

import transcribe2

def load_audio_config(file_path=None):
    """Load audio device configuration from local file with fallback"""
    global INPUT_DEVICE_INDEX, PRIMARY_DEVICE_NAME, SECONDARY_DEVICE_NAME, OVERRIDE_MODE, MODEL_BACKEND, COPY_TO_CLIPBOARD, IS_MUTED, AUTO_TYPE, OUTPUT_MODE, AUTO_TYPE_TRAILING_SPACE, AUTO_TYPE_AUTO_PUNCTUATE, NUMBER_DIGITS, MIDDLE_CLICK_ENABLED, KEEP_BLUETOOTH_HANDSFREE, SOUND_THEME, UI_THEME, PUNCTUATION_MODE, CONFIG_FILE
    if file_path is not None:
        CONFIG_FILE = Path(file_path)
    else:
        # Always re-resolve (honors monkeypatched get_config_file in tests, and
        # picks up any new config file created since import)
        CONFIG_FILE = get_config_file()
    try:
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
            NUMBER_DIGITS = config.get('number_digits', True)
            MIDDLE_CLICK_ENABLED = config.get('middle_click_enabled', True)
            KEEP_BLUETOOTH_HANDSFREE = config.get('keep_bluetooth_handsfree', True)

            raw_punct = config.get('punctuation_mode') or config.get('formatting_level') or 'full'
            if raw_punct in ('semi-formal', 'semi_formal'):
                PUNCTUATION_MODE = 'no_terminal_period'
            else:
                PUNCTUATION_MODE = str(raw_punct).strip().lower()

            env_punct = os.environ.get("VT_PUNCTUATION_MODE", "").strip().lower()
            if env_punct:
                if env_punct in ('semi-formal', 'semi_formal'):
                    PUNCTUATION_MODE = 'no_terminal_period'
                else:
                    PUNCTUATION_MODE = env_punct

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

            env_number_digits = os.environ.get("VT_NUMBER_DIGITS", "").strip().lower()
            if env_number_digits in ["1", "true", "yes"]:
                NUMBER_DIGITS = True
            elif env_number_digits in ["0", "false", "no"]:
                NUMBER_DIGITS = False

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
                            except:
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

        # Keep the post-processor's runtime number-toggle in sync with the loaded config
        set_number_digits(NUMBER_DIGITS)

        # Keep post-processor punctuation mode in sync
        set_punctuation_mode(PUNCTUATION_MODE)

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
    global CONFIG_FILE, AUTO_TYPE, OUTPUT_MODE, COPY_TO_CLIPBOARD, AUTO_TYPE_TRAILING_SPACE, AUTO_TYPE_AUTO_PUNCTUATE
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
            'number_digits': NUMBER_DIGITS,
            'middle_click_enabled': MIDDLE_CLICK_ENABLED,
            'keep_bluetooth_handsfree': KEEP_BLUETOOTH_HANDSFREE,
            'punctuation_mode': PUNCTUATION_MODE,
            'enable_slm': ENABLE_SLM,
            'wait_for_model_on_startup': WAIT_FOR_MODEL_ON_STARTUP,
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


def cycle_output_mode() -> str:
    """Cycle output mode: clipboard -> type -> type_fast."""
    global OUTPUT_MODE, AUTO_TYPE, COPY_TO_CLIPBOARD
    idx = (OUTPUT_MODES.index(OUTPUT_MODE) + 1) % len(OUTPUT_MODES)
    OUTPUT_MODE = OUTPUT_MODES[idx]
    AUTO_TYPE = (OUTPUT_MODE in ("type", "type_fast"))
    COPY_TO_CLIPBOARD = (OUTPUT_MODE not in ("type", "type_fast"))
    if AUTO_TYPE and AUTO_TYPE_AUTO_PUNCTUATE:
        set_punctuation_mode("full")
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


def set_punctuation_mode(mode: str) -> None:
    """Runtime setter for punctuation formatting mode; keeps post-processor in sync."""
    global PUNCTUATION_MODE
    clean = str(mode).strip().lower()
    if clean in ("semi-formal", "semi_formal"):
        clean = "no_terminal_period"
    PUNCTUATION_MODE = clean
    try:
        from post_processor import set_punctuation_mode as post_set_punct
        post_set_punct(PUNCTUATION_MODE)
    except Exception:
        pass


def cycle_punctuation_mode() -> str:
    """Cycle through the 4 punctuation modes and save."""
    global PUNCTUATION_MODE
    idx = PUNCTUATION_MODES.index(PUNCTUATION_MODE) if PUNCTUATION_MODE in PUNCTUATION_MODES else 0
    next_mode = PUNCTUATION_MODES[(idx + 1) % len(PUNCTUATION_MODES)]
    set_punctuation_mode(next_mode)
    return next_mode


def set_number_digits(enabled):
    """Runtime toggle for number-word -> digit conversion; keeps post-processor in sync."""
    global NUMBER_DIGITS
    NUMBER_DIGITS = bool(enabled)
    try:
        from post_processor import set_number_digits_enabled
        set_number_digits_enabled(NUMBER_DIGITS)
    except Exception:
        pass


def set_middle_click_enabled(enabled):
    """Runtime toggle for middle click hold push-to-talk mode."""
    global MIDDLE_CLICK_ENABLED
    MIDDLE_CLICK_ENABLED = bool(enabled)
    try:
        import hotkeys
        hotkeys.set_global_middle_click_enabled(MIDDLE_CLICK_ENABLED)
    except Exception:
        pass


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
                if ch2 == 'H': return 'UP'
                elif ch2 == 'P': return 'DOWN'
                elif ch2 == 'M': return 'RIGHT'
                elif ch2 == 'K': return 'LEFT'
                elif ch2 == '\x0f': return 'UP'  # Shift+Tab
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

    import termios, tty, select
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
                    if ch3 == 'A': return 'UP'
                    elif ch3 == 'B': return 'DOWN'
                    elif ch3 == 'C': return 'RIGHT'
                    elif ch3 == 'D': return 'LEFT'
                    elif ch3 == 'Z': return 'UP'  # Shift+Tab
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

        for row_i, (orig_i, (name, label, desc)) in enumerate(matches):
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

        for row_i, (orig_i, dev) in enumerate(matches):
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


def select_settings_picker():
    """Interactive settings picker modal with live in-place toggling and fuzzy search."""
    global INPUT_DEVICE_INDEX, PRIMARY_DEVICE_NAME, SECONDARY_DEVICE_NAME, OVERRIDE_MODE
    global MODEL_BACKEND, COPY_TO_CLIPBOARD, IS_MUTED, SOUND_THEME, AUTO_TYPE
    global UI_THEME, NUMBER_DIGITS, MIDDLE_CLICK_ENABLED, KEEP_BLUETOOTH_HANDSFREE
    global AUTO_TYPE_TRAILING_SPACE, AUTO_TYPE_AUTO_PUNCTUATE, OUTPUT_MODE, PUNCTUATION_MODE

    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich import box

    console = Console()
    reset_done = False
    query = ""
    selected_idx = 0

    settings_defs = [
        {
            "id": "trailing_space",
            "icon": "␣ ",
            "title": "Trailing Space",
            "keywords": "trailing space auto type whitespace append space",
        },
        {
            "id": "auto_punctuate",
            "icon": "✍️ ",
            "title": "Auto-Punctuation",
            "keywords": "auto punctuate punctuation period sentence grammar enforcement",
        },
        {
            "id": "number_digits",
            "icon": "🔢",
            "title": "Number Conversion",
            "keywords": "numbers digits words spelled format numeric conversion",
        },
        {
            "id": "middle_click",
            "icon": "🖱️ ",
            "title": "Mouse Hotkey",
            "keywords": "middle click mouse hotkey push to talk button hold",
        },
        {
            "id": "sound_mute",
            "icon": "🔊",
            "title": "Sound Effects",
            "keywords": "sound effects mute volume chimes audio audio cues notify",
        },
        {
            "id": "output_mode",
            "icon": "🚀",
            "title": "Output Mode",
            "keywords": "output mode clipboard type typing paste speed fast slow safe",
        },
        {
            "id": "punctuation_mode",
            "icon": "📝",
            "title": "Formatting Mode",
            "keywords": "formatting mode punctuation period lowercase none full",
        },
        {
            "id": "theme",
            "icon": "🎨",
            "title": "UI Color Theme",
            "keywords": "theme color ui palette cyan magenta green blue yellow red white",
        },
        {
            "id": "microphone",
            "icon": "🎤",
            "title": "Audio Device (Mic)",
            "keywords": "mic microphone audio device input hardware primary secondary",
        },
        {
            "id": "reset_terminal",
            "icon": "🔄",
            "title": "Reset Terminal",
            "keywords": "reset terminal clipboard bridge clear state fix",
        },
    ]

    def get_setting_state(item_id):
        eff_theme = UI_THEME.lower() if UI_THEME and UI_THEME.lower() in COLOR_PALETTES and UI_THEME.lower() != "auto" else "green"
        if item_id == "trailing_space":
            if AUTO_TYPE_TRAILING_SPACE:
                return "Enabled (appends ' ')", "[ON]", "green"
            else:
                return "Disabled (exact text)", "[OFF]", "dim white"
        elif item_id == "auto_punctuate":
            if AUTO_TYPE_AUTO_PUNCTUATE:
                return "Enabled (full + period)", "[ON]", "green"
            else:
                return "Disabled (preserve user)", "[OFF]", "dim white"
        elif item_id == "number_digits":
            if NUMBER_DIGITS:
                return "Digits (1, 2, 3)", "[DIGITS]", "green"
            else:
                return "Words (one, two, three)", "[WORDS]", "dim white"
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
        elif item_id == "punctuation_mode":
            mode = PUNCTUATION_MODE
            if mode == "no_terminal_period":
                return "No Trailing Period (Semi-Formal)", "[SEMI]", "cyan"
            elif mode == "no_punctuation":
                return "No Punctuation", "[NONE]", "cyan"
            elif mode == "lowercase_no_punctuation":
                return "Lowercase Without Punctuation", "[LOWER]", "cyan"
            else:
                return "Full Punctuation", "[FULL]", "cyan"
        elif item_id == "theme":
            name = (UI_THEME or "auto").capitalize()
            return f"{name} palette", "[PICKER]", eff_theme
        elif item_id == "microphone":
            dev = PRIMARY_DEVICE_NAME or "Default Microphone"
            if len(dev) > 30:
                dev = dev[:27] + "..."
            return dev, "[SELECT]", "yellow"
        elif item_id == "reset_terminal":
            if reset_done:
                return "Terminal & clipboard bridge reset", "[DONE]", "green"
            else:
                return "Reset terminal state & clipboard bridge", "[RUN]", "yellow"
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

        for row_i, (orig_i, item) in enumerate(matches):
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

        key = _read_key()
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
                if item_id == "trailing_space":
                    toggle_auto_type_trailing_space()
                elif item_id == "auto_punctuate":
                    toggle_auto_type_auto_punctuate()
                elif item_id == "number_digits":
                    set_number_digits(not NUMBER_DIGITS)
                    save_audio_config()
                elif item_id == "middle_click":
                    set_middle_click_enabled(not MIDDLE_CLICK_ENABLED)
                    save_audio_config()
                elif item_id == "sound_mute":
                    IS_MUTED = not IS_MUTED
                    save_audio_config()
                elif item_id == "output_mode":
                    cycle_output_mode()
                    save_audio_config()
                elif item_id == "punctuation_mode":
                    cycle_punctuation_mode()
                    save_audio_config()
                elif item_id == "theme":
                    select_theme_picker()
                elif item_id == "microphone":
                    select_microphone_picker()
                elif item_id == "reset_terminal":
                    reset_terminal()
                    reset_done = True
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
                    # Use a shorter timeout for better responsiveness to the stop event
                    frame = q.get(timeout=0.05)
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
        except Exception as e:
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
        except:
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
                except:
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
    except:
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
        if stop_recording.is_set(): break
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
        import termios, tty
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
    except:
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
        copy_success = False
        
        for attempt in range(max_retries):
            try:
                if copy_text_to_clipboard(transcription):
                    copy_success = True
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
        import termios, tty
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
    except:
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
