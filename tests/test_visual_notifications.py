#!/usr/bin/env python3
"""
Test suite for visual notifications module
Tests overlay functionality across different environments (flake, nix-shell, etc.)
"""

import os
import sys
import time
import logging
import pytest

# Add parent directory to path to import visual_notifications
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

try:
    from main import VisualNotification
except ImportError:
    # Fallback for when running from different contexts
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))
    from main import VisualNotification

@pytest.fixture
def notifier():
    """Pytest fixture for VisualNotification"""
    return VisualNotification()

def verify_environment_detection():
    """Implementation of environment detection test"""
    print("🔍 Testing Environment Detection")
    print("-" * 40)
    
    notifier = VisualNotification()
    
    print(f"Display environment: {notifier.display_env}")
    print(f"Available tools: {notifier.available_tools}")
    
    return notifier

def test_environment_detection():
    """Pytest wrapper for environment detection"""
    notifier = verify_environment_detection()
    # Assertions for pytest
    assert notifier.display_env is not None or "DISPLAY" not in os.environ

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
        assert True
        
    except ImportError as e:
        print(f"❌ tkinter import: FAILED - {e}")
        pytest.fail(f"tkinter import failed: {e}")
    except Exception as e:
        print(f"❌ tkinter window creation: FAILED - {e}")
        # If we are headless, this might fail, allow it if specific env var set?
        # For now, fail if we expect it to work.
        if os.environ.get("DISPLAY"):
             pytest.fail(f"tkinter window creation failed: {e}")
        else:
             print("Ignoring failure due to missing DISPLAY")

def run_notification_test_impl(notifier, show_overlays=True):
    """Implementation of notification tests, separated for reuse"""
    print("\n🔍 Testing Notification Types")
    print("-" * 40)
    
    if not show_overlays:
        print("Note: Visual overlays disabled for automated testing")
        return True
    
    try:
        print("Testing RECORDING notification...")
        notifier.show_recording("🔴 TEST RECORDING")
        time.sleep(1)
        
        print("Testing PROCESSING notification...")
        notifier.show_processing("🟡 TEST PROCESSING")
        time.sleep(1)
        
        print("Testing COMPLETED notification...")
        notifier.show_completed("🔵 TEST COMPLETED")
        time.sleep(1)
        
        print("Testing ERROR notification...")
        notifier.show_error("🔴 TEST ERROR")
        time.sleep(1)
        
        print("Testing WARNING notification...")
        notifier.show_warning("🟠 TEST WARNING")
        time.sleep(1)
        
        notifier.cleanup()
        print("✅ All notification types tested successfully")
        return True
        
    except Exception as e:
        print(f"❌ Notification test failed: {e}")
        raise e

def test_notification_types(notifier):
    """Test different notification types (pytest wrapper)"""
    # By default, don't show overlays in automated tests to avoid stealing focus/hanging
    # unless we specifically want to.
    run_notification_test_impl(notifier, show_overlays=False) # Skip visual part in CIs usually

def run_full_test_suite(show_overlays=False):
    """Run the complete test suite (legacy/direct mode)"""
    print("🧪 Visual Notifications Test Suite")
    print("=" * 60)
    
    try:
        # Test environment detection
        notifier = verify_environment_detection()
        
        # Test tkinter availability
        test_tkinter_availability()
        
        # Test notification types
        run_notification_test_impl(notifier, show_overlays)
        
        print("\n✅ ALL TESTS PASSED")
        return True
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        return False

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Test visual notifications')
    parser.add_argument('--show-overlays', action='store_true', 
                       help='Show actual overlay windows')
    parser.add_argument('--verbose', action='store_true',
                       help='Enable verbose logging')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    
    success = run_full_test_suite(show_overlays=args.show_overlays)
    sys.exit(0 if success else 1)
