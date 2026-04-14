# VT (Voice Transcriber)

A robust, modular voice transcription tool for Linux (Wayland/X11) and Windows.

---

## Quick Start

### Windows
```powershell
# Run the app (auto-installs dependencies on first run)
.\run.ps1
```

### Linux (Nix)
```bash
nix run github:jjamesmartiin/voice-transcriber
```

---

## AI Context (For Code Navigation)

### Entry Points
| Platform | File | Description |
|-----------|------|-------------|
| Windows | `run.ps1` | PowerShell launcher - run this to start on Windows |
| Windows | `src/main_windows.py` | Windows-specific entry point |
| Linux | `src/main.py` | Main application loop |
| CLI | `src/t2.py` | Standalone recording/transcription script |

### Architecture
```
main.py / main_windows.py
    ├── hotkeys.py / hotkeys_windows.py   # Global hotkey handling (Alt+Shift)
    ├── t2.py                              # Audio recording logic
    │       └── transcribe2.py             # Model dispatcher
    │               ├── transcribe_whisper.py   # Faster-Whisper (offline)
    │               └── transcribe_cohere.py   # Cohere API (requires token)
    └── notifications.py / notifications_windows.py  # Visual overlay
```

### Key Variables
- `VT_MODEL_BACKEND` - Set to "whisper" (default/offline) or "cohere" (requires HF_TOKEN)
- `INPUT_DEVICE_INDEX` - Audio input device (auto-detected, configurable via Ctrl+Alt+I)

### Hotkeys
- **Alt+Shift** (hold) - Start recording, (release) - Stop and transcribe
- **Ctrl+Alt+I** - Open settings menu (device selection)

---

## Installation & Usage (Nix)

This project uses Nix Flakes for reproducible environments.

### Running Immediately
```bash
# add your user to the input group 
# then run this: 
nix run github:jjamesmartiin/voice-transcriber

# or just run as root (bad practice)
sudo nix run github:jjamesmartiin/voice-transcriber
```

### Hugging Face API Token (for first run)
To download the necessary models from Hugging Face, you need to provide your API token. Create a file named `HF_TOKEN` in the project root directory and paste your token into it. This file is in `.gitignore` and will not be committed. This is only required for the initial download.

**TODO**: Remove this requirement once the model is fully public and does not require access-based authentication.

## Features
- **Global Hotkeys**: Hold `Alt+Shift` to record, release to transcribe & copy to clipboard.
- **Multi-Model Support**: Switch between **Cohere Transcribe** (high quality) and **Faster Whisper** (fast/offline).
- **Visual Feedback**: visual overlays and terminal notifications.
- **Low Latency**: Optimized for quick transcription.
- **Privacy-focused**: Runs locally.

## Model Configuration
The model backend is set via the `VT_MODEL_BACKEND` environment variable:
- `"whisper"` (default) - Faster Whisper, works offline
- `"cohere"` - Cohere Transcribe, requires HF_TOKEN file

Set before running:
```powershell
$env:VT_MODEL_BACKEND = "cohere"
.\run.ps1
```

### Model Details
- **Cohere Transcribe**: Uses `CohereLabs/cohere-transcribe-03-2026`. Requires a Hugging Face token and access to the gated model.
- **Faster Whisper**: Uses the Whisper `small` model. Fully local and does not require authentication.


## Codebase Context 

This section provides a high-level overview of the project's architecture and technology stack.

### Purpose
Low-latency, privacy-focused voice transcription for Linux (Wayland/X11) and Windows desktops.

### Tech Stack
- **Core**: Python 3.x
- **Transcription Engines**:
  - **Cohere**: `CohereLabs/cohere-transcribe-03-2026` via `transformers` (higher quality, requires gating)
  - **Faster Whisper**: `faster-whisper` (fast, fully local/offline)
- **Global Hotkeys**: 
  - Linux: `evdev` + `uinput`
  - Windows: `pynput`
- **Audio Engine**: `sounddevice` / `PortAudio`
- **Visuals**: 
  - Linux: `Tkinter` or `zenity`
  - Windows: `Tkinter` overlay + `winsound`
- **Packaging**: Nix Flakes (Linux), PowerShell (Windows)

