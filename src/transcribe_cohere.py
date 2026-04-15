import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
import warnings
import threading
import time
import torch
import numpy as np
from transformers import AutoProcessor, AutoModelForSpeechSeq2Seq
from huggingface_hub import login

MODEL_ID = "CohereLabs/cohere-transcribe-03-2026"
MODEL_REVISION = "499888924f5f1313b48ab0686c8f3a94178a4709"

def get_bundled_model_dir():
    """Get the model directory - bundled in EXE or use default cache"""
    local_model_dir = os.path.join(os.path.dirname(sys.executable), "models", "huggingface")
    if os.path.isdir(local_model_dir):
        return local_model_dir
    
    if getattr(sys, 'frozen', False):
        meipass = sys._MEIPASS
        bundled_models = os.path.join(meipass, 'models', 'huggingface')
        if os.path.isdir(bundled_models):
            return bundled_models
    
    local_cache = os.path.expanduser("~/.cache/huggingface/hub")
    if os.path.isdir(local_cache):
        return local_cache
    return None


def extract_bundled_models():
    """Extract bundled models to local directory on first run"""
    if not getattr(sys, 'frozen', False):
        return None
    
    meipass = sys._MEIPASS
    source_models = os.path.join(meipass, 'models', 'huggingface')
    
    if not os.path.isdir(source_models):
        return None
    
    local_models_dir = os.path.join(os.path.dirname(sys.executable), "models")
    local_hf_dir = os.path.join(local_models_dir, "huggingface")
    
    if os.path.isdir(local_hf_dir):
        return local_hf_dir
    
    print(f"Extracting bundled models to {local_models_dir}...")
    print("This may take a minute...")
    
    import shutil
    try:
        os.makedirs(local_hf_dir, exist_ok=True)
        shutil.copytree(source_models, local_hf_dir, dirs_exist_ok=True)
        print("Model extraction complete!")
        return local_hf_dir
    except Exception as e:
        print(f"Error extracting models: {e}")
        return None

def get_hf_token_location():
    """Get HF_TOKEN location - bundled in EXE or local files"""
    if getattr(sys, 'frozen', False):
        meipass = sys._MEIPASS
        try:
            token_file = os.path.join(meipass, 'HF_TOKEN')
            if os.path.isfile(token_file):
                return token_file
            token_file = os.path.join(meipass, 'models', 'HF_TOKEN')
            if os.path.isfile(token_file):
                return token_file
        except OSError:
            pass
    return None

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*Init provider bridge failed.*")

for logger_name in ["transformers", "huggingface_hub", "httpx", "tqdm", "urllib3", "requests", "urllib"]:
    logging.getLogger(logger_name).setLevel(logging.CRITICAL)

_devnull = os.open(os.devnull, os.O_WRONLY)
_old_stderr = os.dup(2)
os.dup2(_devnull, 2)
os.close(_devnull)

_model = None
_processor = None
_model_lock = threading.Lock()

def get_token():
    bundled_token = get_hf_token_location()
    if bundled_token:
        try:
            with open(bundled_token, "r") as f:
                token = f.read().strip()
                if token:
                    return token
        except Exception as e:
            print(f"Error reading bundled token: {e}")

    cwd_token_file = os.path.join(os.getcwd(), "HF_TOKEN")
    if os.path.exists(cwd_token_file):
        try:
            with open(cwd_token_file, "r") as f:
                token = f.read().strip()
                if token:
                    return token
        except Exception as e:
            print(f"Error reading {cwd_token_file}: {e}")

    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    hf_token_file = os.path.join(root_dir, "HF_TOKEN")
    if os.path.exists(hf_token_file):
        try:
            with open(hf_token_file, "r") as f:
                token = f.read().strip()
                if token:
                    return token
        except:
            pass

    token = os.environ.get("HF_TOKEN")
    if token:
        return token
    
    token_path = os.path.expanduser("~/.cache/huggingface/token")
    if os.path.exists(token_path):
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
            os.environ["HF_TOKEN"] = token
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
    
    extract_bundled_models()
    bundled_dir = get_bundled_model_dir()
    cache_dir = bundled_dir if bundled_dir else os.path.expanduser("~/.cache/huggingface/hub")
    
    try:
        print(f"Loading model from cache...")
        processor = AutoProcessor.from_pretrained(
            model_id, 
            revision=revision,
            trust_remote_code=True,
            token=token,
            local_files_only=bundled_dir is not None,
            cache_dir=cache_dir
        )
        
        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            model_id,
            revision=revision,
            torch_dtype=dtype,
            trust_remote_code=True,
            token=token,
            local_files_only=bundled_dir is not None,
            cache_dir=cache_dir
        ).to(device)
        
        print("Loaded from local cache.")
        return model, processor
    except Exception as e:
        if bundled_dir:
            print(f"Model not found in bundle: {e}")
            print("Falling back to cache directory...")
        
        if not bundled_dir:
            print(f"Downloading/Verifying model '{model_id}'...")
            check_auth()
            token = get_token()
        
        try:
            processor = AutoProcessor.from_pretrained(
                model_id, 
                revision=revision,
                trust_remote_code=True,
                token=token,
                local_files_only=False,
                cache_dir=os.path.expanduser("~/.cache/huggingface/hub")
            )
            
            model = AutoModelForSpeechSeq2Seq.from_pretrained(
                model_id,
                revision=revision,
                torch_dtype=dtype,
                trust_remote_code=True,
                token=token,
                local_files_only=False,
                cache_dir=os.path.expanduser("~/.cache/huggingface/hub")
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

def transcribe_audio(audio_data=None, audio_path=None, sample_rate=16000, device="cpu", language="en"):
    try:
        model, processor = get_model(device=device)
    except Exception as e:
        return f"Error loading model: {e}"
    
    start_time = time.time()
    
    try:
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
            
    except Exception as e:
        print(f"Transcription error: {e}")
        transcription = ""
    
    elapsed = time.time() - start_time
    print(f"Transcription completed in {elapsed:.2f} seconds")
    
    return transcription

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