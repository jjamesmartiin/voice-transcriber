# Voice Transcriber

A modular, low-latency voice transcription engine with global push-to-talk
hotkeys and real-time text injection. One shared core engine, three supported
host environments: **Linux (Wayland/X11)**, **Windows (native)**, and
**Windows via WSL2 (NixOS)**.

---

## Platform Support

| Platform | Status | Hotkeys | Output injection | Audio capture | Guide | Quick run |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Linux (Wayland / X11)** | Supported | `evdev` + `uinput` | `wl-copy` / `ydotool` / `xdotool` | PulseAudio / PipeWire | [Linux guide](platforms/linux/README.md) | `nix run .` or `./platforms/linux/run.sh` |
| **Windows (native)** | Supported | `pynput` + `keyboard` | Win32 `SendInput` (Unicode/emoji) | WASAPI / DirectSound | [Windows guide](platforms/windows/README.md) | `.\platforms\windows\run.ps1` |
| **Windows (WSL2 / NixOS)** | Supported | Windows-host bridge (PowerShell ⇄ socket IPC) | Host-side synthetic paste (`clip.exe` + `Ctrl+V`) | WSLg PulseAudio (`RDPSource`) | [WSL guide](platforms/wsl/README.md) | `powershell -File platforms\wsl\run_wsl.ps1` |

All three share the same engine, ASR backend, post-processor, and config format.
Only the four HAL backends differ: **hotkeys**, **clipboard/typing**,
**audio cues**, and **notifications** (`src/platform/<os>/`).

---

## Quick Start

### Linux (Wayland / X11)
```bash
# Recommended: Nix flake (provides PyTorch, PortAudio, wl-clipboard, evdev, uinput)
nix run .

# Or Python directly (requires the deps in flake.nix + membership in the 'input' group)
./platforms/linux/run.sh
```
One-time setup: `sudo usermod -a -G input $USER` (then re-login) so global
hotkeys work without root. See the [Linux guide](platforms/linux/README.md).

### Windows (native)
From PowerShell in the repo root:
```powershell
# Creates .venv, installs platforms/windows/requirements.txt, launches the app
.\platforms\windows\run.ps1
```
Requires Python 3.10+. See the [Windows guide](platforms/windows/README.md).

### Windows via WSL2 (NixOS)
From Windows PowerShell in the repo root:
```powershell
# One-time: enable VM Platform + WSL, then register NixOS
powershell -ExecutionPolicy Bypass -File platforms\wsl\setup_wsl.ps1

# Run
powershell -ExecutionPolicy Bypass -File platforms\wsl\run_wsl.ps1
```
Or from inside the NixOS WSL shell: `nix run .`.
See the [WSL guide](platforms/wsl/README.md) and
[WSL troubleshooting](platforms/wsl/TROUBLESHOOTING.md).

---

## Controls

| Gesture | Action |
| :--- | :--- |
| **`Alt+Shift`** (hold) | **Push-to-Talk.** Hold while speaking; release to transcribe and paste/type into the active window. |
| **`Space`** (tap while holding `Alt+Shift`) | **Hands-Free Latch.** Release the keys and keep speaking; tap `Alt+Shift` again when finished. |
| **Middle-click** (hold ~0.25 s) | **Mouse Push-to-Talk.** A quick click (< 0.25 s) passes through and is ignored. |
| **`Ctrl`** (held at release) | **Clipboard override.** Forces clipboard output for this utterance even when auto-type is enabled. |
| **`Ctrl+Alt+I`** (or `i` in the terminal) | **Settings menu.** Pick audio device, output mode, sound theme, UI theme, punctuation, and number handling. |

Hotkey behaviour is portable across all three platforms, including the
hands-free latch and the middle-click hold threshold.

---

## Terminal UI

