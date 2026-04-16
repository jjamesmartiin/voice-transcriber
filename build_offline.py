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
import threading
import time

def copy_with_progress(src, dst, desc="Copying"):
    """Copy file/directory with progress feedback for large files"""
    total_size = 0
    if src.is_dir():
        for item in src.rglob("*"):
            if item.is_file():
                total_size += item.stat().st_size
    else:
        total_size = src.stat().st_size
    
    size_mb = total_size / (1024 * 1024)
    print(f"{desc}: {src.name} ({size_mb:.1f} MB)...")
    
    copied = [0]
    def progress_copy():
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
            copied[0] = total_size
        else:
            shutil.copy2(src, dst)
            copied[0] = total_size
        print(f"{desc} complete: {size_mb:.1f} MB")
    
    if size_mb > 100:
        thread = threading.Thread(target=progress_copy)
        thread.start()
        while thread.is_alive():
            time.sleep(2)
            pct = (copied[0] / total_size * 100) if total_size > 0 else 0
            print(f"  {desc}: {pct:.0f}% complete ({size_mb:.1f} MB total)")
            thread.join(timeout=0.5)
    else:
        progress_copy()

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
        print("Cleaning up old build models...")
        shutil.rmtree(MODELS_DIR)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Copy Whisper cache
    if WHISPER_CACHE.exists():
        dest_whisper = MODELS_DIR / "whisper"
        copy_with_progress(WHISPER_CACHE, dest_whisper, "Copying Whisper cache")
    else:
        print("WARNING: No Whisper cache found - will be downloaded on first run")
    
    # Copy HuggingFace cache - Cohere model
    cohere_src = HF_CACHE / "models--CohereLabs--cohere-transcribe-03-2026"
    if cohere_src.exists():
        print(f"\nFound Cohere model in cache: {cohere_src.name}")
        dest_hf = MODELS_DIR / "huggingface" / "hub"
        dest_cohere = dest_hf / "models--CohereLabs--cohere-transcribe-03-2026"
        os.makedirs(dest_hf, exist_ok=True)
        copy_with_progress(cohere_src, dest_cohere, "Copying Cohere model")
    else:
        print("\nWARNING: No Cohere model cache found!")
        print("  The model will be downloaded on first run of the EXE")
        print("  To avoid this, run the app while connected to the internet first")
    
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
        print("\nNOTE: No HF_TOKEN found in project root")
        print("  If the Cohere model is gated, you'll need to provide HF_TOKEN at runtime")
        print("  Create a file named 'HF_TOKEN' next to the EXE with your token")
    
    print(f"\nBundle prepared at: {MODELS_DIR}")
    
    # Print summary of what's being bundled
    total_size = sum(f.stat().st_size for f in MODELS_DIR.rglob("*") if f.is_file())
    print(f"Total bundle size: {total_size / (1024*1024):.1f} MB")
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
        "--onedir",
        "--console",
        "--clean",
        "-y",
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
        
        # Disable upx to avoid issues with large bundles
        "--upx-dir=NONE",
    ]
    
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
