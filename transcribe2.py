# ok so now with rec.py we can record the audio and save it as a wav file
# now we need to use whisper to transcribe the audio

import warnings
from faster_whisper import WhisperModel

# Filter out UserWarning about FP16 not supported on CPU
warnings.filterwarnings("ignore", message="FP16 is not supported on CPU; using FP32 instead")

# Move model loading into function to avoid loading during import
def load_model(model_name="small-v3", device="cpu", compute_type="int8"):
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    return model

def transcribe_audio(audio_path="output.wav", model_name="large-v3", device="cpu"):
    # Load model on demand with device specification
    model = load_model(model_name, device)
    
    # Transcribe audio with faster-whisper
    segments, info = model.transcribe(audio_path)
    
    # Combine all segments into one text
    result = " ".join([segment.text for segment in segments])
    return result

# Only execute this if the script is run directly (not imported)
if __name__ == "__main__":
    # Load model and transcribe
    result = transcribe_audio("output.wav")
    print(result)
