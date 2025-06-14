# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs

block_cipher = None

# Define data files to include
added_files = [
    ('app/sounds/*.mp3', 'app/sounds'),  # Include sound files
    ('app/transcribe2.py', 'app'),       # Include transcription module
    ('app/visual_notifications.py', 'app'), # Include visual notifications
]

# Collect all data files for problematic packages
datas = added_files

# Try to collect PyAudio data files
try:
    pyaudio_datas, pyaudio_binaries, pyaudio_hiddenimports = collect_all('pyaudio')
    datas.extend(pyaudio_datas)
except:
    pass

# Try to collect faster_whisper data files
try:
    fw_datas, fw_binaries, fw_hiddenimports = collect_all('faster_whisper')
    datas.extend(fw_datas)
except:
    pass

# Try to collect ctranslate2 data files
try:
    ct2_datas, ct2_binaries, ct2_hiddenimports = collect_all('ctranslate2')
    datas.extend(ct2_datas)
except:
    pass

# Hidden imports that PyInstaller might miss
hidden_imports = [
    # Core packages
    'pyaudio',
    'faster_whisper',
    'ctranslate2',
    'tokenizers',
    'pynput',
    'pynput.keyboard',
    'pynput.mouse',
    'pyperclip',
    'numpy',
    'wave',
    'json',
    'threading',
    'subprocess',
    'tkinter',
    'tkinter.ttk',
    'time',
    'os',
    'sys',
    'select',
    'queue',
    'warnings',
    'logging',
    
    # Audio and ML related
    'av',
    'onnxruntime',
    'huggingface_hub',
    'tqdm',
    'typing_extensions',
    'filelock',
    'urllib3',
    'certifi',
    'charset_normalizer',
    'idna',
    'requests',
    
    # Windows specific
    'msvcrt',
    'six',
]

# Binaries to include
binaries = []

# Try to collect binaries for problematic packages
try:
    binaries.extend(collect_dynamic_libs('pyaudio'))
except:
    pass

try:
    binaries.extend(collect_dynamic_libs('ctranslate2'))
except:
    pass

try:
    binaries.extend(collect_dynamic_libs('faster_whisper'))
except:
    pass

a = Analysis(
    ['app/t3.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',  # Exclude large unused packages
        'scipy.spatial.cKDTree',
        'PyQt5',
        'PyQt6',
        'PySide2',
        'PySide6',
        'IPython',
        'jupyter',
        'notebook',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='T3-Voice-Transcriber',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Keep console for debugging
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # You can add an icon file here if you have one
) 