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

### Run Performance Latency Benchmark
To measure inference speed, cold load time, and Real-Time Factor (RTF):
```powershell
# Via Nix Flake
wsl -d NixOS -- bash -c "cd $(wslpath -u (Get-Location)) && nix run .#benchmark"
```

---

## 4. Performance & Latency Benchmark Results

Tested on NixOS WSL2 with sample audio (3.80s speech, 16000Hz 1ch PCM):

| Transcription Engine | Model Size | Cold Load | Avg Warm Latency | Min / Max Latency | Real-Time Factor (RTF) | Throughput / Speedup | Accuracy Score |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Faster Whisper** | `small` (offline) | 0.609 s | **240.2 ms** | 236 ms / 245 ms | **0.063x** | **15.8x faster** than real-time | **100% PASS** |
| **Cohere Transcribe** | `03-2026` | 0.000 s | **392.3 ms** | 385 ms / 402 ms | **0.103x** | **9.7x faster** than real-time | **100% PASS** |

### Benchmark Highlights:
- **Instant Response**: Warm latency is only **~240ms** for Whisper and **~392ms** for Cohere, producing near-instantaneous transcription after releasing the hotkey.
- **Ultra-low RTF (0.063x)**: The pipeline transcribes almost **16 seconds of speech per second**.
- **Microphone Passthrough**: Captured through WSLg PulseAudio UNIX socket (`RDPSource` 16000Hz PCM) with negligible latency overhead.

---

## 5. Hybrid Host-Guest Mode (Windows Hotkeys + NixOS Backend)

To use Windows system-wide global hotkeys (`Alt+Shift`) with automatic paste into active Windows applications while running the transcription backend in NixOS WSL:

```powershell
.\run_wsl_bridge.ps1
```

1. Starts the lightweight NixOS WSL transcription daemon in the background (`src/wsl_bridge.py`).
2. Starts the Windows global keyboard listener (`src/wsl_bridge_host.py`).
3. Press and hold `Alt+Shift` anywhere in Windows to speak.
4. Release `Alt+Shift` to transcribe; text is automatically copied to the Windows clipboard and pasted into your active application.

