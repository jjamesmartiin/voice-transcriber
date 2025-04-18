# ok now we should focus on automating the process of transcribing the audio

# we have rec.py to record the audio and save it as a wav file
# we have transcribe.py to transcribe the audio

# now we need to combine these two files

# first we need to record the audio
import rec

# then we need to transcribe the audio
import transcribe
import pyperclip

# Write transcription to a temporary file
with open('/tmp/transcription.txt', 'w') as f:
    f.write(transcribe.result["text"].strip())


import subprocess

# Copy transcription to clipboard
subprocess.run(['xclip', '-selection', 'clipboard'], input=transcribe.result["text"].strip().encode())

# Execute vim to read and copy the transcription
# subprocess.run(['vim', '-c', 'normal! ggdG', '-c', ':r /tmp/transcription.txt', '-c', 'normal! ggVG"*y', '-c', 'q!', '/tmp/transcription.txt'])

# the above command is working sort of, it copies but 

# 
# Hello, test12.
#
# it has extra return before and after 
# so we need to remove the extra return







