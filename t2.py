#!/usr/bin/env python3
# Optimized script to record audio and transcribe it

import sys
import os
import pyperclip
import subprocess
from transcribe2 import transcribe_audio

def record_and_transcribe():
    # Check if GPU is available
    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        device = "cpu"
    
    print("Recording audio...")
    # Use subprocess to run rec.py
    subprocess.run(["python", "rec.py"], check=True)
    
    print("Transcribing audio...")
    # Transcribe with GPU if available
    result = transcribe_audio(device=device)
    
    # Get transcription and strip whitespace
    transcription = result.strip()
    
    # Copy to clipboard
    try:
        pyperclip.copy(transcription)
        print("Transcription copied to clipboard")
    except Exception as e:
        print(f"Failed to use pyperclip: {e}")
        try:
            subprocess.run(['xclip', '-selection', 'clipboard'], 
                          input=transcription.encode(), check=True)
            print("Transcription copied using xclip")
        except Exception:
            print("Unable to copy to clipboard")
    
    print(f"{transcription}")
    return transcription

def getch():
    """Get a single character from standard input without waiting for enter"""
    try:
        # For Unix/Linux/MacOS
        import termios, tty
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return ch
    except ImportError:
        # For Windows
        import msvcrt
        return msvcrt.getch().decode()

def main():
    print("T2 Transcription Tool")
    print("Press Enter or Space to start recording, or type 'q' to exit")
    
    while True:
        try:
            print("> ", end="", flush=True)
            ch = getch()
            
            if ch in [' ', '\r', '\n']:  # Space or Enter key
                print()  # Move to next line after keypress
                record_and_transcribe()
            elif ch.lower() in ['q', 'Q']:
                print("\nExiting...")
                break
            
            print("\nReady for next recording (press Enter or Space to start, or type 'q' to exit)")
        except KeyboardInterrupt:
            print("\nExiting...")
            break

if __name__ == "__main__":
    main()

# ok now we should focus on automating the process of transcribing the audio

# we have rec.py to record the audio and save it as a wav file
# we have transcribe.py to transcribe the audio

# now we need to combine these two files

# first we need to record the audio
# import rec

# # then we need to transcribe the audio
# import transcribe
# import pyperclip

# # Write transcription to a temporary file
# with open('/tmp/transcription.txt', 'w') as f:
#     f.write(transcribe.result["text"].strip())


# import subprocess

# # Copy transcription to clipboard
# subprocess.run(['xclip', '-selection', 'clipboard'], input=transcribe.result["text"].strip().encode())

# Execute vim to read and copy the transcription
# subprocess.run(['vim', '-c', 'normal! ggdG', '-c', ':r /tmp/transcription.txt', '-c', 'normal! ggVG"*y', '-c', 'q!', '/tmp/transcription.txt'])

# the above command is working sort of, it copies but 

# 
# Hello, test12.
#
# it has extra return before and after 
# so we need to remove the extra return







