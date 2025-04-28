# ok so now with rec.py we can record the audio and save it as a wav file
# now we need to use faster-whisper to transcribe the audio

MODEL = "small"

import warnings
import threading
from faster_whisper import WhisperModel, BatchedInferencePipeline
import time

# Filter out UserWarning about FP16 not supported on CPU
warnings.filterwarnings("ignore", message="FP16 is not supported on CPU; using FP32 instead")

# Global variable to hold the preloaded model
_model = None
_model_lock = threading.Lock()

def load_model(model_name=MODEL, device="cpu", compute_type="int8"):
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    batched_model = BatchedInferencePipeline(model=model)
    return batched_model

def get_model(model_name=MODEL, device="cpu", compute_type="int8"):
    """Get or initialize the model singleton"""
    global _model
    
    with _model_lock:
        if _model is None:
            print("Loading transcription model... (first run only)")
            start_time = time.time()
            _model = load_model(model_name, device, compute_type)
            elapsed = time.time() - start_time
            print(f"Model loaded in {elapsed:.2f} seconds")
    
    return _model

def preload_model(model_name=MODEL, device="cpu", compute_type="int8"):
    """Preload the model in a background thread"""
    def _preload():
        get_model(model_name, device, compute_type)
    
    thread = threading.Thread(target=_preload)
    thread.daemon = True
    thread.start()
    return thread

def transcribe_audio(audio_path="output.wav", model_name=MODEL, device="cpu"):
    # Use the singleton model instead of loading it each time
    model = get_model(model_name, device)
    
    # Time the transcription process
    start_time = time.time()
    
    # Transcribe audio with faster-whisper
    segments, info = model.transcribe(audio_path, batch_size=8)  # increased batch size for better performance
    
    # Print language and timing info
    elapsed = time.time() - start_time
    print(f"Transcription completed in {elapsed:.2f} seconds")
    print("Detected language '%s' with probability %f" % (info.language, info.language_probability))
    
    # Return the full transcript
    return " ".join([segment.text for segment in segments])

# Only execute this if the script is run directly (not imported)
if __name__ == "__main__":
    # Load model and transcribe
    result = transcribe_audio("output.wav")
    print(result)
    
    # Optional: Uncomment to print segments with timestamps
    # model = get_model()
    # segments, info = model.transcribe("output.wav")
    # for segment in segments:
    #     print("[%.2fs -> %.2fs] %s" % (segment.start, segment.end, segment.text))
