# speech recognition file using whisper

import whisper

# import pyaudio
# import sys
# import wave

# load the model
model = whisper.load_model("base")
filename = "test.mp3"


# -------------------------------------



# pyaudio example:
import wave
import sys

import pyaudio

CHUNK = 1024

filename = sys.argv[1] if len(sys.argv) > 1 else "output.wav"

with wave.open(filename, 'rb') as wf:
    # Instantiate PyAudio and initialize PortAudio system resources (1)
    p = pyaudio.PyAudio()

    # Open stream (2)
    stream = p.open(format=p.get_format_from_width(wf.getsampwidth()),
                    channels=wf.getnchannels(),
                    rate=wf.getframerate(),
                    output=True)

    # Play samples from the wave file (3)
    while len(data := wf.readframes(CHUNK)):  # Requires Python 3.8+ for :=
        stream.write(data)

    # Close stream (4)
    stream.close()

    # Release PortAudio system resources (5)
    p.terminate()

