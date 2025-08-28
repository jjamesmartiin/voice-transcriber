# Voice Transcriber

A fast, offline voice transcription tool built with faster-whisper for efficient speech-to-text conversion.

## Overview

Voice Transcriber provides real-time voice-to-text transcription with automatic clipboard integration. Designed for accessibility and productivity, it helps reduce typing strain while maintaining privacy through offline processing.

## Features

- **Fast offline transcription** using faster-whisper
- **Automatic clipboard copying** of transcribed text
- **Visual feedback** during transcription process
- **Global keyboard shortcuts** (Linux/Wayland with proper permissions)
- **Cross-platform support** (Linux/Unix via Nix, Windows)

## Installation & Usage

### Linux/Unix (Nix)

1. Get [Nix](https://nixos.org/download.html) installed on your system
2. Clone the repository and navigate to it:
   ```bash
   git clone https://github.com/jjamesmartiin/voice-transcriber.git
   cd voice-transcriber
   ```
3. Run the Nix shell:
   ```bash
   nix-shell
   ```
4. Start the application:
   ```bash
   python app/t3.py
   # Or with elevated permissions for global shortcuts:
   sudo python app/t3.py
   ```

**Note**: For global keyboard shortcut support on Wayland, add your user to the `input` group or run with elevated permissions.

### Windows

For Windows installation and usage instructions, please see the [Windows branch](https://github.com/jjamesmartiin/voice-transcriber/tree/main-windows) which contains detailed setup instructions and Windows-specific optimizations.

## How It Works

1. Press and hold the designated hotkey to start recording
2. Speak your message clearly
3. Release the hotkey to stop recording
4. The transcribed text is automatically copied to your clipboard
5. Paste the text wherever needed

## Requirements

- Python 3.8+
- Microphone access
- Internet connection for initial model download (subsequent usage is offline)

## Contributing

Contributions are welcome! Please feel free to submit issues or pull requests.

## License

This project is open source. Please check the license file for details.

---

## Development Roadmap
- [ ] Live transcription during recording


## Tags

`voice-to-text` `transcription` `accessibility` `offline` `faster-whisper` `speech-recognition`