# ok so now with rec.py we can record the audio and save it as a wav file
# now we need to use whisper to transcribe the audio

MODEL="small"

import whisper
import warnings

# Filter out UserWarning about FP16 not supported on CPU
warnings.filterwarnings("ignore", message="FP16 is not supported on CPU; using FP32 instead")

# Move model loading into function to avoid loading during import
def load_model(model_name=MODEL, device=None):
    model = whisper.load_model(model_name)
    if device:
        model.to(device)
    return model

def transcribe_audio(audio_path="output.wav", model_name=MODEL, device=None):
    # Load model on demand with device specification
    model = load_model(model_name, device)
    
    # Load the audio file
    audio = whisper.load_audio(audio_path)
    
    # Transcribe without device parameter
    result = model.transcribe(audio)
    return result

# Only execute this if the script is run directly (not imported)
if __name__ == "__main__":
    # Load model only when needed
    model = load_model()
    
    # Load the audio file
    audio = whisper.load_audio("output.wav")
    
    # Transcribe the audio
    result = model.transcribe(audio)
    print(result["text"])
