# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['app\\t3.py'],
    pathex=[],
    binaries=[],
    datas=[('app/sounds', 'app/sounds'), ('app/transcribe2.py', 'app'), ('app/visual_notifications.py', 'app')],
    hiddenimports=['pyaudio', 'pynput', 'faster_whisper', 'ctranslate2', 'numpy', 'pyperclip'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='T3-Voice-Transcriber-Simple',
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
