# Voice Transcriber

A modular, low-latency voice transcription engine with global push-to-talk
hotkeys and real-time text injection. One shared core engine, three supported
host environments: **Linux (Wayland/X11)**, **Windows (native)**, and
**Windows via WSL2 (NixOS)**.

---

## Platform Support

| Platform | Status | Hotkeys | Output injection | Audio capture | Guide | Quick run |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Linux (Wayland / X11)** | Supported | `evdev` + `uinput` | `wl-copy`/`xclip` (copy) · `ydotool`/`xdotool` (type) | PulseAudio / PipeWire | [Linux guide](platforms/linux/README.md) | `nix run .` or `./platforms/linux/run.sh` |
| **Windows (native)** | Supported | `pynput` + `keyboard` | Win32 `SendInput` (Unicode/emoji) | WASAPI / DirectSound | [Windows guide](platforms/windows/README.md) | `setup.bat` → `run.bat` |
| **Windows (WSL2 / NixOS)** | Supported | Windows-host bridge (PowerShell ⇄ socket IPC) | Host-side synthetic paste (`clip.exe` + `Ctrl+V`) | WSLg PulseAudio (`RDPSource`) | [WSL guide](platforms/wsl/README.md) | `powershell -File platforms\wsl\run_wsl.ps1` |

All three share the same engine, ASR backend, post-processor, and config format.
Only the four HAL backends differ: **hotkeys**, **clipboard/typing**,
**audio cues**, and **notifications** (`src/platform/<os>/`).

---

## Requirements

Read the **Common** table first — it applies to every platform — then the table
for the platform you are targeting.

### Common (all platforms)

| Requirement | Detail |
| :--- | :--- |
| **CPU** | `x86_64` or `arm64`. Inference is **CPU-only** — no GPU is required or used. |
| **Disk (running from source)** | ~4 GB for the Cohere weights (`model.safetensors` is 3.9 GB) plus ~1.5 GB for the Python environment — budget ~6 GB. |
| **Disk (standalone Windows EXE)** | ~6 GB — the bundle carries its own Python, PyTorch, and the full 3.9 GB model. |
| **Network** | ~2.8 GB download on **first launch only**, for the compressed model weights. Fully offline afterwards. Set `VT_AUTO_DOWNLOAD_MODEL=0` to skip the download and supply weights yourself. |
| **Model weights** | Auto-installed on first run, or provided via `VT_MODEL_DIR` / a local `models/cohere/`. The download is split-xz and needs ~2.8 GB down / ~4 GB on disk. |
| **Audio** | A working microphone exposed as the system default input device. |
| **Python** | **3.10+** — only needed to run from source or to build. The Nix package and the Windows EXE bundle their own interpreter. |

### Linux (Wayland / X11)

| Requirement | Detail |
| :--- | :--- |
| **Architecture** | `x86_64-linux` or `aarch64-linux` (both are flake outputs). |
| **Nix (recommended)** | Nix with flakes enabled. `nix run .` supplies Python, PyTorch, PortAudio, `wl-clipboard`, `evdev`, and `uinput` — nothing else to install. |
| **Python (alternative)** | Python 3.10+ with the dependencies from `flake.nix` / `platforms/windows/requirements.txt`. |
| **Audio server** | PipeWire or PulseAudio running, with your microphone as the default source. |
| **`input` group** | Required for global hotkeys without root: `sudo usermod -a -G input $USER`, then re-login. |
| **evdev + uinput** | Read access to `/dev/input/event*` and the `uinput` kernel module. Powers push-to-talk, the mouse chord, and synthetic keystrokes. |
| **Clipboard sink** | Wayland: `wl-clipboard`. X11: `xclip` to copy. |
| **Auto-type** | `ydotool` (Wayland or X11) or `xdotool` (X11). Without them the app degrades to clipboard-only output. |
| **Optional** | `libnotify` (desktop notifications), `mpg123` / `lame` / `alsa-utils` (audio cues), `zenity` (GUI prompts). |

### Windows (native)

| Requirement | Detail |
| :--- | :--- |
| **OS** | Windows 10 or 11, 64-bit. |
| **Running from source** | Python 3.10+ installed from python.org with **"Add python.exe to PATH"** checked. |
| **Running the prebuilt EXE** | None of the above — the bundle is fully self-contained. |
| **Shell** | PowerShell 5.1+ (or PowerShell 7+). `setup.bat` / `run.bat` bypass the execution policy for you; invoking a raw `.ps1` needs `-ExecutionPolicy Bypass`. |
| **Microphone** | Windows Settings → Privacy & security → Microphone → allow **microphone access** and **let desktop apps access your microphone**. |
| **Path length** | Keep the checkout at a short path (e.g. `C:\voice-transcriber`) or enable `LongPathsEnabled`, otherwise `pip` fails with `[WinError 206]`. `setup.bat` offers to enable it via one UAC prompt. |
| **Admin** | Not normally required. Only the optional long-path registry change prompts for elevation. |
| **Frontend** | The Rich (Python) frontend is the default on native Windows; the ratatui TUI is opt-in via `VT_TUI_BIN`. |

