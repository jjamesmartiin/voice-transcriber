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

### End users: the AppImage (recommended)
Download `vt-x86_64.AppImage` from the releases page, `chmod +x` it, and run it.
No Nix, no Python, no setup — the closure (Python, PyTorch, clipboard tools) is
bundled. The weights download on first run, or put a `model-bundle/` directory
next to the `.AppImage` for a fully offline install. See the
[main README](../../README.md#-linux-wayland--x11) for the FUSE / hotkey / GPU
prerequisites (all host-level, none Nix-related).

### Running from a git checkout
One verb = one script: `./setup.sh`, `./run.sh`, `./test.sh`, `./build.sh`,
`./clean.sh`. Each root script detects the toolchain and dispatches to
`platforms/<toolchain>/`:

- **Nix** (recommended): `nix run .` builds and launches with no venv.
- **Native** (apt + venv): `./setup.sh` then `./run.sh`.

Force one over auto-detection with `VT_USE_VENV=1` (native venv) or
`VT_TOOLCHAIN=nix|linux`.

```bash
./setup.sh        # venv + dependencies + model (~2.8 GB, one time)
./run.sh          # launch
./run.sh doctor   # system diagnostics
```

### With Nix directly
```bash
nix run .                         # launch
nix run .#setup                   # acquire the model
nix develop                       # dev shell (python -m pytest tests/shared)
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

---

## Troubleshooting

### 1. Microphone Captures Silence While in Discord, Browser, or WebRTC Calls
**Symptom:** Voice Transcriber records silence or reports "No audio recorded", even though Discord or your browser call hears you fine.  
**Causes & Fixes:**
- **Multiple Microphones (e.g. Headset vs. Desk USB Mic):** Discord explicitly selected your headset mic, while PipeWire/GNOME's system default was set to another input. Voice Transcriber listens to the system `default` input.
  - To route your headset mic to both:
    ```bash
    wpctl status               # Look under "Sources:" for your headset microphone ID
    wpctl set-default <ID>     # e.g. wpctl set-default 101
    ```
    Or change it in desktop settings (**GNOME Settings → Sound → Input**). PipeWire will fan out audio to both apps simultaneously.
- **Direct ALSA Hardware Device (`hw:X,Y`):** Raw ALSA hardware devices enforce exclusive single-app access. If Discord or PipeWire opens `hw:X,Y`, Voice Transcriber will fail with `EBUSY`.
  - In Voice Transcriber's settings (`s`), always select **`default`** or **`pipewire`** instead of raw `hw:X,Y` devices.

### 2. Global Hotkeys Not Detected
**Symptom:** Pressing `Alt+Shift` or the middle mouse button does nothing.  
**Fix:**
- Ensure your user is in the `input` group:
  ```bash
  sudo usermod -a -G input $USER
  ```
  Then log out and log back in.
- Run diagnostics to check device permissions:
  ```bash
  ./run.sh doctor
  ```

### 3. Keystrokes Not Appearing in Wayland Applications
**Symptom:** The notification shows "COMPLETED", but no text is typed into the focused window.  
**Fix:**
- Ensure `/dev/uinput` is writable (verified by `./run.sh doctor`).
- If using an application that rejects synthetic keystrokes, switch to clipboard mode (`./run.sh output clipboard`), which pastes via `wl-copy`.
