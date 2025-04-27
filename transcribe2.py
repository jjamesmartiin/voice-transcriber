# ok so now with rec.py we can record the audio and save it as a wav file
# now we need to use faster-whisper to transcribe the audio

MODEL = "small"

import warnings
from faster_whisper import WhisperModel, BatchedInferencePipeline

# Filter out UserWarning about FP16 not supported on CPU
warnings.filterwarnings("ignore", message="FP16 is not supported on CPU; using FP32 instead")

# Move model loading into function to avoid loading during import
def load_model(model_name=MODEL, device="cpu", compute_type="int8"):
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    batched_model = BatchedInferencePipeline(model=model)
    #return model
    return batched_model

def transcribe_audio(audio_path="output.wav", model_name=MODEL, device="cpu"):
    # Load model on demand with device specification
    model = load_model(model_name, device)
    
    # Transcribe audio with faster-whisper
    # segments, info = model.transcribe(audio_path) # single core?
    segments, info = model.transcribe(audio_path, batch_size=4) # batch size = how many threads to use  
    
    # Print language info
    print("Detected language '%s' with probability %f" % (info.language, info.language_probability))
    
    # Return the full transcript
    return " ".join([segment.text for segment in segments])

# Only execute this if the script is run directly (not imported)
if __name__ == "__main__":
    # Load model and transcribe
    result = transcribe_audio("output.wav")
    print(result)
    
    # Optional: Uncomment to print segments with timestamps
    # model = load_model()
    # segments, info = model.transcribe("output.wav")
    # for segment in segments:
    #     print("[%.2fs -> %.2fs] %s" % (segment.start, segment.end, segment.text))
