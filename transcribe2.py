# Optimized whisper transcription with performance focus

MODEL = "small"  # The smallest model that provides acceptable accuracy

import warnings
import threading
import os
import time
from faster_whisper import WhisperModel, BatchedInferencePipeline

# Filter out UserWarning about FP16 not supported on CPU
warnings.filterwarnings("ignore", message="FP16 is not supported on CPU; using FP32 instead")

# Global variable to hold the preloaded model
_model = None
_model_lock = threading.Lock()

def load_model(model_name=MODEL, device="cpu", compute_type=None):
    """Load model with optimized parameters for the current device"""
    # Automatically select the best compute type for the device
    if compute_type is None:
        if device == "cuda":
            compute_type = "float16"  # Best performance on CUDA with reduced precision
        else:
            compute_type = "int8"  # Best for CPU

    model = WhisperModel(model_name, device=device, compute_type=compute_type, download_root=os.path.expanduser("~/.cache/whisper"))
    
    # Use batched inference pipeline for performance
    batched_model = BatchedInferencePipeline(model=model)
    return batched_model

def get_model(model_name=MODEL, device="cpu", compute_type=None):
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

def preload_model(model_name=MODEL, device="cpu", compute_type=None):
    """Preload the model in a background thread"""
    def _preload():
        get_model(model_name, device, compute_type)
    
    thread = threading.Thread(target=_preload)
    thread.daemon = True
    thread.start()
    return thread

def transcribe_audio(audio_path="output.wav", model_name=MODEL, device="cpu"):
    """Transcribe audio with performance optimizations"""
    # Use the singleton model instead of loading it each time
    model = get_model(model_name, device)
    
    # Time the transcription process
    start_time = time.time()
    
    # Optimized parameters for faster transcription
    # - Larger batch_size for better parallelization, especially on GPU
    # - beam_size=1 for faster decoding (at slight quality cost)
    # - best_of=1 for faster computation
    # - condition_on_previous_text=False to avoid context overhead
    # - initial_prompt=None to keep it simple
    segments, info = model.transcribe(
        audio_path, 
        batch_size=16 if device == "cuda" else 8,  # Larger batch size for GPU
        beam_size=1,                              # Fastest decoding (greedy)
        best_of=1,                                # Don't generate alternatives
        temperature=0.0,                          # Use greedy decoding
        language="en",                            # Specify language if known for faster processing
        vad_filter=True,                          # Filter out non-speech parts
        vad_parameters=dict(min_silence_duration_ms=500)  # Skip silences
    )
    
    # Print language and timing info
    elapsed = time.time() - start_time
    print(f"Transcription completed in {elapsed:.2f} seconds")
    print("Detected language '%s' with probability %f" % (info.language, info.language_probability))
    
    # Join segments efficiently
    text_parts = []
    for segment in segments:
        text_parts.append(segment.text)
    
    # Return the full transcript
    return " ".join(text_parts)

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
