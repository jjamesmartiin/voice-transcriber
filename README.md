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
From the repo root:
```cmd
# Double-click or run from Command Prompt / PowerShell:
setup.bat   # (One-time) creates .venv and installs dependencies
run.bat     # Launches the application
```
Or via PowerShell:
```powershell
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
| **`Ctrl+Alt+I`** (or `S`, `,`, `i` in terminal) | **Settings modal.** Interactive modal (`⚙️ Settings & Configuration`) with fuzzy search, in-place toggle badges, output modes, and formatting. |
| **`M`** (in terminal) | **Microphone picker.** Interactive device picker modal (`🎤 Microphone Input Device`) with active and default indicators. |
| **`t`** (in terminal) | **Theme picker.** Interactive UI accent color palette modal (`🎨 Select UI Color Theme`). |

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

Both frontends share complete 1:1 visual and interactive parity:
- **Interactive Modal Pickers**: `⚙️ Settings & Configuration`, `🎤 Microphone Input Device`, and `🎨 Select UI Color Theme` overlays with real-time search filtering, arrow/Tab navigation, and in-place toggling.
- **Inline CLI Prompt Stream**: Responsive status prompt line with active mic, model, sound, output mode, trailing space, punctuation, numbers, and mouse hold badges.
- **Clean Word-Wrapped Transcriptions**: Direct terminal scrollback with timing metadata dividers and zero border interference for 100% clean copy-paste.

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
(`.github/workflows/ci.yml`). Each job runs the **shared** tier plus its own
platform tier:

| CI job | Runner | Command |
| :--- | :--- | :--- |
| **Unit tests (Linux)** | `ubuntu-latest` | `pytest tests/shared tests/linux` (Nix dev shell) |
| **Unit tests (Windows)** | `windows-latest` | `pytest tests/shared tests/windows` (no PyTorch needed) |
| **Unit tests (WSL)** | `ubuntu-latest` | `pytest tests/shared tests/wsl` (Nix, `VT_PLATFORM=wsl`) |

### Test tiers

| Tier | Directory | Needs model? | Runs where |
| :--- | :--- | :--- | :--- |
| **Shared** | `tests/shared/` | No | All three OSes, every push |
| **Platform** | `tests/linux/`, `tests/windows/`, `tests/wsl/` | No | That OS only, every push |
| **End-to-end** | `tests/e2e/` | Yes | Local only (never CI) |

### Running the tests locally

One command per tier, from the repo root:

| Tier | Linux / WSL | Windows |
| :--- | :--- | :--- |
| **All (shared + platform)** | `./test.sh` | `.\test.ps1` |
| **Shared only** | `./test.sh shared` | `.\test.ps1 shared` |
| **Platform only** | `./test.sh platform` | `.\test.ps1 windows` |
| **End-to-end (model)** | `./test.sh e2e` | `.\test.ps1 e2e` |

`./test.sh` auto-detects Linux vs WSL and runs the matching platform tier. Both
scripts forward extra args to pytest, e.g. `.\test.ps1 shared -k tui -v`. The
launchers also work: `.\platforms\windows\run.ps1 test`.

The **end-to-end** tier needs the downloaded ASR model and (for the loopback
suite) a real speaker + mic, so it is intentionally **not** part of CI. See
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
   account or token required.

Once the weights are on disk the app runs **fully offline** with no network
access.

> **Note for Windows testers:** `models/` is gitignored, so a fresh clone has
> no weights. First launch auto-installs them from the GitHub release asset,
> which is the one slow step (a few minutes) before dictation starts.

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

## Custom Word & Phrase Dictionary

Voice Transcriber includes a high-speed Trie-compacted custom dictionary replacer (~0.005 ms execution time) that matches spoken words and technical phrases case-insensitively, respects word boundaries, and applies exact replacement casing and formatting.

The dictionary lives in `config/dictionary.yaml`.

### Quick-Add via CLI

You can add, list, remove, or test dictionary entries directly from the terminal without manually editing YAML files:

```bash
# Add or update a spoken phrase -> replacement mapping
python -m src.dictionary add "cube ctl" "kubectl"
python -m src.dictionary add "deep seq" "Deepseek"

# Test how a sentence is transformed by the dictionary & post-processor
python -m src.dictionary test "i deployed on nixos using cube ctl"
# Output: "I deployed on NixOS using kubectl."

# List current dictionary entries (optionally filtered)
python -m src.dictionary list
python -m src.dictionary list nixos

# Remove an entry
python -m src.dictionary remove "cube ctl"
```

### Manual YAML Editing

You can also edit `config/dictionary.yaml` directly:

```yaml
dictionary:
  # Spoken phrase: Desired Output
  cube ctl: kubectl
  k eight s: Kubernetes
  git hub: GitHub
  pull request: PR
  pull request review: PR review
  c pipeline: CI pipeline
```

- **Case-Insensitive Matching**: `github`, `GitHub`, and `GIT HUB` all match the rule.
- **Word-Boundary Protection**: Shorter words will not corrupt longer words (e.g., `pr` will not match the "pr" inside "program" or "spring").
- **Acronym Preservation**: Target words with capital letters (e.g., `GitHub`, `PR`, `Kubernetes`, `CI`) are automatically protected from mid-sentence lowercasing.

---

## License

Apache License 2.0. See [LICENSE](LICENSE). The bundled Cohere Transcribe model
is distributed under Apache-2.0 as well (see `config/licenses/`).
