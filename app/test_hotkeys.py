#!/usr/bin/env python3
"""
Simple test script to verify Alt+Shift hotkey detection is working
"""

import sys
import os
import logging
import time

# Add the current directory to path so we can import from t3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from t3 import WaylandGlobalHotkeys

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class HotkeyTester:
    def __init__(self):
        self.recording = False
    
    def start_recording(self):
        if not self.recording:
            self.recording = True
            print("🔴 HOTKEY PRESSED - Recording would start now")
            logger.info("Recording started")
    
    def stop_recording(self):
        if self.recording:
            self.recording = False
            print("⏹️  HOTKEY RELEASED - Recording would stop now")
            logger.info("Recording stopped")

def main():
    print("Hotkey Test Tool")
    print("================")
    print("This tool tests if Alt+Shift hotkey detection is working.")
    print("Press Alt+Shift and hold to see if it's detected.")
    print("Press Ctrl+C to exit.")
    print()
    
    tester = HotkeyTester()
    hotkey_system = WaylandGlobalHotkeys(
        callback_start=tester.start_recording,
        callback_stop=tester.stop_recording
    )
    
    if not hotkey_system.devices:
        print("❌ No keyboard devices found!")
        print("Make sure you're running as root or in the input group.")
        return False
    
    print(f"✅ Found {len(hotkey_system.devices)} keyboard device(s)")
    print("Hold Alt+Shift to test hotkey detection...")
    print()
    
    try:
        hotkey_system.run()
    except KeyboardInterrupt:
        print("\nTest finished.")
        hotkey_system.stop()
        return True

if __name__ == "__main__":
    main() 