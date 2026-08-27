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

# In WSL, ALSA needs to be told to use PulseAudio explicitly, otherwise PortAudio finds 0 devices
if "microsoft" in os.uname().release.lower() or os.path.exists("/mnt/wslg"):
    alsa_conf_path = "/tmp/vt-alsa-pulse.conf"
    if not os.path.exists(alsa_conf_path):
        with open(alsa_conf_path, "w") as f:
            f.write("pcm.!default { type pulse }\nctl.!default { type pulse }\n")
    os.environ["ALSA_CONFIG_PATH"] = alsa_conf_path

import queue
import pyperclip
import threading
import time
import numpy as np
import sounddevice as sd
import soundfile as sf
import warnings
from transcribe2 import transcribe_audio, get_model
import transcribe2
import json
import tempfile
import contextlib
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
MODEL_BACKEND = os.environ.get("VT_MODEL_BACKEND", "cohere").lower() # 'cohere' or 'whisper'
COPY_TO_CLIPBOARD = True
AUTO_TYPE = False
IS_MUTED = True
SOUND_THEME = "proximity"
UI_THEME = "auto"
GLOBAL_CONFIG_FILE = get_data_dir() / 'audio_device_config.json'

def get_config_file():
    """Find local project config.yaml/config.yml/config.json (in root or config/ dir), fallback to global data dir"""
    candidates = [
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
        # Reset terminal state
        os.system('reset')
        
        # Kill stuck clipboard processes (Wayland)
        try:
            subprocess.run(['pkill', 'wl-copy'], stderr=subprocess.DEVNULL)
            subprocess.run(['pkill', 'wl-paste'], stderr=subprocess.DEVNULL)
        except:
            pass

        # Also re-initialize termios just in case
        import termios, sys
        fd = sys.stdin.fileno()
        try:
            termios.tcgetattr(fd)
        except:
            # If it's already broken, this might help
            pass
    except:
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
    global INPUT_DEVICE_INDEX, PRIMARY_DEVICE_NAME, SECONDARY_DEVICE_NAME, OVERRIDE_MODE, MODEL_BACKEND, COPY_TO_CLIPBOARD, IS_MUTED, AUTO_TYPE, SOUND_THEME, UI_THEME, CONFIG_FILE
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
            if CONFIG_FILE.suffix in ['.yaml', '.yml']:
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
            AUTO_TYPE = config.get('auto_type', False)
            
            env_muted = os.environ.get("VT_IS_MUTED", "").strip().lower()
            if env_muted in ["1", "true", "yes"]:
                IS_MUTED = True
            elif env_muted in ["0", "false", "no"]:
                IS_MUTED = False
            
            env_auto_type = os.environ.get("VT_AUTO_TYPE", "").strip().lower()
            if env_auto_type in ["1", "true", "yes"]:
                AUTO_TYPE = True
            elif env_auto_type in ["0", "false", "no"]:
                AUTO_TYPE = False

            env_sound = os.environ.get("VT_SOUND_THEME", "").strip()
            SOUND_THEME = env_sound or config.get('sound_theme', 'proximity')
            if SOUND_THEME.lower() in ["silent", "muted", "none"]:
                IS_MUTED = True

            env_backend = os.environ.get("VT_MODEL_BACKEND", "").lower()
            MODEL_BACKEND = env_backend or config.get('model_backend', 'cohere')
            COPY_TO_CLIPBOARD = config.get('copy_to_clipboard', True)
            
            env_theme = os.environ.get("VT_UI_THEME", "").strip().lower()
            UI_THEME = env_theme or config.get('ui_theme', 'auto')
            
            # Update backend in transcribe2
            transcribe2.set_backend(MODEL_BACKEND)
            
            # If we have an override, try that first
            if OVERRIDE_MODE == 'primary' and PRIMARY_DEVICE_NAME:
                idx = find_device_index(PRIMARY_DEVICE_NAME)
                if idx is not None:
                    INPUT_DEVICE_INDEX = idx
                    print(f"[Override] Using primary device: {PRIMARY_DEVICE_NAME} (index {idx})")
                else:
                    print(f"[Override] Primary device not found: {PRIMARY_DEVICE_NAME}")
            elif OVERRIDE_MODE == 'secondary' and SECONDARY_DEVICE_NAME:
                idx = find_device_index(SECONDARY_DEVICE_NAME)
                if idx is not None:
                    INPUT_DEVICE_INDEX = idx
                    print(f"[Override] Using secondary device: {SECONDARY_DEVICE_NAME} (index {idx})")
                else:
                    print(f"[Override] Secondary device not found: {SECONDARY_DEVICE_NAME}")
            
            # If no override or override failed, try the standard auto logic
            if INPUT_DEVICE_INDEX is None:
                # Attempt to find primary
                idx = find_device_index(PRIMARY_DEVICE_NAME)
                if idx is not None:
                    INPUT_DEVICE_INDEX = idx
                    print(f"Using primary audio device: {PRIMARY_DEVICE_NAME} (index {idx})")
                else:
                    # Attempt to find secondary
                    idx = find_device_index(SECONDARY_DEVICE_NAME)
                    if idx is not None:
                        INPUT_DEVICE_INDEX = idx
                        print(f"Using secondary audio device: {SECONDARY_DEVICE_NAME} (index {idx})")
                    else:
                        # Fallback to index if names fail (for backward compatibility or if names are not set)
                        INPUT_DEVICE_INDEX = config.get('input_device_index')
                        if INPUT_DEVICE_INDEX is not None:
                            try:
                                d = sd.query_devices(INPUT_DEVICE_INDEX)
                                print(f"Falling back to saved device index {INPUT_DEVICE_INDEX}: {d['name']}")
                            except:
                                INPUT_DEVICE_INDEX = None
            
            if INPUT_DEVICE_INDEX is not None:
                sd.default.device = INPUT_DEVICE_INDEX
                # Print secondary device info
                if SECONDARY_DEVICE_NAME:
                    sec_idx = find_device_index(SECONDARY_DEVICE_NAME)
                    if sec_idx is not None:
                        print(f"Secondary audio device: {SECONDARY_DEVICE_NAME} (index {sec_idx})")
            else:
                print("No configured audio devices found. Using system default.")
    except Exception as e:
        print(f"Could not load audio config: {e}")

def save_audio_config(file_path=None):
    """Save audio device configuration to local file preserving existing keys and format"""
    global CONFIG_FILE
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
                if CONFIG_FILE.suffix in ['.yaml', '.yml']:
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

        existing_config.update({
            'input_device_index': INPUT_DEVICE_INDEX,
            'primary_device_name': PRIMARY_DEVICE_NAME,
            'secondary_device_name': SECONDARY_DEVICE_NAME,
            'override_mode': OVERRIDE_MODE,
            'is_muted': IS_MUTED,
            'auto_type': AUTO_TYPE,
            'sound_theme': SOUND_THEME,
            'model_backend': MODEL_BACKEND,
            'copy_to_clipboard': COPY_TO_CLIPBOARD,
            'ui_theme': UI_THEME
        })

        if CONFIG_FILE.suffix in ['.yaml', '.yml']:
            try:
                import yaml
                out_content = yaml.dump(existing_config, default_flow_style=False, sort_keys=False)
            except Exception:
                out_content = json.dumps(existing_config, indent=2)
        else:
            out_content = json.dumps(existing_config, indent=2)

        with open(CONFIG_FILE, 'w') as f:
            f.write(out_content)
        print(f"Saved audio device config to {CONFIG_FILE}")
    except Exception as e:
        print(f"Could not save audio config: {e}")

def select_audio_device():
    """Interactive audio device selection with Primary/Secondary support & Rich styling"""
    global INPUT_DEVICE_INDEX, PRIMARY_DEVICE_NAME, SECONDARY_DEVICE_NAME, OVERRIDE_MODE, MODEL_BACKEND, COPY_TO_CLIPBOARD, IS_MUTED, SOUND_THEME, AUTO_TYPE, UI_THEME
    
    # Always reset terminal before interaction to fix terminal state
    reset_terminal() 
    
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    
    console = Console()
    is_wsl = "microsoft" in os.uname().release.lower() or os.path.exists("/mnt/wslg")
    
    table = Table(expand=True, border_style="cyan", show_header=True, header_style="bold yellow")
    table.add_column("Key", style="bold cyan", width=6)
    table.add_column("Setting Description", style="white")
    table.add_column("Current Status", style="bold green")

    if is_wsl:
        table.add_row("W", "Open Windows Microphone Settings", "WSL Bridge Active")
    else:
        table.add_row("P", "Set Primary Device", PRIMARY_DEVICE_NAME or "Not Set")
        table.add_row("S", "Set Secondary Device", SECONDARY_DEVICE_NAME or "Not Set")
        
    table.add_row("M", "Toggle Sound Effects", "MUTED" if IS_MUTED else "Sound On")
    table.add_row("E", "Select Sound Effect Theme", SOUND_THEME.capitalize() if SOUND_THEME else "Proximity")
    table.add_row("C", "Select UI Color Theme", f"{UI_THEME.upper()}")
    table.add_row("B", "Switch Model Backend", MODEL_BACKEND.capitalize())
    table.add_row("T", "Toggle Auto-Type Output", "ENABLED" if AUTO_TYPE else "DISABLED (Clipboard Only)")
    table.add_row("R", "Reset Terminal & Audio Bridge", "Ready")
    
    if not is_wsl:
        p_marker = "[ACTIVE]" if OVERRIDE_MODE == 'primary' else ""
        s_marker = "[ACTIVE]" if OVERRIDE_MODE == 'secondary' else ""
        a_marker = "[ACTIVE]" if OVERRIDE_MODE == 'auto' else ""
        table.add_row("p", "Use Primary Device (Manual Override)", p_marker or "Inactive")
        table.add_row("s", "Use Secondary Device (Manual Override)", s_marker or "Inactive")
        table.add_row("a", "Automatic Selection (Default)", a_marker or "Inactive")
        
    table.add_row("q / ↵", "Save and Exit Configuration Menu", "Done")

    panel = Panel(table, title="⚙️  Voice Transcriber Settings", border_style="bright_cyan", padding=(0, 1))
    console.print(panel)
    console.print("[dim white]Press choice key: [/dim white]", end="", flush=True)
    
    choice = getch()
    print() # Newline after getch
    
    if choice.lower() in ['q', '\r', '\n']: 
        reset_terminal()
        return True

    if choice.upper() == 'C':
        palettes = ["auto", "green", "cyan", "blue", "magenta", "yellow", "red", "white"]
        try:
            curr_idx = palettes.index(UI_THEME.lower())
            next_idx = (curr_idx + 1) % len(palettes)
        except ValueError:
            next_idx = 1
        UI_THEME = palettes[next_idx]
        print(f"UI Color Theme set to: {UI_THEME.upper()}")
        save_audio_config()
        reset_terminal()
        return select_audio_device()
    
    if choice.lower() == 'r':
        reset_terminal()
        return select_audio_device()
    
    if choice.lower() == 'm':
        IS_MUTED = not IS_MUTED
        print(f"Sounds {'Muted' if IS_MUTED else 'Enabled'}")
        save_audio_config()
        reset_terminal()
        return select_audio_device()
    
    if choice.upper() == 'E':
        reset_terminal()
        print("\n--- Select Sound Effect Theme ---")
        print("  1. Proximity Chime (Default - Modern soft Windows chime)")
        print("  2. Speech Tones (Cortana Speech On/Off)")
        print("  3. Windows Notify (Classic Ding)")
        print("  4. Subtle Tap (Navigation Start)")
        print("  5. Retro Classic (Tada / Chimes)")
        print("  6. Muted (Silent)")
        print("  7. Custom Windows .wav Path")
        print("  b. Back to main menu")
        print("\nYour choice (1-7): ", end="", flush=True)
        s_choice = getch()
        print()
        theme_map = {
            '1': 'proximity',
            '2': 'speech',
            '3': 'notify',
            '4': 'navigation',
            '5': 'classic',
            '6': 'silent'
        }
        if s_choice in theme_map:
            SOUND_THEME = theme_map[s_choice]
            IS_MUTED = (SOUND_THEME == 'silent')
        elif s_choice == '7':
            custom_p = input("Enter full path to .wav file: ").strip()
            if custom_p:
                SOUND_THEME = custom_p
                
        # Send update to active hotkey bridge if running
        try:
            import hotkeys
            hotkeys.set_global_sound_theme(SOUND_THEME)
        except:
            pass
            
        save_audio_config()
        reset_terminal()
        return select_audio_device()
    
    if choice == 'B':
        if MODEL_BACKEND == 'whisper':
            MODEL_BACKEND = 'cohere'
        else:
            MODEL_BACKEND = 'whisper'
        print(f"Model backend set to: {MODEL_BACKEND}")
        transcribe2.set_backend(MODEL_BACKEND)
        save_audio_config()
        reset_terminal()
        return select_audio_device()
    
    if choice == 'T':
        AUTO_TYPE = not AUTO_TYPE
        print(f"Auto-Type set to: {'Enabled' if AUTO_TYPE else 'Disabled (Clipboard Only)'}")
        save_audio_config()
        reset_terminal()
        return select_audio_device()
        
    if is_wsl and choice.lower() == 'w':
        print("\nWSL Environment detected. The WSL bridge automatically uses the Windows Default Microphone.")
        print("Opening Windows Sound Settings (ms-settings:sound) to change your microphone...")
        import subprocess
        try:
            subprocess.run(['powershell.exe', '-NoProfile', '-Command', 'Start-Process', 'ms-settings:sound'], 
                           check=False, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        except:
            pass
        reset_terminal()
        return select_audio_device()
        
    if not is_wsl:
        if choice == 'p':
            OVERRIDE_MODE = 'primary'
            if PRIMARY_DEVICE_NAME:
                idx = find_device_index(PRIMARY_DEVICE_NAME)
                if idx is not None:
                    INPUT_DEVICE_INDEX = idx
                    sd.default.device = INPUT_DEVICE_INDEX
                    print(f"Set to Primary Device: {PRIMARY_DEVICE_NAME}")
                else:
                    print(f"Primary device not found: {PRIMARY_DEVICE_NAME}")
            else:
                print("Primary device not configured yet.")
            save_audio_config()
            return True
        elif choice == 's':
            OVERRIDE_MODE = 'secondary'
            if SECONDARY_DEVICE_NAME:
                idx = find_device_index(SECONDARY_DEVICE_NAME)
                if idx is not None:
                    INPUT_DEVICE_INDEX = idx
                    sd.default.device = INPUT_DEVICE_INDEX
                    print(f"Set to Secondary Device: {SECONDARY_DEVICE_NAME}")
                else:
                    print(f"Secondary device not found: {SECONDARY_DEVICE_NAME}")
            else:
                print("Secondary device not configured yet.")
            save_audio_config()
            return True
        elif choice.lower() == 'a':
            OVERRIDE_MODE = 'auto'
            print("Mode: Automatic Selection")
            # Let record_audio_stream handle the logic for auto selection
            save_audio_config()
            return True

    if choice not in ['P', 'S']:
        print("Invalid choice.")
        return False
        
    is_primary = (choice == 'P')
    label = "Primary" if is_primary else "Secondary"
    
    # NEW LOGIC FOR WSL:
    if "microsoft" in os.uname().release.lower() or os.path.exists("/mnt/wslg"):
        print(f"\nWSL Environment detected. {label} device uses the Windows Default Microphone.")
        print("Opening Windows Sound Settings (ms-settings:sound) to change your microphone...")
        import subprocess
        try:
            subprocess.run(['powershell.exe', '-NoProfile', '-Command', 'Start-Process', 'ms-settings:sound'], 
                           check=False, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        except:
            pass
        reset_terminal()
        return True
    
    print(f"\nAvailable Audio Input Devices for {label}:")
    print("=" * 60)
    
    devices = sd.query_devices()
    input_devices = [(i, d) for i, d in enumerate(devices) if d['max_input_channels'] > 0]
    
    for i, (device_idx, device_info) in enumerate(input_devices):
        markers = []
        if PRIMARY_DEVICE_NAME and PRIMARY_DEVICE_NAME.lower() in device_info['name'].lower():
            markers.append("PRIMARY")
        if SECONDARY_DEVICE_NAME and SECONDARY_DEVICE_NAME.lower() in device_info['name'].lower():
            markers.append("SECONDARY")
            
        marker_str = " ← " + " & ".join(markers) if markers else ""
        print(f"  {i}: {device_info['name']}{marker_str}")
    
    if not input_devices:
        print("No input devices found!")
        return False
    
    try:
        prompt = f"Enter the number (0-{len(input_devices)-1}) of the device you want to use as {label}, or 'c' to cancel: "
        # Use regular input here because we need numbers (could be multi-digit)
        # But ensure we are in a sane terminal state
        print(prompt, end="", flush=True)
        choice = input().strip().lower()
        if choice == 'c' or not choice: return False
        
        device_idx = int(choice)
        if 0 <= device_idx < len(input_devices):
            selected_idx = input_devices[device_idx][0]
            selected_name = input_devices[device_idx][1]['name']
            
            if is_primary:
                PRIMARY_DEVICE_NAME = selected_name
                INPUT_DEVICE_INDEX = selected_idx
                sd.default.device = INPUT_DEVICE_INDEX
            else:
                SECONDARY_DEVICE_NAME = selected_name
            
            print(f"{label} Selected: {selected_name}")
            save_audio_config()
            reset_terminal()
            return True
        else:
            print("Invalid choice")
            reset_terminal()
            return False
    except Exception as e:
        print(f"Selection error: {e}")
        reset_terminal()
        return False

def record_audio_stream(interactive_mode=False, stream_callback=None):
    """Record audio using sounddevice with fallback and auto-recovery support"""
    global INPUT_DEVICE_INDEX, ACTUAL_RATE, LAST_USED_DEVICE_NAME
    
    is_wsl = "microsoft" in os.uname().release.lower() or os.path.exists("/mnt/wslg")
    
    if is_wsl:
        # In WSL, we always rely on the single default ALSA-Pulse audio bridge
        INPUT_DEVICE_INDEX = None
        sd.default.device = None
    else:
        # Manual Override Logic for Native Windows/Linux
        if OVERRIDE_MODE == 'primary' and PRIMARY_DEVICE_NAME:
            primary_idx = find_device_index(PRIMARY_DEVICE_NAME)
            if primary_idx is not None:
                if INPUT_DEVICE_INDEX != primary_idx:
                    print(f"[Override] Using primary device: {PRIMARY_DEVICE_NAME}")
                    INPUT_DEVICE_INDEX = primary_idx
                    sd.default.device = INPUT_DEVICE_INDEX
            else:
                print(f"[Override] Primary device not found: {PRIMARY_DEVICE_NAME}")
        elif OVERRIDE_MODE == 'secondary' and SECONDARY_DEVICE_NAME:
            secondary_idx = find_device_index(SECONDARY_DEVICE_NAME)
            if secondary_idx is not None:
                if INPUT_DEVICE_INDEX != secondary_idx:
                    print(f"[Override] Using secondary device: {SECONDARY_DEVICE_NAME}")
                    INPUT_DEVICE_INDEX = secondary_idx
                    sd.default.device = INPUT_DEVICE_INDEX
            else:
                print(f"[Override] Secondary device not found: {SECONDARY_DEVICE_NAME}")
        elif PRIMARY_DEVICE_NAME:
            # Auto-recovery: Always try to see if the primary device has returned before starting
            primary_idx = find_device_index(PRIMARY_DEVICE_NAME)
            if primary_idx is not None:
                if INPUT_DEVICE_INDEX != primary_idx:
                    print(f"Switching to primary device: {PRIMARY_DEVICE_NAME}")
                    INPUT_DEVICE_INDEX = primary_idx
                    sd.default.device = INPUT_DEVICE_INDEX
            elif SECONDARY_DEVICE_NAME:
                # If primary is gone, ensure we at least use the secondary if it's available
                secondary_idx = find_device_index(SECONDARY_DEVICE_NAME)
                if secondary_idx is not None and INPUT_DEVICE_INDEX != secondary_idx:
                    print(f"Using secondary device: {SECONDARY_DEVICE_NAME}")
                    INPUT_DEVICE_INDEX = secondary_idx
                    sd.default.device = INPUT_DEVICE_INDEX

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

        try:
            # Suppress ALSA/PortAudio errors at OS level
            with silence_stderr():
                with sd.InputStream(samplerate=rate, channels=CHANNELS, callback=callback, device=device_idx):
                    while not stop_recording.is_set():
                        try:
                            # Use a shorter timeout for better responsiveness to the stop event
                            frame = q.get(timeout=0.05)
                            if rate != 16000 and scipy is not None:
                                frame_flat = frame.flatten().astype(np.float32)
                                frame_processed = scipy.signal.resample_poly(frame_flat, 16000, rate).astype(np.float32)
                            else:
                                frame_processed = frame

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
                                frame_processed = frame

                            frames.append(frame_processed)
                            if stream_callback:
                                stream_callback(frame_processed)
                        except queue.Empty:
                            break
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
                sd.default.device = INPUT_DEVICE_INDEX
                print("Fallback successful!")
        else:
            print("No valid secondary device found or secondary device is the same as failed device.")
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

    return np.concatenate(frames, axis=0) if frames else np.array([])



def countdown_timer():
    """Display countdown timer"""
    for i in range(RECORD_SECONDS, 0, -1):
        if stop_recording.is_set(): break
        print(f'Recording: {i}s... (press space to stop)', end='\r')
        time.sleep(1)

def check_for_stop_key():
    """Check for space key"""
    import select
    try:
        import termios, tty
        old_settings = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin.fileno())
        while not stop_recording.is_set():
            if select.select([sys.stdin], [], [], 0) == ([sys.stdin], [], []):
                c = sys.stdin.read(1)
                if c == ' ':
                    stop_recording.set()
                    break
            time.sleep(0.1)
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
    
    # Transcribe directly from numpy array
    try:
        # sounddevice returns data in float32, mono recording should be flattened to 1D
        if hasattr(audio_data, "flatten"):
            audio_data = audio_data.flatten()
            
        result = transcribe_audio(audio_data=audio_data, sample_rate=ACTUAL_RATE, device=DEVICE)
    except Exception as e:
        print(f"Processing error: {e}")
        result = ""
    finally:
        # Explicitly free audio data memory
        if audio_data is not None and hasattr(audio_data, 'reshape'):
            try:
                audio_data = audio_data.reshape(0)
            except:
                pass
        del audio_data
        
    transcribe_end_time = time.time()
    
    return result, transcribe_end_time - transcribe_start_time

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
                pyperclip.copy(transcription)
                copy_success = True
                print("Transcription copied to clipboard")
                break
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
            elif ch.lower() == 'i':
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
