# Linux Setup and Usage

Voice Transcriber runs on Linux under both Wayland and X11.

## Prerequisites

1. **Input Group Membership** (required for global hotkeys without root):
   ```bash
   sudo usermod -a -G input $USER
   ```
   Log out and log back in for this change to take effect.

   *NixOS configuration equivalent:*
   ```nix
   users.users.<username>.extraGroups = [ "input" ];
   ```

2. **Audio Stack**:
   - PipeWire or PulseAudio running.
   - Microphone configured as default source.

## Running

### ⚡ 1-Command Launcher (Recommended)
From the repo root:
```bash
./run.sh
```
`./run.sh` automatically checks if Nix is available (using `nix run .`), or bootstraps a local `.venv` virtual environment if running outside Nix.

### One-Time Setup (Non-Nix)
```bash
./setup.sh
```
Checks system dependencies (`portaudio`, `wl-clipboard`/`xclip`, `ydotool`/`xdotool`), creates `.venv`, installs requirements, and tests permissions.

### System Diagnostics (Doctor)
```bash
./run.sh doctor
```

### With Nix directly
```bash
nix run .
```

## Hotkeys

- `Alt+Shift` (hold): Push-to-Talk — record while held; transcribe and paste/type on release.
- `Space` (tap while holding `Alt+Shift`): Hands-free latch mode. Release the keys and continue speaking; tap `Alt+Shift` again to stop.
- Middle-click (hold ~0.25 s): Mouse Push-to-Talk. A tap under ~100 ms is replayed as a normal middle click; a longer hold is swallowed (no primary-selection paste) and starts recording.
- `Ctrl` (held at release): Force clipboard output for this utterance even when auto-type is enabled.
- `Space` or `Enter` (in the terminal): Start/stop recording. Tap to start, tap again to stop, so you never hold a key while dictating.
- `s`, `S`, or `,` (in the terminal): Open the interactive settings modal — the only configuration entry point. The microphone, theme and mode-preset pickers live inside it.
- `r` (in the terminal): Reset the terminal and clipboard bridge.
- `q`, `Esc`, or `Ctrl+C` (in the terminal): Quit.

There is no global hotkey for settings; use the [Control API](../README.md#control-api)
to drive it from outside the terminal.

## Verification

Run the shared + Linux tiers (no audio devices or model weights required),
from the repo root:
```bash
./test.sh
```

Targeted tiers:
```bash
./test.sh shared     # cross-platform, model-free
./test.sh platform   # tests/linux
./test.sh e2e        # model/audio end-to-end (local only)
```

The live speaker→microphone acoustic suite needs real audio hardware:
```bash
nix develop --command python tests/e2e/test_live_speaker_mic_loopback.py all
```
