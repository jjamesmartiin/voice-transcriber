"""NVIDIA Parakeet TDT 0.6B v3 ASR backend, run through sherpa-onnx (int8 ONNX).

Implements the backend contract declared in :mod:`voice_transcriber.transcribe2`
(``transcribe_audio`` + ``preload_model``, plus the ``get_model`` / ``load_model``
/ ``unload_model`` helpers the app and the eval call) so it can be dropped into
``transcribe2.BACKENDS`` next to ``transcribe_cohere`` without an upstream special
case.

Import discipline, mirroring :mod:`voice_transcriber.transcribe2`: importing this
module must **not** import ``sherpa_onnx``. The dependency is loaded behind
:func:`_get_sherpa`, so a model-free test run never pays for it.

What the model does **not** do
------------------------------
* **No language selection.** The sherpa ``from_transducer`` API has no language
  parameter; the multilingual v3 checkpoint auto-detects. The app's ``language``
  argument is accepted for contract compatibility and otherwise ignored.
* **No punctuation control / ITN.** sherpa-onnx here has no inverse-text-
  normalisation rules wired up (``rule_fsts``/``rule_fars`` are not shipped), so
  digits and punctuation come out exactly as the model emits them.
* **No silence gate of its own.** The acoustic decoder will happily emit a phrase
  for pure noise; :func:`transcribe_audio` therefore mirrors Cohere and applies
  the shared energy guard before it ever reaches the decoder.
* **No batch interface.** The eval and the app both call it one clip/chunk at a
  time; there is no batching path.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
import threading
import time
from typing import Any

# ``src/`` -- the shim directory -- rather than this package's own directory,
# matching the other backends.
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

import numpy as np

logger = logging.getLogger(__name__)

try:
    from post_processor import clean_speech_transcription
except ImportError:  # pragma: no cover - shim for standalone imports
    def clean_speech_transcription(text, skip_slm=True):
        return text

# ---------------------------------------------------------------------------
# Model layout
# ---------------------------------------------------------------------------
#
# This backend predates a ``parakeet`` entry in model_download.MODELS (the shared
# model-registry workstream owns that file), so the weights are located by search
# order here. Once a ModelSpec exists, :func:`models_dir` can prefer it.

#: Human-readable model name (matches the extracted directory the bundle unpacks).
MODEL_NAME = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"

#: Explicit model-directory override, matching ``VT_MODEL_DIR`` for Cohere.
MODEL_DIR_ENV = "VT_PARAKEET_MODELS"

#: Thread-count override; defaults to ``min(cpu_count, 8)``.
THREADS_ENV = "VT_PARAKEET_THREADS"

#: Model input sample rate (mirrors the Cohere backend's ``TARGET_SR``).
SAMPLE_RATE = 16000

#: The three ONNX graphs plus the token table a complete install must contain.
_GRAPH_FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx")
TOKENS_FILE = "tokens.txt"
_REQUIRED_FILES = _GRAPH_FILES + (TOKENS_FILE,)


def _looks_like_model_dir(directory: str) -> bool:
    return bool(directory) and all(
        os.path.isfile(os.path.join(directory, f)) for f in _REQUIRED_FILES
    )


def _search_bases() -> list[str]:
    """Candidate parent directories for the extraction, in priority order."""
    bases: list[str] = []
    root = None
    data_dir = None
    try:
        from voice_transcriber import model_download

        root = model_download.find_repo_root()
        try:
            data_dir = model_download.get_data_dir()
        except Exception:  # pragma: no cover - defensive
            data_dir = None
    except Exception:  # pragma: no cover - model_download is stdlib-only
        pass
    if root:
        bases.append(os.path.join(root, "models", "parakeet"))
    if data_dir:
        bases.append(os.path.join(data_dir, "models", "parakeet"))
    # Last resort: cwd-relative, matching how the repo's own tooling runs.
    bases.append(os.path.join(os.getcwd(), "models", "parakeet"))
    return bases


def models_dir() -> str:
    """Base directory that holds the Parakeet extraction (or should hold it).

    Resolution order: ``$VT_PARAKEET_MODELS`` override -> ``<repo>/models/
    parakeet`` -> per-user data dir ``~/.local/share/vt/models/parakeet``.
    """
    override = os.environ.get(MODEL_DIR_ENV, "").strip()
    if override:
        return os.path.abspath(override)
    bases = _search_bases()
    for base in bases:
        if _looks_like_model_dir(base) or os.path.isdir(os.path.join(base, MODEL_NAME)):
            return base
    return bases[0] if bases else ""


def _resolve_model_dir() -> str | None:
    """Directory containing the four required files, or ``None`` if absent.

    Accepts an override that points either straight at the files or at a parent
    that contains the extraction directory -- both are common ways to hand a
    model to a tool.
    """
    override = os.environ.get(MODEL_DIR_ENV, "").strip()
    if override:
        candidates = [os.path.abspath(override)]
    else:
        candidates = []
        for base in _search_bases():
            candidates.append(base)
            candidates.append(os.path.join(base, MODEL_NAME))

    for candidate in candidates:
        if _looks_like_model_dir(candidate):
            return candidate
        # One level of nesting: <base>/<anything>/weights
        try:
            for name in sorted(os.listdir(candidate)):
                nested = os.path.join(candidate, name)
                if os.path.isdir(nested) and _looks_like_model_dir(nested):
                    return nested
        except OSError:
            continue
    return None


def model_paths() -> tuple[str, str, str, str]:
    """``(encoder, decoder, joiner, tokens)`` absolute paths.

    When the weights are absent the paths are still returned (anchored at
    :func:`models_dir`) so the error message names a real location.
    """
    resolved = _resolve_model_dir() or os.path.join(models_dir(), MODEL_NAME)
    return (
        os.path.join(resolved, "encoder.int8.onnx"),
        os.path.join(resolved, "decoder.int8.onnx"),
        os.path.join(resolved, "joiner.int8.onnx"),
        os.path.join(resolved, TOKENS_FILE),
    )


# ---------------------------------------------------------------------------
# Lazy dependency and engine cache
# ---------------------------------------------------------------------------

_sherpa: Any = None

#: The loaded recognizer. Named ``_model`` (not ``_recognizer``) because the
#: app's model-load watcher reads ``backend_mod._model is not None``.
_model: Any = None
_model_lock = threading.Lock()


def _get_sherpa() -> Any:
    """Import ``sherpa_onnx`` once, on first use. Never at module import."""
    global _sherpa
    if _sherpa is None:
        import sherpa_onnx  # noqa: PLC0415 - lazy by design

        _sherpa = sherpa_onnx
    return _sherpa


def _dependency_available() -> bool:
    """Whether ``sherpa_onnx`` can be imported, without importing it."""
    return importlib.util.find_spec("sherpa_onnx") is not None


def reset() -> None:
    """Drop the cached module and recognizer. For tests."""
    global _sherpa, _model
    _sherpa = None
    _model = None


def available() -> bool:
    """Cheap readiness probe: dependency importable and all weights present."""
    if not _dependency_available():
        return False
    resolved = _resolve_model_dir()
    return resolved is not None


def _default_threads() -> int:
    raw = os.environ.get(THREADS_ENV, "").strip()
    if raw.isdigit():
        return max(1, int(raw))
    return max(1, min(os.cpu_count() or 4, 8))


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def load_model(device="cpu"):
    """Load the int8 transducer graphs and return the recognizer.

    ``device`` is accepted for contract compatibility; sherpa-onnx selects its
    execution provider from the ``provider`` argument, and only ``cpu`` is wired
    up here (the shipped weights are CPU int8, and a GPU path would need a
    different artifact set).
    """
    del device  # only the cpu provider is supported by this artifact
    sherpa = _get_sherpa()
    encoder, decoder, joiner, tokens = model_paths()
    missing = [p for p in (encoder, decoder, joiner, tokens) if not os.path.isfile(p)]
    if missing:
        raise RuntimeError(
            "Parakeet weights are not installed. Missing: "
            + ", ".join(missing)
            + f". Set ${MODEL_DIR_ENV} or place the model in '{models_dir()}'."
        )
    start = time.time()
    logger.info("Loading Parakeet TDT 0.6B v3 (sherpa-onnx int8) from %s", os.path.dirname(encoder))
    recognizer = sherpa.OfflineRecognizer.from_transducer(
        encoder=encoder,
        decoder=decoder,
        joiner=joiner,
        tokens=tokens,
        num_threads=_default_threads(),
        sample_rate=SAMPLE_RATE,
        feature_dim=80,
        decoding_method="greedy_search",
        model_type="nemo_transducer",
        provider="cpu",
    )
    logger.info("Parakeet model ready in %.2fs", time.time() - start)
    return recognizer


def get_model(device="cpu"):
    """Load once, lazily, and return the recognizer (thread-safe)."""
    global _model
    with _model_lock:
        if _model is None:
            _model = load_model(device=device)
    return _model


def unload_model():
    """Drop the recognizer and let the ONNX sessions be collected."""
    global _model
    with _model_lock:
        _model = None
    import gc

    gc.collect()


def preload_model(device="cpu"):
    """Start a daemon thread that loads and warms the model, and return it.

    Mirrors :func:`voice_transcriber.transcribe_cohere.preload_model`: the app
    tracks and joins the returned thread, so it must never block the hotkey or
    UI threads on model loading.
    """

    def _preload():
        try:
            recognizer = get_model(device=device)
            logger.info("Warming up Parakeet model...")
            _decode(recognizer, np.zeros(int(SAMPLE_RATE * 0.1), dtype=np.float32), SAMPLE_RATE)
            logger.info("Parakeet warmup complete. Ready for instant transcription.")
        except Exception as e:  # pragma: no cover - environment dependent
            logger.error("Parakeet preload/warmup error: %s", e)

    thread = threading.Thread(target=_preload, daemon=True)
    thread.start()
    return thread


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def _decode(recognizer, audio: "np.ndarray", sample_rate: int):
    """Run one float32 array through the recognizer and return its result."""
    stream = recognizer.create_stream()
    # sherpa resamples internally when sample_rate differs from the model's.
    stream.accept_waveform(sample_rate=int(sample_rate), waveform=audio)
    recognizer.decode_stream(stream)
    return stream.result


def _as_float32_mono(audio) -> "np.ndarray":
    if not isinstance(audio, np.ndarray):
        audio = np.asarray(audio, dtype=np.float32)
    else:
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32, copy=False)
        if audio.ndim > 1:
            audio = audio.ravel()
    return audio


def _load_audio_path(audio_path: str, target_sr: int = SAMPLE_RATE):
    import soundfile as sf

    data, sr = sf.read(audio_path, dtype="float32", always_2d=False)
    if getattr(data, "ndim", 1) > 1:
        data = data.mean(axis=1)
    return np.ascontiguousarray(data, dtype=np.float32), sr


try:
    from micro_batcher import has_speech_activity
except Exception:  # pragma: no cover - defensive
    def has_speech_activity(audio_data):
        return True


def transcribe_audio(audio_data=None, audio_path=None, sample_rate=16000, device="cpu", language="en"):
    """Transcribe one clip/chunk and return its text.

    Accepts the same call shapes as the Cohere backend: a float32 ``audio_data``
    array at ``sample_rate``, or an ``audio_path``. ``language`` is accepted for
    contract compatibility and ignored -- the v3 checkpoint auto-detects (see the
    module docstring).
    """
    del language
    try:
        if audio_data is not None:
            audio = _as_float32_mono(audio_data)
            sr = sample_rate
        elif audio_path is not None:
            audio, sr = _load_audio_path(audio_path)
        else:
            return ""
    except Exception as e:
        print(f"Parakeet audio load error: {e}")
        return ""

    if audio.size == 0:
        return ""

    if not has_speech_activity(audio):
        return ""

    try:
        recognizer = get_model(device=device)
    except Exception as e:
        return f"Error loading model: {e}"

    start = time.time()
    try:
        result = _decode(recognizer, audio, sr)
        text = result.text or ""
        text = clean_speech_transcription(text, skip_slm=True)
    except Exception as e:  # pragma: no cover - inference failure
        print(f"Parakeet transcription error: {e}")
        text = ""
    elapsed = time.time() - start
    print(f"Transcription completed in {elapsed:.2f} seconds")
    return text.strip()


def transcribe_with_timestamps(
    audio_data=None,
    audio_path=None,
    sample_rate=16000,
    device="cpu",
    language="en",
):
    """Transcribe and return text plus token/word timings.

    This is the function used to establish whether the model's timestamps are
    usable. It is **not** part of the backend contract (``transcribe_audio``
    returns a plain string); it exists so callers can opt in.

    Returns a dict::

        {
          "text": str,
          "tokens": [str, ...],          # BPE word-pieces
          "timestamps": [float, ...],    # start time of each token, seconds
          "durations": [float, ...],
          "words": [{"word": str, "start": float, "end": float}, ...],
          "sample_rate": int,
        }

    Word boundaries are reconstructed from the word-piece tokens: a token whose
    surface begins with a space starts a new word, and the previous word ends at
    that token's start time. This is the standard NeMo/sherpa word-piece layout.
    """
    del language
    if audio_data is not None:
        audio = _as_float32_mono(audio_data)
        sr = sample_rate
    elif audio_path is not None:
        audio, sr = _load_audio_path(audio_path)
    else:
        raise ValueError("audio_data or audio_path is required")

    recognizer = get_model(device=device)
    result = _decode(recognizer, audio, sr)
    tokens = list(getattr(result, "tokens", []) or [])
    timestamps = list(getattr(result, "timestamps", []) or [])
    durations = list(getattr(result, "durations", []) or [])
    words = _merge_word_pieces(
        tokens, timestamps, audio.size / float(sr or SAMPLE_RATE), durations=durations
    )
    return {
        "text": (result.text or "").strip(),
        "tokens": tokens,
        "timestamps": timestamps,
        "durations": durations,
        "words": words,
        "sample_rate": sr,
    }


def _merge_word_pieces(tokens, timestamps, fallback_end, durations=None):
    """Merge BPE word-pieces into words using the space-prefix word-start marker.

    A token whose surface begins with a space (or the SentencePiece ``\u2581``
    marker) starts a new word. Each word's start is its first token's start time
    and its end is its last token's end time (``start + duration``) when
    ``durations`` is supplied, otherwise the next word's start; the final word
    falls back to ``fallback_end`` (the clip length) only when no duration is
    known.
    """
    words = []
    current = None
    for i, tok in enumerate(tokens):
        start = timestamps[i] if i < len(timestamps) else None
        if durations is not None and i < len(durations) and start is not None:
            end = start + durations[i]
        else:
            end = None
        is_word_start = bool(tok) and (tok[0].isspace() or tok[0] == "\u2581")
        if is_word_start or current is None:
            if current is not None:
                if current["end"] is None:
                    current["end"] = start if start is not None else fallback_end
                words.append(current)
            current = {"word": tok.strip().replace("\u2581", ""), "start": start, "end": end}
        else:
            current["word"] += tok.replace("\u2581", "")
            if end is not None:
                current["end"] = end
    if current is not None:
        if current["end"] is None:
            current["end"] = fallback_end
        words.append(current)
    return words
