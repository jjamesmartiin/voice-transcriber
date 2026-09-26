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

### With Nix (Recommended)
From repo root:
```bash
nix run .
```

### With Python directly
```bash
./platforms/linux/run.sh
```
Or:
```bash
export PYTHONPATH=src
python3 src/main.py
```

## Hotkeys

- `Alt+Shift` (hold): Push-to-Talk — record while held; transcribe and paste/type on release.
- `Space` (tap while holding `Alt+Shift`): Hands-free latch mode. Release the keys and continue speaking; tap `Alt+Shift` again to stop.
- Middle-click (hold ~0.25 s): Mouse Push-to-Talk. A quick click passes through and is ignored.
- `Ctrl` (held at release): Force clipboard output for this utterance even when auto-type is enabled.
- `Ctrl+Alt+I` (or `i` in the terminal): Open the interactive settings menu.

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
