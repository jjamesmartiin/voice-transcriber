# speech recognition file using whisper

# Configuration
RECORD_SECONDS = 10  # Change this value to adjust recording duration

# -------------------------------------

import time
import threading
import wave
import sys
import os
import pyaudio
import select

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

# Shared variable to signal early stopping
stop_recording = False

def is_input_available():
    """Check if there's input available without blocking"""
    return select.select([sys.stdin], [], [], 0) == ([sys.stdin], [], [])

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

def countdown():
    global stop_recording
    for i in range(RECORD_SECONDS, 0, -1):
        if stop_recording:
            break
        print(f'Recording time remaining: {i} seconds... (press space to stop)', end='\r')
        time.sleep(1)

def input_thread_func():
    """Thread to check for key presses"""
    global stop_recording
    # Configure terminal for non-blocking input
    try:
        import termios, tty
        old_settings = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin.fileno())

        while not stop_recording:
            if is_input_available():
                c = sys.stdin.read(1)
                if c == ' ':  # Space key
                    print("\nStopping recording early...")
                    stop_recording = True
                    break
            time.sleep(0.1)

        # Restore terminal settings
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
    except (ImportError, AttributeError, termios.error):
        # Different approach for Windows or if terminal handling fails
        print("Press space to stop recording")
        while not stop_recording:
            if msvcrt.kbhit():
                if msvcrt.getch() == b' ':
                    print("\nStopping recording early...")
                    stop_recording = True
                    break
            time.sleep(0.1)

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

    print('Recording... Press space to stop')
    countdown_thread = threading.Thread(target=countdown)
    countdown_thread.daemon = True
    countdown_thread.start()
    
    # Start thread to check for key presses
    input_thread = threading.Thread(target=input_thread_func)
    input_thread.daemon = True
    input_thread.start()
    
    max_chunks = RATE // CHUNK * RECORD_SECONDS
    for i in range(0, max_chunks):
        if stop_recording:
            break
        wf.writeframes(stream.read(CHUNK))
    
    print('\nDone')

    stream.close()
    p.terminate()





