# Voice Transcriber

A voice transcription tool with global hotkeys for Windows and Linux.

## Quick Start

### Windows
```powershell
.\run.ps1
```

### Linux
```bash
nix run github:jjamesmartiin/voice-transcriber
```

## Usage

### Controls
- **Alt+Shift** (hold) - Start recording, release to transcribe
- **Ctrl+Alt+I** - Open settings menu

### Settings Menu
- P/S - Set primary/secondary audio device
- M - Toggle mute
- B - Switch model (whisper/cohere)
- T - Toggle auto-type to screen
- p/s/a - Manual device override
- c - Save and exit

### Model Options
- **whisper** (default) - Faster Whisper, works offline
- **cohere** - Cohere Transcribe, higher quality

Set model:
```powershell
$env:VT_MODEL_BACKEND = "cohere"
.\run.ps1
```

## Configuration

- Config file: `%APPDATA%/vt/audio_device_config.json` (Windows) or `~/.local/share/vt/` (Linux)
- Cohere model: Requires `HF_TOKEN` file in project root

## Requirements

### Windows
- Windows 10+
- Python 3.10+

### Linux
- Linux (Wayland/X11)
- Nix package manager

## Testing
```powershell
# Windows
.\run.ps1 test

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
=======
# Linux
nix run .#test
```

## File Structure
```
src/
├── main.py              # Linux entry point
├── main_windows.py     # Windows entry point
├── t2.py               # Recording/transcription logic
├── transcribe2.py      # Model dispatcher
├── transcribe_whisper.py
├── transcribe_cohere.py
├── hotkeys.py          # Linux hotkeys (evdev)
├── hotkeys_windows.py # Windows hotkeys (pynput)
└── notifications*.py # Visual notifications
```