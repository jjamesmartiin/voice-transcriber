#!/usr/bin/env python3
# Optimized script to record audio and transcribe it

import sys
import os
import pyperclip
import subprocess
from transcribe import transcribe_audio

def main():
    # Check if GPU is available
    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        device = None
    
    print("Recording audio...")
    # Use subprocess to run rec.py
    subprocess.run(["python", "rec.py"], check=True)
    
    print("Transcribing audio...")
    # Transcribe with GPU if available
    result = transcribe_audio(device=device)
    
    # Get transcription and strip whitespace
    transcription = result["text"].strip()
    
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
    
    print(f"Transcription: {transcription}")
    return transcription

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







