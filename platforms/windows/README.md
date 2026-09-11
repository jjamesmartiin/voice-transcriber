# Voice Transcriber - Native Windows Guide

Run Voice Transcriber natively on Windows with global hotkeys, Windows audio cues, and active-window clipboard pasting.

---

## Quick Start

### 1. Prerequisites
- Python 3.10+ installed on Windows (with "Add Python to PATH" enabled).
- PowerShell 5.1+ or PowerShell 7+.

### 2. Setup Virtual Environment
In PowerShell from the repository root:
```powershell
# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install Windows dependencies
pip install -r platforms\windows\requirements.txt
```

### 3. Launch the Application
```powershell
# Using the Windows launcher:
.\platforms\windows\run.ps1

# Or run directly with Python:
$env:PYTHONPATH = "src"
python src\main.py
```

---

## Controls

- **Alt + Shift (hold)**: Push-to-Talk — hold while speaking, release to transcribe and paste to active window.
- **Space (tap while holding Alt + Shift)**: Hands-free recording — release keys and keep talking; tap `Alt + Shift` when finished to transcribe.
- **Ctrl + Alt + I**: Open in-terminal interactive settings menu (change audio devices, models, UI theme, formatting level).

---

## How it Works on Native Windows

- **Hardware/OS Abstraction Layer (HAL)**: Auto-detects Windows host and loads `src/platform/windows/`.
- **Global Hotkeys**: Uses `pynput` and `keyboard` libraries to capture system-wide keystrokes even when Voice Transcriber is minimized or in the background.
- **Audio Capture**: Captures 16kHz audio from your default Windows microphone via `sounddevice` (WASAPI/DirectSound).
- **Audio Feedback**: Plays native Windows chimes (e.g. `Windows Proximity Notification.wav` or `Speech On/Off.wav`) via `winsound`.
- **Active-Window Paste**: Copies transcription to Windows clipboard via `pyperclip` and injects `Ctrl+V` into your currently focused window via Win32 `keybd_event`.
- **AI Processing**: Runs the full Whisper or Cohere model with streaming VAD, dynamic energy gating, and post-processing (Trie dictionary, formatting, number conversion).

---

## Automated Testing on Windows

Before doing live dictation, verify the full cross-platform test suite on Windows:
```powershell
python -m pytest tests/test_end_to_end_crossplatform.py -v
python -m pytest tests/test_platform_hal.py -v
```

---

## Building a Standalone Offline EXE

To package Voice Transcriber into a self-contained `.exe`:
```powershell
python build_offline.py
```
This produces an offline distribution in `dist/` that requires no Python installation on the target Windows machine.
