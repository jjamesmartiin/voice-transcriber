# Voice Transcriber - Native Windows Guide

Run Voice Transcriber natively on Windows with global hotkeys, Windows audio cues, and active-window clipboard pasting.

---

## Quick Start

There are **two ways to run Voice Transcriber on Windows**: from source (below),
or from the prebuilt self-contained EXE (see
[Building a Standalone Offline EXE](#building-a-standalone-offline-exe)). Running
from source needs Python and a one-time `setup.bat`; the EXE needs neither.

### 1. Prerequisites
- Python 3.10+ installed on Windows (from https://www.python.org/downloads/ with "Add python.exe to PATH" checked).
- PowerShell 5.1+ or PowerShell 7+ (or Command Prompt).

> If Python is missing, `setup.bat` offers to install Python 3.13 for you with
> `winget` (included with Windows 10/11) before it continues.

### 2. One-Click Setup & Launch (Recommended)
From the repository root, you can simply run the batch launchers (by double-clicking them in File Explorer, or running from Command Prompt / PowerShell):
```cmd
# Run setup (creates venv and installs dependencies):
setup.bat

# Launch Voice Transcriber:
run.bat
```

> **Note:** The `.bat` launchers automatically bypass PowerShell's script execution policy (`-ExecutionPolicy Bypass`), so you will not run into digital signature or `PSSecurityException` errors on downloaded scripts.

> **Setup and run are separate.** `setup.bat` prepares the machine (Python,
> `.venv`, dependencies, and the model). `run.bat` only launches — it never
> installs anything. `test.bat` / `build.bat` / `clean.bat` are their own entry
> points.

### 2a. The five entry points

```cmd
setup.bat                  :: prepare: venv + deps + model (drop weights in models\)
setup.bat --no-dev         :: skip pytest / PyInstaller (faster)
setup.bat --no-model       :: do not acquire the model (weights placed later)

run.bat                    :: launch the app
run.bat doctor             :: forward a control verb (start/stop/status/doctor/...)
run.bat --model-dir D:\vt\models\cohere   :: load weights from another location
run.bat --venv C:\py\vt-venv               :: use a venv outside the repo
run.bat --no-model                         :: do not auto-download the model

test.bat                   :: environment check + shared & Windows test tiers
test.bat verify            :: environment check only (test == verify)
test.bat shared -k tui -v  :: one tier (all|shared|windows|platform|e2e|model)

build.bat                  :: build the standalone EXE (weights included)
build.bat --no-model       :: build the ~1 GB model-free bundle

clean.bat                  :: remove .venv / build / dist / caches
clean.bat --models --yes   :: ...plus the downloaded weights, no prompt
```

`--help` works on every script (`setup.bat --help`, `run.bat --help`, ...).

**Where the model lives.** Drop weights in `<repo>\models\`:

- `models\cohere\` — an unpacked copy, used as-is; or
- a split bundle in `models\` (`*.partN.xz` + `SHA256SUMS`, or a `.zip`/`.tar`)
  — assembled offline by the app's own installer, no extra tools.

`setup.bat` checks that folder first, assembles a bundle if present, and only
downloads (~2.8 GB, one time) if neither is there. `test.bat verify` reports
whether a complete model is present without changing anything. To relocate an
existing copy instead, pass `run.bat --model-dir <path>` (or set `VT_MODEL_DIR`).
See [`docs/offline_install.md`](../../docs/offline_install.md).

**Dev tooling** — if pytest / PyInstaller are already in the venv, `test.bat` and
`build.bat` use them and install nothing.

**When something goes wrong.** Output is also written to a per-user log at
`%LOCALAPPDATA%\vt\vt.log` — override the path with `VT_LOG_FILE`, raise the
detail with `VT_LOG_LEVEL=INFO`. A `run.bat` started by double-click keeps its
window open on error so the message is readable.

**Legacy code pages.** When the console (or a redirected pipe/file) cannot encode
the UI's emoji and box-drawing glyphs, they fall back to ASCII automatically —
`⚡ saved: +12s` becomes `* saved: +12s`, `│` becomes `|`. Nothing raises and no
output is lost. Set `VT_ASCII=1` to force that ASCII rendering even on a UTF-8
console.

### 3. Setup via PowerShell
If you prefer running via PowerShell directly, pass `-ExecutionPolicy Bypass` (since Windows blocks downloaded `.ps1` scripts by default):
```powershell
# Run the automated setup script:
powershell -ExecutionPolicy Bypass -File .\platforms\windows\setup.ps1

# Launch the application:
powershell -ExecutionPolicy Bypass -File .\platforms\windows\run.ps1
```

*(Tip: Alternatively, you can run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` once in your PowerShell session to allow running `.\platforms\windows\setup.ps1` and `.\platforms\windows\run.ps1` directly.)*

### 4. Manual Setup (Alternative)
If setting up manually from the repository root:
```powershell
# Create virtual environment
python -m venv .venv

# Install dependencies (using python -m pip ensures pip runs even if not activated)
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r platforms\windows\requirements.txt

# Launch:
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe src\main.py
```

---

## Controls

- **Alt + Shift (hold)**: Push-to-Talk — hold while speaking, release to transcribe and paste/type to the active window.
- **Space (tap while holding Alt + Shift)**: Hands-free recording — release keys and keep talking; tap `Alt + Shift` when finished to transcribe.
- **Middle-click (hold ~0.25 s)**: Mouse Push-to-Talk. Releasing before the hold delay leaves it a normal middle click.
- **Ctrl (held at release)**: Force clipboard output for this utterance even when auto-type is enabled.
- **Space** / **Enter** (in the terminal): Start/stop recording — hands-free once started; tap again to stop.
- **s**, **S**, or **,** (in the terminal): Open the interactive Settings modal (`⚙️ Settings & Configuration`) with real-time fuzzy filter, in-place toggle badges, and sub-pickers.
- **r** (in the terminal): Reset the terminal and clipboard bridge.
- **q** / **Esc** / **Ctrl+C** (in the terminal): Quit.

The Settings modal is the **single entry point** for configuration. There is no
global hotkey for it — a system-wide settings shortcut would fire while you were
typing in another application — so opening it is a terminal action. The
microphone device picker, the mode preset switcher and the UI color theme picker
all live inside it as sub-pickers; the one-off `g` / `t` / `M` shortcuts are gone.

To change settings from outside the terminal, use the control API:
```cmd
.venv\Scripts\python.exe src\main.py status
.venv\Scripts\python.exe src\main.py mute
```
See [Control API](../README.md#control-api).

---

## How it Works on Native Windows

- **Hardware/OS Abstraction Layer (HAL)**: Auto-detects Windows host and loads `src/platform/windows/`.
- **Global Hotkeys**: Uses `pynput` and `keyboard` libraries to capture system-wide keystrokes even when Voice Transcriber is minimized or in the background.
- **Audio Capture**: Captures 16kHz audio from your default Windows microphone via `sounddevice` (WASAPI/DirectSound).
- **Instant Capture Start**: Keeps the WASAPI input stream constructed (stopped between recordings) so push-to-talk doesn't drop the first syllable while the device opens. Set `VT_WARM_MIC=0` to disable (opens the device fresh on each recording).
- **Audio Feedback**: Plays native Windows chimes (e.g. `Windows Proximity Notification.wav` or `Speech On/Off.wav`) via `winsound`.
- **Auto-Type with Per-App Fallback**: Injects real virtual-key keystrokes into the focused window (Unicode/emoji via `KEYEVENTF_UNICODE`). Fast mode auto-drops to slow pacing for classic controls (Notepad, RichEdit, `Edit`) that garble rapid injection — terminals and modern apps stay instant. Override per window class with `VT_TYPE_FAST_CLASSES` / `VT_TYPE_SLOW_CLASSES` (comma-separated).
- **AI Processing**: Runs the high-accuracy Cohere Transcribe model with streaming VAD, dynamic energy gating, and post-processing (Trie dictionary, formatting, number conversion).

---

## Automated Testing on Windows

From the repo root, one command per tier:
```powershell
.\test.ps1              # shared + Windows tiers (all)
.\test.ps1 shared       # cross-platform, model-free
.\test.ps1 windows      # Windows-specific
.\test.ps1 e2e          # model/audio end-to-end (local only)
```

The test runner is a separate entry point:
```cmd
test.bat            :: environment check + shared & Windows tiers
test.bat verify     :: environment check only (test == verify)
```
or via PowerShell:
```powershell
.\test.ps1
powershell -ExecutionPolicy Bypass -File .\platforms\windows\test.ps1
```

> The shared/platform tiers are PyTorch-free, so they run on a bare Python
> install. The end-to-end tier (`tests/e2e/`) needs the model weights and is run
> separately with `.\test.ps1 e2e`.

> **pytest is installed by `setup.bat`** (via
> `platforms\windows\requirements-dev.txt`), so `.\test.ps1` works on a fresh
> clone with no manual `pip install`. `test.bat` (and `test.ps1`) install the
> test tooling on demand if it is missing.

---

## Building a Standalone Offline EXE

To package Voice Transcriber into a self-contained `.exe`:
```cmd
build.bat
```
or via PowerShell:
```powershell
powershell -ExecutionPolicy Bypass -File .\platforms\windows\build.ps1
```
or directly with Python:
```powershell
python platforms\windows\build_offline.py
```
This produces an offline distribution in `dist/` that requires no Python installation on the target Windows machine.

> PyInstaller is installed by `setup.bat`; if it is missing, `build.bat`
> installs it from `platforms\windows\requirements-dev.txt` before building.

### Model-free build (airgapped / small)

To produce a ~1.1 GB bundle that installs the weights on first run instead of
bundling them:

```cmd
build.bat --no-model
```

On first run the app installs the Cohere weights either from the release assets
(online) or from an offline split bundle. For an airgapped host, copy the split
bundle next to the executable as `model-bundle\` (a directory of
`cohere-transcribe-<rev>.partN.xz` + `SHA256SUMS`, or a single `.zip`), or point
`VT_MODEL_SOURCE_DIR` at it. The app verifies and assembles the parts with its
own bundled `lzma`/`tarfile` — no `7z`, `xz`, or `unzip` needed. See
[`docs/offline_install.md`](../../docs/offline_install.md).

---

## Troubleshooting

### 1. Script Cannot Be Loaded (`PSSecurityException` / `UnauthorizedAccess`)
**Symptom:**
```text
setup.ps1 cannot be loaded. The file is not digitally signed. You cannot run this script on the current system.
```
**Cause:** Windows restricts running downloaded `.ps1` scripts by default ("Mark of the Web").  
**Fix:**
- Use the batch file: **`setup.bat`** (or **`run.bat`**). It automatically bypasses the execution policy for that run.
- Or pass `-ExecutionPolicy Bypass` in PowerShell:
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\platforms\windows\setup.ps1
  ```
- Or unblock scripts for your current PowerShell terminal session:
  ```powershell
  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
  ```

### 2. Path Too Long (`[WinError 206] The filename or extension is too long`)
**Symptom:**
```text
ERROR: Could not install packages due to an OSError: [WinError 206] The filename or extension is too long: '...\.venv\Lib\site-packages\...'
```
**Cause:** Windows enforces a legacy 260-character maximum path limit (`MAX_PATH`) by default. Downloading a GitHub zip extracts by default into deeply nested directories (e.g. `Downloads\voice-transcriber-<hash>\voice-transcriber-<hash>\`), which exceeds 260 characters when `pip` installs package dependencies.  
**Fix:**
- **Automated (Zero manual typing):** Run **`setup.bat`** and click **Yes** when Windows prompts for permission to enable long path support. `setup.bat` will enable `LongPathsEnabled` and resume setup automatically.
- **Alternative:** Move or rename the extracted project folder to a shorter path (e.g. `C:\voice-transcriber`), or enable long paths manually in an Administrator PowerShell window:
  ```powershell
  New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force
  ```

### 3. The Window Closes Immediately (App Fails to Start)
**Symptom:** Double-clicking `run.bat` flashes a console and nothing else happens, or the app quits right after starting.
**Cause:** The launcher exits non-zero before or during launch (missing Python, a pip failure, or a startup crash). A double-clicked `run.bat` now keeps its window open on error, but the full detail is in the log.
**Fix:**
- Read the log: `%LOCALAPPDATA%\vt\vt.log` (override with `VT_LOG_FILE`, add detail with `VT_LOG_LEVEL=INFO`).
- Run `test.bat verify` for a status report, or run `run.bat` from an already-open Command Prompt so the output stays visible.

### 4. Model Download Is Slow, or You Want to Skip It
**Symptom:** The first launch seems to hang while the ~2.8 GB Cohere model downloads.
**Fix:**
- `setup.bat` acquires the model on its own: it checks `models\`, assembles a split bundle if one is present, and only downloads as a last resort.
- `test.bat verify` reports whether a complete model is already present. Drop one into `<repo>\models\cohere` (or a split bundle into `models\`), or pass `run.bat --model-dir <path>`.
- `run.bat --no-model` launches without downloading one (useful only if a model is already installed).

### 5. Microphone Captures Silence While in Discord or Communication Apps
**Symptom:** Voice Transcriber records silence when Discord, a game, or a browser call is open.  
**Cause:** Windows allows apps to claim "Exclusive Mode" on audio devices, blocking other apps from capturing audio simultaneously.  
**Fix:**
1. Press `Win + R`, type `mmsys.cpl` and press Enter to open Sound Control Panel.
2. Go to the **Recording** tab, right-click your microphone, and select **Properties**.
3. Go to the **Advanced** tab.
4. **Uncheck** *"Allow applications to take exclusive control of this device"*.
5. Click **Apply** and **OK**. Both Discord and Voice Transcriber can now share the microphone stream.

### 6. Emoji or Box Characters Appear as ASCII (`*`, `|`) or Question Marks
**Symptom:** The transcript divider shows `* saved: +12s` instead of `⚡ saved: +12s`, or stray `?` characters appear.  
**Cause:** The output stream cannot encode those glyphs — a legacy console code page (e.g. a redirected `run.bat > log.txt`), or `PYTHONIOENCODING=ascii` in the environment. The app downgrades them on purpose: Rich otherwise raises `UnicodeEncodeError` and the divider disappears entirely.  
**Fix:**
1. This is safe and lossless — every glyph has an ASCII stand-in.  
2. For the full glyph set, use Windows Terminal (UTF-8 by default), or set `PYTHONIOENCODING=utf-8`.  
3. To force ASCII everywhere *including* UTF-8 consoles, set `VT_ASCII=1`.  
