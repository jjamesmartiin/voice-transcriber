#!/usr/bin/env python3
"""
Build script for offline Voice Transcriber EXE
Bundles the local Cohere model (models/cohere) for completely offline operation
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
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent if (SCRIPT_DIR.parent.parent / "src").exists() else SCRIPT_DIR
SRC_DIR = PROJECT_ROOT / "src"
BUILD_DIR = PROJECT_ROOT / "build"
DIST_DIR = PROJECT_ROOT / "dist"
MODELS_DIR = BUILD_DIR / "models"

# Local model location (installed from the GitHub release assets on first run)
LOCAL_COHERE = PROJECT_ROOT / "models" / "cohere"

def ensure_cached_models():
    """Ensure the Cohere model is present locally before building"""
    print("=" * 60)
    print("Checking local Cohere model...")
    print("=" * 60)

    if (LOCAL_COHERE / "model.safetensors").exists():
        print(f"Cohere model: FOUND at {LOCAL_COHERE}")
    else:
        print(f"Cohere model: NOT FOUND at {LOCAL_COHERE}")
        print("  Run the app once (online) to install it from the GitHub release assets.")

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
    
    # The runtime loads from models/cohere (installed from the GitHub release
    # assets on first run). Bundle that directory so the EXE runs fully offline.
    if (LOCAL_COHERE / "model.safetensors").exists():
        print(f"\nFound local Cohere model: {LOCAL_COHERE}")
        dest_cohere = MODELS_DIR / "cohere"
        copy_with_progress(LOCAL_COHERE, dest_cohere, "Copying Cohere model")
    else:
        print(f"\nWARNING: No local Cohere model found at {LOCAL_COHERE}")
        print("  Run the app once (online) to install it, then rebuild.")
    
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
        str(SRC_DIR / "main.py"),
        "--name=VoiceTranscriber",
        "--onedir",
        "--console",
        "--clean",
        "-y",
        "--log-level=INFO",
        
        # Add models directory
        f"--add-data={MODELS_DIR}{os.pathsep}models",
        
        # Add all src files to root (not in src/ subfolder)
        f"--add-data={SRC_DIR}{os.pathsep}.",
        
        # Hidden imports
        "--hidden-import=hal",
        "--hidden-import=platform.windows",
        "--hidden-import=platform.windows.hotkeys",
        "--hidden-import=platform.windows.clipboard",
        "--hidden-import=platform.windows.audio_cues",
        "--hidden-import=platform.windows.notifications",
        "--hidden-import=sounddevice",
        "--hidden-import=soundfile",
        "--hidden-import=numpy",
        "--hidden-import=torch",
        "--hidden-import=transformers",
        "--hidden-import=pynput",
        "--hidden-import=keyboard",
        "--hidden-import=pyperclip",
        "--hidden-import=psutil",
        "--hidden-import=tqdm",
        "--hidden-import=tokenizers",
        "--hidden-import=onnxruntime",
        "--hidden-import=rich",
        
        # Collect all for these packages
        "--collect-all=transformers",
        "--collect-all=torch",
        "--collect-all=tokenizers",
        
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
        print("3. Test recording with Cohere")
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
