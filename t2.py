# ok now we should focus on automating the process of transcribing the audio

# we have rec.py to record the audio and save it as a wav file
# we have transcribe.py to transcribe the audio

# now we need to combine these two files

# first we need to record the audio
import rec

# then we need to transcribe the audio
import transcribe

# Write transcription to a temporary file
with open('/tmp/transcription.txt', 'w') as f:
    f.write(transcribe.result["text"])

# :r /tmp/transcription.txt