### Windows via WSL2 (NixOS)

| Requirement | Detail |
| :--- | :--- |
| **OS** | Windows 10 (2004+) or 11, 64-bit. |
| **Virtualization** | **Virtual Machine Platform** and **WSL2** enabled — one-time, Administrator. `platforms\wsl\enable_wsl_features.bat` or `setup_wsl.ps1` does it for you. |
| **Distro** | **NixOS-WSL** registered. `setup_wsl.ps1` downloads the `nixos.wsl` image and registers it. |
| **Nix** | Nix with `nix-command` and `flakes` (the setup script writes `~/.config/nix/nix.conf`). |
| **Audio** | **WSLg** (bundled with modern WSL) provides the PulseAudio server at `/mnt/wslg/runtime-dir/pulse/native`. No extra audio setup. |
| **Host microphone** | Same Windows privacy setting as native — allow microphone access for desktop apps. |
| **Disk** | ~4 GB+ for the Nix store, plus ~4 GB for the model weights. |
| **Bridge** | Windows `powershell.exe` reachable from inside WSL — the global-hotkey bridge is a PowerShell process on the host. |

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
# Recommended (runs without PowerShell execution policy restrictions):
setup.bat   # (One-time) creates .venv and installs dependencies
run.bat     # Launches the application
```
Or via PowerShell (downloaded scripts require `-ExecutionPolicy Bypass`):
```powershell
# One-time setup:
powershell -ExecutionPolicy Bypass -File .\platforms\windows\setup.ps1

# Launch:
powershell -ExecutionPolicy Bypass -File .\platforms\windows\run.ps1
```
*(Tip: Or run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` once in your PowerShell session.)*