The default frontend is a [ratatui](https://ratatui.rs) (Rust) TUI, launched
automatically by the Python engine over a local socket — see
[`tui-rs/README.md`](tui-rs/README.md). `nix run` builds it via the flake; no
separate build step is needed.

Set `VT_TUI=rich` to force the original Rich frontend, or `VT_TUI_BIN=/path` to
point at a specific `vt-tui` binary (e.g. a local `cargo` build during frontend
development). If no binary is found, the app falls back to Rich automatically —
this is the default on native Windows.

---

## Architecture

Voice Transcriber unifies all supported platforms over a single shared core
engine using a Hardware/OS Abstraction Layer (HAL):

```
Voice Transcriber Architecture
┌─────────────────────────────────────────────────────────────┐
│                      Core Engine (src/)                     │
│                                                             │
│  - Audio Pipeline & Streaming VAD (t2.py, micro_batcher.py) │
│  - ASR Engine (Cohere Transcribe)                           │
│  - Wispr Flow Post-Processor (post_processor.py)            │
│    * Trie-compacted dictionary replacer                     │
│    * Filler-word and stutter removal                        │
│    * Verbal retraction parser ("no wait", "scratch that")   │
│    * Spoken numbers to digits conversion                    │
│    * Sentence casing & terminal punctuation                 │
│  - TUI: ratatui (default) / Rich (fallback)                 │
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
Linux (evdev/uinput,     Windows (pynput,            WSL (PowerShell bridge,
 wl-copy, ydotool)        pyperclip, user32)          clip.exe interop)
```

Platform detection lives in `src/platform/__init__.py`; override it with
`VT_PLATFORM=linux|windows|wsl` (an unknown value is a hard error by design).

---

## Testing & CI

GitHub Actions runs a three-OS matrix on every push and PR
(`.github/workflows/ci.yml`):

| CI job | Runner | Environment |
| :--- | :--- | :--- |
| **Unit tests (Linux)** | `ubuntu-latest` | Nix dev shell |
| **Unit tests (Windows)** | `windows-latest` | Python 3.11 + `pip` (no PyTorch needed) |
| **Unit tests (WSL)** | `ubuntu-latest` | Nix dev shell with `VT_PLATFORM=wsl` |

### Running the tests locally

```bash
# Linux / WSL — run the entire tests/ directory
nix run .#test

# Linux / WSL — targeted subsets
nix develop --command python -m pytest tests/test_end_to_end_crossplatform.py tests/test_platform_hal.py -v
nix develop --command python -m pytest tests/test_dictionary.py tests/test_micro_batcher_fast.py tests/test_config_sync.py tests/test_tui.py tests/test_user_workflows.py tests/test_wsl.py

# Linux / WSL — live acoustic loopback (needs a real speaker + mic)
nix develop --command python tests/test_live_speaker_mic_loopback.py all
```

```powershell
# Windows (native) — full hardware-independent suite
.\platforms\windows\run.ps1 test

# Windows (native) — targeted subset (extra args are forwarded to pytest)
.\platforms\windows\run.ps1 test tests\test_platform_hal.py -v
```

The acoustic end-to-end and live speaker→microphone suites require real audio
devices and a loaded model, so they are intentionally **not** part of CI. See
[`docs/agent_testing_workflow.md`](docs/agent_testing_workflow.md) for the
release-quality protocol.

### Quality gates

| Metric gate | Target SLA | Ceiling | Description |
| :--- | :--- | :--- | :--- |
| **Post-release latency** | $\le 1.00\text{ s}$ | $\le 1.50\text{ s}$ | Elapsed time from key release (`stop_recording`) to clipboard paste/typing. |
| **Accuracy match** | $\ge 90.0\%$ | $\ge 80.0\%$ | Match ratio against ground-truth benchmarks across words, phrases, and 30 s speech. |
| **Hallucination rejection** | $0$ noise tokens | $0$ noise tokens | Ambient room noise or silence must return the empty string `""`. |
| **Punctuation coverage** | $100\%$ | $100\%$ | Multi-word sentences must terminate with valid punctuation (`.`, `!`, `?`). |

---

## Model Weights & Offline Operation

The only backend is **Cohere Transcribe**
(`CohereLabs/cohere-transcribe-03-2026`, Apache-2.0, ~2 GB). Acquisition order:

1. A local copy under `models/cohere/` (repo checkout) or `VT_MODEL_DIR`.
2. A per-user install dir (`%APPDATA%\vt\models\cohere` on Windows,
   `~/.local/share/vt/models/cohere` elsewhere, or `$XDG_DATA_HOME/vt`).
3. **Auto-download of the Apache-2.0 GitHub release asset** — no Hugging Face
   account required.
4. Hugging Face (only if a token is configured), then first-run download.

Once the weights are on disk the app runs **fully offline** with no network
access.

> **Note for Windows testers:** `models/` is gitignored, so a fresh clone has
> no weights. First launch auto-installs them from the GitHub release asset,
> which is the one slow step (a few minutes) before dictation starts.

If you do pull from Hugging Face, set `hf_token` in `config/config.yaml` or
export `HF_TOKEN` in your environment (the model is gated there).

---

## Building Distributables

| Target | Command | Output |
| :--- | :--- | :--- |
| **Linux (Nix)** | `nix build .` | `result/bin/vt` |
| **Windows (offline EXE)** | `.\platforms\windows\run.ps1 build` (or `python platforms\windows\build_offline.py`) | `dist/VoiceTranscriber/` (PyInstaller `--onedir`, bundles the model) |

The Windows build is self-contained (~2–3 GB: Python + PyTorch + Cohere
weights) and needs no Python install on the target machine. See
[`platforms/windows/plan-to-compile.md`](platforms/windows/plan-to-compile.md).

---

## Configuration

Settings live in `config/config.yaml`. To customize startup defaults, copy the
template:
```bash
cp config/example-config/config.yaml.example config/config.yaml
```

Supported options:
- `is_muted`: `true` or `false`.
- `output_mode`: `clipboard`, `type` (slow/safe), or `type_fast` (optimized).
- `auto_type`: `true` (direct keystroke injection) or `false` (clipboard only).
- `copy_to_clipboard`: `true` or `false`.
- `auto_type_trailing_space`: append a space after auto-typed text.
- `auto_type_auto_punctuate`: enforce terminal punctuation on auto-typed text.
- `number_digits`: `true` (convert spoken numbers to digits) or `false`.
- `keep_bluetooth_handsfree`: `true` keeps Bluetooth devices in hands-free mode so media does not pause when recording ends.
- `punctuation_mode`: `full`, `no_terminal_period`, `no_punctuation`, or `lowercase_no_punctuation` (legacy alias `semi-formal` → `no_terminal_period`).
- `enable_slm`: `true` to enable the optional local vLLM grammar-polish pass (default `false`).
- `dictionary`: key-value map of custom phrase replacements (see `config/dictionary.yaml`).

Options can also be toggled live from the settings menu (`Ctrl+Alt+I`).

---

## License

Apache License 2.0. See [LICENSE](LICENSE). The bundled Cohere Transcribe model
is distributed under Apache-2.0 as well (see `config/licenses/`).
