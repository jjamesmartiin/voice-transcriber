# Voice Transcriber

A voice transcription tool with global hotkeys for Windows and Linux.

## Quick Start

### Windows
```
# Clone the repository
git clone git@github.com:jjamesmartiin/voice-transcriber.git
cd voice-transcriber

# Run
.\run.ps1
```

### Linux
See the [main](https://github.com/jjamesmartiin/voice-transcriber/tree/main) branch.


## Usage

### Controls
- **Alt+Shift** (hold) - Start recording, release to transcribe
- **Ctrl+Alt+I** - Open settings menu

### Settings Menu
- P/S - Set primary/secondary audio device
- M - Toggle mute
- B - Switch model (whisper/cohere)
- T - Toggle auto-type to screen
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