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
│  Transcription Engine (Cohere Transcribe)                   │
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

### Step 2: Register NixOS-WSL (If not already installed)
To register NixOS in WSL2:
```powershell
# From the repository root:
powershell -ExecutionPolicy Bypass -File platforms\wsl\setup_wsl.ps1
```

---

## 2. Microphone Passthrough Explained

WSL2 includes **WSLg**, which runs a built-in PulseAudio audio server connected directly to Windows' audio devices.

### How the code connects:
1. When running inside NixOS WSL, the socket is located at:
   ```
   /mnt/wslg/runtime-dir/pulse/native
   ```
2. Setting `PULSE_SERVER=unix:/mnt/wslg/runtime-dir/pulse/native` redirects all Linux audio recording (`sounddevice`, `PortAudio`, `parec`, `arecord`) to your Windows default recording device.
3. The Hardware/OS Abstraction Layer (HAL) automatically detects the WSLg socket and sets `PULSE_SERVER` accordingly.

### Windows Microphone Permissions Checklist:
Ensure Windows privacy settings allow WSL to access your microphone:
- Open **Windows Settings** -> **Privacy & Security** -> **Microphone**
- Ensure **"Microphone access"** is **ON**
- Ensure **"Let desktop apps access your microphone"** is **ON**

---

## 3. Running the App

### Option A: Launch from Windows (Recommended)
From Windows PowerShell in the repository root:
```powershell
powershell -ExecutionPolicy Bypass -File platforms\wsl\run_wsl.ps1
```

### Option B: Launch from inside WSL
Inside your NixOS WSL terminal:
```bash
nix run .
```

> **How it works:**
> 1. Nix provides PyTorch, PortAudio, Cohere Transcribe, and all Python dependencies in an isolated sandbox.
> 2. The app detects WSL and automatically connects to your Windows microphone via WSLg PulseAudio (`RDPSource`).
> 3. It automatically connects a lightweight background bridge to Windows so you can press and hold **`Alt+Shift`** anywhere in Windows (Chrome, VS Code, Discord, etc.) to speak.
> 4. When you release **`Alt+Shift`**, it transcribes in **~230ms** and pastes the text directly at your cursor in Windows.

---

### Run Test Suite
```bash
nix run .#test
```

### Run Performance Latency Benchmark
```bash
nix run .#benchmark
```

---

## 4. Performance & Latency Benchmark Results

Tested on NixOS WSL2 with sample audio (3.80s speech, 16000Hz 1ch PCM):

| Transcription Engine | Model Size | Cold Load | Avg Warm Latency | Min / Max Latency | Real-Time Factor (RTF) | Throughput / Speedup | Accuracy Score |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Faster Whisper** | `small` (offline) | 0.58 s | **231.8 ms** | 227 ms / 236 ms | **0.061x** | **16.4x faster** than real-time | **100% PASS** |
| **Cohere Transcribe** | `03-2026` | 0.90 s | **377.3 ms** | 374 ms / 380 ms | **0.099x** | **10.1x faster** than real-time | **100% PASS** |

### Benchmark Highlights:
- **Instant Response**: Warm latency is only **~231ms** for Whisper and **~377ms** for Cohere, producing near-instantaneous transcription after releasing the hotkey.
- **Ultra-low RTF (0.061x)**: The pipeline transcribes over **16 seconds of speech per second**.
- **Microphone Passthrough**: Captured through WSLg PulseAudio UNIX socket (`RDPSource` 16000Hz PCM) with zero perceived latency.
