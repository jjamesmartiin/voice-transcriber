# Plan to Compile Voice Transcriber to Single Offline EXE

## Project Overview

Voice Transcriber is a voice transcription tool with:
- **Entry point**: `src/main.py` → `src/t2.py`
- **Model backend**:
  - Cohere Transcribe (`src/transcribe_cohere.py`) - high accuracy (~2.79% WER), ~2GB
- **Audio capture**: sounddevice, soundfile, numpy
- **Hotkeys**: pynput + keyboard (Windows)
- **Notifications**: Tkinter overlay / Rich TUI

## Goal

Build a **single self-contained EXE** that:
- Works **completely offline** without any network downloads
- Includes the **Cohere Transcribe model**
- Retains all UI features (hotkeys, device selection, formatting)
- Can be distributed on USB drive or file hosting

## Requirements

- Single one-file EXE (PyInstaller `--onefile`) or directory bundle (`--onedir`)
- Estimated size: **2-3GB** (Python + PyTorch + deps + Cohere weights)
- Works on Windows 10+

---

## Technical Plan

### Step 1: Model Loading

**Cohere** (`transcribe_cohere.py`):
- Uses `transformers` library
- Cached locally in: `~/.cache/huggingface/hub`
- Code: `AutoProcessor.from_pretrained()`, `AutoModelForSpeechSeq2Seq.from_pretrained()`

### Step 2: Model Loading for Bundled Models

Modify `transcribe_cohere.py` to:

1. **Check PyInstaller's temp extraction dir first** (`_MEIPASS`)
2. **Fall back to default cache locations** if not in EXE

```python
def get_model_dir():
    """Get the model directory - bundled in EXE or cache"""
    import sys
    import os
    
    # If running as EXE, check extracted directory
    if getattr(sys, 'frozen', False):
        meipass = sys._MEIPASS
        bundled_models = os.path.join(meipass, 'models')
        if os.path.exists(bundled_models):
            return bundled_models
    
    # Otherwise use default cache locations
    return None
```

For Cohere, update `load_model()` to pass `local_files_only=False` during build but `True` at runtime (or check `_MEIPASS` first).

### Step 3: Prepare Model Cache for Build

Before building:

1. **Run app once with network** to cache the Cohere model
2. **Verify cache location**:
   - `~/.cache/huggingface/hub/` - contains Cohere model files

### Step 4: Create Build Script

Run `python platforms/windows/build_offline.py`

### Step 5: Include HF_TOKEN

For Cohere gated model access:

1. Place `HF_TOKEN` file in project root before building
2. `build_offline.py` copies `HF_TOKEN` into the bundle

### Step 6: Build and Test

1. Run build script: `python platforms/windows/build_offline.py`
2. Test EXE with network disabled
3. Verify transcription works

---

## Pre-Build Checklist

- [ ] Cohere model cached locally (`~/.cache/huggingface/hub/`)
- [ ] HF_TOKEN file available for Cohere model
- [ ] PyInstaller installed: `pip install pyinstaller`
- [ ] Windows dependencies installed: `pip install -r platforms/windows/requirements.txt`

---

## Testing Offline Mode

After building, test by:
1. Disconnecting network / enabling Airplane mode
2. Running the EXE from `dist/VoiceTranscriber/VoiceTranscriber.exe`
3. Recording via Alt+Shift
4. Verifying transcriptions paste/type into active window
