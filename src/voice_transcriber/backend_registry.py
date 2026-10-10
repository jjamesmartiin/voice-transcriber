"""Shared mechanics for the ASR, formatter and diarization backend registries.

Each feature declares its own ``BACKENDS``/``DEFAULT_BACKEND``/
``REQUIRED_FUNCTIONS`` and keeps its own public function names, but the lookup,
validation, caching and lazy-import logic is identical, so it lives here once.

Nothing here imports a backend: the caller passes ``import_module`` and it is
only called on a cache miss, so a model-free run never pays for ``torch`` or
``sherpa_onnx``.
"""

from __future__ import annotations


class UnknownBackendError(ValueError):
    """A backend name that is not in a registry, or a declared-but-broken module."""


def available_backends(backends):
    """Every backend name a config may use, sorted."""
    return sorted(backends)


def resolve_backend_name(name, backends, default, label, active=None):
    """Validate ``name`` against ``backends`` without importing anything."""
    resolved = str(name or active or default).strip().lower()
    if resolved not in backends:
        raise UnknownBackendError(
            f"Unknown {label} backend {resolved!r}. "
            f"Available: {', '.join(available_backends(backends))}"
        )
    return resolved


def load_backend(
    name, backends, default, required, loaded, label, *, strict, import_module,
    active=None,
):
    """Import and validate a backend module, caching it in ``loaded``.

    ``strict`` (the ASR registry) raises for a declared-but-broken backend so
    startup names what is missing. Lenient (formatter, diarization) returns
    ``None`` and caches it: a missing optional dependency must read as "feature
    off", never as a crash on the dictation path.
    """
    try:
        resolved = resolve_backend_name(name, backends, default, label, active=active)
    except UnknownBackendError:
        if strict:
            raise
        return None
    if resolved in loaded:
        return loaded[resolved]
    path = backends[resolved]
    if strict:
        try:
            module = import_module(path)
        except ImportError as e:  # pragma: no cover - depends on the environment
            raise UnknownBackendError(
                f"{label} backend {resolved!r} is declared as {path!r} "
                f"but could not be imported: {e}"
            ) from e
        missing = [fn for fn in required if not callable(getattr(module, fn, None))]
        if missing:
            raise UnknownBackendError(
                f"{label} backend {resolved!r} ({path}) does not expose: "
                f"{', '.join(missing)}"
            )
    else:
        try:
            module = import_module(path)
        except Exception:
            module = None
        if module is not None:
            missing = [fn for fn in required if not hasattr(module, fn)]
            if missing:
                module = None
    loaded[resolved] = module
    return module


def reset_backend_cache(loaded):
    """Forget loaded backends. For tests, and for a config change at runtime."""
    loaded.clear()
