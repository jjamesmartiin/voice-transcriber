# Voice Transcriber - Native Windows Guide

Run Voice Transcriber natively on Windows with global hotkeys, Windows audio cues, and active-window clipboard pasting.

---

## Quick Start

### 1. Prerequisites
- Python 3.10+ installed on Windows (from https://www.python.org/downloads/ with "Add python.exe to PATH" checked).
- PowerShell 5.1+ or PowerShell 7+ (or Command Prompt).

### 2. One-Click Setup & Launch (Recommended)
From the repository root, you can simply run the batch launchers (by double-clicking them in File Explorer, or running from Command Prompt / PowerShell):
```cmd
# Run setup (creates venv and installs dependencies):
setup.bat

# Launch Voice Transcriber:
run.bat
```

> **Note:** The `.bat` launchers automatically bypass PowerShell's script execution policy (`-ExecutionPolicy Bypass`), so you will not run into digital signature or `PSSecurityException` errors on downloaded scripts.

### 3. Setup via PowerShell
If you prefer running via PowerShell directly, pass `-ExecutionPolicy Bypass` (since Windows blocks downloaded `.ps1` scripts by default):
```powershell
# Run the automated setup script:
powershell -ExecutionPolicy Bypass -File .\platforms\windows\setup.ps1

# Launch the application:
powershell -ExecutionPolicy Bypass -File .\platforms\windows\run.ps1
```

*(Tip: Alternatively, you can run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` once in your PowerShell session to allow running `.\platforms\windows\setup.ps1` and `.\platforms\windows\run.ps1` directly.)*

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
- **g** (in the terminal): Open the interactive Mode Preset switcher (`✨ Mode Preset Switcher`) for Default, Casual, Autocorrect, Aesthetic Lowercase, and Pure Gen Z modes.
- **M** (in the terminal): Open the interactive Microphone device picker (`🎤 Microphone Input Device`).
- **t** (in the terminal): Open the interactive UI Color Theme picker (`🎨 Select UI Color Theme`).

---

## How it Works on Native Windows

- **Hardware/OS Abstraction Layer (HAL)**: Auto-detects Windows host and loads `src/platform/windows/`.
- **Global Hotkeys**: Uses `pynput` and `keyboard` libraries to capture system-wide keystrokes even when Voice Transcriber is minimized or in the background.
- **Audio Capture**: Captures 16kHz audio from your default Windows microphone via `sounddevice` (WASAPI/DirectSound).
- **Instant Capture Start**: Keeps the WASAPI input stream constructed (stopped between recordings) so push-to-talk doesn't drop the first syllable while the device opens. Set `VT_WARM_MIC=0` to disable (opens the device fresh on each recording).
- **Audio Feedback**: Plays native Windows chimes (e.g. `Windows Proximity Notification.wav` or `Speech On/Off.wav`) via `winsound`.
- **Auto-Type with Per-App Fallback**: Injects real virtual-key keystrokes into the focused window (Unicode/emoji via `KEYEVENTF_UNICODE`). Fast mode auto-drops to slow pacing for classic controls (Notepad, RichEdit, `Edit`) that garble rapid injection — terminals and modern apps stay instant. Override per window class with `VT_TYPE_FAST_CLASSES` / `VT_TYPE_SLOW_CLASSES` (comma-separated).
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
```cmd
run.bat test
```
or via PowerShell:
```powershell
powershell -ExecutionPolicy Bypass -File .\platforms\windows\run.ps1 test
```

> The shared/platform tiers are PyTorch-free, so they run on a bare Python
> install. The end-to-end tier (`tests/e2e/`) needs the model weights and is run
> separately with `.\test.ps1 e2e`.

---

## Building a Standalone Offline EXE

To package Voice Transcriber into a self-contained `.exe`:
```cmd
run.bat build
```
or via PowerShell:
```powershell
powershell -ExecutionPolicy Bypass -File .\platforms\windows\run.ps1 build
```
or directly with Python:
```powershell
python platforms\windows\build_offline.py
```
This produces an offline distribution in `dist/` that requires no Python installation on the target Windows machine.

---

## Troubleshooting

### 1. Script Cannot Be Loaded (`PSSecurityException` / `UnauthorizedAccess`)
**Symptom:**
```text
setup.ps1 cannot be loaded. The file is not digitally signed. You cannot run this script on the current system.
```
**Cause:** Windows restricts running downloaded `.ps1` scripts by default ("Mark of the Web").  
**Fix:**
- Use the batch file: **`setup.bat`** (or **`run.bat`**). It automatically bypasses the execution policy for that run.
- Or pass `-ExecutionPolicy Bypass` in PowerShell:
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\platforms\windows\setup.ps1
  ```
- Or unblock scripts for your current PowerShell terminal session:
  ```powershell
  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
  ```

### 2. Path Too Long (`[WinError 206] The filename or extension is too long`)
**Symptom:**
```text
ERROR: Could not install packages due to an OSError: [WinError 206] The filename or extension is too long: '...\.venv\Lib\site-packages\...'
```
**Cause:** Windows enforces a legacy 260-character maximum path limit (`MAX_PATH`) by default. Downloading a GitHub zip extracts by default into deeply nested directories (e.g. `Downloads\voice-transcriber-<hash>\voice-transcriber-<hash>\`), which exceeds 260 characters when `pip` installs package dependencies.  
**Fix:**
- **Automated (Zero manual typing):** Run **`setup.bat`** and click **Yes** when Windows prompts for permission to enable long path support. `setup.bat` will enable `LongPathsEnabled` and resume setup automatically.
- **Alternative:** Move or rename the extracted project folder to a shorter path (e.g. `C:\voice-transcriber`), or enable long paths manually in an Administrator PowerShell window:
  ```powershell
  New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force
  ```
