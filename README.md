# Voice Transcriber

[![Release](https://img.shields.io/github/v/release/jjamesmartiin/voice-transcriber?color=blue&style=flat-square)](https://github.com/jjamesmartiin/voice-transcriber/releases/latest)
[![CI](https://img.shields.io/github/actions/workflow/status/jjamesmartiin/voice-transcriber/ci.yml?branch=main&label=CI&style=flat-square)](https://github.com/jjamesmartiin/voice-transcriber/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20Windows%20%7C%20WSL2%20%7C%20macOS-lightgrey.svg?style=flat-square)](#platform-support)

**Voice Transcriber** is a private, ultra-low-latency voice dictation engine with system-wide push-to-talk hotkeys and instant text injection. Hold a global shortcut anywhere on your desktop, speak naturally, and clean formatted text types directly into your focused window — browser, IDE, terminal, or chat.

![Voice Transcriber Demo](docs/assets/vt-demo.gif)

### Why Voice Transcriber?

- **100% Offline & Private**: Powered by local Cohere Transcribe weights (`cohere-transcribe-03-2026`). Zero API keys, zero cloud subscriptions, zero telemetry.
- **Microsecond Post-Processing (~16 µs)**: Removes verbal retractions (*"no wait, make that Wednesday"*), parses spoken ordinals/dates (*"October 20th"*), converts numbers to digits, and applies personal developer dictionaries with homophone guards (*"push to Gitea"* vs *"cup of tea"*).
- **Push-to-Talk + Hands-Free Space Latch**: Hold `Alt+Shift` (or middle mouse button) anywhere on the desktop to talk. Tap `Space` while holding to latch hands-free. `Alt+Shift` is just the default — the settings modal (or `vt hotkey add`) binds as many chords as you like.
- **Direct OS Keystroke Injection**: Types natively into any focused window across Linux (Wayland `uinput`/`ydotool`, X11), Windows (Win32 `SendInput`), and WSL2.
- **Ratatui Terminal REPL**: High-performance inline Rust TUI with live status, all-time time-saved tracking, and simultaneous multi-device microphone VU metering.

---

## ⚡ 30-Second Quickstart

| Platform | Quick Start Command | Artifact / Details |
| :--- | :--- | :--- |
| **Linux (AppImage)** | `chmod +x vt-x86_64.AppImage && ./vt-x86_64.AppImage` | [Download AppImage](https://github.com/jjamesmartiin/voice-transcriber/releases/latest) (Standalone bundle, no setup) |
| **Linux / macOS (Nix)** | `nix run github:jjamesmartiin/voice-transcriber` | Builds & runs in one command via Nix Flakes |
| **Windows (Standalone ZIP)** | Download `VoiceTranscriber-windows-x86_64.zip` → Run `VoiceTranscriber.exe` | [Download Windows ZIP](https://github.com/jjamesmartiin/voice-transcriber/releases/latest) (Self-contained Python runtime) |
| **macOS / Source (Git)** | `git clone https://github.com/jjamesmartiin/voice-transcriber.git && cd voice-transcriber`<br>`./scripts/setup.sh && ./scripts/run.sh` | Sets up venv, installs dependencies & downloads weights |

---

## 📊 Benchmark & Accuracy

Voice Transcriber is evaluated with a reproducible test suite in `eval/` across 154 speech and silence clips, scoring the real streaming pipeline with dynamic INT8 quantization on CPU:

| Dataset Slice | Sample Count (N) | Word Error Rate (WER) | Character Error Rate (CER) | Exact Match Rate | Silence Hallucination |
| :--- | ---: | ---: | ---: | ---: | ---: |
| **Clean read speech** | 44 | **2.81%** | 1.43% | 70.5% | — |
| **Noisy speech (5–20 dB SNR)** | 24 | **3.01%** | 1.24% | 70.8% | — |
| **Long-form speech (15–40s)** | 12 | **1.78%** | 0.87% | 50.0% | — |
| **Technical vocabulary** | 28 | **8.44%** | 5.46% | 50.0% | — |
| **Technical + background noise** | 12 | 8.12% | 4.52% | 50.0% | — |
| **Accented meeting speech** | 22 | 12.32% | 7.66% | 13.6% | — |
| **Overall Speech Pipeline** | **142** | **5.10%** | **2.97%** | **54.2%** | — |
| **Silence / Quiet Room Gate** | **12** | — | — | — | **0 / 12 (0.0%)** |

> **Latency Breakdown**: Streaming VAD: `0 ms` · Post-Processor: `~16 µs` · Cohere CPU Inference: `~0.15x RTF` (~0.7s on 5s audio) · End-to-End Delivery: `< 1.0 s`.  
> *Read the engineering deep dive in the [Technical Blog Post](docs/blog_post.md).*

The same 154-clip harness was used to evaluate **Parakeet TDT 0.6B v3** as an
alternative ASR backend. It is faster and much lighter, but loses every accuracy
slice, so **Cohere remains the default**; set `model_backend: parakeet` to opt
in. The measurement and verdict are in [`docs/asr-bakeoff.md`](docs/asr-bakeoff.md).

---

## 📝 Formatting Mode Presets

Voice Transcriber supports 5 instant punctuation & formatting styles (switchable via `s` in the TUI or `python src/main.py punctuation <mode>`):

| Preset | Switcher ID | Sample Output (`"Hey, how are you? I'm good."`) | Best For |
| :--- | :--- | :--- | :--- |
| **Default (Standard)** | `full` | `"Hey, how are you? I'm good."` | Professional documents, code comments, emails |
| **Casual (No Period)** | `no_terminal_period` | `"Hey, how are you? I'm good"` | Slack, Discord, chat messages without trailing period |
| **Autocorrect (Phone)** | `no_punctuation` | `"Hey how are you I'm good"` | Search queries, fast notes, terminal commands |
| **Aesthetic Lowercase** | `aesthetic_lowercase` | `"hey, how are you? i'm good"` | Aesthetic notes, lowercase prose |
| **Pure Gen Z** | `gen_z` | `"hey how are you i'm good"` | Unpunctuated casual chat |

---

## Platform Support

| Platform | Status | Hotkeys | Output injection | Audio capture | Guide | Quick run |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Linux (Wayland / X11)** | **Verified** — used daily | `evdev` + `uinput` | `wl-copy`/`xclip` (copy) · `ydotool`/`xdotool` (type) | PulseAudio / PipeWire | [Linux guide](platforms/linux/README.md) | `vt-x86_64.AppImage`, or `nix run .` |
| **Windows (native)** | Supported, unit-tested | `pynput` + `keyboard` | Win32 `SendInput` (Unicode/emoji) | WASAPI / DirectSound | [Windows guide](platforms/windows/README.md) | EXE folder, or `scripts\run.bat` from source |
| **Windows (WSL2, any distro)** | Supported, unit-tested | Windows-host bridge (PowerShell ⇄ socket IPC) | Host-side synthetic paste (`clip.exe` + `Ctrl+V`) | WSLg PulseAudio (`RDPSource`) | [WSL guide](platforms/wsl/README.md) | Double-click `scripts\run_wsl.bat` |
| **macOS (Apple Silicon / Intel)** | Supported, unit-tested | `pynput` (Accessibility) | `pbcopy` (copy) · AppleScript / Quartz (type) | CoreAudio | [macOS guide](platforms/macos/README.md) | `nix run .`, or `./scripts/setup.sh` + `./scripts/run.sh` |

All four share the same engine, ASR backend, post-processor, and config format.
Only the HAL backends differ: **hotkeys**, **clipboard/typing**,
**audio cues**, and **notifications** (`src/platform/<os>/`).

### What "verified" means here — please read this

This is a one-maintainer project, and only **Linux** has been run end-to-end on
real hardware (a daily driver, with a real microphone and real dictation).

**Windows, WSL2 and macOS are unit-tested, not end-to-end verified.** Concretely:

| | What is proven | What is *not* |
| :--- | :--- | :--- |
| **Linux** | Everything: capture, hotkeys, injection, the packaged AppImage | — |
| **Windows (native)** | The suite runs green on a `windows-latest` CI runner; the code path is exercised with fakes | Never launched on a Windows machine by the maintainer. No real global hotkey, `SendInput`, or microphone has been exercised |
| **Windows (WSL2)** | The suite runs green on an Ubuntu CI runner with `VT_PLATFORM=wsl` | The PowerShell host bridge has never executed — CI has no `powershell.exe` |
| **macOS** | The suite runs green on a `macos-latest` CI runner | Never launched on a Mac by the maintainer. No real Accessibility permission or CoreAudio device has been exercised |

That means the platform code is written, reviewed and pinned structurally, but a
first-run problem on Windows or macOS is plausible.
[`docs/TODO.md`](docs/TODO.md) lists exactly what needs confirming on each platform, with the
steps to do it — it is a real checklist, not a formality. If you hit something
there, an issue with the output of `./scripts/run.sh doctor` (or `scripts\run.bat doctor`) is the
most useful thing you can send, and it will be treated as a bug in the claim
above rather than as user error.

> **Native Windows has no ratatui frontend.** Stock CPython on Windows never
> exposes `socket.AF_UNIX` ([bpo-33408](https://bugs.python.org/issue33408)), and
> the `tui-rs` crate imports `std::os::unix`, so the Rich TUI is used there
> instead of the Rust one. The **control API** is unaffected: it falls back to
> token-authenticated loopback TCP. Linux, WSL2 and macOS are unaffected. See
> [`docs/control_api.md`](docs/control_api.md).

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
| **Optional on-device formatter** | Off by default. Needs a `llama-server` binary (from `pkgs.llama-cpp` in the dev shell, or llama.cpp's prebuilt Windows build) and ~462 MiB for the S1-mini Q4 GGUF. The first-use download prompt is implemented, but the bundle is not published yet — until it is, supply the weights locally. |
| **Optional meeting mode (diarization)** | Off by default. Needs ~40 MB for the two sherpa-onnx graphs (pyannote segmentation + 3D-Speaker). Registered and packaged, but there is no in-app download path yet — place them under the model directory. |
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
| **Shell** | PowerShell 5.1+ (or PowerShell 7+). `scripts\setup.bat` / `scripts\run.bat` bypass the execution policy for you; invoking a raw `.ps1` needs `-ExecutionPolicy Bypass`. |
| **Microphone** | Windows Settings → Privacy & security → Microphone → allow **microphone access** and **let desktop apps access your microphone**. |
| **Path length** | Keep the checkout at a short path (e.g. `C:\voice-transcriber`) or enable `LongPathsEnabled`, otherwise `pip` fails with `[WinError 206]`. `scripts\setup.bat` offers to enable it via one UAC prompt. |
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

### macOS (Darwin)

| Requirement | Detail |
| :--- | :--- |
| **Architecture** | Apple Silicon (`aarch64-darwin`) or Intel (`x86_64-darwin`). |
| **Running from source** | Python 3.10+ and PortAudio (`brew install portaudio`). |
| **Nix** | Nix with flakes enabled (`nix run .`). |
| **Audio server** | macOS CoreAudio default input microphone. |
| **Accessibility permissions** | Required for global hotkeys and synthetic typing: *System Settings → Privacy & Security → Accessibility* (enable your Terminal / app). |
| **Microphone permissions** | Allow microphone access when prompted on first recording. |
| **Hardware Performance** | Apple Silicon CPU vector acceleration (NEON / Accelerate SIMD) with dynamic INT8 quantization (~0.15x RTF). |

---

## Detailed Platform Setup & Guides

#### 🐧 Linux (Wayland / X11)

**End users: download the AppImage — no Nix, no Python, no setup.** Grab
`vt-x86_64.AppImage` from the [releases page](https://github.com/jjamesmartiin/voice-transcriber/releases),
`chmod +x` it, and run it. It bundles Python, PyTorch and the clipboard tools,
so it works on any glibc **x86_64** distro. The weights are **not** bundled: it
downloads them on first run, or reads a `model-bundle/` directory placed next to
the `.AppImage` (or set `VT_MODEL_SOURCE_DIR`) for a fully offline install.

```bash
chmod +x vt-x86_64.AppImage
./vt-x86_64.AppImage            # or --appimage-extract-and-run without FUSE 2
```

> **AppImage prerequisites (host-level, cannot be bundled):**
> - **FUSE 2:** if your distro ships only FUSE 3, install `libfuse2` or run
>   `./vt-x86_64.AppImage --appimage-extract-and-run` (equivalently
>   `APPIMAGE_EXTRACT_AND_RUN=1`).
> - **Global hotkeys:** `input` group membership and a writable `/dev/uinput`
>   (udev rule). **Typing:** `ydotool` on Wayland or `xdotool` on X11.
> - **GPU:** the bundle is **CPU-only**; CUDA needs host drivers and a
>   CUDA-enabled build (run from source / Nix).
> - **NixOS:** the bundled ALSA cannot find a config file (there is no
>   `/usr/share/alsa/alsa.conf`), so PortAudio fails to initialise. Point it at
>   the host copy — `ALSA_CONFIG_PATH=/nix/store/...-alsa-lib-*/share/alsa/alsa.conf`,
>   or just use `nix run .` on NixOS, which is the better fit anyway.
>
> Only **x86_64-linux** is built today (see `docs/TODO.md`).

**Running from a git checkout** (contributors / no AppImage):
```bash
# 1. One-time setup (venv + dependencies + model):
./scripts/setup.sh

# 2. Launch every time:
./scripts/run.sh
```
> **Note:** If you are not yet in the `input` group for global hotkeys, run `sudo usermod -aG input $USER` and log back in. Run `./scripts/run.sh doctor` to test permissions and audio devices anytime. With Nix installed, `nix run .` is the recommended path (it needs no venv).

#### 🪟 Windows (Native)
**Option A: Prebuilt Standalone EXE (no Python or build tools required)**
1. Download **`VoiceTranscriber-windows-x86_64.zip`** from [Latest Release](https://github.com/jjamesmartiin/voice-transcriber/releases/latest) and extract it anywhere.
2. Run **`VoiceTranscriber.exe`**.
3. On first launch, it will offer to download the Cohere model weights (~2.8 GB) automatically. If offline or preferred, drop the unpacked model files into `models\cohere\` next to `VoiceTranscriber.exe`.

**Option B: From Source (with local venv)**
From File Explorer or Command Prompt in the repo root:
```cmd
scripts\setup.bat     :: One-time: create .venv, install dependencies, get the model
scripts\run.bat       :: Launch (or: scripts\run.bat doctor)
```
> `scripts\setup.bat` also works offline: drop weights in `models\` and it uses them.

#### 🐧 Windows via WSL2 (any distro)
From File Explorer or Command Prompt in the repo root:
```cmd
scripts\setup_wsl.bat                :: one-time: register/configure a guest (NixOS default)
scripts\run_wsl.bat                  :: 1-click launcher (auto-picks NixOS if registered)
scripts\run_wsl.bat -Distro Ubuntu   :: use a specific WSL distribution
scripts\run_wsl.bat setup            :: install the app + model inside the guest
```
> The guest runs its own platform dispatcher, so **NixOS-WSL, Ubuntu-WSL, or any
distro with Nix** all work — it picks Nix or its native apt/venv path exactly
like bare Linux. Host hotkeys/clipboard go through the Windows PowerShell bridge.

#### 🍎 macOS (Apple Silicon / Intel)
```bash
./scripts/setup.sh    # one-time: venv + deps + model (checks brew portaudio)
./scripts/run.sh      # launch
```
> **Permissions note:** When prompted or in *System Settings → Privacy & Security*, allow **Accessibility** and **Microphone** access for your terminal app. Run `./scripts/run.sh doctor` to verify status.

---

## 🩺 System Self-Check (Doctor)

Voice Transcriber includes a built-in diagnostic tool to verify microphones, system permissions, hardware acceleration (CUDA/MPS/CPU), and model weight cache status:

```bash
# Linux / macOS:
./scripts/run.sh doctor

# Windows:
scripts\run.bat doctor
```

It also reports whether your **default microphone is muted**, which no other check can see: a muted source opens fine, reports sane channels and sample rate, and then records pure silence (Discord calls it *"no audio input detected"*). `doctor` fails loudly on it, and `--fix` unmutes it for you:

```bash
./scripts/run.sh doctor --fix     # Linux / macOS
scripts\run.bat doctor --fix      # Windows (no PipeWire: reported as unchecked, not failed)
```

---

## Controls

| Gesture | Action |
| :--- | :--- |
| **Your bound chords** (hold) — default **`Alt+Shift`** | **Push-to-Talk.** Hold while speaking; release to transcribe and paste/type into the active window. Any number of chords can be bound, including remapped and single keys (`f13`); see [Push-to-talk bindings](#push-to-talk-bindings). |
| **`Space`** (tap while holding a bound chord) | **Hands-Free Latch.** Release the keys and keep speaking; tap your chord again when finished. |
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

> **Two ways to record, one of them hands-free.** Hold a bound chord — or the
> middle mouse button — for push-to-talk anywhere on the desktop, and release to
> finish. Or tap `Space` in the terminal to start and tap it again to stop. While
> holding your chord, tapping `Space` *latches* the recording: let go of both
> keys and keep talking, then tap your chord again to finish.

### Push-to-talk bindings

`Alt+Shift` is only the shipped default. Binds are a list, edited three ways:

```bash
vt hotkey                          # list what is bound
vt hotkey add ctrl+shift           # add a chord (as many as you like)
vt hotkey add f13                  # a single key works too
vt hotkey remove alt+shift         # drop one
vt hotkey reset                    # back to Alt+Shift
```

…or with `a` / `d` inside **Settings → Push-to-Talk Keys**, or by hand:

```yaml
hotkeys:
  - keys: [alt, shift]
    action: dictate
  - keys: [f13]
    action: dictate
```

Key names are OS-neutral. **`vt hotkey keys`** is the authoritative list (it is
served straight from the one table the backends resolve through, so it cannot
drift), grouped and with aliases:

```bash
vt hotkey keys            # human-readable
vt hotkey keys --json     # [{name, group, aliases}, ...]
```

In short: modifiers (`ctrl` / `leftctrl` / `rightctrl`, `alt`, `shift`, `meta` —
also spelled `super` / `win` / `cmd`), `f1`–`f24`, letters, digits,
`space`/`tab`/`esc`/`enter`, `backspace`/`delete`/`insert`, the arrows plus
`home`/`end`/`pageup`/`pagedown`, and `capslock`/`printscreen`/`scrolllock`/
`pause`/`menu`. A bare modifier matches **either** side; `rightctrl+shift`
matches only the right one. Aliases resolve to the same chord, so `option+shift`
and `alt+shift` are one bind, not two.

Chords are evaluated against the *remapped* key stream, so a keyboard remapper
(`kanata`, `input-remapper`) can be the thing that produces your chord — hold
`s`+`f` and bind `alt+shift` to match what kanata emits, not the physical keys
underneath it.

The middle mouse button is deliberately *not* bindable: it already has its own
toggle with tap-vs-hold semantics.

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

> **Windows works too, over loopback TCP.** Stock CPython on Windows never exposes
> `socket.AF_UNIX` (bpo-33408), so the engine falls back to a token-authenticated
> socket on `127.0.0.1` and publishes its port and token to a per-user file — the
> verbs are the same. The **ratatui frontend** is still Unix-only (the Rust crate
> imports `std::os::unix`), so native Windows uses the Rich TUI. See
> [`docs/control_api.md`](docs/control_api.md#transport).

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
| **Devices** | `mics`, `set-mic`, `rescan-mics` |
| **Settings** | `output`, `numbers`, `punctuation`, `theme`, `trailing-space`, `auto-punctuate`, `serial`, `spell`, `middle-click`, `mute` |
| **Formatting** | `structure`, `cleanup`, `formatter`, `formatter-style`, `formatter-context`, `formatter-model` |
| **Meeting** | `meeting`, `meeting-start`, `meeting-stop`, `meeting-spill`, `meeting-format`, `meeting-output`, `speakers`, `diarization`, `diarization-speakers`, `diarization-model` |
| **Modals** | `settings`, `mic` (interactive — take over the terminal) |
| **Lifecycle** | `reset-defaults`, `reset-terminal`, `ping`, `doctor`, `help`, `quit` |

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

![Voice Transcriber ratatui terminal UI](docs/assets/vt-tui-demo.svg)

<sub>The ratatui frontend: inline scrollback, a live status line, and a timing
divider per transcription.</sub>

Both frontends share the same visual layout and keyboard model — `s`/`S`/`,`
for settings, `Space`/`Enter` to record — with the microphone and theme
sub-pickers reached from inside the settings modal:
- **Interactive Modal Pickers**: `⚙️ Settings & Configuration`, `🎤 Microphone Input Device`, and `🎨 Select UI Color Theme` overlays with real-time search filtering, arrow/Tab navigation, and in-place toggling. The microphone picker shows a **live level for every visible device at once** — one capture stream per row — so you can see which mic is actually hearing you instead of selecting one and hoping. Streams follow the visible rows, so the search box also narrows what is monitored.
- **Inline CLI Prompt Stream**: Responsive status prompt line with active mic, model, sound, output mode, trailing space, punctuation, numbers, and mouse hold badges.
- **Clean Word-Wrapped Transcriptions**: Direct terminal scrollback with timing metadata dividers and zero border interference for 100% clean copy-paste.
- **Persistent Time-Saved Counter**: every transcription divider carries a `⚡ saved: +14s (session: 2m 15s · total: 1h 20m)` badge — the time dictation saved versus typing, the running session total, and an **all-time total that survives restarts**. The estimate uses the `typing_wpm` setting.
- **Encoding-safe output**: on a console or pipe that cannot encode the emoji and box-drawing glyphs (a legacy Windows code page, `LANG=C`, `PYTHONIOENCODING=ascii`), they degrade to ASCII stand-ins — `⚡`→`*`, `│`→`|`, `·`→`|` — instead of raising `UnicodeEncodeError` and losing the divider. Nothing is ever dropped silently. Set `VT_ASCII=1` to force the ASCII rendering everywhere.

![Settings and configuration modal](docs/assets/vt-settings-modal.svg)

<sub>Settings &amp; configuration — the single configuration entry point, with
fuzzy filtering, in-place toggle badges, and sub-pickers for the microphone,
theme and push-to-talk keys.</sub>

---

## Architecture

Voice Transcriber unifies all supported platforms over a single shared core
engine behind a Hardware/OS Abstraction Layer (HAL). The core owns the audio
pipeline, the ASR backend, the post-processor and the frontends; `hal.py` is
the loader and `src/platform/` holds the four per-OS backends.

![Voice Transcriber system architecture](docs/assets/vt-architecture.svg)

<sub>The pipeline end to end — audio capture and VAD, the ASR backend, the
post-processor, the frontends, and the HAL that maps each per-OS concern onto
Linux, Windows, WSL2 and macOS.</sub>

Platform detection lives in `src/platform/__init__.py`; override it with
`VT_PLATFORM=linux|windows|wsl|macos` (an unknown value is a hard error by design).

> **Planning a port to another language?** Read
> [`docs/archive/java-fork-plan.md`](docs/archive/java-fork-plan.md). It maps what is reusable
> as-is (the `vt-tui` protocol, the control API, the revision-keyed weights
> bundle) and why the ASR re-host, not the port, is the critical path.

---

## Testing & CI

GitHub Actions runs a four-job matrix on every push and PR
(`.github/workflows/ci.yml`). Each job runs the **shared** tier plus its own
platform tier:

| CI job | Runner | Command |
| :--- | :--- | :--- |
| **Unit tests (Linux)** | `ubuntu-latest` | `pytest tests/shared tests/linux` (Nix dev shell) |
| **Unit tests (macOS)** | `macos-latest` | `pytest tests/shared tests/macos` |
| **Unit tests (Windows)** | `windows-latest` | `pytest tests/shared tests/windows` (no PyTorch needed) |
| **Unit tests (WSL)** | `ubuntu-latest` | `pytest tests/shared tests/wsl` (Nix, `VT_PLATFORM=wsl`) |

The Linux job also lints (`ruff check`, `shellcheck`) and builds the Rust
frontend (`nix build .#vt-tui`, which runs the crate's `cargo test`).

### Test tiers

| Tier | Directory | Needs model? | Runs where |
| :--- | :--- | :--- | :--- |
| **Shared** | `tests/shared/` | No | Every OS, every push |
| **Platform** | `tests/linux/`, `tests/macos/`, `tests/windows/`, `tests/wsl/` | No | That OS only, every push |
| **End-to-end** | `tests/e2e/` | Yes | Local only (never CI) |

### Running the tests locally

One command per tier, from the repo root:

| Tier | Linux / WSL | Windows |
| :--- | :--- | :--- |
| **All (shared + platform)** | `./scripts/test.sh` | `.\scripts\test.ps1` |
| **Shared only** | `./scripts/test.sh shared` | `.\scripts\test.ps1 shared` |
| **Platform only** | `./scripts/test.sh platform` | `.\scripts\test.ps1 windows` |
| **End-to-end (model)** | `./scripts/test.sh e2e` | `.\scripts\test.ps1 e2e` |

`./scripts/test.sh` auto-detects Linux vs WSL and runs the matching platform tier. Both
scripts forward extra args to pytest, e.g. `.\scripts\test.ps1 shared -k tui -v`. On
Windows the same thing is `scripts\test.bat` (or
`powershell -ExecutionPolicy Bypass -File .\platforms\windows\test.ps1`).
`scripts\setup.bat` installs pytest (from `platforms\windows\requirements-dev.txt`), and
`scripts\test.bat` / `scripts\test.ps1` install it on demand if it is missing.

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

Two optional models are registered bundles:

* **On-device formatter** - `"S1-mini" by "Superwhisper"`, a ~462 MiB Q4 GGUF
  (`superwhisper/s1-mini-GGUF`) run by a local `llama-server`. Enabling the
  formatter states that size and asks before anything downloads. Licensed
  Apache-2.0 **plus an additional naming term**; see
  [`config/licenses/S1-mini-Apache-2.0.txt`](config/licenses/S1-mini-Apache-2.0.txt).
* **Meeting-mode diarization** - the ~40 MB pyannote segmentation + 3D-Speaker
  graphs (`k2-fsa/sherpa-onnx`, MIT + Apache-2.0); see
  [`config/licenses/`](config/licenses/).

The bundles are produced by the release tooling, but **none has been published
yet**: the formatter's first-use download prompt cannot fetch an unpublished
asset, and diarization has no in-app download path at all. Until they are
published, place the weights under the model directory (`VT_MODEL_DIR`, or the
per-user install dir). The app degrades to unformatted text / a single speaker
rather than failing when they are absent. Every install writes a `SOURCE.json`
next to the weights recording the upstream repo, revision and sha256.

### Language Support & Disclaimer

- **Primary / Officially Supported**: **English (`en`)**
- **Model Architecture**: The underlying acoustic weights (`CohereLabs/cohere-transcribe-03-2026`) support 14 languages: English (`en`), French (`fr`), German (`de`), Spanish (`es`), Italian (`it`), Portuguese (`pt`), Dutch (`nl`), Polish (`pl`), Greek (`el`), Arabic (`ar`), Japanese (`ja`), Chinese (`zh`), Vietnamese (`vi`), and Korean (`ko`).
- **English Pipeline Disclaimer**: Voice Transcriber is designed and optimized specifically for **English dictation**. The post-processor (verbal retraction parser like *"scratch that"*, filler-word removal, stutter collapse, number-to-digit conversion, homophone disambiguation, and casing) is written exclusively for English. Other languages can be specified via `VT_LANGUAGE` or `config.yaml` (`language: "<code\>"`), but English is the primary officially supported language.

> **Note for Windows testers:** `models/` is gitignored, so a fresh clone has
> no weights. First launch auto-installs them from the GitHub release asset
> (~2.8 GB down, ~4 GB on disk), which is the one slow step (a few minutes on a
> fast connection, longer on a slow one) before dictation starts.

---

## Meeting Mode (diarization)

Meeting mode is a long, **non-injecting** capture: it records a meeting,
transcribes it in the background when you stop, and writes a transcript document
instead of typing into the focused window. It is **off by default**; with
`meeting: off` the dictation path is byte-identical to a build without the
feature.

| Setting | Values | Default |
| :--- | :--- | :--- |
| `meeting` | `off`, `on` | `off` |
| `meeting_spill_minutes` | 1–240 | `10` |
| `meeting_output_dir` | path | `meetings` (repo-local, gitignored) |
| `meeting_output_format` | `text`, `json`, `markdown` | `text` |
| `diarization` | `off`, `on` | `off` |
| `diarization_speakers` | `auto`, `2`–`8` | `auto` |
| `diarization_model` | registry name | `diarization` |

Start and stop a capture with `python src/main.py meeting-start` / `meeting-stop`;
`meeting-format`, `meeting-output` and `meeting-spill` set the rest. With
`diarization: on`, the pass labels each turn with its speaker (`Speaker 1`,
`Speaker 2`, …); `off` never loads the model and produces a single-speaker
transcript. `diarization_speakers` is only a hint. All three diarization keys are
on the settings modal in both Python frontends and in the config file.

**Speaker names** are transcript metadata, not configuration: they are an ordered
list you edit live on the meeting screen, or set without a TUI with
`python src/main.py speakers "Alex, Priya, Sam"` (blank slots and speakers past
the end of the list fall back to the readable `Speaker N` label). The map is read
when the document is written, so a rename applies to the finished transcript;
`status` reports it as `speakers`.

Meeting transcripts land in a repo-local, gitignored `<repo>/meetings/` by
default; an absolute `meeting_output_dir` is used as-is, and
`VT_MEETING_OUTPUT_DIR` always wins. See
[`docs/meeting_mode.md`](docs/meeting_mode.md).

> **Diarization needs the two sherpa-onnx graphs**, which are a registered
> bundle but have no in-app download path yet — place them under the model
> directory (see [Model Weights](#model-weights--offline-operation)). Without
> them, `diarization: on` degrades to one speaker rather than failing.

---

## Building Distributables

| Target | Command | Output |
| :--- | :--- | :--- |
| **Linux (Nix)** | `nix build .` (or `./scripts/build.sh`) | `result/bin/vt` |
| **Windows (offline EXE)** | `scripts\build.bat` (or `powershell -ExecutionPolicy Bypass -File .\platforms\windows\build.ps1`) | `dist/VoiceTranscriber/` (PyInstaller `--onedir`, bundles the model) |
| **Windows (model-free)** | `scripts\build.bat --no-model` | `dist/VoiceTranscriber/` (~1.1 GB; weights installed on first run) |

The Windows build is self-contained (~6 GB: Python + PyTorch + the 3.9 GB Cohere
model) and needs no Python install on the target machine. See
[`platforms/windows/plan-to-compile.md`](platforms/windows/plan-to-compile.md).

> **Airgapped / offline installs (any OS).** The app and the weights ship
> separately: a small model-free app bundle per OS, plus **one OS-agnostic split
> model bundle**. The app verifies and assembles the weights itself (`urllib` +
> `lzma` + `tarfile`, all bundled) — no external `xz`/`7z`/`unzip`, no Python on
> the target. Put the bundle in a `model-bundle/` directory next to the
> executable, or point `VT_MODEL_SOURCE_DIR` at it. See
> [`docs/offline_install.md`](docs/offline_install.md).

> **Prebuilt Windows binary:** `VoiceTranscriber-windows-x86_64.zip` is attached
> to every release on GitHub. It contains a self-contained Python runtime, PyTorch,
> and `VoiceTranscriber.exe`. Model weights are either auto-downloaded on first run
> or loaded from a `models\cohere\` folder placed next to the executable. You can
> also build a standalone bundle locally on Windows using `scripts\build.bat` (or
> `scripts\build.bat --no-model`). Once built, the whole `dist/VoiceTranscriber/` folder is
> portable to any compatible x64 Windows machine.

> **Stale `result/` symlink:** the `result/` symlink in a checkout points at the
> last `nix build`, which may predate recent frontend changes. Re-run `nix build .`
> (and re-launch) after pulling to pick up a rebuilt `vt-tui`.

> **Cutting a release?** See [`docs/releasing.md`](docs/releasing.md). CI builds
> and attaches the AppImage. The model weights are published **separately**, to
> a revision-keyed bundle tag, and only when the model revision actually
> changes — never per release. A new version reuses the existing bundle and
> uploads nothing; only a `REVISION` bump needs the publish step:
>
> ```bash
> nix develop --command ./scripts/publish_model_bundle.sh --dry-run  # show the plan
> nix develop --command ./scripts/publish_model_bundle.sh --build    # ~2.9 GB, once per revision
> ```

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
- `number_digits`: `auto` (default: only consecutive spoken digit chains such as phone/serial numbers become digits, while ordinals and small isolated counts read as words — `"the 5th item"` → `"the fifth item"`, `"I have 2 dogs"` → `"I have two dogs"`), `digits` (always convert), or `words` (never convert). Legacy `true`/`false` are accepted as aliases for `digits`/`words`. Spoken dates are the one exception in `auto`/`digits`: the day is always written as an ordinal numeral (`"October twentieth"` → `"October 20th"`, `"the twentieth of October"` → `"the 20th of October"`, `"October twentieth twenty twenty five"` → `"October 20th, 2025"`); `words` mode leaves them spelled out.
- `serial_collapse`: `true` (default) writes serial numbers, model/part codes and NATO phonetic dictation as a single token (`"A B C 1 2 3"` → `ABC123`, `"Alpha Bravo 4"` → `AB4`); `false` keeps single-space separators. Prose is never touched: a quantity followed by the indefinite article (`"170 a month"`, `"20 a year"`) stays as spoken, so only trigger-anchored or unambiguous runs collapse.
- `spell_command`: `true` (default) enables the verbal spell command (`"spell C A T"` → `CAT`); `false` leaves `"spell ..."` phrases untouched.
- `keep_bluetooth_handsfree`: `true` keeps Bluetooth devices in hands-free mode so media does not pause when recording ends.
- `punctuation_mode`: `full`, `no_terminal_period`, `no_punctuation`, or `lowercase_no_punctuation` (legacy alias `semi-formal` → `no_terminal_period`).
- `structure_mode`: spoken-list formatting — `off` (default), `inline` (markers only: `"- one. - two."`), or `blocks` (real bullets and paragraph breaks). Typed output is automatically downgraded to `inline`, because a newline is an Enter keypress in whatever window has focus; `blocks` is for the clipboard/paste path. See [`docs/formatting.md`](docs/formatting.md).
- `cleanup_mode`: how much the post-processing pass may change the words — `off` (keeps every word, deletes nothing), `artifacts` (removes noise: silence hallucinations, filler, stutters, trailing mutterings — but leaves your corrections verbatim), or `full` (default: also resolves self-corrections such as `"Tuesday no sorry Wednesday"` → `"Wednesday"`). Number/date formatting, the dictionary, casing and the punctuation preset are separate settings and are not gated by it. See [`docs/cleanup_modes.md`](docs/cleanup_modes.md).
- `enable_slm`: `true` to enable the optional local vLLM grammar-polish pass (default `false`).
- `sound_theme`: audio cue pack — `proximity` (default), `pop`, `chime`, or `silent`.
- `ui_theme`: `auto` (follow the terminal) or one of `green`, `cyan`, `blue`, `magenta`, `yellow`, `red`, `white`.
- `typing_wpm`: words-per-minute typing baseline used for the `⚡ saved` estimate (default `40`; preset cycling lives in the `wpm` control verb).
- `dictionary`: key-value map of custom phrase replacements (see `config/dictionary.yaml`).
- `dictionary_file`: path to an external YAML/JSON dictionary; takes precedence over the inline `dictionary` map.

`config/example-config/config.yaml.example` is the annotated reference for every
key, including the ones not listed above (`primary_device_name`,
`middle_click_enabled`, …). Any option can be overridden per-session with its
`VT_*` environment variable, e.g. `VT_OUTPUT_MODE=clipboard`, `VT_UI_THEME=cyan`.

> **Not everything is config.** The all-time time-saved totals live in
> `stats.json` in the platform data directory (`$XDG_DATA_HOME/vt/`,
> `~/.local/share/vt/`, or `%LOCALAPPDATA%\vt\`), **not** in `config.yaml` —
> `reset-defaults` and config edits never touch them. The file (and its
> directory) is created automatically on the first launch, and `stats.json` is
> gitignored wherever it lands. Point `VT_STATS_FILE` somewhere else, or delete
> the file to start counting from zero. Read the totals with
> `python src/main.py status` on any platform.

Options can also be changed live from the settings modal (`s`/`S`/`,` in the
terminal), or programmatically via the [Control API](#control-api).

---

## Custom Word & Phrase Dictionary

Voice Transcriber includes a high-speed Trie-compacted custom dictionary replacer (~0.005 ms execution time) that matches spoken words and technical phrases case-insensitively, respects word boundaries, and applies exact replacement casing and formatting.

### Two-Tier Dictionary Architecture

Voice Transcriber automatically layers two dictionaries at runtime with zero latency penalty:

1. **Common Dictionary (`config/dictionary.yaml`)**:
   - **Committed to git**.
   - Shipped with the repository for shared developer tools, cloud infrastructure, and open-source packages (`kubectl`, `NixOS`, `systemctl`, `GitHub`, `0xDEADBEEF`, `UART`, etc.).
2. **Personal / Local Dictionary (`config/dictionary.local.yaml`)**:
   - **Gitignored** (`.gitignore`).
   - Private to your machine. Used for personal system hostnames (`home-jamesm5`), family/coworker names, client names, private project codenames, or personal addresses.
   - Simply copy `config/dictionary.local.yaml` to transfer your personal vocabulary between computers!

Local definitions take precedence over common definitions if there is an overlap.

### Quick-Add via CLI

You can add, list, remove, or test dictionary entries directly from the terminal without manually editing YAML files:

```bash
# Add to your personal dictionary (default, gitignored)
python src/dictionary.py add "home dash james m5" "home-jamesm5"
python src/dictionary.py add "john doe" "John Doe"

# Add to the common dictionary (tracked in git)
python src/dictionary.py add --common "cube ctl" "kubectl"
python src/dictionary.py add --common "deep seq" "Deepseek"

# Test how a sentence is transformed by the combined dictionaries
python src/dictionary.py test "i deployed on home dash james m5 using cube ctl"
# Output: "I deployed on home-jamesm5 using kubectl."

# List current dictionary entries (shows [common] vs [local] tags)
python src/dictionary.py list
python src/dictionary.py list james
python src/dictionary.py list --local

# Check dictionary file paths and status
python src/dictionary.py path

# Remove an entry
python src/dictionary.py remove "home dash james m5"
```

### Manual YAML Editing

You can edit `config/dictionary.local.yaml` (personal) or `config/dictionary.yaml` (common) directly:

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
python src/dictionary.py add-contextual \
  --target "Gitea" \
  --spoken "get tea, git tea" \
  --before "push to, pull from, clone from, hosted on" \
  --after "server, repo, instance" \
  --guards "cup of, drink, iced, green, with, like to"

# List configured contextual rules
python src/dictionary.py list-contextual

# Remove a contextual rule
python src/dictionary.py remove-contextual "Gitea"
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

MIT License. See [LICENSE](LICENSE). The bundled Cohere Transcribe model is
distributed under Apache-2.0. The optional on-device formatter model,
"S1-mini" by "Superwhisper", is Apache-2.0 plus an additional naming term, and
the diarization graphs are MIT (segmentation) and Apache-2.0 (embedding); full
texts and provenance are in [`config/licenses/`](config/licenses/).
