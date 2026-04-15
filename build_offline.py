#!/usr/bin/env python3
"""
Build script for offline Voice Transcriber EXE
Bundles both Whisper and Cohere models for completely offline operation
"""
import os
import sys
import shutil
import subprocess
from pathlib import Path

# Configuration
PROJECT_ROOT = Path(__file__).parent
SRC_DIR = PROJECT_ROOT / "src"
BUILD_DIR = PROJECT_ROOT / "build"
DIST_DIR = PROJECT_ROOT / "dist"
MODELS_DIR = BUILD_DIR / "models"

# Cache locations
WHISPER_CACHE = Path(os.path.expanduser("~/.cache/whisper"))
HF_CACHE = Path(os.path.expanduser("~/.cache/huggingface/hub"))

def ensure_cached_models():
    """Ensure both models are cached locally before building"""
    print("=" * 60)
    print("Checking cached models...")
    print("=" * 60)
    
    # Check Whisper
    if WHISPER_CACHE.exists():
        whisper_files = list(WHISPER_CACHE.glob("**/*"))
        print(f"Whisper cache: {len(whisper_files)} files found")
    else:
        print("Whisper cache: NOT FOUND - will download during build")
    
    # Check Cohere
    cohere_model_dir = HF_CACHE / "models--CohereLabs--cohere-transcribe-03-2026"
    if cohere_model_dir.exists():
        print(f"Cohere cache: FOUND at {cohere_model_dir}")
    else:
        print("Cohere cache: NOT FOUND - will download during build")
    
    print()
    
def prepare_bundled_models():
    """Copy models to build directory for bundling"""
    print("=" * 60)
    print("Preparing bundled models...")
    print("=" * 60)
    
    # Clean up old models
    if MODELS_DIR.exists():
        shutil.rmtree(MODELS_DIR)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Copy Whisper cache
    if WHISPER_CACHE.exists():
        dest_whisper = MODELS_DIR / "whisper"
        print(f"Copying Whisper cache to {dest_whisper}...")
        shutil.copytree(WHISPER_CACHE, dest_whisper, dirs_exist_ok=True)
    else:
        print("WARNING: No Whisper cache found - will be downloaded on first run")
    
    # Copy HuggingFace cache
    if HF_CACHE.exists():
        dest_hf = MODELS_DIR / "huggingface" / "hub"
        print(f"Copying HuggingFace cache to {dest_hf}...")
        # Only copy the Cohere model to save space
        cohere_src = HF_CACHE / "models--CohereLabs--cohere-transcribe-03-2026"
        if cohere_src.exists():
            dest_cohere = dest_hf / "models--CohereLabs--cohere-transcribe-03-2026"
            print(f"  Copying Cohere model...")
            shutil.copytree(cohere_src, dest_cohere, dirs_exist_ok=True)
        else:
            print("WARNING: No Cohere cache found - will be downloaded on first run")
    else:
        print("WARNING: No HuggingFace cache found - will be downloaded on first run")
    
    # Copy HF_TOKEN if it exists
    token_locations = [
        PROJECT_ROOT / "HF_TOKEN",
        SRC_DIR.parent / "HF_TOKEN",
    ]
    for token_loc in token_locations:
        if token_loc.exists():
            dest_token = MODELS_DIR / "HF_TOKEN"
            shutil.copy2(token_loc, dest_token)
            print(f"Copied HF_TOKEN to {dest_token}")
            break
    else:
        print("NOTE: No HF_TOKEN found - will need to be provided at runtime for Cohere")
    
    print(f"Bundle prepared at: {MODELS_DIR}")
    print()

def build_exe():
    """Build the EXE with PyInstaller"""
    print("=" * 60)
    print("Building EXE with PyInstaller...")
    print("=" * 60)
    
    # PyInstaller options - copy src files to root of bundle (not in src/ subfolder)
    pyinstaller_args = [
        sys.executable, "-m", "PyInstaller",
        str(SRC_DIR / "main_windows.py"),
        "--name=VoiceTranscriber",
        "--onefile",
        "--console",
        "--clean",
        "--log-level=INFO",
        
        # Add models directory
        f"--add-data={MODELS_DIR};models",
        
        # Add all src files to root (not in src/ subfolder)
        f"--add-data={SRC_DIR};.",
        
        # Hidden imports
        "--hidden-import=sounddevice",
        "--hidden-import=soundfile",
        "--hidden-import=numpy",
        "--hidden-import=torch",
        "--hidden-import=transformers",
        "--hidden-import=faster_whisper",
        "--hidden-import=pynput",
        "--hidden-import=keyboard",
        "--hidden-import=pyperclip",
        "--hidden-import=psutil",
        "--hidden-import=tqdm",
        "--hidden-import=tokenizers",
        "--hidden-import=ctranslate2",
        "--hidden-import=onnxruntime",
        
        # Collect all for these packages
        "--collect-all=transformers",
        "--collect-all=faster_whisper",
        "--collect-all=torch",
        "--collect-all=tokenizers",
        "--collect-all=ctranslate2",
    ]
    
    # Add icon if it exists
    icon_path = PROJECT_ROOT / "icon.ico"
    if icon_path.exists():
        pyinstaller_args.append(f"--icon={icon_path}")
    
    # Filter out NONE value
    pyinstaller_args = [arg for arg in pyinstaller_args if arg != "NONE"]
    
    print("Running:", " ".join(pyinstaller_args[:10]) + "...")
    print()
    
    result = subprocess.run(pyinstaller_args, cwd=PROJECT_ROOT)
    
    if result.returncode == 0:
        print()
        print("=" * 60)
        print("BUILD SUCCESSFUL!")
        print("=" * 60)
        exe_path = DIST_DIR / "VoiceTranscriber.exe"
        if exe_path.exists():
            size_mb = exe_path.stat().st_size / (1024 * 1024)
            print(f"EXE created: {exe_path}")
            print(f"Size: {size_mb:.1f} MB ({size_mb/1024:.2f} GB)")
        print()
        print("To test offline:")
        print("1. Disconnect network / enable Airplane mode")
        print("2. Run the EXE")
        print("3. Test recording with both Whisper and Cohere")
    else:
        print()
        print("=" * 60)
        print("BUILD FAILED!")
        print("=" * 60)
        sys.exit(result.returncode)

def main():
    print("Voice Transcriber - Offline Build Script")
    print("=" * 60)
    print()
    
    # Check prerequisites
    try:
        import PyInstaller
        print(f"PyInstaller version: {PyInstaller.__version__}")
    except ImportError:
        print("ERROR: PyInstaller not installed")
        print("Install with: pip install pyinstaller")
        sys.exit(1)
    
    # Ensure models are cached
    ensure_cached_models()
    
    # Skip prompt - auto-proceed
    print("\nProceeding with build automatically...")
    
    # Prepare bundled models
    prepare_bundled_models()
    
    # Build the EXE
    build_exe()

if __name__ == "__main__":
    main()
