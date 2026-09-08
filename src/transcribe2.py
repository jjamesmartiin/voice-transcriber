import os
import importlib
import logging
import gc

logger = logging.getLogger(__name__)

# Default backend
_current_backend_name = os.environ.get("VT_MODEL_BACKEND", "cohere").lower()
_backend = None

def get_backend():
    global _backend, _current_backend_name
    
    if _backend is None:
        if _current_backend_name == "whisper":
            logger.debug("Using Whisper (faster-whisper) backend")
            _backend = importlib.import_module("transcribe_whisper")
        else:
            logger.debug("Using Cohere (transformers) backend")
            _backend = importlib.import_module("transcribe_cohere")
            
    return _backend

def set_backend(backend_name):
    global _backend, _current_backend_name
    if backend_name.lower() != _current_backend_name:
        if _backend is not None:
            try:
                if hasattr(_backend, "unload_model"):
                    _backend.unload_model()
            except Exception as e:
                logger.debug(f"Error unloading model: {e}")
                
        _current_backend_name = backend_name.lower()
        _backend = None # Force reload on next call
        logger.debug(f"Backend switched to {_current_backend_name}")
        
        gc.collect()

def preload_model(device="cpu"):
    return get_backend().preload_model(device=device)

def transcribe_audio(audio_data=None, audio_path=None, sample_rate=16000, device="cpu", language="en"):
    return get_backend().transcribe_audio(
        audio_data=audio_data, 
        audio_path=audio_path, 
        sample_rate=sample_rate, 
        device=device, 
        language=language
    )

def get_model(device="cpu"):
    return get_backend().get_model(device=device)

def unload_model():
    global _backend
    if _backend is not None and hasattr(_backend, "unload_model"):
        _backend.unload_model()
    _backend = None
    gc.collect()

def get_backend_name() -> str:
    return _current_backend_name
