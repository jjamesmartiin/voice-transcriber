# Optimized whisper transcription with performance focus

MODEL = "small"

import warnings
import threading
import os
import sys
import time
import numpy as np
from faster_whisper import WhisperModel, BatchedInferencePipeline

def get_bundled_model_dir():
    """Get the model directory - bundled in EXE or use default cache"""
    local_model_dir = os.path.join(os.path.dirname(sys.executable), "models", "whisper")
    if os.path.isdir(local_model_dir):
        return local_model_dir
    
    if getattr(sys, 'frozen', False):
        meipass = sys._MEIPASS
        bundled_models = os.path.join(meipass, 'models', 'whisper')
        if os.path.isdir(bundled_models):
            return bundled_models
        cache_dir = os.path.join(meipass, '.cache', 'whisper')
        if os.path.isdir(cache_dir):
            return cache_dir
    return os.path.expanduser("~/.cache/whisper")


def extract_bundled_models():
    """Extract bundled models to local directory on first run"""
    if not getattr(sys, 'frozen', False):
        return None
    
    meipass = sys._MEIPASS
    source_models = os.path.join(meipass, 'models', 'whisper')
    
    if not os.path.isdir(source_models):
        return None
    
    local_models_dir = os.path.join(os.path.dirname(sys.executable), "models")
    local_whisper_dir = os.path.join(local_models_dir, "whisper")
    
    if os.path.isdir(local_whisper_dir):
        return local_whisper_dir
    
    print(f"Extracting bundled Whisper models to {local_models_dir}...")
    print("This may take a minute...")
    
    import shutil
    try:
        os.makedirs(local_whisper_dir, exist_ok=True)
        shutil.copytree(source_models, local_whisper_dir, dirs_exist_ok=True)
        print("Whisper model extraction complete!")
        return local_whisper_dir
    except Exception as e:
        print(f"Error extracting Whisper models: {e}")
        return None

# Filter out UserWarning about FP16 not supported on CPU
warnings.filterwarnings("ignore", message="FP16 is not supported on CPU; using FP32 instead")
# Filter out ONNX Runtime provider bridge initialization warning
warnings.filterwarnings("ignore", message="Init provider bridge failed")

# Global variable to hold the preloaded model
_model = None
_model_lock = threading.Lock()

def load_model(model_name=MODEL, device="cpu", compute_type=None):
    """Load model with optimized parameters for the current device"""
    extract_bundled_models()
    
    if compute_type is None:
        if device == "cuda":
            compute_type = "float16"
        else:
            compute_type = "int8"

    model = WhisperModel(
        model_name, 
        device=device, 
        compute_type=compute_type, 
        download_root=get_bundled_model_dir()
    )
    
    # Use batched inference pipeline for performance
    batched_model = BatchedInferencePipeline(model=model)
    return batched_model

def get_model(model_name=MODEL, device="cpu", compute_type=None):
    """Get or initialize the model singleton"""
    global _model
    
    with _model_lock:
        if _model is None:
            print(f"Initializing Whisper model '{model_name}'...")
            start_time = time.time()
            _model = load_model(model_name, device, compute_type)
            elapsed = time.time() - start_time
            print(f"Model loaded in {elapsed:.2f} seconds")
    
    return _model

def preload_model(device="cpu"):
    """Preload the model in a background thread"""
    def _preload():
        try:
            get_model(device=device)
        except Exception as e:
            print(f"Preload error: {e}")
    
    thread = threading.Thread(target=_preload)
    thread.daemon = True
    thread.start()
    return thread

def transcribe_audio(audio_data=None, audio_path=None, sample_rate=16000, device="cpu", language="en"):
    """Transcribe audio with performance optimizations"""
    # Use the singleton model instead of loading it each time
    model = get_model(device=device)
    
    # Time the transcription process
    start_time = time.time()
    
    # Prepare input
    if audio_data is not None:
        if hasattr(audio_data, "flatten"):
            audio_data = audio_data.flatten()
        
        # Resample to 16kHz if necessary (Whisper expects 16kHz)
        if sample_rate != 16000:
            try:
                import librosa
                audio_data = librosa.resample(audio_data, orig_sr=sample_rate, target_sr=16000)
            except ImportError:
                try:
                    from scipy.signal import resample
                    num_samples = int(len(audio_data) * 16000 / sample_rate)
                    audio_data = resample(audio_data, num_samples)
                except ImportError:
                    print(f"Warning: Device rate is {sample_rate}Hz but Whisper needs 16000Hz. Resampling failed (librosa/scipy missing).")
        
        input_source = audio_data
    else:
        input_source = audio_path
        
    # Optimized parameters for faster transcription
    segments, info = model.transcribe(
        input_source, 
        batch_size=16 if device == "cuda" else 8,  # Larger batch size for GPU
        beam_size=1,                              # Fastest decoding (greedy)
        best_of=1,                                # Don't generate alternatives
        temperature=0.0,                          # Use greedy decoding
        language=language,                        # Specify language if known for faster processing
        vad_filter=True,                          # Filter out non-speech parts
        vad_parameters=dict(min_silence_duration_ms=500)  # Skip silences
    )
    
    # Join segments efficiently
    text_parts = []
    for segment in segments:
        text_parts.append(segment.text)
    
    elapsed = time.time() - start_time
    print(f"Transcription completed in {elapsed:.2f} seconds")
    if info:
        print(f"Detected language '{info.language}' with probability {info.language_probability:.2f}")
    
    # Return the full transcript
    return " ".join(text_parts).strip()

def unload_model():
    """Unload the model to free up memory"""
    global _model
    with _model_lock:
        if _model is not None:
            print("Unloading Whisper model...")
            del _model
            _model = None
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
            import gc
            gc.collect()
            print("Whisper model unloaded.")
            
            # Force CUDA garbage collection
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.synchronize()
            except:
                pass
