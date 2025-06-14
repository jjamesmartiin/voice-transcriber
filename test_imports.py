#!/usr/bin/env python3
"""Test script to check package availability"""

packages_to_test = [
    'pyaudio',
    'pynput',
    'faster_whisper',
    'ctranslate2',
    'numpy',
    'pyperclip',
    'tkinter'
]

print("Testing package imports:")
print("=" * 40)

for package in packages_to_test:
    try:
        if package == 'pynput':
            import pynput
            from pynput import keyboard
            print(f"✅ {package} - Available")
        elif package == 'faster_whisper':
            import faster_whisper
            print(f"✅ {package} - Available")
        elif package == 'ctranslate2':
            import ctranslate2
            print(f"✅ {package} - Available")
        elif package == 'pyaudio':
            import pyaudio
            print(f"✅ {package} - Available (v{pyaudio.__version__})")
        elif package == 'numpy':
            import numpy
            print(f"✅ {package} - Available (v{numpy.__version__})")
        elif package == 'pyperclip':
            import pyperclip
            print(f"✅ {package} - Available")
        elif package == 'tkinter':
            import tkinter
            print(f"✅ {package} - Available")
        else:
            exec(f"import {package}")
            print(f"✅ {package} - Available")
    except ImportError as e:
        print(f"❌ {package} - Not available: {e}")
    except Exception as e:
        print(f"⚠️  {package} - Error: {e}")

print("=" * 40)
print("Test completed!") 