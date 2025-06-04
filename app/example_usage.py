#!/usr/bin/env python3
"""
Example Usage of Visual Notifications Module
Demonstrates how to integrate the visual_notifications module into your applications.
"""

import time
import threading
from visual_notifications import VisualNotification

def simulate_recording_process():
    """Simulate a recording and processing workflow."""
    print("=== Recording Process Simulation ===")
    
    # Create notification instance
    notifier = VisualNotification("Voice Recorder Example")
    
    try:
        # Step 1: Show recording notification
        print("1. Starting recording...")
        notifier.show_recording("Recording audio input")
        time.sleep(4)  # Simulate recording time
        
        # Step 2: Show processing notification
        print("2. Processing audio...")
        notifier.show_processing("Transcribing speech to text")
        time.sleep(3)  # Simulate processing time
        
        # Step 3: Show completion notification
        print("3. Process completed!")
        notifier.show_completed("Text successfully generated")
        time.sleep(3)  # Let user see completion message
        
    except KeyboardInterrupt:
        print("\nProcess interrupted by user")
    finally:
        # Always cleanup
        notifier.cleanup()
        print("Cleanup completed")

def simulate_error_handling():
    """Demonstrate error and warning notifications."""
    print("\n=== Error Handling Example ===")
    
    notifier = VisualNotification("Error Demo App")
    
    try:
        # Show warning
        print("1. Showing warning...")
        notifier.show_warning("Low disk space detected")
        time.sleep(3)
        
        # Show error
        print("2. Showing error...")
        notifier.show_error("Failed to connect to server")
        time.sleep(3)
        
    finally:
        notifier.cleanup()

def demonstrate_custom_notifications():
    """Show various custom notification examples."""
    print("\n=== Custom Notifications Example ===")
    
    notifier = VisualNotification("Custom Demo")
    
    try:
        # Custom success notification
        print("1. Custom success notification...")
        notifier.show_notification("Upload completed successfully", "#00aa00", False, "📤")
        time.sleep(3)
        
        # Custom info notification
        print("2. Custom info notification...")
        notifier.show_notification("New message received", "#0066cc", False, "📧")
        time.sleep(3)
        
        # Custom persistent notification
        print("3. Persistent notification (will hide after 4 seconds)...")
        notifier.show_notification("Waiting for user input", "#8800aa", True, "⏳")
        time.sleep(4)
        notifier.hide_notification()
        
    finally:
        notifier.cleanup()

def threaded_notifications_example():
    """Demonstrate notifications in threaded applications."""
    print("\n=== Threaded Application Example ===")
    
    notifier = VisualNotification("Threaded App")
    
    def background_task():
        """Simulate a background task with notifications."""
        time.sleep(1)
        notifier.show_processing("Background task running")
        time.sleep(3)
        notifier.show_completed("Background task finished")
    
    try:
        print("Starting background task...")
        thread = threading.Thread(target=background_task)
        thread.start()
        
        # Main thread can continue other work
        print("Main thread continues working...")
        thread.join()  # Wait for background task
        
        time.sleep(2)  # Let completion message show
        
    finally:
        notifier.cleanup()

def quick_functions_example():
    """Demonstrate the convenience functions."""
    print("\n=== Quick Functions Example ===")
    
    from visual_notifications import (
        show_recording_notification,
        show_processing_notification, 
        show_completed_notification,
        show_error_notification
    )
    
    # These are one-liner notifications
    print("1. Quick recording notification...")
    recorder = show_recording_notification("Quick App", "Quick record")
    time.sleep(2)
    recorder.cleanup()
    
    print("2. Quick processing notification...")
    processor = show_processing_notification("Quick App", "Quick process")
    time.sleep(2)
    processor.cleanup()
    
    print("3. Quick completion notification...")
    completer = show_completed_notification("Quick App", "Quick done")
    time.sleep(3)  # Auto-hides, but cleanup anyway
    completer.cleanup()

def main():
    """Run all examples."""
    print("Visual Notifications Module - Example Usage")
    print("=" * 50)
    
    try:
        # Run different examples
        simulate_recording_process()
        simulate_error_handling()
        demonstrate_custom_notifications()
        threaded_notifications_example()
        quick_functions_example()
        
        print("\n" + "=" * 50)
        print("All examples completed successfully!")
        
    except KeyboardInterrupt:
        print("\n\nExamples interrupted by user")
    except Exception as e:
        print(f"\nError running examples: {e}")

if __name__ == "__main__":
    main() 