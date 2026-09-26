# Voice Transcriber - Native Windows Guide

Run Voice Transcriber natively on Windows with global hotkeys, Windows audio cues, and active-window clipboard pasting.

---

## Quick Start

### 1. Prerequisites
- Python 3.10+ installed on Windows (from https://www.python.org/downloads/ with "Add python.exe to PATH" checked).
- PowerShell 5.1+ or PowerShell 7+ (or Command Prompt).

### 2. One-Click Setup & Launch
From the repository root, you can simply run the batch launchers:
```cmd
# Run setup (creates venv and installs dependencies):
setup.bat

# Launch Voice Transcriber:
run.bat
```

### 3. Setup via PowerShell
Or if you prefer PowerShell:
```powershell
# Run the automated setup script:
.\platforms\windows\setup.ps1

# Launch the application:
.\platforms\windows\run.ps1
```

### 4. Manual Setup (Alternative)
If setting up manually from the repository root:
```powershell
# Create virtual environment
python -m venv .venv

# Install dependencies (using python -m pip ensures pip runs even if not activated)
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r platforms\windows\requirements.txt

# Launch:
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe src\main.py
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

From the repo root, one command per tier:
```powershell
.\test.ps1              # shared + Windows tiers (all)
.\test.ps1 shared       # cross-platform, model-free
.\test.ps1 windows      # Windows-specific
.\test.ps1 e2e          # model/audio end-to-end (local only)
```

The launcher runs the same shared + Windows tiers:
```powershell
.\platforms\windows\run.ps1 test
```

> The shared/platform tiers are PyTorch-free, so they run on a bare Python
> install. The end-to-end tier (`tests/e2e/`) needs the model weights and is run
> separately with `.\test.ps1 e2e`.

---

## Building a Standalone Offline EXE

To package Voice Transcriber into a self-contained `.exe`:
```powershell
python build_offline.py
```
This produces an offline distribution in `dist/` that requires no Python installation on the target Windows machine.
