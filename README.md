# Voice Transcriber

A modular voice transcription engine with global hotkeys and real-time text injection, supporting Linux (Wayland/X11), Windows Native, and Windows via WSL.

---

## Platforms & Quick Start

Choose your platform guide for installation and setup:

| Platform | Documentation | Quick Run |
| :--- | :--- | :--- |
| **Linux (Wayland / X11)** | [Linux Setup Guide](platforms/linux/README.md) | `nix run .` or `./platforms/linux/run.sh` |
| **Windows (Native)** | [Windows Native Guide](platforms/windows/README.md) | `.\platforms\windows\run.ps1` |
| **Windows (WSL2 / NixOS)** | [WSL Guide](platforms/wsl/README.md) | `powershell -File platforms\wsl\run_wsl.ps1` |

---

## Controls

- **Alt+Shift** (hold): Push-to-Talk. Hold while speaking, release to transcribe and paste/type into the active window.
- **Space** (tap while holding Alt+Shift): Hands-Free Latch. Release keys and continue speaking without holding; tap `Alt+Shift` again when finished to transcribe.
- **Ctrl+Alt+I**: Settings Menu. Interactive terminal UI to switch models, audio devices, mute state, number conversion, and formatting levels.

---

## Terminal UI

The default frontend is a [ratatui](https://ratatui.rs) (Rust) TUI, launched
automatically by the Python engine over a local socket — see
[`tui-rs/README.md`](tui-rs/README.md). `nix run` builds it via the flake; no
separate build step is needed.

Set `VT_TUI=rich` to force the original Rich frontend, or `VT_TUI_BIN=/path` to
point at a specific `vt-tui` binary (e.g. a local `cargo` build during frontend
development). If no binary is found, the app falls back to Rich automatically.

---

## Architecture

Voice Transcriber unifies all supported platforms over a single shared core engine using a Hardware/OS Abstraction Layer (HAL):

```
Voice Transcriber Architecture
┌─────────────────────────────────────────────────────────────┐
│                      Core Engine (src/)                     │
│                                                             │
│  - Audio Pipeline & Streaming VAD (t2.py, micro_batcher.py) │
│  - ASR Engines (Cohere Transcribe, Faster-Whisper)          │
│  - Wispr Flow Post-Processor (post_processor.py)            │
│    * Trie-compacted dictionary replacer                     │
│    * Filler-word and stutter removal                        │
│    * Verbal retraction parser ("no wait", "scratch that")   │
│    * Spoken numbers to digits conversion                    │
│    * Sentence casing & terminal punctuation                 │
│  - Rich Terminal UI Dashboard (tui.py)                      │
└──────────────────────────────┬──────────────────────────────┘
                               │
            ┌──────────────────┴──────────────────┐
            ▼                                     ▼
┌──────────────────────┐              ┌──────────────────────┐
│  src/hal.py (Loader) │              │  src/platform/ (HAL) │
└───────────┬──────────┘              └──────────┬───────────┘
            │                                    │
    ┌───────┴───────────────┬────────────────────┴───────┐
    ▼                       ▼                            ▼
Linux (evdev/uinput,     Windows (pynput,            WSL (TCP Bridge,
 wl-copy, ydotool)        pyperclip, user32)          clip.exe interop)
```

---

## Quality Control & Testing Protocol

All changes must satisfy automated quantitative SLA gates before deployment:

| Metric Gate | Target SLA | Ceiling | Description |
| :--- | :--- | :--- | :--- |
| **Post-Release Latency** | $\le 1.00\text{ s}$ | $\le 1.50\text{ s}$ | Elapsed time from key release (`stop_recording`) to clipboard paste/typing. |
| **Accuracy Match** | $\ge 90.0\%$ | $\ge 80.0\%$ | Match ratio against ground-truth benchmarks across words, phrases, and 30s speech. |
| **Hallucination Rejection** | $0$ noise tokens | $0$ noise tokens | Ambient room noise or silence must return empty string `""`. |
| **Punctuation Coverage** | $100\%$ | $100\%$ | Multi-word sentences must terminate with valid punctuation (`.`, `!`, `?`). |

### Running the Test Suites

Run the full cross-platform test matrix:
```bash
# Cross-platform end-to-end and platform HAL tests:
nix develop --command python -m pytest tests/test_end_to_end_crossplatform.py tests/test_platform_hal.py -v

# Core dictionary, micro-batcher, and configuration tests:
nix develop --command python -m pytest tests/test_dictionary.py tests/test_micro_batcher_fast.py tests/test_config_sync.py tests/test_tui.py

# Live acoustic loopback test suite:
nix develop --command python tests/test_live_speaker_mic_loopback.py all
```

---

## Configuration

Settings are configured via `config/config.yaml`. To customize startup defaults, copy the template:
```bash
cp config/example-config/config.yaml.example config/config.yaml
```

Supported options include:
- `model_backend`: `cohere` or `whisper`.
- `is_muted`: `true` or `false`.
- `auto_type`: `true` (direct keystroke injection) or `false` (clipboard only).
- `copy_to_clipboard`: `true` or `false`.
- `number_digits`: `true` (convert spoken numbers to digits) or `false`.
- `keep_bluetooth_handsfree`: `true` (keeps Bluetooth devices in hands-free mode to prevent media/videos from pausing when recording ends) or `false`.
- `formatting_level`: `raw`, `standard`, `semi-formal`, or `formal`.
- `dictionary`: Key-value map of custom phrase replacements.

### Hugging Face Access Token

The default Cohere model (`CohereLabs/cohere-transcribe-03-2026`) is gated. If downloading from Hugging Face for the first time, set `hf_token` in `config/config.yaml`, or export `HF_TOKEN` in your environment. Once weights are downloaded locally into `models/cohere`, the app runs completely offline.

---

## License

Apache License 2.0. See [LICENSE](LICENSE) for details.
