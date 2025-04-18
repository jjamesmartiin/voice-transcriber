# speech recognition file using whisper

# Configuration
RECORD_SECONDS = 7  # Change this value to adjust recording duration

# -------------------------------------

import time
import threading

import wave
import sys

import pyaudio

CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1 if sys.platform == 'darwin' else 2
RATE = 44100

def countdown():
    for i in range(RECORD_SECONDS, 0, -1):
        print(f'Recording time remaining: {i} seconds...')
        time.sleep(1)

with wave.open('output.wav', 'wb') as wf:
    p = pyaudio.PyAudio()
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(p.get_sample_size(FORMAT))
    wf.setframerate(RATE)

    stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True)

    print('Recording...')
    countdown_thread = threading.Thread(target=countdown)
    countdown_thread.start()
    
    for _ in range(0, RATE // CHUNK * RECORD_SECONDS):
        wf.writeframes(stream.read(CHUNK))
    
    countdown_thread.join()
    print('Done')

    stream.close()
    p.terminate()





