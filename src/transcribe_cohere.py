import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
import warnings
import threading
import os as _os
import time
import torch
import numpy as np
from transformers import AutoProcessor, AutoModelForSpeechSeq2Seq
from huggingface_hub import login

MODEL_ID = "CohereLabs/cohere-transcribe-03-2026"
MODEL_REVISION = "499888924f5f1313b48ab0686c8f3a94178a4709"

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*Init provider bridge failed.*")

for logger_name in ["transformers", "huggingface_hub", "httpx", "tqdm", "urllib3", "requests", "urllib"]:
    logging.getLogger(logger_name).setLevel(logging.CRITICAL)

_devnull = _os.open(_os.devnull, _os.O_WRONLY)
_old_stderr = _os.dup(2)
_os.dup2(_devnull, 2)
_os.close(_devnull)

_model = None
_processor = None
_model_lock = threading.Lock()

def get_token():
    cwd_token_file = _os.path.join(_os.getcwd(), "HF_TOKEN")
    if _os.path.exists(cwd_token_file):
        try:
            with open(cwd_token_file, "r") as f:
                token = f.read().strip()
                if token:
                    return token
        except Exception as e:
            print(f"Error reading {cwd_token_file}: {e}")

    root_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    hf_token_file = _os.path.join(root_dir, "HF_TOKEN")
    if _os.path.exists(hf_token_file):
        try:
            with open(hf_token_file, "r") as f:
                token = f.read().strip()
                if token:
                    return token
        except:
            pass

    token = _os.environ.get("HF_TOKEN")
    if token:
        return token
    
    token_path = _os.path.expanduser("~/.cache/huggingface/token")
    if _os.path.exists(token_path):
        try:
            with open(token_path, "r") as f:
                token = f.read().strip()
                if token:
                    return token
        except:
            pass
            
    return None

def check_auth():
    token = get_token()
    
    if token:
        masked = token[:6] + "..." + token[-4:] if len(token) > 10 else "******"
        print(f"Authentication detected (token: {masked})")
        try:
            login(token=token, add_to_git_credential=False)
            _os.environ["HF_TOKEN"] = token
            return True
        except Exception as e:
            print(f"Error during Hugging Face login: {e}")
            return False

    print("\nHugging Face Authentication Info")
    print(f"The model '{MODEL_ID}' is gated and requires access.")
    print(f"  - A file named 'HF_TOKEN' exists in your current directory")
    print(f"  - The HF_TOKEN environment variable is set")
    print(f"  - You have logged in via 'huggingface-cli login'")
    print(f"Access must be granted at: https://huggingface.co/{MODEL_ID}\n")
    return False

def load_model(model_id=MODEL_ID, revision=MODEL_REVISION, device="cpu"):
    token = get_token()
    dtype = torch.float16 if device == "cuda" else torch.float32
    
    # Search local candidate directories first
    search_dirs = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "cohere"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "cohere"),
        os.path.join(os.getcwd(), "models", "cohere"),
    ]
    
    local_path = None
    for candidate in search_dirs:
        if os.path.exists(candidate) and (os.path.exists(os.path.join(candidate, "model.safetensors")) or os.path.exists(os.path.join(candidate, "pytorch_model.bin"))):
            local_path = candidate
            break
            
    target_id = local_path if local_path else model_id
    
    try:
        print(f"Loading Cohere model from {target_id}...")
        processor = AutoProcessor.from_pretrained(
            target_id, 
            revision=revision if not local_path else None,
            trust_remote_code=True,
            token=token,
            local_files_only=bool(local_path)
        )
        
        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            target_id,
            revision=revision if not local_path else None,
            torch_dtype=dtype,
            trust_remote_code=True,
            token=token,
            local_files_only=bool(local_path)
        ).to(device)
        
        print("Loaded Cohere model successfully.")
        return model, processor
    except Exception as e:
        print(f"Model not in cache or update needed: {e}")
        print(f"Downloading/Verifying model '{model_id}'...")
        
        check_auth()
        token = get_token()
        
        try:
            processor = AutoProcessor.from_pretrained(
                model_id, 
                revision=revision,
                trust_remote_code=True,
                token=token
            )
            
            model = AutoModelForSpeechSeq2Seq.from_pretrained(
                model_id,
                revision=revision,
                torch_dtype=dtype,
                trust_remote_code=True,
                token=token
            ).to(device)
            
            return model, processor
        except Exception as e:
            error_str = str(e).lower()
            if "403" in error_str or "access" in error_str or "unauthorized" in error_str or "401" in error_str:
                print("\nError: Access denied to gated model.")
                print(f"Make sure you have been granted access at: https://huggingface.co/{model_id}")
                if token:
                    masked = token[:6] + "..." + token[-4:] if len(token) > 10 else "******"
                    print(f"Current token (masked): {masked}")
            raise e

