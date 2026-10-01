"""ASR backend dispatch.

Cohere Transcribe (``transcribe_cohere``) is the only backend. There is no
model selection: it was measured against faster-whisper (base.en / small.en) on
the 154-clip eval and won decisively (WER 2.79% vs 8.75%/9.38%, and faster), so
the whisper backend and the ``model_backend`` config option were removed.
"""

import importlib
import logging
import gc

logger = logging.getLogger(__name__)

_backend = None


def get_backend():
    global _backend
    if _backend is None:
        logger.debug("Using Cohere (transformers) backend")
        _backend = importlib.import_module("transcribe_cohere")
    return _backend


def set_backend(backend_name):
    """Compatibility shim: Cohere is the only backend, so this is a no-op."""
    if backend_name and backend_name.lower() != "cohere":
        logger.warning("Ignoring unknown backend %r; Cohere is the only backend", backend_name)
    return get_backend()


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
    return "cohere"
