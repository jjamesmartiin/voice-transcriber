# Voice Transcriber - macOS Guide

Voice Transcriber runs natively on macOS (both Apple Silicon M-series and Intel Macs) with global hotkeys, CoreAudio recording, PyTorch MPS hardware acceleration, and active-window text injection.

---

## Quick Start

### 1. Prerequisites
Install [Homebrew](https://brew.sh) (if not already installed) and install PortAudio and Python 3.10+:

```bash
brew install portaudio python@3.12
```

### 2. Setup
From the repository root, run the setup script (it creates `.venv`, installs
dependencies, and acquires the model):

```bash
./setup.sh
```

To skip the model for now (and place weights in `models/` yourself):
```bash
./setup.sh --no-model
```

### 3. Permissions (Important)
macOS security controls require granting specific permissions in **System Settings**:

1. **Accessibility** (`System Settings` -> `Privacy & Security` -> `Accessibility`):
   - Add and enable your terminal application (e.g. **Terminal.app**, **iTerm2**, **Alacritty**, **Kitty**, **Ghostty**, or **VS Code**).
   - This permission is required for global hotkey listeners (`pynput`) and synthetic keystroke injection via AppleScript.

2. **Microphone** (`System Settings` -> `Privacy & Security` -> `Microphone`):
   - When Voice Transcriber is launched for the first time, macOS will prompt to allow microphone access for your terminal app. Click **Allow**.

3. **Input Monitoring** (if prompted by macOS):
   - Allow input monitoring for your terminal app in `System Settings` -> `Privacy & Security` -> `Input Monitoring`.

---

## Running Voice Transcriber

Launch the application:

```bash
./run.sh
```

### Controlling a Running Instance
Voice Transcriber can be queried and controlled programmatically from another terminal:

```bash
./run.sh status              # Report engine state and current settings
./run.sh toggle              # Toggle recording on/off
./run.sh start               # Start recording
./run.sh stop                # Stop recording and transcribe
./run.sh output clipboard    # Switch delivery mode to clipboard
./run.sh output type         # Switch delivery mode to auto-type
./run.sh help --json         # Machine-readable verb catalogue
```

---

## Hotkeys & Controls

- **`Option+Shift`** (or **`Cmd+Shift`**) (hold): Push-to-Talk — records while held; transcribes and pastes/types on release.
- **`Space`** (tap while holding `Option+Shift`): Hands-free latch mode. Release the keys and speak freely; tap `Option+Shift` again to stop and transcribe.
- **Middle Mouse Button** (hold ~0.25s): Mouse Push-to-Talk (when using an external mouse).
- **`Ctrl`** (held at release): Force output to clipboard even when auto-typing is active.

### Terminal Interactive Controls
- `Space` / `Enter`: Start / stop recording (tap once to start, tap again to finish).
- `s`, `S`, or `,`: Open interactive settings modal (switch microphone, audio cues, theme, punctuation presets).
- `r`: Reset terminal state and clipboard bridge.
- `q`, `Esc`, or `Ctrl+C`: Quit application.

---

## Apple Silicon Hardware Acceleration (MPS)

On Apple Silicon (M1/M2/M3/M4), PyTorch automatically uses the **Metal Performance Shaders (MPS)** backend for fast, on-device neural network inference with low power consumption.

---

## Troubleshooting

- **Microphone not capturing audio**:
  - Verify that PortAudio is installed: `brew install portaudio`
  - Ensure microphone permission is enabled for your terminal app in `System Settings -> Privacy & Security -> Microphone`.
  - Check available microphones with: `./run.sh mics`
  - Switch active microphone with: `./run.sh set-mic "<device-name>"`

- **Global hotkeys not responding**:
  - Ensure your terminal emulator is checked under `System Settings -> Privacy & Security -> Accessibility`.
  - If permissions were recently updated, restart your terminal app.

- **Auto-type text injection not working**:
  - Ensure Accessibility permissions are granted so AppleScript System Events / pynput can send keystrokes to active windows.
  - As a fallback, switch to clipboard delivery: `./run.sh output clipboard` (which automatically places transcriptions on the macOS clipboard via `pbcopy`).
