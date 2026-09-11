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

- `Alt+Shift` (hold): Record while held; transcribe and copy/paste on release.
- `Space` (tap while holding `Alt+Shift`): Hands-free latch mode. Release keys and continue speaking; tap `Alt+Shift` again to stop.
- `Ctrl+Alt+I`: Open in-terminal interactive settings menu.

## Verification

Run test suites:
```bash
nix develop --command python -m pytest tests/test_end_to_end_crossplatform.py -v
```
