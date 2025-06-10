#!/usr/bin/env python3
"""
Interactive Menu Module
Handles user interface, menu system, and interactive modes.
"""

import sys
import os
import logging
from hotkey_system import check_permissions
from audio_recorder import load_audio_config, select_audio_device, record_audio_stream, stop_recording, CONFIG_FILE
from audio_processor import process_audio_stream, get_device
from text_typer import type_text
from transcribe import preload_model
import threading

logger = logging.getLogger(__name__)

def print_usage():
    """Print usage information"""
    print()
    print("Usage: python app/t3_main.py [MODE]")
    print()
    print("Modes:")
    print("  1        Global hotkeys (Alt+Shift) - requires root/input group")
    print("  2        Interactive mode (Space to record)")
    print("  3 or i   Select audio device")
    print("  4        Exit")
    print("  help     Show this help message")
    print()
    print("Examples:")
    print("  python app/t3_main.py 1      # Start global hotkey mode")
    print("  python app/t3_main.py 2      # Start interactive mode")
    print("  python app/t3_main.py i      # Open device selection")
    print("  python app/t3_main.py        # Show interactive menu")
    print()

def run_interactive_mode():
    """Run interactive mode"""
    device = get_device()
    interactive_mode_running = True
    
    while interactive_mode_running:
        try:
            print("> ", end="", flush=True)
            
            import termios, tty
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setraw(sys.stdin.fileno())
                ch = sys.stdin.read(1)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            
            if ch in [' ', '\r', '\n']:
                print()
                # Record and transcribe
                stop_recording.clear()
                
                # Start recording
                recorded_frames = []
                def record_wrapper():
                    nonlocal recorded_frames
                    recorded_frames = record_audio_stream(interactive_mode=True)
                
                record_thread = threading.Thread(target=record_wrapper)
                record_thread.start()
                
                logger.info("Recording... Press Space to stop")
                
                # Wait for space to stop
                while True:
                    try:
                        tty.setraw(sys.stdin.fileno())
                        ch2 = sys.stdin.read(1)
                        if ch2 == ' ':
                            stop_recording.set()
                            break
                    finally:
                        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                
                # Wait for recording to finish
                record_thread.join()
                
                # Process audio
                result, transcribe_time = process_audio_stream(recorded_frames, device=device)
                transcription = result.strip()
                
                if transcription:
                    if type_text(transcription):
                        logger.info(f"✅ Typed: {transcription}")
                    else:
                        logger.error("Failed to type text")
                else:
                    logger.info("❌ No speech detected")
                
            elif ch.lower() == 'i':
                print()
                select_audio_device()
            elif ch.lower() in ['q', 'Q']:
                print("\nExiting interactive mode...")
                interactive_mode_running = False
                break
            elif ch.lower() == 'm':
                print("\nExiting interactive mode...")
                interactive_mode_running = False
                break
            
            print("\nReady (Space=record, i=device, q=quit)")
            
        except KeyboardInterrupt:
            print("\nExiting interactive mode...")
            interactive_mode_running = False
            break
        except ImportError:
            # Fallback for systems without termios
            choice = input("Space=record, i=device, q=quit: ").strip().lower()
            if choice in ['', ' ']:
                # Simple recording without hotkey stop
                stop_recording.clear()
                
                recorded_frames = []
                def record_wrapper():
                    nonlocal recorded_frames
                    recorded_frames = record_audio_stream(interactive_mode=True)
                
                record_thread = threading.Thread(target=record_wrapper)
                record_thread.start()
                
                input("Recording... Press Enter to stop")
                stop_recording.set()
                record_thread.join()
                
                result, _ = process_audio_stream(recorded_frames, device=device)
                transcription = result.strip()
                
                if transcription:
                    if type_text(transcription):
                        logger.info(f"✅ Typed: {transcription}")
                else:
                    logger.info("❌ No speech detected")
            elif choice == 'i':
                select_audio_device()
            elif choice in ['q', 'm']:
                interactive_mode_running = False
                break

def run_interactive_menu():
    """Run the interactive menu"""
    device = get_device()
    
    # Load audio config
    load_audio_config()
    
    # Check if this is first run (no config file exists)
    first_run = not os.path.exists(CONFIG_FILE)
    
    # Preload model
    preload_thread = preload_model(device=device)
    
    # On first run, automatically show device selection
    if first_run:
        logger.info("")
        logger.info("🎤 FIRST RUN SETUP")
        logger.info("Let's configure your audio input device...")
        logger.info("")
        
        if select_audio_device():
            logger.info("✅ Audio device configured!")
        else:
            logger.info("❌ No device selected - using default")
        
        logger.info("")
        logger.info("🔧 KEYBOARD PERMISSIONS")
        logger.info("For global hotkeys (Alt+Shift), you need:")
        logger.info("  • Run as root, OR")
        logger.info("  • Add user to input group: sudo usermod -a -G input $USER")
        logger.info("  • Then log out and back in")
        logger.info("")
        input("Press Enter to continue to main menu...")
    
    while True:  # Main menu loop
        logger.info("")
        logger.info("Choose mode:")
        logger.info("1. Global hotkeys (Alt+Shift) - requires root/input group")
        logger.info("2. Interactive mode (Space to record)")
        logger.info("3. Select audio device")
        logger.info("i. Select audio device (shortcut)")
        logger.info("4. Exit")
        
        try:
            choice = input("> ").strip().lower()
            
            if choice == '1':
                # Global hotkey mode
                if not check_permissions():
                    logger.error("Cannot use global hotkeys without proper permissions")
                    input("Press Enter to return to main menu...")
                    continue  # Return to menu
                
                # Import and run main transcriber
                from t3_main import T3VoiceTranscriber
                transcriber = T3VoiceTranscriber()
                result = transcriber.run()
                # After hotkey mode exits, return to menu
                logger.info("Global hotkey mode ended")
                input("Press Enter to return to main menu...")
                continue
                
            elif choice == '2':
                # Interactive mode
                logger.info("Interactive mode - Press Space to record, 'i' for device selection, 'q' to quit")
                
                # Wait for model
                if preload_thread.is_alive():
                    logger.info("Waiting for model to load...")
                    preload_thread.join()
                    logger.info("Model loaded!")
                
                run_interactive_mode()
                # After interactive mode ends, return to main menu
                continue
                
            elif choice == '3' or choice == 'i':
                # Device selection
                select_audio_device()
                # After device selection (whether successful or cancelled), return to menu
                continue
                
            elif choice == '4':
                logger.info("Exiting...")
                break
                
            else:
                logger.info("Invalid choice. Please enter 1, 2, 3, i, or 4.")
                continue
                
        except KeyboardInterrupt:
            logger.info("\nExiting...")
            break 