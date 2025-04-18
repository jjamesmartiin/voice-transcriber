# speech recognition file using whisper

# Configuration
RECORD_SECONDS = 7  # Change this value to adjust recording duration

# -------------------------------------

import time
import threading
import wave
import sys
import os
import pyaudio

# Redirect stderr temporarily to suppress ALSA warnings
stderr_fd = os.dup(2)
devnull_fd = os.open(os.devnull, os.O_WRONLY)
os.dup2(devnull_fd, 2)

CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1 if sys.platform == 'darwin' else 2
RATE = 44100

# Restore stderr
os.dup2(stderr_fd, 2)
os.close(devnull_fd)
os.close(stderr_fd)

def countdown():
    for i in range(RECORD_SECONDS, 0, -1):
        print(f'Recording time remaining: {i} seconds...')
        time.sleep(1)

with wave.open('output.wav', 'wb') as wf:
    # Redirect stderr again for PyAudio operations
    stderr_fd = os.dup(2)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull_fd, 2)
    
    p = pyaudio.PyAudio()
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(p.get_sample_size(FORMAT))
    wf.setframerate(RATE)

    stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True)
    
    # Restore stderr for countdown output
    os.dup2(stderr_fd, 2)
    os.close(devnull_fd)
    os.close(stderr_fd)

    print('Recording...')
    countdown_thread = threading.Thread(target=countdown)
    countdown_thread.start()
    
    for _ in range(0, RATE // CHUNK * RECORD_SECONDS):
        wf.writeframes(stream.read(CHUNK))
    
    countdown_thread.join()
    print('Done')

    stream.close()
    p.terminate()





