import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import main

def test_wsl_app_lifecycle():
    print("Testing Voice Transcriber initialization in NixOS WSL...")
    app = main.SimpleVoiceTranscriber()
    print("-> App created and model preloaded!")
    assert app.hotkey_system is not None
    print("-> Hotkey system active!")
    time.sleep(1)
    app.cleanup()
    print("-> App cleanup successful!")

if __name__ == "__main__":
    test_wsl_app_lifecycle()
