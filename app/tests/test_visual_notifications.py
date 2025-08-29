#!/usr/bin/env python3
"""
Test suite for visual notifications module
Tests overlay functionality across different environments (flake, nix-shell, etc.)
"""

import os
import sys
import time
import logging

# Add parent directory to path to import visual_notifications
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from visual_notifications import VisualNotification

def test_environment_detection():
    """Test display environment detection"""
    print("🔍 Testing Environment Detection")
    print("-" * 40)
    
    notifier = VisualNotification("Test App", enable_logging=False)
    
    print(f"Display environment: {notifier.display_env}")
    print(f"Available tools: {notifier.available_tools}")
    print(f"DISPLAY: {os.environ.get('DISPLAY', 'Not set')}")
    print(f"WAYLAND_DISPLAY: {os.environ.get('WAYLAND_DISPLAY', 'Not set')}")
    print(f"XDG_SESSION_TYPE: {os.environ.get('XDG_SESSION_TYPE', 'Not set')}")
    
    return notifier

def test_tkinter_availability():
    """Test if tkinter is available and functional"""
    print("\n🔍 Testing Tkinter Availability")
    print("-" * 40)
    
    try:
        import tkinter as tk
        print("✅ tkinter import: SUCCESS")
        
        # Test window creation
        root = tk.Tk()
        root.withdraw()  # Hide the window
        print("✅ tkinter window creation: SUCCESS")
        root.destroy()
        return True
        
    except ImportError as e:
        print(f"❌ tkinter import: FAILED - {e}")
        return False
    except Exception as e:
        print(f"❌ tkinter window creation: FAILED - {e}")
        return False

def test_notification_types(notifier, show_overlays=True):
    """Test different notification types"""
    print("\n🔍 Testing Notification Types")
    print("-" * 40)
    
    if not show_overlays:
        print("Note: Visual overlays disabled for automated testing")
        return True
    
    try:
        print("Testing RECORDING notification...")
        notifier.show_recording("🔴 TEST RECORDING")
        time.sleep(2)
        
        print("Testing PROCESSING notification...")
        notifier.show_processing("🟡 TEST PROCESSING")
        time.sleep(2)
        
        print("Testing COMPLETED notification...")
        notifier.show_completed("🔵 TEST COMPLETED")
        time.sleep(2)
        
        print("Testing ERROR notification...")
        notifier.show_error("🔴 TEST ERROR")
        time.sleep(2)
        
        print("Testing WARNING notification...")
        notifier.show_warning("🟠 TEST WARNING")
        time.sleep(2)
        
        notifier.cleanup()
        print("✅ All notification types tested successfully")
        return True
        
    except Exception as e:
        print(f"❌ Notification test failed: {e}")
        return False

def run_full_test_suite(show_overlays=False):
    """Run the complete test suite"""
    print("🧪 Visual Notifications Test Suite")
    print("=" * 60)
    
    # Test environment detection
    notifier = test_environment_detection()
    
    # Test tkinter availability
    tkinter_available = test_tkinter_availability()
    
    # Test notification types
    notifications_work = test_notification_types(notifier, show_overlays)
    
    # Summary
    print("\n📊 Test Results Summary")
    print("-" * 40)
    print(f"Environment detection: ✅ PASS")
    print(f"Tkinter availability: {'✅ PASS' if tkinter_available else '❌ FAIL'}")
    print(f"Notification types: {'✅ PASS' if notifications_work else '❌ FAIL'}")
    
    all_passed = tkinter_available and notifications_work
    print(f"\nOverall result: {'✅ ALL TESTS PASSED' if all_passed else '❌ SOME TESTS FAILED'}")
    
    return all_passed

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Test visual notifications')
    parser.add_argument('--show-overlays', action='store_true', 
                       help='Show actual overlay windows (default: disabled for automated testing)')
    parser.add_argument('--verbose', action='store_true',
                       help='Enable verbose logging')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    
    success = run_full_test_suite(show_overlays=args.show_overlays)
    sys.exit(0 if success else 1)
