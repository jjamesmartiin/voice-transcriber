# AGENTS.md - Voice Transcriber Autonomous Agent Guidelines & Task Blueprint

## Project Mission
Build a **standalone, offline-capable voice transcriber** that runs in WSL (NixOS on Windows) and Windows natively.
The application records audio from the microphone via hotkey, transcribes it using local models (Faster-Whisper or Cohere), and automatically pastes the transcription into the active application window via clipboard synchronization.

## Workspace & Target Branch
- **Repository Directory**: `/home/jamesm/gitprojects/voice-transcriber`
- **Active Branch**: `main-windows`

## Architecture Map
- `src/main_windows.py` / `src/main.py`: Main application entry points & hotkey event handlers.
- `src/t2.py`: Application state machine & window overlay (Tkinter).
- `src/transcribe2.py`: Unified backend selector & router.
- `src/transcribe_whisper.py`: Faster-Whisper backend wrapper (offline local caching).
- `src/transcribe_cohere.py`: Cohere Transcribe model wrapper (HF Transformers).
- `plan-to-compile.md`: Technical specification for single-file PyInstaller build (`build_offline.py`).

## Key Developer Commands & Verification
Always verify changes before marking tasks as complete:

```bash
# 1. Run unit test suite
python tests/test_transcribe.py

# 2. Run application dry-run check
python -c "import src.transcribe2 as t2; print('Backend selector OK')"

# 3. Test offline build script validation
python build_offline.py --check-only
```

## Task Execution Priorities for Autonomous Agent
1. **WSL / Cross-Platform Audio Capture**: Ensure `sounddevice` / ALSA / WASAPI interop handles microphone passthrough seamlessly without device lock errors.
2. **Clipboard Paste Integration**: Verify `wl-clipboard` (on Linux/WSL) and PowerShell clipboard interop (on Windows) paste transcribed text smoothly into focused windows.
3. **Offline Model Bundling**: Maintain zero network dependency at runtime when models are cached in `~/.cache/whisper` or `~/.cache/huggingface`.
4. **Error Handling**: Graceful recovery if microphone disconnects or model fails to load without crashing the main loop.
