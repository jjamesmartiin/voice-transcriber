#!/usr/bin/env python3
"""
Windows Voice Transcriber - Main Entry Point
This is a self-contained Windows version that uses Windows-specific modules
"""
import sys
import os

# Set default backend to Whisper for Windows (works offline)
os.environ.setdefault("VT_MODEL_BACKEND", "whisper")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# Get the src directory
src_dir = os.path.dirname(os.path.abspath(__file__))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

# Patch the module imports before main loads
import importlib.util

# Load hotkeys_windows
spec = importlib.util.spec_from_file_location("hotkeys_windows", os.path.join(src_dir, "hotkeys_windows.py"))
hotkeys_mod = importlib.util.module_from_spec(spec)
sys.modules['hotkeys'] = hotkeys_mod
spec.loader.exec_module(hotkeys_mod)

# Load notifications_windows
spec = importlib.util.spec_from_file_location("notifications_windows", os.path.join(src_dir, "notifications_windows.py"))
notif_mod = importlib.util.module_from_spec(spec)
sys.modules['notifications'] = notif_mod
spec.loader.exec_module(notif_mod)

# Now load main with patched modules
import main as main_orig

# Replace classes with Windows versions
main_orig.VisualNotification = notif_mod.VisualNotification
main_orig.WaylandGlobalHotkeys = hotkeys_mod.WindowsGlobalHotkeys

# Windows permission check (always passes)
def check_permissions():
    import logging
    logging.getLogger(__name__).debug("Windows - permission check passed")
    return True

main_orig.check_permissions = check_permissions

# Run the app
if __name__ == "__main__":
    print("Voice Transcriber - Windows Edition")
    print("=" * 40)
    
    app = main_orig.SimpleVoiceTranscriber()
    app.run()