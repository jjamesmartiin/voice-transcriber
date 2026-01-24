# VT (Voice Transcriber)

A robust, modular voice transcription tool for Linux (Wayland/X11).

## Installation & Usage (Nix)

This project uses Nix Flakes for reproducible environments.

### Running Immediately
```bash
# add your user to the input group 
# then run this: 
nix run .

# or just run as root (bad practice)
sudo nix run .
```


## Features
- **Global Hotkeys**: Hold `Alt+Shift` to record, release to transcribe.
- **Visual Feedback**: visual overlays and terminal notifications.
- **Low Latency**: Optimized for quick transcription.
- **Privacy-focused**: Runs locally using Faster Whisper.

## Project Structure
- `src/`: Source code
  - `main.py`: Entry point and orchestration.
  - `notifications.py`: Visual notification system.
  - `hotkeys.py`: Global hotkey handling.
  - `t2.py` / `transcribe2.py`: Audio recording and transcription logic.
- `tests/`: Feature parity tests.

## Requirements
- Linux (Wayland or X11)
- Nix package manager
- User must be in `input` group for global hotkeys (or run as root).

### Development Environment
To enter a shell with all dependencies (including Python environment):
```bash
nix develop
```

Then inside the shell:
- Run app: `python src/main.py`

### Testing
- Run all tests: `python -m pytest tests/` (or `nix run .#test`)
- Run specific tests or pass arguments to pytest:
```bash
# Filter tests by keyword
nix run .#test -- -k transcription

# Run with verbose output
nix run .#test -- -v
```