> **There are two supported ways to run on Windows.**
>
> 1. **From source** — `setup.bat` (one-time, creates `.venv` and installs
>    dependencies) then `run.bat`. Needs Python 3.10+ and installs ~1.5 GB of
>    dependencies. `run.bat` also bootstraps the venv on first launch if you skip
>    `setup.bat`. This is the path the commands above use, and what you develop against.
> 2. **From the prebuilt EXE** — `dist\VoiceTranscriber\VoiceTranscriber.exe`.
>    Needs no Python at all and no setup step; just run it. See
>    [Building Distributables](#building-distributables).
>
> The EXE is **not** committed to this repository (`dist/` is gitignored), so a
> fresh clone only gives you option 1 until you build it once with `run.bat build`.

> **Note on path length (`[WinError 206]`):** Windows has a 260-character path limit by default. If extracting from a zip, place the repository in a short path (e.g. `C:\voice-transcriber`) rather than deeply nested download folders, or enable `LongPathsEnabled`. See [Windows Troubleshooting](platforms/windows/README.md#troubleshooting).

Requires Python 3.10+ **only for source runs**. See the [Windows guide](platforms/windows/README.md).

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
| **Middle-click** (hold) | **Mouse Push-to-Talk.** A press is buffered for a ~250 ms hold delay: releasing before it leaves the click a normal middle click, holding past it starts recording and the release stops it. On Linux a tap under ~100 ms is replayed untouched; a longer hold is swallowed so no primary-selection paste leaks. |
| **Left+right click** (within ~50 ms) | **Enter.** While mouse mode is on, a near-simultaneous left+right click is swallowed and replayed as one `Enter` keystroke. |
| **`Ctrl`** (held at release) | **Clipboard override.** Forces clipboard output for this utterance even when auto-type is enabled. |
| **`Space`** / **`Enter`** (in terminal) | **Start / stop recording.** Tap to start, tap again to stop — hands-free once started. |
| **`s`** / **`S`** / **`,`** (in terminal) | **Settings modal — the single configuration entry point.** Fuzzy search, in-place toggle badges, output modes, formatting, plus the microphone and theme sub-pickers and **Reset to Defaults**. |
| **`r`** (in terminal) | **Reset terminal & clipboard bridge.** |
| **`q`** / **`Esc`** / **`Ctrl+C`** (in terminal) | **Quit.** |

The settings modal is the only place settings are changed. There is deliberately
**no global hotkey for it** — a system-wide settings shortcut would fire while you
were typing in another application. Everything else (microphone, theme, mode
presets) is a sub-picker inside that modal. From outside the terminal, drive the
same actions with the [Control API](#control-api).

> **Two ways to record, one of them hands-free.** Hold `Alt+Shift` — or the
> middle mouse button — for push-to-talk anywhere on the desktop, and release to
> finish. Or tap `Space` in the terminal to start and tap it again to stop. While
> holding `Alt+Shift`, tapping `Space` *latches* the recording: let go of both
> keys and keep talking, then tap `Alt+Shift` again to finish.

Push-to-talk, the hands-free latch, and the ~250 ms middle-click hold delay are
portable across all three platforms. The only platform extra is the sub-100 ms
tap-replay decision window, which is Linux-only — see below.

The left+right → `Enter` chord is Linux-only: it needs `EVIOCGRAB` plus a
virtual mouse to suppress the underlying clicks. The backend mirrors each
grabbed mouse through `uinput`, so clicks gain at most a ~50 ms decision delay
and are otherwise replayed untouched — except middle click, where a sub-100 ms
tap is replayed normally and a longer hold is consumed for push-to-talk.
Absolute pointers (touchpads/tablets) are left alone so gesture support is
preserved. If grabbing or `uinput` is unavailable, the chord is skipped and
the mouse behaves normally.

---

## Control API

Any program can drive the engine — start and stop recording, change a setting,
read back the last transcript — without simulating keystrokes. The engine listens
on a Unix socket, and `main.py` itself is the client:

```bash
python src/main.py toggle        # start recording, or stop if already recording
python src/main.py start
python src/main.py stop
python src/main.py wait          # block until the transcript is ready
python src/main.py status        # state, device, model, output mode, ...
python src/main.py mics
python src/main.py set-mic "usb"
python src/main.py output type_fast
python src/main.py mute
python src/main.py help          # every verb
python src/main.py help --json   # machine-readable verb catalogue
```

Running `python src/main.py` with no verb still launches the app. Add `--json`
for machine-readable output, or `--socket PATH` to target a specific instance.
Under Nix the same verbs pass through the wrapper: `nix run . -- status`.

| | |
| :--- | :--- |
| **Socket** | `$XDG_RUNTIME_DIR/vt-control-<uid>.sock` — override with `VT_CONTROL_SOCKET` |
| **Protocol** | One newline-delimited JSON request and one reply per connection |
| **Concurrency** | Many clients at once; each is served on its own daemon thread |
| **Isolation** | A malformed or hostile request can never take the engine down |
| **Exit codes** | `0` ok, `1` rejected/no instance, `2` unknown verb |

| Group | Verbs |
| :--- | :--- |
| **Recording** | `start`, `stop`, `toggle`, `wait`, `status` |
| **Devices** | `mics`, `set-mic` |
| **Settings** | `output`, `numbers`, `punctuation`, `theme`, `trailing-space`, `auto-punctuate`, `serial`, `spell`, `middle-click`, `mute` |
| **Modals** | `settings`, `mic` (interactive — take over the terminal) |
| **Lifecycle** | `reset-defaults`, `reset-terminal`, `ping`, `help`, `quit` |

Hand-typed clients work too, because a bare verb is accepted:

```bash
printf 'toggle\n' | socat - "UNIX-CONNECT:$XDG_RUNTIME_DIR/vt-control-$(id -u).sock"
```

**Full reference:** [`docs/control_api.md`](docs/control_api.md) — protocol and
reply shapes, every verb with its accepted values, scripting recipes, and a
section written for LLM agents (discover the surface with `help --json` rather
than guessing).

The engine and the control API share one command vocabulary, so tests can drive
a real running instance instead of reaching into internals — see
[`tests/shared/test_control.py`](tests/shared/test_control.py).

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

Both frontends share the same visual layout and keyboard model — `s`/`S`/`,`
for settings, `Space`/`Enter` to record — with the microphone and theme
sub-pickers reached from inside the settings modal:
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
 wl-copy/xclip/ydotool)   pyperclip, user32)          clip.exe interop)
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
launchers also work: `run.bat test` (or `powershell -ExecutionPolicy Bypass -File .\platforms\windows\run.ps1 test`).

### Mouse-mode coverage

Middle-click push-to-talk is pinned by
`tests/shared/test_mouse_mode_contract.py`, which runs in all three CI jobs and
drives each backend through its own event seam (skipping cleanly where a native
listener is unavailable). Windows-specific toggling lives in
`tests/windows/test_middle_click_hotkey.py`; the PowerShell host bridge is
pinned structurally in `tests/wsl/test_mouse_mode.py` (the WSL CI runner has no
`powershell.exe`). The Linux-only extras — tap-passthrough, left+right → Enter,
and suppressed X11 middle-click paste — live in
`tests/linux/test_middle_click_hotkey.py`.