### File Structure
```
src/
├── main.py             # Linux main application
├── main_windows.py    # Windows entry point
├── t2.py              # Audio recording & transcription logic
├── transcribe2.py      # Model dispatcher (whisper/cohere)
├── transcribe_whisper.py  # Whisper transcription
├── transcribe_cohere.py   # Cohere transcription
├── hotkeys.py         # Linux global hotkeys (evdev)
├── hotkeys_windows.py # Windows global hotkeys (pynput)
├── notifications.py   # Linux notifications
├── notifications_windows.py # Windows overlay notifications
└── sounds/            # Audio feedback files
```

### Configuration
- Audio device config: `~/.local/share/vt/audio_device_config.json` (Linux) or `%APPDATA%/vt/` (Windows)
- Model backend: Set `VT_MODEL_BACKEND` env var before running

## Contributing

We welcome contributions from everyone and of any type! Whether you're fixing a bug, adding a feature, improving documentation, or just sharing an idea, your help is appreciated.

### How to Contribute
1. **Fork** the repository.
2. **Create a branch** for your feature or fix.
3. **Make your changes**.
4. **Run tests** to ensure everything is working correctly (`nix run .#test`).
5. **Submit a Pull Request** with a clear description of what you've done.

We value all types of contributions, including:
- **Code**: Bug fixes, new features, or performance improvements.
- **Documentation**: Fixing typos, improving clarity, or adding examples.
- **Feedback**: Reporting bugs or suggesting new features via Issues.
- **Design**: Improving visual notifications or UI elements.

## Requirements

### Windows
- Windows 10+
- Python 3.10+
- Run `.\run.ps1` (auto-installs dependencies)

### Linux
- Linux (Wayland or X11)
- Nix package manager
- User must be in `input` group for global hotkeys (or run as root).

## Windows Setup

```powershell
# Clone and run
.\run.ps1

# First run: creates .venv, installs dependencies, downloads Whisper model (~75MB)
# Subsequent runs: just starts the app

# Hotkeys
Alt+Shift     # Hold to record, release to transcribe
Ctrl+Alt+I    # Settings (device selection)

# The app uses Whisper by default (offline, no token needed)
# For Cohere model: create HF_TOKEN file with your HuggingFace token
```

### Building .exe

Build `.exe` that bundles dependencies:

1. **Install dependencies** (if not using `run.ps1`):
   ```powershell
   pip install -r requirements.txt
   ```

2. **Build the executable**:
   ```powershell
   python build_windows.py
   ```

   Or manually with PyInstaller:
   ```powershell
   python -m PyInstaller voice_transcriber.spec --clean --noconfirm
   ```

3. **Output**: The executable is at `dist\VoiceTranscriber.exe`

4. **Run**:
   ```powershell
   .\dist\VoiceTranscriber.exe
   ```

**Notes:**
- The first run downloads the Whisper model (~75MB) to `~/.cache/whisper/`.
- For the Cohere model, place an `HF_TOKEN` file in the same directory as the executable.
- The build includes `torch` and `transformers`, so the final .exe will be large (~1-2GB).

## Troubleshooting

### Clipboard Issues (Wayland)
If `wl-clipboard` fails to copy text or seems stuck:
- The app now includes a retry mechanism (3 attempts).
- You can manually reset clipboard processes by pressing `r` in the terminal menu after a recording (if prompted) to reset both the terminal and clipboard.
- Alternatively, run:
  ```bash
  pkill wl-copy
  pkill wl-paste
  ```
- Ensure `wl-clipboard` is installed (it is included in the Nix flake).

### Hotkey Issues
- Ensure you have permissions to `/dev/input/`. Add your user to the `input` group:
  ```bash
  sudo usermod -a -G input $USER
  ```
- Then reboot or log out and back in.

### Development Environment
To enter a shell with all dependencies (including Python environment):
```bash
nix develop
```

Then inside the shell:
- Run app: `python src/main.py`

### Testing

Run the full test suite:
```bash
python -m pytest tests/ -v
nix run .#test
.\run.ps1 test
```

```bash
python -m pytest tests/test_transcribe_whisper.py -v
python -m pytest tests/test_transcribe_cohere.py -v
python -m pytest tests/test_transcribe2.py -v
python -m pytest tests/test_hotkeys.py -v
python -m pytest tests/test_hotkeys_windows.py -v
python -m pytest tests/test_notifications.py -v
python -m pytest tests/test_notifications_windows.py -v
```
