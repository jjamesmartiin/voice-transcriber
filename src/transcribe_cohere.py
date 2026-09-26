import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
import warnings
import threading
import os as _os
import time
import torch
import torch.nn as nn
from torch import Tensor
import numpy as np
from transformers import AutoProcessor, AutoModelForSpeechSeq2Seq

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

logger = logging.getLogger(__name__)

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
            # 8 threads achieved the best throughput/latency on the original
            # 8-core Linux tuning box (1.05s median for 5s audio at 0.21x RTF
            # vs 1.20s at 4 threads).
            cpu_cnt = os.cpu_count() or 8
            if sys.platform.startswith("win"):
                # Windows is frequently CPU-only (no CUDA), so make use of the
                # actual physical cores instead of a hard 8-thread ceiling. On a
                # 12-core / 24-thread Zen 5 (Ryzen AI 9 HX 370) a 30s clip took
                # 6.85s at 8 threads, 5.08s at 12 (physical), and *slower* again
                # at 24 (7.47s, SMT oversubscription). Physical cores hit the
                # sweet spot. Linux keeps the historical min(cpu_cnt, 8) to stay
                # bit-for-bit unchanged.
                phys_cores = None
                try:
                    import psutil
                    phys_cores = psutil.cpu_count(logical=False)
                except Exception:
                    # Fallback: assume 2 logical threads per physical core.
                    phys_cores = max(1, cpu_cnt // 2)
                target_threads = max(1, min(cpu_cnt, phys_cores or 8))
            else:
                target_threads = max(1, min(cpu_cnt, 8))
            
        if _cached_cpu_threads != target_threads:
            if hasattr(torch, "set_num_threads"):
                torch.set_num_threads(target_threads)
            _cached_cpu_threads = target_threads

def _cpu_supports_bf16():
    """Detect native CPU bfloat16 support (AVX512-BF16 or AMX-BF16)."""
    # Linux: /proc/cpuinfo is authoritative and has always been the source of
    # truth here, so keep this path first to leave Linux/WSL detection untouched.
    try:
        with open("/proc/cpuinfo") as f:
            flags = f.read()
        return "avx512_bf16" in flags or "amx_bf16" in flags
    except Exception:
        pass
    # Windows (and any other non-/proc platform): use NumPy's CPUID-based CPU
    # feature table. Without this, Windows always fell back to float32 even on
    # CPUs that natively support BF16 (e.g. Zen 4/5, Sapphire Rapids), roughly
    # halving CPU inference speed instead of using the bit-lossless BF16 weights.
    try:
        um = getattr(np, "_core", getattr(np, "core", None))
        um = getattr(um, "_multiarray_umath", None)
        feats = getattr(um, "__cpu_features__", None)
        if feats is not None:
            return bool(feats.get("AVX512BF16")) or bool(feats.get("AMXBF16"))
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

class _Conv1dAsLinear(nn.Module):
    """Equivalent of a kernel_size=1 ``nn.Conv1d`` backed by an ``nn.Linear``.

    ``torch.ao.quantization.quantize_dynamic`` supports ``nn.Linear`` but not
    ``nn.Conv1d``.  The conformer's pointwise (1x1) convolutions are the heaviest
    non-Linear matmuls, so rewriting them as Linears lets them be int8-quantized
    too.  The math is bit-for-bit identical for kernel_size == 1.
    """

    def __init__(self, conv: nn.Conv1d):
        super().__init__()
        assert tuple(conv.kernel_size) == (1,), conv.kernel_size
        self.linear = nn.Linear(conv.in_channels, conv.out_channels, bias=conv.bias is not None)
        with torch.no_grad():
            self.linear.weight.copy_(conv.weight[:, :, 0])
            if conv.bias is not None:
                self.linear.bias.copy_(conv.bias)

    def forward(self, x: Tensor) -> Tensor:
        return self.linear(x.transpose(1, 2)).transpose(1, 2)


def _patch_pointwise_convs(model):
    """Rewrite every conformer 1x1 conv as a Linear so it can be int8-quantized."""
    n = 0
    for layer in getattr(getattr(model, "encoder", None), "layers", []):
        conv = getattr(layer, "conv", None)
        if conv is None:
            continue
        for attr in ("pointwise_conv1", "pointwise_conv2"):
            sub = getattr(conv, attr, None)
            if isinstance(sub, nn.Conv1d) and tuple(sub.kernel_size) == (1,):
                setattr(conv, attr, _Conv1dAsLinear(sub))
                n += 1
    return n


def _maybe_quantize_dynamic(model, device):
    """Apply CPU dynamic int8 quantization to the Linear-heavy model.

    Measured ~22% faster median single-inference latency on CPU vs the bf16
    baseline (interleaved A/B, n>=10), with ~10-13% more from also converting
    the 1x1 conformer convs to quantizable Linears.  Normalized exact-match
    transcriptions are preserved on every test clip.  Disabled by default on
    CPU (it adds ~11 s one-time model-load time and is accuracy-neutral);
    enable with VT_INT8_DYNAMIC=1.  Any failure falls back to the
    original (un-quantized) model so the app never breaks.
    """
    if device != "cpu":
        return model
    if os.environ.get("VT_INT8_DYNAMIC", "0").strip().lower() in ("0", "false", "no", "off"):
        return model
    try:
        start = time.time()
        # Dynamic quantization requires float32 weights.
        model = model.float()
        if os.environ.get("VT_INT8_CONV_PATCH", "1").strip().lower() not in ("0", "false", "no", "off"):
            patched = _patch_pointwise_convs(model)
        else:
            patched = 0
        model = torch.ao.quantization.quantize_dynamic(model, {nn.Linear}, dtype=torch.qint8)
        print(f"Applied dynamic int8 quantization ({patched} conv kernels patched) in {time.time() - start:.1f}s")
        return model
    except Exception as e:
        print(f"int8 dynamic quantization unavailable, using full-precision model: {e}")
        return model


def _load_model_once(target_id, dtype, local_files_only, device):
    _configure_torch_runtime(device)
    processor = AutoProcessor.from_pretrained(
        target_id,
        trust_remote_code=True,
        local_files_only=local_files_only,
    )
    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        target_id,
        torch_dtype=dtype,
        trust_remote_code=True,
        local_files_only=local_files_only,
    ).to(device)
    
    # Put in evaluation mode and disable gradient computation
    model.eval()
    for param in model.parameters():
        param.requires_grad = False

    model = _maybe_quantize_dynamic(model, device)

    model, processor = _optimize_cohere_runtime(model, processor)
    return model, processor

