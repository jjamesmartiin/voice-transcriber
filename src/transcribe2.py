import os
import importlib
import logging

logger = logging.getLogger(__name__)

# Default backend
_current_backend_name = os.environ.get("VT_MODEL_BACKEND", "whisper").lower()
_backend = None

def get_backend():
    global _backend, _current_backend_name
    
    # Check if backend needs to be changed (e.g. from config)
    # For now, we'll use a global that can be updated
    
    if _backend is None:
        if _current_backend_name == "whisper":
            logger.info("Using Whisper (faster-whisper) backend")
            _backend = importlib.import_module("transcribe_whisper")
        else:
            logger.info("Using Cohere backend")
            _backend = importlib.import_module("transcribe_cohere")
            
    return _backend

def set_backend(backend_name):
    global _backend, _current_backend_name
    if backend_name.lower() != _current_backend_name:
        # Try to unload old backend if it exists
        if _backend is not None:
            try:
                _backend.unload_model()
            except Exception as e:
                logger.debug(f"Error unloading model: {e}")
                
        _current_backend_name = backend_name.lower()
        _backend = None # Force reload on next call
        logger.debug(f"Backend switched to {_current_backend_name}")
        
        # Force garbage collection after unloading
        import gc
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
