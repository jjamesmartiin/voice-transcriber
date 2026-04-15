#!/usr/bin/env python3
"""
Windows Voice Transcriber - Main Entry Point
This is a self-contained Windows version that uses Windows-specific modules
"""
import sys
import os

# Set default backend to Whisper for Windows (works offline)
# os.environ.setdefault("VT_MODEL_BACKEND", "whisper")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# Determine if running as bundled EXE
def get_app_dir():
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

app_dir = get_app_dir()
if app_dir not in sys.path:
    sys.path.insert(0, app_dir)

# Patch the module imports before main loads
import importlib.util

# Load hotkeys_windows
spec = importlib.util.spec_from_file_location("hotkeys_windows", os.path.join(app_dir, "hotkeys_windows.py"))
hotkeys_mod = importlib.util.module_from_spec(spec)
sys.modules['hotkeys'] = hotkeys_mod
spec.loader.exec_module(hotkeys_mod)

# Load notifications_windows
spec = importlib.util.spec_from_file_location("notifications_windows", os.path.join(app_dir, "notifications_windows.py"))
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