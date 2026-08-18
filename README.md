# Voice Transcriber

A robust, modular voice transcription tool with global hotkeys for Linux (Wayland/X11) and Windows (via NixOS WSL or Native).

---

## Quick Start

### Windows
- **Native Windows**: See the [main-windows](https://github.com/jjamesmartiin/voice-transcriber/tree/main-windows) branch.
- **Windows via NixOS WSL (Recommended)**: See the **[`main-wsl`](https://github.com/jjamesmartiin/voice-transcriber/tree/main-wsl)** branch!
  - **Zero-Setup Windows Host Bridge**: Hold `Alt+Shift` anywhere across Windows with automatic host clipboard paste.
  - **Streaming Micro-Batching**: Real-time VAD speech chunking for near-instant post-release copying.
  - **100% Word Accuracy**: Verified across 8 standardized dictation benchmarks.
  - **Speech Energy Gate & Tail Trimmer**: Eliminates silence/noise hallucinations while keeping spoken phrases accurate.
  - **Configurable Sound Themes**: Instant audio cue feedback customizable directly in `run.sh` or settings.

```bash
# To run on Windows via WSL:
git checkout main-wsl
./run.sh
```

---

### Linux
```bash
# Add user to input group for global hotkeys
sudo usermod -a -G input $USER
# Log out and back in, then run:
nix run github:jjamesmartiin/voice-transcriber

# Or run as root (not recommended)
sudo nix run github:jjamesmartiin/voice-transcriber
```

#### NixOS Example
```nix
users.users.yourusername.extraGroups = [ "input" ];
```

See [NixOS options](https://search.nixos.org/options?channel=25.11&include_modular_service_options=1&include_nixos_options=1&query=users.users.*.extra) for more info.

---

## Usage

### Controls
- **Alt+Shift** (hold) - Start recording, release to transcribe
- **Ctrl+Alt+I** - Open settings menu

### Settings Menu
- P/S - Set primary/secondary audio device
- M - Toggle mute
- B - Switch model (whisper/cohere)
- T - Toggle auto-type to screen
- c - Save and exit

---

## Model Details
- **Cohere Transcribe**: Uses `CohereLabs/cohere-transcribe-03-2026`. Requires a Hugging Face token on first run (`HF_TOKEN` file in root).
- **Faster Whisper**: Uses the Whisper `small` model. Fully local and offline.

---

## License
See [LICENSE](LICENSE) for details.
