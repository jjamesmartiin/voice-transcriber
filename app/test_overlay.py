#!/usr/bin/env python3
"""
Test Overlay - Interactive demonstration of T3.py's visual notification system
This test simulates the complete voice transcriber workflow with visual overlays.
"""

import time
import threading
import sys
import os

# Import the visual notifications module
from visual_notifications import VisualNotification

def clear_screen():
    """Clear the terminal screen"""
    os.system('clear' if os.name == 'posix' else 'cls')

def print_header():
    """Print the test header"""
    print("🎤 T3 Voice Transcriber - Overlay Test")
    print("=" * 50)
    print()

def print_controls():
    """Print available controls"""
    print("Controls:")
    print("  r - Start recording simulation")
    print("  p - Show processing simulation")
    print("  c - Show completion simulation")
    print("  e - Show error simulation")
    print("  w - Show warning simulation")
    print("  f - Full workflow simulation")
    print("  s - Show all states quickly")
    print("  q - Quit")
    print()

def simulate_recording(notifier):
    """Simulate the recording phase"""
    print("🔴 Starting recording simulation...")
    notifier.show_recording("Recording - Release Alt+Shift to stop")
    
    # Simulate recording duration with countdown
    for i in range(5, 0, -1):
        print(f"Recording... {i} seconds remaining", end='\r')
        time.sleep(1)
    print("Recording complete!                    ")

def simulate_processing(notifier):
    """Simulate the processing phase"""
    print("⚡ Processing audio...")
    notifier.show_processing("Transcribing audio")
    
    # Simulate processing time
    for i in range(3):
        print(f"Processing{'.' * (i + 1)}", end='\r')
        time.sleep(1)
    print("Processing complete!   ")

def simulate_completion(notifier):
    """Simulate successful completion"""
    print("✅ Transcription completed!")
    notifier.show_completed("Text typed successfully")
    time.sleep(2)

def simulate_error(notifier):
    """Simulate an error state"""
    print("❌ Error occurred!")
    notifier.show_error("Transcription failed")
    time.sleep(3)

def simulate_warning(notifier):
    """Simulate a warning state"""
    print("⚠️ Warning!")
    notifier.show_warning("No speech detected")
    time.sleep(3)

def full_workflow_simulation(notifier):
    """Simulate the complete T3.py workflow"""
    print("🎬 Starting full workflow simulation...")
    print()
    
    # Phase 1: Recording
    print("Phase 1: Recording")
    simulate_recording(notifier)
    time.sleep(1)
    
    # Phase 2: Processing
    print("\nPhase 2: Processing")
    simulate_processing(notifier)
    time.sleep(1)
    
    # Phase 3: Completion (80% chance) or Error (20% chance)
    import random
    if random.random() < 0.8:
        print("\nPhase 3: Success")
        simulate_completion(notifier)
    else:
        print("\nPhase 3: Error")
        simulate_error(notifier)
    
    print("\n🎬 Workflow simulation complete!")

def show_all_states(notifier):
    """Quickly show all notification states"""
    states = [
        ("Recording", lambda: notifier.show_recording("Recording Audio")),
        ("Processing", lambda: notifier.show_processing("Transcribing")),
        ("Completed", lambda: notifier.show_completed("Success")),
        ("Error", lambda: notifier.show_error("Failed")),
        ("Warning", lambda: notifier.show_warning("No Speech")),
    ]
    
    for state_name, state_func in states:
        print(f"Showing {state_name} state...")
        state_func()
        time.sleep(2)
    
    print("All states demonstrated!")

def interactive_mode():
    """Run the interactive test mode"""
    # Create notification instance
    notifier = VisualNotification("T3 Voice Transcriber Test")
    
    try:
        while True:
            clear_screen()
            print_header()
            print_controls()
            
            try:
                choice = input("Enter your choice: ").strip().lower()
                
                if choice == 'q':
                    print("Exiting test...")
                    break
                elif choice == 'r':
                    simulate_recording(notifier)
                elif choice == 'p':
                    simulate_processing(notifier)
                elif choice == 'c':
                    simulate_completion(notifier)
                elif choice == 'e':
                    simulate_error(notifier)
                elif choice == 'w':
                    simulate_warning(notifier)
                elif choice == 'f':
                    full_workflow_simulation(notifier)
                elif choice == 's':
                    show_all_states(notifier)
                else:
                    print("Invalid choice. Please try again.")
                
                if choice != 'q':
                    input("\nPress Enter to continue...")
                    
            except KeyboardInterrupt:
                print("\nExiting test...")
                break
                
    finally:
        # Clean up
        notifier.cleanup()
        print("Test completed!")

def demo_mode():
    """Run automatic demo mode"""
    print("🎬 Running automatic demo...")
    notifier = VisualNotification("T3 Voice Transcriber Demo")
    
    try:
        # Show each state with explanation
        demos = [
            ("Recording State", "This appears when Alt+Shift is held", 
             lambda: notifier.show_recording("Recording - Release Alt+Shift to stop")),
            ("Processing State", "This appears while transcribing audio",
             lambda: notifier.show_processing("Transcribing audio")),
            ("Success State", "This appears when text is typed successfully",
             lambda: notifier.show_completed("Text typed successfully")),
            ("Error State", "This appears when transcription fails",
             lambda: notifier.show_error("Transcription failed")),
            ("Warning State", "This appears when no speech is detected",
             lambda: notifier.show_warning("No speech detected")),
        ]
        
        for title, description, demo_func in demos:
            print(f"\n{title}")
            print(f"Description: {description}")
            print("Showing overlay...")
            demo_func()
            time.sleep(3)
        
        print("\n🎬 Demo completed!")
        
    finally:
        notifier.cleanup()

def main():
    """Main function"""
    print("T3 Voice Transcriber - Overlay Test")
    print("=" * 40)
    print()
    
    if len(sys.argv) > 1 and sys.argv[1].lower() == 'demo':
        demo_mode()
    else:
        print("Choose mode:")
        print("1. Interactive mode (manual control)")
        print("2. Demo mode (automatic)")
        print()
        
        try:
            choice = input("Enter choice (1 or 2): ").strip()
            
            if choice == '1':
                interactive_mode()
            elif choice == '2':
                demo_mode()
            else:
                print("Invalid choice. Running interactive mode...")
                interactive_mode()
                
        except KeyboardInterrupt:
            print("\nExiting...")

if __name__ == "__main__":
    main() 