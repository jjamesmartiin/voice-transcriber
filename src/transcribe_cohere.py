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

def _token_from_config():
    """Read hf_token from local config.yaml/config.yml (root or config/ dir)."""
    import json as _json
    project_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    candidates = [
        _os.path.join(_os.getcwd(), "config.yaml"),
        _os.path.join(_os.getcwd(), "config.yml"),
        _os.path.join(project_root, "config.yaml"),
        _os.path.join(project_root, "config.yml"),
        _os.path.join(project_root, "config", "config.yaml"),
        _os.path.join(project_root, "config", "config.yml"),
        _os.path.join(project_root, "config.json"),
        _os.path.join(project_root, "config", "config.json"),
    ]
    for path in candidates:
        if not _os.path.exists(path):
            continue
        try:
            with open(path, "r") as f:
                content = f.read()
            if path.endswith(".json"):
                data = _json.loads(content)
            else:
                import yaml
                data = yaml.safe_load(content) or {}
            if isinstance(data, dict):
                token = data.get("hf_token")
                if token:
                    return str(token).strip()
        except Exception:
            continue
    return None


def get_token():
    """Resolve Hugging Face token from config.yaml, env var, or huggingface-cli login."""
    token = _token_from_config()
    if token:
        return token

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
    print(f"  - Set 'hf_token' in your local config.yaml (gitignored)")
    print(f"  - Or set the HF_TOKEN environment variable")
    print(f"  - Or log in via 'huggingface-cli login'")
    print(f"Access must be granted at: https://huggingface.co/{MODEL_ID}\n")
    return False

def _cpu_supports_bf16():
    """Detect native CPU bfloat16 support (AVX512-BF16 or AMX-BF16)."""
    try:
        with open("/proc/cpuinfo") as f:
            flags = f.read()
        if "avx512_bf16" in flags or "amx_bf16" in flags:
            return True
    except Exception:
        pass
    return False


def _resolve_dtype(device):
    """Resolve weight dtype.

    CPU defaults to bfloat16 when the hardware supports it: the Cohere weights
    are stored on disk as BF16, so loading them as BF16 is bit-lossless while
    halving memory (and memory bandwidth / power). Falls back to float32
    otherwise. Override with VT_MODEL_DTYPE=fp32|fp16|bf16.
    """
    override = os.environ.get("VT_MODEL_DTYPE", "").strip().lower()
    if override in ("fp32", "float32", "float"):
        return torch.float32
    if override in ("fp16", "float16", "half"):
        return torch.float16
    if override in ("bf16", "bfloat16"):
        return torch.bfloat16

    if device == "cuda":
        return torch.float16
    if _cpu_supports_bf16():
        return torch.bfloat16
    return torch.float32


def _load_model_once(target_id, revision, token, dtype, local_files_only, device):
    processor = AutoProcessor.from_pretrained(
        target_id,
        revision=revision,
        trust_remote_code=True,
        token=token,
        local_files_only=local_files_only,
    )
    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        target_id,
        revision=revision,
        torch_dtype=dtype,
        trust_remote_code=True,
        token=token,
        local_files_only=local_files_only,
    ).to(device)
    return model, processor


def load_model(model_id=MODEL_ID, revision=MODEL_REVISION, device="cpu"):
    token = get_token()
    dtype = _resolve_dtype(device)

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

    # Prefer the resolved dtype; fall back to FP32 on CPU if BF16 load fails
    # (e.g. an unsupported op in the model code) so the app never breaks.
    dtypes = [dtype]
    if device == "cpu" and dtype == torch.bfloat16:
        dtypes.append(torch.float32)

    last_err = None
    # Attempt local load first
    for attempt_dtype in dtypes:
        try:
            print(f"Loading Cohere model from {target_id} (dtype={attempt_dtype})...")
            model, processor = _load_model_once(
                target_id,
                revision=revision if not local_path else None,
                token=token,
                dtype=attempt_dtype,
                local_files_only=bool(local_path),
                device=device,
            )
            print(f"Loaded Cohere model successfully (dtype={attempt_dtype}).")
            return model, processor
        except Exception as e:
            last_err = e
            print(f"Load attempt failed (dtype={attempt_dtype}): {e}")

    # Fall through to download / verify path
    print(f"Model not in cache or update needed: {last_err}")
    print(f"Downloading/Verifying model '{model_id}'...")

    check_auth()
    token = get_token()

    for attempt_dtype in dtypes:
        try:
            model, processor = _load_model_once(
                model_id,
                revision=revision,
                token=token,
                dtype=attempt_dtype,
                local_files_only=False,
                device=device,
            )
            print(f"Loaded Cohere model successfully (dtype={attempt_dtype}).")
            return model, processor
        except Exception as e:
            last_err = e
            error_str = str(e).lower()
            if "403" in error_str or "access" in error_str or "unauthorized" in error_str or "401" in error_str:
                print("\nError: Access denied to gated model.")
                print(f"Make sure you have been granted access at: https://huggingface.co/{model_id}")
                if token:
                    masked = token[:6] + "..." + token[-4:] if len(token) > 10 else "******"
                    print(f"Current token (masked): {masked}")
                raise e
    raise last_err

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
        # Set CPU thread count (configurable via VT_CPU_THREADS; default matches prior behavior)
        if device == "cpu" and hasattr(torch, "set_num_threads"):
            default_threads = max(1, min(os.cpu_count() or 4, 8))
            env_threads = os.environ.get("VT_CPU_THREADS", "").strip()
            if env_threads.isdigit():
                default_threads = max(1, min(int(env_threads), os.cpu_count() or 8))
            torch.set_num_threads(default_threads)
            
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