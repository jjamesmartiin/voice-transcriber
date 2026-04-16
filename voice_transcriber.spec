# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Voice Transcriber
Builds a single portable .exe for Windows 11

Build: python -m PyInstaller voice_transcriber.spec --clean
"""
import os

project_root = os.path.dirname(os.path.abspath(SPEC))
src_dir = os.path.join(project_root, 'src')

a = Analysis(
    [os.path.join(src_dir, 'main_windows.py')],
    pathex=[src_dir],
    binaries=[],
    datas=[
        (os.path.join(src_dir, 'sounds', '*.mp3'), 'sounds'),
    ],
    hiddenimports=[
        # Core app modules (loaded dynamically by main_windows.py)
        'main',
        't2',
        'transcribe2',
        'transcribe_whisper',
        'transcribe_cohere',
        'hotkeys_windows',
        'notifications_windows',
        'notifications',
        'hotkeys',
        # Transcription
        'faster_whisper',
        'whisper',
        'torch',
        'transformers',
        'huggingface_hub',
        'sentencepiece',
        'protobuf',
        'accelerate',
        'librosa',
        'datasets',
        'soundfile',
        'sounddevice',
        # Input/Output
        'pynput',
        'pynput.keyboard',
        'pynput.mouse',
        'keyboard',
        'pyperclip',
        # System
        'numpy',
        'scipy',
        'scipy.signal',
        'tkinter',
        'ctypes',
        'winsound',
        'json',
        'queue',
        'threading',
        'tempfile',
        'pathlib',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Linux-only modules
        'evdev',
        'uinput',
        'python-uinput',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='VoiceTranscriber',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
