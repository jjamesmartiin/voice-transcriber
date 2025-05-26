# TODO
- [ ] REALLY make sure that it works offline and that we don't need to download anything after running it once
- [ ] REALLY make sure that it works offline and that we don't need to download anything after running it once
- [ ] REALLY make sure that it works offline and that we don't need to download anything after running it once
- [ ] REALLY make sure that it works offline and that we don't need to download anything after running it once
- [ ] add an interrupt during transcription in case it's wrong
- [x] fix the warning from `faster-whisper` onnx stuff (surpressed but not quite fixed)
- [x] add a test for the transcriber
- [x] use `faster-whisper` for faster transcriptions
- [x] add some global shortcut/gui branch
- [ ] add a sound notification when the text is copied to clipboard, like a ding or pop 
- [ ] try it out on wsl/hyper v
    - [ ] fix breaking terminal output when quitting using 'q'
        - this only breaks on windows i think
- [x] make something to cache the nixpkgs so it works totally offline...
    > maybe done...
    > nope not yet these are trying to be downloaded: 
      - /nix/store/x9d49vaqlrkw97p9ichdwrnbh013kq7z-bash-interactive-5.2p37
      - /nix/store/k3a7dzrqphj9ksbb43i24vy6inz8ys51-ncurses-6.4.20221231
      - /nix/store/8yff66km6d5mav6i2101kmvp78vgqfcc-readline-8.2p13
    > maybe this is done now? I added all 3 of these the shell.nix

# voice-transcriber

A fast offline voice transcription tool with global shortcut support for GNOME/Wayland.

## Usage

### Terminal Mode
```shell
nix-shell --run "python t2.py"

# for working global shortcut: 
nix-shell --run "python simple_voice_transcriber.py"
```

### GUI Mode (RECOMMENDED!)
```shell
# Start the GUI application
./start_gui.sh

# Or manually:
nix-shell --run "python3 voice_gui.py"
```

The GUI app provides:
- **Visual interface** with status updates and transcription results
- **Multiple keyboard shortcuts** - F12, Space, or Ctrl+R to toggle recording
- **Click-to-record button** as alternative to shortcuts
- **Background operation** - minimize to run in background
- **Real-time feedback** with progress indicators
- **Always on top option** to keep window accessible

**Note**: Keyboard shortcuts work when the window has focus. Use "Keep on Top" button to keep it accessible.

### Global Shortcut Mode (Command Line)
```shell
# Start the global shortcut daemon
./start_daemon.sh

# Or manually:
nix-shell --run "python3 global_shortcut.py"
```

Once the daemon is running, **hold the Control key** to start recording, then **release Control** to stop recording and transcribe. The transcribed text will be automatically copied to your clipboard.

## Features

- **Fast offline transcription** using faster-whisper
- **Multiple interfaces**: GUI app, global shortcuts, or terminal mode
- **F12 global hotkey** (GUI mode) or Control key (daemon mode)
- **Desktop notifications** for transcription status
- **Automatic clipboard copying** of transcribed text
- **Audio feedback** with pop sound on completion
- **Background operation** - minimize GUI to system tray

## Recommended Usage

**For best experience, use the GUI mode** (`./start_gui.sh`):
1. Opens a user-friendly window
2. Keep window focused or use "Keep on Top" button
3. Press F12, Space, or Ctrl+R to start/stop recording
4. See transcription results in real-time
5. Minimize to background when not actively using shortcuts

## Global Shortcut Setup

The global shortcut daemon (`global_shortcut.py`) uses keyboard monitoring to detect when the Control key is held down. This approach works reliably across different desktop environments including:

- GNOME/Wayland
- KDE/Plasma
- XFCE
- i3/sway
- Any X11 or Wayland desktop

**Usage**: Hold Control key to record, release to stop and transcribe. Press Escape to exit the daemon.

## tips:
### to get a copy/paste notification 
- on gnome: use the `pano` extension
