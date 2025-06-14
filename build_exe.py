#!/usr/bin/env python3
"""
Build script for T3 Voice Transcriber EXE
"""

import subprocess
import sys
import os

def build_exe():
    """Build the standalone EXE"""
    
    print("Building T3 Voice Transcriber EXE...")
    print(f"Using Python: {sys.executable}")
    print(f"Current directory: {os.getcwd()}")
    
    # Check if required packages are available
    required_packages = ['pyaudio', 'pynput', 'faster_whisper', 'ctranslate2', 'numpy', 'pyperclip']
    missing_packages = []
    
    for package in required_packages:
        try:
            __import__(package)
            print(f"✅ {package} - Available")
        except ImportError:
            print(f"❌ {package} - Missing")
            missing_packages.append(package)
    
    if missing_packages:
        print(f"\nMissing packages: {missing_packages}")
        print("Please install missing packages before building.")
        return False
    
    # Build command
    build_cmd = [
        sys.executable,
        '-m', 'PyInstaller',
        '--clean',
        '--onefile',
        '--add-data', 'app/sounds;app/sounds',
        '--add-data', 'app/transcribe2.py;app',
        '--add-data', 'app/visual_notifications.py;app',
        '--hidden-import=pyaudio',
        '--hidden-import=pynput',
        '--hidden-import=pynput.keyboard',
        '--hidden-import=faster_whisper',
        '--hidden-import=ctranslate2',
        '--hidden-import=numpy',
        '--hidden-import=pyperclip',
        '--hidden-import=tkinter',
        '--hidden-import=wave',
        '--hidden-import=json',
        '--hidden-import=threading',
        '--hidden-import=subprocess',
        '--hidden-import=msvcrt',
        '--name=T3-Voice-Transcriber-Working',
        'app/t3.py'
    ]
    
    print(f"\nRunning command:")
    print(' '.join(build_cmd))
    print()
    
    try:
        result = subprocess.run(build_cmd, check=True, capture_output=False)
        print("\n✅ Build completed successfully!")
        print("EXE file location: dist/T3-Voice-Transcriber-Working.exe")
        return True
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Build failed with error code: {e.returncode}")
        return False
    except Exception as e:
        print(f"\n❌ Build failed with error: {e}")
        return False

if __name__ == "__main__":
    success = build_exe()
    sys.exit(0 if success else 1) 