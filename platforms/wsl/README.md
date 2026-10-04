# Voice Transcriber (VT) - WSL Guide

> **Any WSL distribution works.** The recommended guest is **NixOS-WSL** (Nix
> provides every dependency reproducibly), but a plain **Ubuntu** guest is
> supported too: the guest runs its own `./setup.sh` / `./run.sh`, which uses
> the native apt + venv path. Pass `-Distro <name>` to `run_wsl.ps1`,
> `run_wsl_bridge.ps1` or `setup_wsl.ps1`; without it NixOS is auto-picked when
> registered. Only the Windows-host hotkey/clipboard bridge is distro-specific,
> and it is unchanged.

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
To register NixOS in WSL2, simply double-click or run:
```cmd
setup_wsl.bat
```
*(Or via PowerShell: `powershell -ExecutionPolicy Bypass -File platforms\wsl\setup_wsl.ps1`)*

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

### The settings microphone picker shows several identical levels
**Expected, not a bug** (and not yet confirmed on a real WSL host — see
`TODO.md`). The picker in the settings modal meters every visible device at once
by opening one capture stream per row. On WSL those streams all reach the same
place: WSLg publishes your Windows default input as a single PulseAudio source
(`RDPSource`), so `default`, `pipewire` and the `RDPSource`-backed entries
report the *same* level. The levels are correct — there is simply one input
behind several names. Only the *selection* matters on WSL: set the Windows
default microphone (or the source you want) and pick `default` or `pipewire`.
On Linux, where each `hw:` device is its own source, the rows usually differ.

---

## 3. Running the App

### Option A: Launch from Windows (1-Click)
Double-click or run from the repo root:
```cmd
run_wsl.bat
```
*(Or in PowerShell: `powershell -ExecutionPolicy Bypass -File platforms\wsl\run_wsl.ps1`)*

### Option B: Launch from inside WSL
Inside your NixOS WSL terminal:
```bash
nix run .
```

> **How it works:**
> 1. Nix provides PyTorch, PortAudio, Cohere Transcribe, and all Python dependencies in an isolated sandbox.
> 2. The app detects WSL and automatically connects to your Windows microphone via WSLg PulseAudio (`RDPSource`).
> 3. It automatically connects a lightweight background bridge to Windows so you can press and hold **`Alt+Shift`** anywhere in Windows (Chrome, VS Code, Discord, etc.) to speak.
> 4. When you release **`Alt+Shift`**, it transcribes in **~380ms** and pastes the text directly at your cursor in Windows.

### Controls

- `Alt+Shift` (hold): Push-to-Talk (captured by the Windows-host bridge).
- `Space` (tap while holding `Alt+Shift`): Hands-free latch mode.
- Middle-click (hold ~0.25 s): Mouse Push-to-Talk, forwarded to the host bridge.
- `Ctrl` (held at release): Force clipboard output instead of auto-type.
- `Space` or `Enter` (in the terminal): Start/stop recording. Tap to start, tap again to stop (hands-free).
- `s`, `S`, or `,` (in the terminal): Interactive settings menu — the only configuration entry point. The microphone, theme and mode-preset pickers live inside it.
- `r` (in the terminal): Reset the terminal and clipboard bridge.
- `q`, `Esc`, or `Ctrl+C` (in the terminal): Quit.

There is no global hotkey for settings; use the [Control API](../README.md#control-api)
to drive it from outside the terminal.

---

### Run Test Suite

From the repo root — `./test.sh` auto-detects WSL and runs `tests/shared` + `tests/wsl`:
```bash
./test.sh
```

Targeted tiers:
```bash
./test.sh shared     # cross-platform, model-free
./test.sh platform   # tests/wsl
./test.sh e2e        # model/audio end-to-end (local only)
```

### Run Synthetic End-to-End Benchmark
```bash
nix develop --command python tests/benchmark_synthetic_e2e.py
```

---

## 4. Performance & Latency Benchmark Results

Tested on NixOS WSL2 with sample audio (3.80s speech, 16000Hz 1ch PCM):

| Transcription Engine | Model Size | Cold Load | Avg Warm Latency | Min / Max Latency | Real-Time Factor (RTF) | Throughput / Speedup | Accuracy Score |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Cohere Transcribe** | `03-2026` | 0.90 s | **377.3 ms** | 374 ms / 380 ms | **0.099x** | **10.1x faster** than real-time | **100% PASS** |

### Benchmark Highlights:
- **Instant Response**: Warm latency is **~377 ms**, producing near-instantaneous transcription after releasing the hotkey.
- **Ultra-low RTF (0.099x)**: The pipeline transcribes over **10 seconds of speech per second**.
- **Microphone Passthrough**: Captured through the WSLg PulseAudio UNIX socket (`RDPSource` 16000Hz PCM) with zero perceived latency.

---

## 5. Troubleshooting

### 1. Microphone Not Capturing in WSL
**Symptom:** Voice Transcriber in WSL2 captures silence or cannot find an input audio device.  
**Causes & Fixes:**
- **Windows Host Privacy Settings:** On the Windows host, open **Settings → Privacy & security → Microphone**. Ensure **Microphone access** and **Let desktop apps access your microphone** are both toggled **ON**.
- **WSLg PulseAudio Socket:** WSLg routes the Windows default microphone into WSL as `RDPSource`. Ensure the socket exists at `/mnt/wslg/PulseServer`.
- **Diagnostics:** Run `./run.sh doctor` inside WSL to verify audio device enumeration and permissions.

### 2. Windows Global Hotkeys Not Triggering
**Symptom:** Pressing `Alt+Shift` inside a Windows application does not trigger recording in WSL.  
**Fix:**
- Ensure the background bridge is running. Launch via `run_wsl.bat` on the Windows host, which spawns `src/voice_transcriber/platform/wsl/wsl_win_hotkeys.ps1` to listen for global hotkeys and forward them across the WSL boundary.
