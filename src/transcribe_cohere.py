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

try:
    from post_processor import clean_speech_transcription
except ImportError:
    def clean_speech_transcription(text, skip_slm=True):
        return text

try:
    from model_download import cohere_models_dir, ensure_local_cohere
except Exception:
    cohere_models_dir = None
    ensure_local_cohere = None

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
_cached_cpu_threads = None
_interop_configured = False

def _configure_torch_runtime(device="cpu"):
    """Tune PyTorch runtime parameters for optimal CPU/GPU inference latency and power efficiency."""
    global _cached_cpu_threads, _interop_configured
    if device == "cpu":
        torch.set_grad_enabled(False)
        if not _interop_configured:
            try:
                torch.set_num_interop_threads(1)
            except RuntimeError:
                pass
            _interop_configured = True
            
        if hasattr(torch, "set_float32_matmul_precision"):
            try:
                torch.set_float32_matmul_precision("high")
            except Exception:
                pass
        
        env_threads = os.environ.get("VT_CPU_THREADS", "").strip()
        if env_threads.isdigit():
            target_threads = max(1, min(int(env_threads), os.cpu_count() or 8))
        else:
            cpu_cnt = os.cpu_count() or 4
            target_threads = max(1, min(cpu_cnt, 8))
            
        if _cached_cpu_threads != target_threads:
            if hasattr(torch, "set_num_threads"):
                torch.set_num_threads(target_threads)
            _cached_cpu_threads = target_threads

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
    print("  - Set 'hf_token' in your local config.yaml (gitignored)")
    print("  - Or set the HF_TOKEN environment variable")
    print("  - Or log in via 'huggingface-cli login'")
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


def _optimize_cohere_runtime(model, processor):
    """Optimize model and processor with prompt caching and tokenizer result memoization."""
    # 1. Prompt string formatting cache
    if hasattr(model, "build_prompt"):
        orig_build_prompt = model.build_prompt
        prompt_cache = {}
        def cached_build_prompt(language: str, punctuation: bool = True) -> str:
            key = (language, punctuation)
            if key not in prompt_cache:
                prompt_cache[key] = orig_build_prompt(language, punctuation)
            return prompt_cache[key]
        model.build_prompt = cached_build_prompt

    # 2. Tokenizer output memoization for repetitive prompts
    if hasattr(processor, "tokenizer") and hasattr(processor, "__call__"):
        token_cache = {}
        orig_processor_call = processor.__call__
        
        def cached_processor_call(audio=None, text=None, sampling_rate=None, return_tensors=None, **kwargs):
            if audio is None:
                raise ValueError("audio is required for CohereAsrProcessor.")
            result = processor.feature_extractor(audio, sampling_rate=sampling_rate, return_tensors=return_tensors)
            if text is not None:
                add_special_tokens = kwargs.get("add_special_tokens", False)
                cache_key = None
                if isinstance(text, str):
                    cache_key = (text, return_tensors, add_special_tokens)
                elif isinstance(text, (list, tuple)) and all(isinstance(t, str) for t in text):
                    cache_key = (tuple(text), return_tensors, add_special_tokens)
                
                if cache_key is not None and cache_key in token_cache:
                    cached = token_cache[cache_key]
                    result["input_ids"] = cached["input_ids"].clone()
                    if "attention_mask" in cached and cached["attention_mask"] is not None:
                        result["attention_mask"] = cached["attention_mask"].clone()
                else:
                    kwargs_copy = dict(kwargs)
                    add_spec = kwargs_copy.pop("add_special_tokens", False)
                    text_inputs = processor.tokenizer(
                        text,
                        return_tensors=return_tensors,
                        add_special_tokens=add_spec,
                        **kwargs_copy,
                    )
                    result["input_ids"] = text_inputs["input_ids"]
                    if "attention_mask" in text_inputs:
                        result["attention_mask"] = text_inputs["attention_mask"]
                    if cache_key is not None:
                        token_cache[cache_key] = {
                            "input_ids": text_inputs["input_ids"].clone(),
                            "attention_mask": text_inputs["attention_mask"].clone() if "attention_mask" in text_inputs else None,
                        }
            return result
        processor.__call__ = cached_processor_call
    return model, processor

def _load_model_once(target_id, revision, token, dtype, local_files_only, device):
    _configure_torch_runtime(device)
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
    
    # Put in evaluation mode and disable gradient computation
    model.eval()
    for param in model.parameters():
        param.requires_grad = False
        
    model, processor = _optimize_cohere_runtime(model, processor)
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
    # Per-user writable install dir used by the GitHub-release auto-installer
    # (also the only writable candidate when installed from the read-only Nix store).
    if cohere_models_dir is not None:
        search_dirs.insert(0, cohere_models_dir())

    local_path = None
    for candidate in search_dirs:
        if os.path.exists(candidate) and (os.path.exists(os.path.join(candidate, "model.safetensors")) or os.path.exists(os.path.join(candidate, "pytorch_model.bin"))):
            local_path = candidate
            break

    # No local copy yet: try the mirrored Apache-2.0 GitHub-release asset first,
    # so users never need a Hugging Face account. Falls back to HF below.
    if local_path is None and ensure_local_cohere is not None:
        print("No local Cohere model found. Checking GitHub release assets...")
        installed = ensure_local_cohere()
        if installed:
            local_path = installed

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
            
            with torch.inference_mode():
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
    if audio_data is None:
        return False
    if isinstance(audio_data, np.ndarray):
        arr = audio_data
    else:
        arr = np.asarray(audio_data, dtype=np.float32)
    if arr.size == 0:
        return False
    # Short-circuit peak computation first using max and min to avoid allocating an intermediate array
    max_val = float(np.max(arr))
    min_val = float(np.min(arr))
    peak = max(abs(max_val), abs(min_val))
    if peak >= 0.015:
        return True
    # If peak is borderline, compute RMS energy via zero-allocation SIMD dot product
    if arr.dtype != np.float32:
        arr = arr.astype(np.float32, copy=False)
    if arr.ndim > 1:
        arr = arr.ravel()
    rms = float(np.sqrt(np.dot(arr, arr) / len(arr)))
    return rms >= 0.0035

def transcribe_audio(audio_data=None, audio_path=None, sample_rate=16000, device="cpu", language="en"):
    # Guard against pure silence / background noise
    if audio_data is not None and not has_speech_activity(audio_data):
        return ""
        
    try:
        model, processor = get_model(device=device)
    except Exception as e:
        return f"Error loading model: {e}"
    
    _configure_torch_runtime(device)
    start_time = time.time()
    
    try:
        with torch.inference_mode():
            if audio_data is not None:
                if not isinstance(audio_data, np.ndarray):
                    audio_data = np.asarray(audio_data, dtype=np.float32)
                else:
                    if audio_data.dtype != np.float32:
                        audio_data = audio_data.astype(np.float32, copy=False)
                    if audio_data.ndim > 1:
                        audio_data = audio_data.ravel()
                
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
