# ok so now with rec.py we can record the audio and save it as a wav file
# now we need to use whisper to transcribe the audio

import whisper

model = whisper.load_model("base")

# load the audio file
audio = whisper.load_audio("output.wav")

# transcribe the audio
result = model.transcribe(audio)
print(result["text"])