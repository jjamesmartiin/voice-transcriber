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

### 2. Antivirus & EDR Compliance (FortiEDR, CrowdStrike, SentinelOne)
* **Non-Invasive Hotkey Probing**: Avoids intrusive system-wide keyboard hooks (`WH_KEYBOARD_LL` or raw input listeners) that trigger keylogger heuristics. Only queries the explicit asynchronous state of modifier keys (`Alt` + `Shift`).
* **Zero Keystroke Scraping**: The engine cannot and does not monitor, log, or store arbitrary alphanumeric keystrokes, passwords, or personal user activity.
* **Standard User-Space Execution**: Operates entirely with non-elevated user permissions. Requires no root, `sudo`, or Windows Administrator privileges.

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

### Hugging Face Authentication (For Cohere Model)
To download the Cohere weights for the first time:
1. Accept model terms at [Hugging Face: CohereLabs/cohere-transcribe-03-2026](https://huggingface.co/CohereLabs/cohere-transcribe-03-2026).
2. Create a file named `HF_TOKEN` in the repository root containing your Hugging Face token (`hf_...`).
3. Run `./run.sh` — the model will be cached locally for all subsequent offline runs.

---

## ⌨️ Controls & Keybindings

| Key Combination | Action |
| :--- | :--- |
| **`Alt` + `Shift`** *(Hold)* | Start recording audio |
| **`Alt` + `Shift`** *(Release)* | Stop recording, transcribe speech, and copy text to clipboard |
| **`Ctrl` + `Alt` + `I`** | Open Audio Settings menu |
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

## 📄 License & Attribution

This project is licensed under the [MIT License](LICENSE).  
For third-party dependencies, licenses, and compliance disclosures, see [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