def get_model(model_id=MODEL_ID, revision=MODEL_REVISION, device="cpu"):
    global _model, _processor
    
    with _model_lock:
        if _model is None:
            start_time = time.time()
            _model, _processor = load_model(model_id, revision, device)
            elapsed = time.time() - start_time
            print(f"Model loaded and ready in {elapsed:.2f} seconds")
    
    return _model, _processor

def preload_model(device="cpu"):
    def _preload():
        try:
            model, processor = get_model(device=device)
            
            print("Warming up model...")
            warmup_audio = np.zeros(int(16000 * 0.1), dtype=np.float32)
            
            model.transcribe(
                processor=processor,
                audio_arrays=[warmup_audio],
                sample_rates=[16000],
                language="en"
            )
            print("Warmup complete! Ready for instant transcription.")
        except Exception as e:
            print(f"Preload/Warmup error: {e}")
    
    thread = threading.Thread(target=_preload)
    thread.daemon = True
    thread.start()
    return thread

def has_speech_activity(audio_data):
    """Check if audio contains actual speech energy rather than silence / noise floor"""
    if audio_data is None or len(audio_data) == 0:
        return False
    peak = np.max(np.abs(audio_data))
    rms = np.sqrt(np.mean(audio_data.astype(np.float32)**2))
    return peak >= 0.015 or rms >= 0.0035

def transcribe_audio(audio_data=None, audio_path=None, sample_rate=16000, device="cpu", language="en"):
    # Guard against pure silence / background noise
    if audio_data is not None and not has_speech_activity(audio_data):
        return ""
        
    try:
        model, processor = get_model(device=device)
    except Exception as e:
        return f"Error loading model: {e}"
    
    start_time = time.time()
    
    try:
        # Set optimal CPU thread count
        if device == "cpu" and hasattr(torch, "set_num_threads"):
            torch.set_num_threads(max(1, min(os.cpu_count() or 4, 8)))
            
        with torch.inference_mode():
            if audio_data is not None:
                if hasattr(audio_data, "flatten"):
                    audio_data = audio_data.flatten()
                
                results = model.transcribe(
                    processor=processor,
                    audio_arrays=[audio_data],
                    sample_rates=[sample_rate],
                    language=language
                )
            else:
                results = model.transcribe(
                    processor=processor,
                    audio_files=[audio_path],
                    language=language
                )
            
            if isinstance(results, list):
                transcription = " ".join(results)
            else:
                transcription = str(results)
            
            from post_processor import clean_speech_transcription
            transcription = clean_speech_transcription(transcription, skip_slm=True)
            
    except Exception as e:
        print(f"Transcription error: {e}")
        transcription = ""
    
    elapsed = time.time() - start_time
    print(f"Transcription completed in {elapsed:.2f} seconds")
    
    return transcription.strip()

def unload_model():
    global _model, _processor
    with _model_lock:
        if _model is not None:
            print("Unloading Cohere model...")
            _model = None
            _processor = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            import gc
            gc.collect()
            print("Cohere model unloaded.")