#!/usr/bin/env python3
"""
Build script for Voice Transcriber Windows .exe
Creates a portable executable with all dependencies bundled.

Usage:
    python build_windows.py
"""
import subprocess
import sys
import os

def main():
    project_root = os.path.dirname(os.path.abspath(__file__))
    spec_file = os.path.join(project_root, "voice_transcriber.spec")

    if not os.path.exists(spec_file):
        print(f"Error: {spec_file} not found")
        sys.exit(1)

    print("Building Voice Transcriber .exe ...")
    print(f"Spec file: {spec_file}")
    print(f"Project root: {project_root}")
    print()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        spec_file,
        "--clean",
        "--noconfirm",
    ]

    print(f"Running: {' '.join(cmd)}")
    print()

    result = subprocess.run(cmd, cwd=project_root)

    if result.returncode == 0:
        exe_path = os.path.join(project_root, "dist", "VoiceTranscriber.exe")
        print()
        print("Build successful!")
        print(f"Executable: {exe_path}")
        print()
        print("To run: .\\dist\\VoiceTranscriber.exe")
    else:
        print()
        print("Build failed. Check the output above for errors.")
        sys.exit(1)


if __name__ == "__main__":
    main()
