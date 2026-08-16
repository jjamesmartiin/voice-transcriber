# Voice Transcriber (VT) - NixOS on WSL Guide

This document explains how Voice Transcriber runs inside **NixOS on WSL2**, how dependencies are managed reproducibly with **Nix Flakes**, and how **Windows microphone audio passthrough** works.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                       WINDOWS HOST                          │
│                                                             │
│  [Physical Microphone]  <──────────────┐                   │
│          │                             │                    │
│          ▼                             │                    │
│  Windows CoreAudio Engine              │                    │
│          │                             │                    │
│          ▼                             │                    │
│  WSLg PulseAudio Server                │                    │
│  (unix:/mnt/wslg/runtime-dir/pulse/native)                  │
└──────────┼─────────────────────────────┼────────────────────┘
           │ (UNIX Socket Passthrough)   │ (Hardware acceleration)
┌──────────▼─────────────────────────────▼────────────────────┐
│                      NIXOS (WSL2)                           │
│                                                             │
│  PortAudio / Sounddevice (Python)                           │
│          │                                                  │
│          ▼                                                  │
│  Audio Recorder (src/t2.py)                                 │
│          │                                                  │
│          ▼                                                  │
│  Transcription Engine (Faster-Whisper / Cohere)             │
│  Managed entirely by Nix Flake (flake.nix)                  │
└─────────────────────────────────────────────────────────────┘
```

---

## 1. Prerequisites & Installation

### Step 1: Enable Windows Virtualization (One-Time Admin Step)
If the Windows Virtual Machine Platform feature is not yet active:
1. Open PowerShell as **Administrator** (Right click PowerShell -> "Run as administrator").
2. Run:
   ```powershell
   dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
   dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
   ```
   *(If prompted, reboot your PC to finalize Windows hypervisor features).*

### Step 2: Register NixOS-WSL
The NixOS-WSL image (`nixos.wsl`) is stored at `C:\Users\jjame\WSL\nixos.wsl`.

To register NixOS in WSL2:
```powershell
wsl --install --from-file C:\Users\jjame\WSL\nixos.wsl
```
*(Or run `.\setup_wsl.ps1` to perform the complete setup automatically).*

---

## 2. Microphone Passthrough Explained

WSL2 includes **WSLg**, which runs a built-in PulseAudio audio server connected directly to Windows' audio devices.

### How the code connects:
1. When running inside NixOS WSL, the socket is located at:
   ```
   /mnt/wslg/runtime-dir/pulse/native
   ```
2. Setting `PULSE_SERVER=unix:/mnt/wslg/runtime-dir/pulse/native` redirects all Linux audio recording (`sounddevice`, `PortAudio`, `parec`, `arecord`) to your Windows default recording device.
3. In this `wsl` branch, [`src/t2.py`](file:///C:/Users/jjame/gitprojects/vt2/src/t2.py) and [`src/check_devices.py`](file:///C:/Users/jjame/gitprojects/vt2/src/check_devices.py) automatically detect the WSLg socket and set `PULSE_SERVER` automatically!

### Windows Microphone Permissions Checklist:
Ensure Windows privacy settings allow WSL to access your microphone:
- Open **Windows Settings** -> **Privacy & Security** -> **Microphone**
- Ensure **"Microphone access"** is **ON**
- Ensure **"Let desktop apps access your microphone"** is **ON**

---

## 3. Running the App

### Test Audio Devices inside NixOS WSL
To check detected input devices:
```powershell
.\run_wsl.ps1 check-audio
```

### Run Voice Transcriber via Nix Flake
To run the full application with all dependencies handled by Nix:
```powershell
.\run_wsl.ps1
```

### Run Test Suite
```powershell
.\run_wsl.ps1 test
```