def load_model(model_id=MODEL_ID, revision=MODEL_REVISION, device="cpu"):
    """Load the Cohere model from a local copy.

    The weights are distributed as Apache-2.0 GitHub-release assets (see
    ``model_download``) and installed locally on first run, so no Hugging Face
    account or token is required. ``model_id``/``revision`` are kept for
    signature compatibility and provenance only.
    """
    dtype = _resolve_dtype(device)

    # Search local candidate directories first
    search_dirs = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "cohere"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "cohere"),
        os.path.join(os.getcwd(), "models", "cohere"),
    ]
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        search_dirs.insert(0, os.path.join(sys._MEIPASS, "models", "cohere"))
        search_dirs.insert(0, os.path.join(sys._MEIPASS, "models"))
        search_dirs.insert(0, sys._MEIPASS)
    # Install target of the GitHub-release auto-installer: <repo>/models/cohere
    # when running from a git checkout, else the per-user dir (read-only Nix
    # store / AppImage installs).
    if cohere_models_dir is not None:
        search_dirs.insert(0, cohere_models_dir())

    local_path = None
    for candidate in search_dirs:
        if os.path.exists(candidate) and (os.path.exists(os.path.join(candidate, "model.safetensors")) or os.path.exists(os.path.join(candidate, "pytorch_model.bin"))):
            local_path = candidate
            break

    # No local copy yet: fetch the mirrored Apache-2.0 GitHub-release assets.
    if local_path is None and ensure_local_cohere is not None:
        print("No local Cohere model found. Downloading GitHub release assets...")
        installed = ensure_local_cohere()
        if installed:
            local_path = installed

    if not local_path:
        raise RuntimeError(
            "No local Cohere model found and the GitHub release download failed. "
            "Set VT_MODEL_DIR to a directory containing model.safetensors, or run "
            "with network access so the release assets can be fetched."
        )

    # Prefer the resolved dtype; fall back to FP32 on CPU if BF16 load fails
    # (e.g. an unsupported op in the model code) so the app never breaks.
    dtypes = [dtype]
    if device == "cpu" and dtype == torch.bfloat16:
        dtypes.append(torch.float32)

    last_err = None
    for attempt_dtype in dtypes:
        try:
            logger.info(f"Loading Cohere model from {local_path} (dtype={attempt_dtype})...")
            model, processor = _load_model_once(
                local_path,
                dtype=attempt_dtype,
                local_files_only=True,
                device=device,
            )
            logger.info(f"Loaded Cohere model successfully (dtype={attempt_dtype}).")
            return model, processor
        except Exception as e:
            last_err = e
            logger.debug(f"Load attempt failed (dtype={attempt_dtype}): {e}")

    raise last_err

def get_model(model_id=MODEL_ID, revision=MODEL_REVISION, device="cpu"):
    global _model, _processor
    
    with _model_lock:
        if _model is None:
            start_time = time.time()
            _model, _processor = load_model(model_id, revision, device)
            elapsed = time.time() - start_time
            logger.info(f"Model loaded and ready in {elapsed:.2f} seconds")
    
    return _model, _processor

def preload_model(device="cpu"):
    def _preload():
        try:
            model, processor = get_model(device=device)
            
            logger.info("Warming up model...")
            warmup_audio = np.zeros(int(16000 * 0.1), dtype=np.float32)
            
            with torch.inference_mode():
                model.transcribe(
                    processor=processor,
                    audio_arrays=[warmup_audio],
                    sample_rates=[16000],
                    language="en"
                )
            logger.info("Warmup complete! Ready for instant transcription.")
        except Exception as e:
            logger.error(f"Preload/Warmup error: {e}")
    
    thread = threading.Thread(target=_preload)
    thread.daemon = True
    thread.start()
    return thread

from micro_batcher import has_speech_activity

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