The **end-to-end** tier needs the downloaded ASR model and (for the loopback
suite) a real speaker + mic, so it is intentionally **not** part of CI. See
[`docs/agent_testing_workflow.md`](docs/agent_testing_workflow.md) for the
release-quality protocol.

The [Control API](#control-api) lets a test drive a **real running instance** as
a black box (`toggle`, `status`, `output …`) instead of reaching into internals
or faking hotkeys. `tests/shared/test_control.py` pins the wire protocol, the
"a bad client cannot kill the engine" guarantees, and that every documented verb
is either handled or explicitly rejected.

### Quality gates

| Metric gate | Target SLA | Ceiling | Description |
| :--- | :--- | :--- | :--- |
| **Post-release latency** | $\le 1.00\text{ s}$ | $\le 1.50\text{ s}$ | Elapsed time from key release (`stop_recording`) to clipboard paste/typing. |
| **Accuracy match** | $\ge 90.0\%$ | $\ge 80.0\%$ | Match ratio against ground-truth benchmarks across words, phrases, and 30 s speech. |
| **Hallucination rejection** | $0$ noise tokens | $0$ noise tokens | Ambient room noise or silence must return the empty string `""`. |
| **Punctuation coverage** | $100\%$ | $100\%$ | Multi-word sentences must terminate with valid punctuation (`.`, `!`, `?`). |

The **Ceiling** column is the enforced value: `tests/e2e/test_end_to_end_crossplatform.py`
asserts `POST_RELEASE_LATENCY_BUDGET_SEC = 1.5` and `ACCURACY_FLOOR = 0.80`, so a
run that breaches the ceiling fails rather than merely regresses. The Target SLA
is the goal the implementation is tuned for.

---

## Model Weights & Offline Operation

The only backend is **Cohere Transcribe**
(`CohereLabs/cohere-transcribe-03-2026`, Apache-2.0, ~4 GB on disk / ~2.8 GB
download). Acquisition order:

1. A local copy under `models/cohere/` (repo checkout) or `VT_MODEL_DIR`.
2. A per-user install dir (`%APPDATA%\vt\models\cohere` on Windows,
   `~/.local/share/vt/models/cohere` elsewhere, or `$XDG_DATA_HOME/vt`).
3. **Auto-download of the Apache-2.0 GitHub release asset** — no Hugging Face
   account or token required.

Once the weights are on disk the app runs **fully offline** with no network
access.

### Language Support & Disclaimer

- **Primary / Officially Supported**: **English (`en`)**
- **Model Architecture**: The underlying acoustic weights (`CohereLabs/cohere-transcribe-03-2026`) support 14 languages: English (`en`), French (`fr`), German (`de`), Spanish (`es`), Italian (`it`), Portuguese (`pt`), Dutch (`nl`), Polish (`pl`), Greek (`el`), Arabic (`ar`), Japanese (`ja`), Chinese (`zh`), Vietnamese (`vi`), and Korean (`ko`).
- **English Pipeline Disclaimer**: Voice Transcriber is designed and optimized specifically for **English dictation**. The post-processor (verbal retraction parser like *"scratch that"*, filler-word removal, stutter collapse, number-to-digit conversion, homophone disambiguation, and casing) is written exclusively for English. Other languages can be specified via `VT_LANGUAGE` or `config.yaml` (`language: "<code\>"`), but English is the primary officially supported language.

> **Note for Windows testers:** `models/` is gitignored, so a fresh clone has
> no weights. First launch auto-installs them from the GitHub release asset
> (~2.8 GB down, ~4 GB on disk), which is the one slow step (a few minutes on a
> fast connection, longer on a slow one) before dictation starts.

---

## Building Distributables

| Target | Command | Output |
| :--- | :--- | :--- |
| **Linux (Nix)** | `nix build .` | `result/bin/vt` |
| **Windows (offline EXE)** | `run.bat build` (or `powershell -ExecutionPolicy Bypass -File .\platforms\windows\run.ps1 build`) | `dist/VoiceTranscriber/` (PyInstaller `--onedir`, bundles the model) |

The Windows build is self-contained (~6 GB: Python + PyTorch + the 3.9 GB Cohere
model) and needs no Python install on the target machine. See
[`platforms/windows/plan-to-compile.md`](platforms/windows/plan-to-compile.md).

> **No prebuilt Windows binary is published.** `dist/` is gitignored and the
> release workflow only ships the Linux AppImage, so the Windows EXE must be
> built on a Windows machine — PyInstaller cannot cross-compile. Once built,
> the whole `dist/VoiceTranscriber/` folder is portable: copy it to any x64
> Windows box and run `VoiceTranscriber.exe`.

> **Stale `result/` symlink:** the `result/` symlink in a checkout points at the
> last `nix build`, which may predate recent frontend changes. Re-run `nix build .`
> (and re-launch) after pulling to pick up a rebuilt `vt-tui`.

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
- `number_digits`: `auto` (default: only consecutive spoken digit chains such as phone/serial numbers become digits, while ordinals and small isolated counts read as words — `"the 5th item"` → `"the fifth item"`, `"I have 2 dogs"` → `"I have two dogs"`), `digits` (always convert), or `words` (never convert). Legacy `true`/`false` are accepted as aliases for `digits`/`words`.
- `serial_collapse`: `true` (default) writes serial numbers, model/part codes and NATO phonetic dictation as a single token (`"A B C 1 2 3"` → `ABC123`, `"Alpha Bravo 4"` → `AB4`); `false` keeps single-space separators.
- `spell_command`: `true` (default) enables the verbal spell command (`"spell C A T"` → `CAT`); `false` leaves `"spell ..."` phrases untouched.
- `keep_bluetooth_handsfree`: `true` keeps Bluetooth devices in hands-free mode so media does not pause when recording ends.
- `punctuation_mode`: `full`, `no_terminal_period`, `no_punctuation`, or `lowercase_no_punctuation` (legacy alias `semi-formal` → `no_terminal_period`).
- `enable_slm`: `true` to enable the optional local vLLM grammar-polish pass (default `false`).
- `sound_theme`: audio cue pack — `proximity` (default), `pop`, `chime`, or `silent`.
- `ui_theme`: `auto` (follow the terminal) or one of `green`, `cyan`, `blue`, `magenta`, `yellow`, `red`, `white`.
- `dictionary`: key-value map of custom phrase replacements (see `config/dictionary.yaml`).
- `dictionary_file`: path to an external YAML/JSON dictionary; takes precedence over the inline `dictionary` map.

`config/example-config/config.yaml.example` is the annotated reference for every
key, including the ones not listed above (`primary_device_name`,
`middle_click_enabled`, …). Any option can be overridden per-session with its
`VT_*` environment variable, e.g. `VT_OUTPUT_MODE=clipboard`, `VT_UI_THEME=cyan`.

Options can also be changed live from the settings modal (`s`/`S`/`,` in the
terminal), or programmatically via the [Control API](#control-api).

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

### Contextual Disambiguation Rules

Some technical terms sound identical to everyday English words (homophones). For example:
- *"push to **get tea**"* should become *"push to **Gitea**"*, but *"would you like to **get tea** with me?"* must remain untouched.
- *"connect over **you art** port"* should become *"connect over **UART** port"*, but *"are you an **art** student?"* must remain untouched.

Contextual rules solve this by requiring surrounding trigger words (before or after) and guarding against everyday English vocabulary:

```yaml
contextual_rules:
  - target: Gitea
    spoken: ["get tea", "git tea", "git ea"]
    triggers_before: ["push to", "pull from", "clone from", "commit to", "repo on", "hosted on", "our"]
    triggers_after: ["server", "instance", "repo", "repository", "remote", "url"]
    guards: ["cup of", "drink", "hot", "iced", "with", "would you like to", "order"]
```

#### Managing Contextual Rules via CLI
```bash
# Add a contextual rule
python -m src.dictionary add-contextual \
  --target "Gitea" \
  --spoken "get tea, git tea" \
  --before "push to, pull from, clone from, hosted on" \
  --after "server, repo, instance" \
  --guards "cup of, drink, iced, green, with, like to"

# List configured contextual rules
python -m src.dictionary list-contextual

# Remove a contextual rule
python -m src.dictionary remove-contextual "Gitea"
```

#### Prompting an LLM to Generate Rules
If you ask an AI assistant to add a technical term, you can simply give it examples:
> *"Whenever I say 'you art', I mean 'UART' when talking about serial ports or pins, but keep it 'art' if I talk about museums or drawing."*

The LLM can directly emit the clean YAML entry:
```yaml
  - target: UART
    spoken: ["you art", "u art"]
    triggers_after: ["port", "bus", "pins", "interface", "baud"]
    guards: ["museum", "gallery", "drawing", "class", "student"]
```

---

## License

Apache License 2.0. See [LICENSE](LICENSE). The bundled Cohere Transcribe model
is distributed under Apache-2.0 as well (see `config/licenses/`).
