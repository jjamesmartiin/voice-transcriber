# Voice Transcriber

A robust, modular voice transcription tool with global hotkeys for Linux (Wayland/X11) and Windows (via NixOS WSL or Native).

---

## Quick Start

### Windows
- **Native Windows**: See the [main-windows](https://github.com/jjamesmartiin/voice-transcriber/tree/main-windows) branch.
- **Windows via NixOS WSL**: See the [`main-wsl`](https://github.com/jjamesmartiin/voice-transcriber/tree/main-wsl) branch.
  - Global push-to-talk (`Alt+Shift`) with automatic host clipboard paste.
  - Streaming VAD speech chunking for low-latency transcription.
  - Energy gating and silence trimming to prevent background hallucinations.
  - Audio cue feedback configurable in `run.sh` or settings.

```bash
# To run on Windows via WSL:
git checkout main-wsl
./run.sh
```

---

### Linux
```bash
# Add user to input group for global hotkeys
sudo usermod -a -G input $USER
# Log out and back in, then run:
nix run github:jjamesmartiin/voice-transcriber

# Or run as root (not recommended)
sudo nix run github:jjamesmartiin/voice-transcriber
```

#### NixOS Example
```nix
users.users.yourusername.extraGroups = [ "input" ];
```

See [NixOS options](https://search.nixos.org/options?channel=25.11&include_modular_service_options=1&include_nixos_options=1&query=users.users.*.extra) for more info.

---

## Usage

### Controls
- **Alt+Shift** (hold) - Start recording, release to transcribe
- **Ctrl+Alt+I** - Open settings menu

### Settings Menu
- P/S - Set primary/secondary audio device
- M - Toggle mute
- B - Switch model (whisper/cohere)
- T - Toggle auto-type to screen
- c - Save and exit

---

## Model Details

- **Cohere Transcribe (`CohereLabs/cohere-transcribe-03-2026`)**: Primary default model. High precision, low hallucination rate.
- **Faster-Whisper (`small` / `medium`)**: Local Whisper engine fallback with CTranslate2 optimization.

---

## Wispr Flow Real-Time Self-Correction & Post-Processor

Voice Transcriber features a multi-tiered Wispr Flow post-processing pipeline for real-time speech self-correction and disfluency cleanup:

### 1. Configuration & Environment Options

| Environment Variable | Default Value | Description |
| :--- | :--- | :--- |
| `VT_ENABLE_SLM` | `1` (or `0` to disable) | Toggles local `vLLM` SLM post-processing pass (`1` = active, `0` = Technique A pre-pass only). |
| `VT_SLM_MODEL` | `Qwen/Qwen2.5-0.5B-Instruct` | Local SLM model served on vLLM (`Qwen2.5-0.5B`, `Llama-3.2-1B`, etc.). |
| `VT_VLLM_URL` | `http://localhost:8000/v1/chat/completions` | Local vLLM OpenAI-compatible REST API endpoint. |
| `VT_SLM_TIMEOUT` | `1.5` (seconds) | Maximum timeout before gracefully falling back to Technique A ASR text. |
| `VT_CPU_THREADS` | `min(4, os.cpu_count())` | PyTorch CPU thread cap to prevent CPU spinning. |
| `VT_MODE` | `auto` (`auto`, `fixed`, `stop-and-wait`) | Audio micro-batching mode for real-time streaming. |

---

### 2. Latency Benchmarks (Synthetic E2E Speech Audio $\rightarrow$ ASR $\rightarrow$ Wispr Flow)

Measured on AMD Ryzen (`pc-jamesm2`) serving local `vLLM` (`Qwen2.5-0.5B-Instruct` on CPU):

| Benchmark Test Case | Audio Duration | ASR Decoding Latency | Wispr Flow SLM Pass | Total E2E Latency | Idempotent Match |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Self-Correction Retraction** (*"5 PM... actually 6 PM"*) | 4.78s | 1328.7 ms | **446.8 ms** | **1775.5 ms** | ✅ **100% Yes** |
| **Name Retraction** (*"John... I mean Alice"*) | 3.37s | 1220.0 ms | **282.1 ms** | **1502.1 ms** | ✅ **100% Yes** |
| **Date Self-Correction** (*"Tuesday... no wait Wednesday"*) | 3.95s | 1239.2 ms | **216.4 ms** | **1455.6 ms** | ✅ **100% Yes** |
| **Voice Erasure** (*"scratch that"*) | 3.37s | 1319.5 ms | **347.5 ms** | **1667.0 ms** | ✅ **100% Yes** |
| **Subordinating Conjunction Fix** (*"specifically. because"*) | 3.38s | 1273.0 ms | **258.3 ms** | **1531.3 ms** | ✅ **100% Yes** |

---

### 3. Synthetic Audio Test Suite & Latency Benchmark Runner

You can generate synthetic speech audio on-the-fly and run end-to-end latency benchmarks without relying on manual microphone recordings:

```bash
# Run synthetic E2E audio test suite and print latency table:
python3 tests/benchmark_synthetic_e2e.py

# Run in Nix shell:
nix-shell -p espeak-ng python3Packages.pytest python3Packages.numpy python3Packages.soundfile python3Packages.torch python3Packages.transformers python3Packages.librosa python3Packages.sentencepiece --run "python3 tests/benchmark_synthetic_e2e.py"
```

---

## License
See [LICENSE](LICENSE) for details.
