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

- **Alt + Shift (hold)**: Push-to-Talk — hold while speaking, release to transcribe and paste/type to the active window.
- **Space (tap while holding Alt + Shift)**: Hands-free recording — release keys and keep talking; tap `Alt + Shift` when finished to transcribe.
- **Middle-click (hold ~0.25 s)**: Mouse Push-to-Talk. A quick click passes through and is ignored.
- **Ctrl (held at release)**: Force clipboard output for this utterance even when auto-type is enabled.
- **Ctrl + Alt + I** (or `S`, `,`, `i` in the terminal): Open the interactive Settings modal (`⚙️ Settings & Configuration`) with real-time fuzzy filter, in-place toggle badges, and sub-pickers.
- **M** (in the terminal): Open the interactive Microphone device picker (`🎤 Microphone Input Device`).
- **t** (in the terminal): Open the interactive UI Color Theme picker (`🎨 Select UI Color Theme`).

---

## How it Works on Native Windows

- **Hardware/OS Abstraction Layer (HAL)**: Auto-detects Windows host and loads `src/platform/windows/`.
- **Global Hotkeys**: Uses `pynput` and `keyboard` libraries to capture system-wide keystrokes even when Voice Transcriber is minimized or in the background.
- **Audio Capture**: Captures 16kHz audio from your default Windows microphone via `sounddevice` (WASAPI/DirectSound).
- **Audio Feedback**: Plays native Windows chimes (e.g. `Windows Proximity Notification.wav` or `Speech On/Off.wav`) via `winsound`.
- **Active-Window Paste**: Copies transcription to Windows clipboard via `pyperclip` and injects keystrokes into your currently focused window via Win32 `SendInput` with full Unicode and emoji fidelity.
- **AI Processing**: Runs the high-accuracy Cohere Transcribe model with streaming VAD, dynamic energy gating, and post-processing (Trie dictionary, formatting, number conversion).

---

## Automated Testing on Windows

Before live dictation, verify the hardware-independent suite. The launcher
creates the venv, installs `requirements.txt`, and runs pytest. From the repo
root:
```powershell
# Full suite (platform HAL, dictionary, config, post-processor, TUI, workflows, WSL):
.\platforms\windows\run.ps1 test

# Targeted run — extra arguments are forwarded to pytest:
.\platforms\windows\run.ps1 test tests\test_platform_hal.py -v
```

Or directly from `platforms\windows\`, with the venv active and `PYTHONPATH=src`:
```powershell
.\run.ps1 test
```

Or directly, with the venv active and `PYTHONPATH=src`:
```powershell
python -m pytest tests/test_platform_hal.py tests/test_dictionary.py `
  tests/test_config_sync.py tests/test_post_processor.py tests/test_tui.py `
  tests/test_user_workflows.py tests/test_wsl.py -v
```

> These tests are PyTorch-free, so they run on a bare Python install. The
> acoustic end-to-end suite (`tests/test_end_to_end_crossplatform.py`) needs the
> model weights loaded and is run separately:
> `python -m pytest tests/test_end_to_end_crossplatform.py -v`

---

## Building a Standalone Offline EXE

To package Voice Transcriber into a self-contained `.exe`:
```powershell
python build_offline.py
```
This produces an offline distribution in `dist/` that requires no Python installation on the target Windows machine.
