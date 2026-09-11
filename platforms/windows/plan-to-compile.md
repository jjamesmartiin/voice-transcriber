# Plan to Compile Voice Transcriber to Single Offline EXE

## Project Overview

Voice Transcriber is a voice transcription tool with:
- **Entry point**: `src/main_windows.py` → `src/main.py` → `src/t2.py`
- **Two model backends**:
  - Faster-Whisper (`src/transcribe_whisper.py`) - works offline, ~500MB
  - Cohere Transcribe (`src/transcribe_cohere.py`) - higher quality, ~2GB, gated model
- **Audio capture**: sounddevice, soundfile, numpy
- **Hotkeys**: pynput + keyboard (Windows)
- **Notifications**: Tkinter overlay

The app already supports switching between models via the **Ctrl+Alt+I** menu (press and hold while the app is running).

## Goal

Build a **single self-contained EXE** that:
- Works **completely offline** without any network downloads
- Includes **both transcription models** (Whisper + Cohere)
- Retains all UI features (hotkeys, model switching, device selection)
- Can be distributed on USB drive or file hosting

## Requirements

- Single one-file EXE (PyInstaller `--onefile`)
- Estimated size: **3-4GB** (Python + deps + both models)
- Works on Windows 10+
- User can switch between Whisper/Cohere via the in-app menu

---

## Technical Plan

### Step 1: Understand Current Model Loading

**Faster-Whisper** (`transcribe_whisper.py`):
- Uses `faster-whisper` library
- Downloads to: `~/.cache/whisper/`
- Code: `WhisperModel(model_name, device=device, compute_type=compute_type, download_root=...)`

**Cohere** (`transcribe_cohere.py`):
- Uses `transformers` library
- Downloads to: `~/.cache/huggingface/`
- Requires HF_TOKEN for gated model
- Code: `AutoProcessor.from_pretrained()`, `AutoModelForSpeechSeq2Seq.from_pretrained()`

### Step 2: Modify Model Loading for Bundled Models

Modify both `transcribe_whisper.py` and `transcribe_cohere.py` to:

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

1. **Run app once with network** to cache both models
2. **Verify cache locations**:
   - `~/.cache/whisper/` - contains Whisper model files
   - `~/.cache/huggingface/hub/` - contains Cohere model files

### Step 4: Create Build Script

Create `build_offline.py`:

```python
import PyInstaller.__main__
import os
import shutil

# Paths
CACHE_WHISPER = os.path.expanduser("~/.cache/whisper")
CACHE_HUGGINGFACE = os.path.expanduser("~/.cache/huggingface/hub")
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# Copy models to build/models folder
# (build script will include this in the EXE)

PyInstaller.__main__.run([
    'src/main_windows.py',
    '--name=VoiceTranscriber',
    '--onefile',
    '--windowed',  # No console
    '--icon=icon.ico',  # Optional
    '--add-data=models;models',  # Include bundled models
    '--hidden-import=sounddevice',
    '--hidden-import=soundfile',
    '--hidden-import=torch',
    '--hidden-import=transformers',
    '--hidden-import=faster_whisper',
    '--hidden-import=pynput',
    '--hidden-import=keyboard',
    '--collect-all=transformers',
    '--collect-all=faster_whisper',
    '--collect-all=torch',
    '--clean',
])
```

### Step 5: Include HF_TOKEN

For Cohere gated model access:

1. Place `HF_TOKEN` file in project root before building
2. Modify `transcribe_cohere.py` to look in EXE bundle:
   - Copy `HF_TOKEN` to `_MEIPASS` during build
   - Or embed token in code (less secure but easier)

### Step 6: Build and Test

1. Run build script
2. Test EXE with network disabled
3. Verify both models work

---

## Files to Modify

| File | Change |
|------|--------|
| `src/transcribe_whisper.py` | Add `_MEIPASS` model path detection |
| `src/transcribe_cohere.py` | Add `_MEIPASS` model path detection |
| `build_offline.py` | New PyInstaller build script |
| `plan-to-compile.md` | This document |

---

## Estimated Output

- **Single EXE**: ~3-4GB
- **Working offline**: Yes
- **Model switching**: Via Ctrl+Alt+I menu (unchanged)
- **Features preserved**: All original functionality

---

## Pre-Build Checklist

- [ ] Both models cached locally (`~/.cache/whisper/`, `~/.cache/huggingface/hub/`)
- [ ] HF_TOKEN file available for Cohere model
- [ ] PyInstaller installed: `pip install pyinstaller`
- [ ] All dependencies installed: `pip install -r requirements.txt`

---

## Testing Offline Mode

After building, test by:
1. Disconnecting network / enabling Airplane mode
2. Running the EXE
3. Recording with both Whisper and Cohere
4. Verifying transcriptions work

---

## Notes

- The EXE will be large (3-4GB) - this is unavoidable with both models
- Consider using `--upx-dir` for compression if UPX is installed
- First launch may be slow as EXE extracts temp files
- Model switching via Ctrl+Alt+I → B key works exactly as before

---

## Clarifying Decisions

- **Offline behavior**: If models are not bundled (e.g., re-running EXE after extraction), the app will:
  1. First check bundled model directories (`_MEIPASS/models/`)
  2. If not found, fall back to default cache locations (`~/.cache/whisper/`, `~/.cache/huggingface/hub`)
  3. If still not found, attempt to download (requires network)

This ensures the EXE works offline when models are properly bundled, but also provides graceful fallback for development/testing.
