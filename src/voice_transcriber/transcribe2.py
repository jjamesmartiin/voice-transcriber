"""ASR backend dispatch.

`BACKENDS` below is the single place a backend is declared: a name, and the module
that implements it. Everything else — validation, the error messages, what the
config may name — is derived from it, so adding a backend is one entry plus a
module exposing `transcribe_audio` and `preload_model`.

Cohere Transcribe is the only entry. It won its measurement against
faster-whisper (base.en / small.en) on the 154-clip eval in `eval/` — WER 2.79%
against 8.75% and 9.38%, and faster — which is why whisper is not here.

Nothing in this module imports a backend at import time, so a model-free test run
never pays for `torch`.
"""
from __future__ import annotations

import gc
import importlib
import logging

logger = logging.getLogger(__name__)


class UnknownBackendError(ValueError):
    """A `model_backend` value that is not in the registry, or a broken backend."""


#: Backend name -> import path. One line per backend; everything else is derived.
BACKENDS: dict[str, str] = {
    "cohere": "voice_transcriber.transcribe_cohere",
}

#: Backend used when none is named. Must be a key of :data:`BACKENDS`.
DEFAULT_BACKEND = "cohere"

#: What a backend module must expose. Checked on first load rather than at import
#: time, so declaring a backend stays free and a half-added one fails at startup
#: with a message naming what is missing — not at the first utterance.
REQUIRED_FUNCTIONS = ("transcribe_audio", "preload_model")

_loaded: dict[str, object] = {}
_active: str | None = None


def available_backends() -> list[str]:
    """Every backend name a config may use, sorted."""
    return sorted(BACKENDS)


def resolve_backend_name(name: str | None = None) -> str:
    """Validate ``name`` against the registry without importing anything."""
    resolved = (name or _active or DEFAULT_BACKEND)
    resolved = str(resolved).strip().lower()
    if resolved not in BACKENDS:
        raise UnknownBackendError(
            f"Unknown ASR backend {resolved!r}. Available: {', '.join(available_backends())}"
        )
    return resolved


def _load(name: str):
    """Import and validate a backend module (cached)."""
    if name in _loaded:
        return _loaded[name]
    path = BACKENDS[name]
    try:
        module = importlib.import_module(path)
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise UnknownBackendError(
            f"ASR backend {name!r} is declared as {path!r} but could not be imported: {e}"
        ) from e
    missing = [fn for fn in REQUIRED_FUNCTIONS if not callable(getattr(module, fn, None))]
    if missing:
        raise UnknownBackendError(
            f"ASR backend {name!r} ({path}) does not expose: {', '.join(missing)}"
        )
    _loaded[name] = module
    return module


def get_backend(name: str | None = None):
    """Return the named backend module, defaulting to the active one.

    Selecting a backend here also makes it the active one, so a caller that
    resolves a specific backend gets it on the next call too.
    """
    global _active
    resolved = resolve_backend_name(name)
    _active = resolved
    return _load(resolved)


def set_backend(name: str | None) -> str:
    """Select the backend by name and return the resolved name.

    Raises :class:`UnknownBackendError` for a name that is not in the registry
    rather than silently keeping the previous backend: a config that names a
    backend which does not exist should say so, not quietly do something else.

    Deliberately does **not** import the backend — that keeps it callable from the
    config loader, where importing `torch` this early would be a startup cost.
    """
    global _active
    resolved = resolve_backend_name(name)
    _active = resolved
    logger.debug("ASR backend: %s (%s)", resolved, BACKENDS[resolved])
    return resolved


def get_backend_name() -> str:
    """The active backend's name, without importing it."""
    return _active or DEFAULT_BACKEND


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
    """Give every loaded backend the chance to free its weights, then forget them."""
    for module in list(_loaded.values()):
        unload = getattr(module, "unload_model", None)
        if callable(unload):
            try:
                unload()
            except Exception as e:  # pragma: no cover - backend-specific teardown
                logger.debug("Backend unload failed: %s", e)
    _loaded.clear()
    gc.collect()
