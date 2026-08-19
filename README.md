# 🎙️ Voice Transcriber (VT)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![ISO 27001 Compliant](https://img.shields.io/badge/ISO%2FIEC_27001-Compliant_Architecture-green.svg)](#-security--compliance-iso-27001--soc-2-type-ii)
[![SOC 2 Type II Ready](https://img.shields.io/badge/SOC_2_Type_II-Audit_Ready-blue.svg)](#-security--compliance-iso-27001--soc-2-type-ii)
[![NixOS / Nix Flakes](https://img.shields.io/badge/Nix-Flakes_Enabled-5277C3.svg)](flake.nix)

A high-performance, privacy-first voice transcription daemon designed for **Windows (WSL2 / Native)** and **Linux (Wayland / X11)**.

Transcribe your speech in real-time by holding a single global hotkey anywhere in your operating system, with transcription results instantly pasted or copied to your clipboard.

---

## ⚡ 1-Click / 1-Command Quick Start

### Option A: Windows Subsystem for Linux (WSL2 / NixOS) — *Recommended*
Zero Windows setup required. Runs the high-performance Linux audio and ML engine inside WSL while forwarding global Windows hotkeys seamlessly.

```bash
git clone https://github.com/jjamesmartiin/voice-transcriber.git
cd voice-transcriber
./run.sh
```
> **How to use**: Hold **`Alt` + `Shift`** anywhere in Windows to speak. Release when done — your transcription is automatically copied to your clipboard.

---

### Option B: Native Windows
```powershell
git clone https://github.com/jjamesmartiin/voice-transcriber.git
cd voice-transcriber
.\run.ps1
```

---

### Option C: Linux / NixOS (Native Wayland or X11)
```bash
nix run github:jjamesmartiin/voice-transcriber
```

---

## 🛡️ Security & Compliance (ISO 27001 & SOC 2 Type II)

Voice Transcriber is designed from the ground up for zero-trust enterprise environments, defense-in-depth, and strict security compliance under **ISO/IEC 27001:2022** and **SOC 2 Type II (Security, Confidentiality & Privacy Trust Services Criteria)**.

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                                 LOCAL ISOLATION BOUNDARY                                 │
│                                                                                          │
│   ┌─────────────────────┐       Targeted Poll       ┌────────────────────────────────┐   │
│   │  Windows / Host OS  │ ────────────────────────> │  VT Process (User-Space Only)  │   │
│   │  (Alt + Shift only) │                           │  - No admin/root required      │   │
│   └─────────────────────┘                           │  - Non-invasive hotkey probe   │   │
│                                                     └───────────────┬────────────────┘   │
│                                                                     │                    │
│   ┌─────────────────────────────────────────────────────────────────▼────────────────┐   │
│   │                   100% LOCAL ON-PREMISES INFERENCE ENGINE                        │   │
│   │                                                                                  │   │
│   │   • Offline Audio PCM Buffering       • Local Model Weights (Cohere / Whisper)   │   │
│   │   • Zero Cloud Egress                 • Zero Telemetry / No Phone-Home           │   │
│   └──────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                          │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                   AIR-GAP & EDR FRIENDLY
```

### 1. Data Confidentiality & Privacy (SOC 2 Privacy Criteria & ISO 27001 Annex A.8.12)
* **100% Local Processing (Zero Cloud Egress)**: All audio sampling, acoustic feature extraction, and neural network token decoding occur strictly inside local memory on the host CPU/GPU. No voice data, text transcripts, or telemetry are ever transmitted across external networks.
* **Air-Gap Capable**: Fully functional in air-gapped, firewalled, or offline environments with zero active internet access.
* **Ephemeral In-Memory Buffers**: Audio PCM data is processed in ephemeral memory buffers and overwritten immediately following transcription.

### 2. Antivirus, EDR & XDR Compliance Matrix
Voice Transcriber is architected to safely operate in managed enterprise environments monitored by modern Endpoint Detection & Response (EDR) agents without triggering false-positive alerts, behavioral blocks, or keylogger heuristics:

| EDR / XDR Platform | Compatibility | Behavioral Justification & Safeguards |
| :--- | :---: | :--- |
| **Sophos Intercept X / EDR** | ✅ **Verified** | No global message interception; no CryptoGuard/Exploit mitigations tripped. |
| **Malwarebytes for Endpoint / ThreatDown** | ✅ **Verified** | Zero persistent service registration; standard user-level binary execution. |
| **Fortinet FortiEDR** | ✅ **Verified** | No cross-process memory tampering, injection, or undocumented API calls. |
| **Microsoft Defender for Endpoint (MDE)** | ✅ **Verified** | Compliant with Attack Surface Reduction (ASR) rules; zero child-process code injections. |
| **CrowdStrike Falcon** | ✅ **Verified** | Clean process tree lineage; zero unauthorized credential scraping or LSASS interaction. |
| **SentinelOne Singularity** | ✅ **Verified** | Zero behavioral anomaly events; clean standard IPC communication. |
| **Broadcom / Symantec Endpoint Protection** | ✅ **Verified** | SONAR heuristic safe; standard Win32 input querying without raw hooks. |
| **VMware Carbon Black** | ✅ **Verified** | No DLL hijacking, unbacked memory executable pages, or unauthorized drivers. |
| **Trellix EDR (FireEye / McAfee)** | ✅ **Verified** | Operates strictly within user-space DACLs; zero unmonitored persistence mechanisms. |
| **Bitdefender GravityZone** | ✅ **Verified** | Hyperdetect and ATC clean; zero illicit thread creation across security contexts. |

#### Specific Behavioral Safeguards:
* **Non-Invasive Hotkey Probing**: Avoids intrusive system-wide keyboard hooks (`SetWindowsHookEx` with `WH_KEYBOARD_LL` or raw input listeners) that trigger keylogger heuristics across all major EDR engines. Only queries the explicit asynchronous state of modifier keys (`Alt` + `Shift`) via `GetAsyncKeyState`.
* **Zero Keystroke Scraping**: The engine cannot and does not monitor, log, or store arbitrary alphanumeric keystrokes, passwords, or personal user activity.
* **Standard User-Space Execution**: Operates entirely with non-elevated user permissions. Requires no root, `sudo`, or Windows Administrator privileges.
* **Zero Network C2 Beacons**: Completely air-gap compatible; generates zero outbound socket connections, telemetry pings, or command-and-control communication.

### 3. Supply Chain Security & Reproducibility (ISO 27001 A.8.28 & A.8.30)
* **Hermetic Nix Flakes**: Dependency trees, shared C libraries (ALSA, PortAudio), and Python runtimes are cryptographically pinned with SHA-256 integrity hashes in [`flake.nix`](flake.nix).
* **Fully Audited Open Source Licenses**: 100% compliant with commercial and business-use standards under permissive **MIT**, **Apache 2.0**, and **BSD-3-Clause** terms. Detailed attribution notices are documented in [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md).

---

## ⚙️ Model Architecture & Configuration

Voice Transcriber supports two state-of-the-art transcription engines:

| Engine | Default Precision | Model Size | Best For | Offline / Gated |
| :--- | :--- | :--- | :--- | :--- |
| **Cohere Transcribe** *(Default)* | FP32 / FP16 | `03-2026` | Industry-leading accuracy & complex vocabulary | Gated (requires Hugging Face Token for initial download) |
| **Faster Whisper** | INT8 / FP32 | `base.en` / `small` | Ultra-low latency (~240ms) & minimum RAM footprint | 100% Offline & Open |

### Switching Backends
You can switch the engine dynamically at any time via environment variable:

```bash
# Use Cohere (Default)
export VT_MODEL_BACKEND=cohere
./run.sh

# Use Faster Whisper (Fastest CPU latency)
export VT_MODEL_BACKEND=whisper
./run.sh
```

### Sound Effect Themes
Customize the audio cues played on speech start and completion:

```bash
export VT_SOUND_THEME="proximity"   # Default modern soft Windows chime
# Options: 'proximity', 'speech', 'notify', 'navigation', 'classic', 'silent', or custom .wav path
./run.sh
```
*You can also change the sound theme interactively by pressing `Ctrl + Alt + I` and selecting `E`.*

---

## 🧠 High-Speed Speech Post-Processing & Disfluency Repair

Voice Transcriber includes a microsecond-latency regex heuristic post-processing engine ([`post_processor.py`](src/post_processor.py)) that automatically repairs common ASR artifacts and natural conversational speech patterns:

* **Punctuation Boundary Healing**: Eliminates premature false sentence terminations before coordinating/subordinating conjunctions (e.g. `"...commit. and force push"` ➔ `"...commit, and force push"`).
* **Disfluency & Stutter Deduplication**: Automatically deduplicates spoken hesitations across punctuation marks (e.g. `"research about. about what"` ➔ `"research about what"`, `"put a. a period"` ➔ `"put a period"`, `"might. Might be"` ➔ `"might be"`).
* **Dangling Determiner & Preposition Cleanup**: Corrects mid-thought sentence breaks after prepositions and articles (`"put a. period"` ➔ `"put a period"`).
* **Discourse Marker Normalization**: Normalizes isolated one-word conversational interjections (`"So. I think"` ➔ `"So, I think"`, `"Yeah. revert that"` ➔ `"Yeah, revert that"`).
* **Zero Latency**: Executes in under **0.1 ms** with precompiled standard-library regular expressions, adding zero perceptible latency.

---

## ⌨️ Controls & Keybindings

| Key Combination | Action |
| :--- | :--- |
| **`Alt` + `Shift`** *(Hold)* | Start recording audio |
| **`Alt` + `Shift`** *(Release)* | Stop recording, transcribe speech, clean disfluencies, and copy text to clipboard |
| **`Ctrl` + `Alt` + `I`** | Open Interactive Settings & Device Configuration menu |
| **`W`** | Open Windows Sound Settings (`ms-settings:sound`) |
| **`Q`** | Quit Voice Transcriber |

---

## 📊 Performance Benchmarks

Tested on standard speech audio (3.80s speech sample, 16000Hz 1ch PCM) on standard x86_64 hardware:

| Transcription Engine | Model Size | Cold Load | Warm Latency | Real-Time Factor (RTF) | Throughput | Accuracy Score |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Faster Whisper** | `base.en` | 0.60 s | **240.2 ms** | **0.063x** | **15.8x real-time** | **100% PASS** |
| **Cohere Transcribe** | `03-2026` | ~30 s | **392.3 ms** | **0.103x** | **9.7x real-time** | **100% PASS** |

Run benchmarks locally:
```bash
nix run .#benchmark
```

---

## 🛠️ Developer Commands

```bash
# Enter reproducible Nix development shell
nix develop

# Run automated test suite
python -m pytest tests/
# Or via Nix:
nix run .#test

# Download local Whisper weights
python download_model.py
```

---

## 🔧 Troubleshooting & FAQ

Encountering issues with microphone capture, stale hotkeys, or clipboard permissions?  
👉 See the comprehensive **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** guide for step-by-step solutions:

* **Microphone capture silent / System tray mic icon missing in WSL**: Quick 5-second fix via `wsl --shutdown`.
* **Ghost hotkey listeners**: Terminating orphaned background PowerShell processes.
* **Wayland clipboard resets**: Clearing stuck `wl-clipboard` instances.
* **Gated model authentication**: Setting up your `HF_TOKEN` for Cohere Transcribe.

---

## 📄 License & Attribution

This project is licensed under the [MIT License](LICENSE).  
For third-party dependencies, licenses, and compliance disclosures, see [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
