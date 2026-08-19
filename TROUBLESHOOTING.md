# Troubleshooting Guide

This guide covers common issues and resolutions for **Voice Transcriber (VT)** on Windows (via WSL) and Linux (Wayland/X11).

---

## Table of Contents
1. [WSL / Windows Issues](#1-wsl--windows-issues)
   - [Microphone Not Capturing / System Tray Mic Missing / Pure Silence](#microphone-not-capturing--system-tray-mic-missing--pure-silence)
   - [Ghost Listeners or Stale Hotkey Processes](#ghost-listeners-or-stale-hotkey-processes)
   - [Windows Clipboard Not Updating](#windows-clipboard-not-updating)
2. [Linux / Wayland Issues](#2-linux--wayland-issues)
   - [Hotkey Permission Denied (`/dev/input`)](#hotkey-permission-denied-devinput)
   - [Stuck or Unresponsive Clipboard (`wl-clipboard`)](#stuck-or-unresponsive-clipboard-wl-clipboard)
3. [Model & Transcription Issues](#3-model--transcription-issues)
   - [Hugging Face Authentication (`HF_TOKEN`)](#hugging-face-authentication-hf_token)
   - [Switching Between Cohere and Faster-Whisper](#switching-between-cohere-and-faster-whisper)
4. [Audio & Sound Themes](#4-audio--sound-themes)
   - [Customizing Audio Cues](#customizing-audio-cues)

---

## 1. WSL / Windows Issues

### Microphone Not Capturing / System Tray Mic Missing / Pure Silence
* **Symptom**: You hold `Alt+Shift`, sounds play, but the Windows taskbar does not show the microphone icon, or the terminal reports `"No speech detected"`.
* **Cause**: WSLg's RDP audio bridge occasionally unloads the microphone input source (`RDPSource`) after Windows sleep or device disconnects, defaulting to `RDPSink.monitor` (speaker silence).
* **Fix (5 seconds)**:
  1. Open Windows PowerShell or Command Prompt.
  2. Run:
     ```powershell
     wsl --shutdown
     ```
  3. Reopen your WSL terminal and run:
     ```bash
     ./run.sh
     ```
  4. Verify Windows Microphone Privacy: Go to **Windows Settings ➔ Privacy & Security ➔ Microphone** and ensure **"Let desktop apps access your microphone"** is enabled.

---

### Ghost Listeners or Stale Hotkey Processes
* **Symptom**: Hotkeys feel sluggish, or multiple chime sounds play simultaneously.
* **Cause**: Previous background PowerShell bridge processes remained active after abrupt terminal closures.
* **Fix**:
  The application automatically cleans up stale bridge processes on launch. You can also manually terminate them from Windows PowerShell:
  ```powershell
  Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*wsl_win_hotkeys.ps1*' } | Stop-Process -Force
  ```

---

### Windows Clipboard Not Updating
* **Symptom**: Speech transcribes in the terminal, but does not paste into Windows apps.
* **Cause**: Interop with `clip.exe` was blocked.
* **Fix**: Ensure WSL Interop is enabled in `/etc/wsl.conf`:
  ```ini
  [interop]
  enabled = true
  appendWindowsPath = true
  ```

---

## 2. Linux / Wayland Issues

### Hotkey Permission Denied (`/dev/input`)
* **Symptom**: Global hotkey does not register when pressing `Alt+Shift` on native Linux.
* **Cause**: The user lacks read permissions for raw input devices in `/dev/input/`.
* **Fix**:
  1. Add your user to the `input` group:
     ```bash
     sudo usermod -a -G input $USER
     ```
  2. Log out and log back in (or reboot).

---

### Stuck or Unresponsive Clipboard (`wl-clipboard`)
* **Symptom**: Transcription finishes, but the clipboard hangs or fails to paste on Wayland.
* **Fix**:
  Reset active clipboard helper processes in your terminal:
  ```bash
  pkill wl-copy
  pkill wl-paste
  ```
  Or press **`r`** in the interactive configuration menu (`Ctrl+Alt+I`).

---

## 3. Model & Transcription Issues

### Hugging Face Authentication (`HF_TOKEN`)
* **Symptom**: App errors on first startup with `Access to model CohereLabs/cohere-transcribe-03-2026 is gated`.
* **Fix**:
  1. Accept the model terms on Hugging Face: [Cohere Transcribe Model](https://huggingface.co/CohereLabs/cohere-transcribe-03-2026).
  2. Create a file named `HF_TOKEN` in the repository root directory containing your Hugging Face API token:
     ```bash
     echo "your_hf_token_here" > HF_TOKEN
     ```

---

### Switching Between Cohere and Faster-Whisper
* **Offline / Fast Mode**: If you prefer fully offline transcription without Hugging Face authentication, switch to Faster-Whisper:
  ```bash
  VT_MODEL_BACKEND=whisper ./run.sh
  ```
  Or press **`Ctrl+Alt+I`** (or **`i`** in terminal) and press **`B`** to toggle backends.

---

## 4. Audio & Sound Themes

### Customizing Audio Cues
You can customize or mute sound effects directly in `run.sh` or via environment variables:
```bash
# Available themes: proximity (default), speech, notify, navigation, classic, silent
VT_SOUND_THEME=speech ./run.sh

# Or run completely silent:
VT_SOUND_THEME=silent ./run.sh
```
