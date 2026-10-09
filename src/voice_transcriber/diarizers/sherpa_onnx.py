"""Real speaker diarization via sherpa-onnx (milestone D3).

Implements the backend contract defined in :mod:`voice_transcriber.diarize`
(``available`` / ``warm`` / ``diarize``) on top of the two-stage sherpa-onnx
pipeline chosen in ``docs/plan-diarization.md`` sec 4.1:

* a pyannote segmentation graph, ``sherpa-onnx-pyannote-segmentation-3-0``;
* a 3D-Speaker ``eres2net`` speaker-embedding graph.

Both graphs are ~47 MB together and are installed under the ``diarization``
model subdirectory (see :func:`models_dir`). Nothing here downloads weights: if
they are absent the backend reports itself unavailable and :func:`diarize`
returns ``[]``, which the caller reads as "one speaker, one transcript".

Import discipline, mirroring :mod:`voice_transcriber.transcribe2`: importing
this module must **not** import ``sherpa_onnx``. The dependency is loaded behind
:func:`_get_sherpa`, and a model-free test run never pays for it.

Two details are the library's job, not ours, and are deliberately absent here:
turn merging (``min_duration_on`` / ``min_duration_off`` are forwarded) and
progress reporting (the caller's callback is handed to
``OfflineSpeakerDiarization.process``).
"""

from __future__ import annotations

import importlib.util
import logging
import os
from typing import Any, Callable

from voice_transcriber.diarize import (
    DEFAULT_THRESHOLD,
    MIN_DURATION_OFF_S,
    MIN_DURATION_ON_S,
    Turn,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model layout
# ---------------------------------------------------------------------------
#
# Declared here rather than in the model registry because the registry entry has
# not landed yet (the shared model workstream owns that file). Once a
# ``diarization`` ModelSpec exists, :func:`models_dir` picks its directory up
# automatically through ``model_download.models_dir``; these constants then
# describe only the files inside it.

#: Segmentation graph, relative to the ``diarization`` model directory.
SEGMENTATION_FILENAME = os.path.join(
    "sherpa-onnx-pyannote-segmentation-3-0", "model.onnx"
)

#: Speaker-embedding graph, relative to the ``diarization`` model directory.
EMBEDDING_FILENAME = (
    "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
)

#: Explicit model-directory override, matching ``VT_MODEL_DIR`` for the ASR.
MODEL_DIR_ENV = "VT_DIARIZATION_MODELS"

#: ``num_clusters`` value the library uses for "infer the speaker count".
AUTO_NUM_CLUSTERS = -1


def models_dir() -> str:
    """Directory holding the two diarization graphs.

    Resolution order mirrors :func:`model_download.models_dir`: an explicit
    ``VT_DIARIZATION_MODELS`` override, then a ``diarization`` registry entry
    (when the shared model workstream adds one), then ``<repo>/models/
    diarization`` for a writable checkout, then the per-user data dir.
    """
    override = os.environ.get(MODEL_DIR_ENV, "").strip()
    if override:
        return os.path.abspath(override)
    try:
        from voice_transcriber import model_download
    except Exception:  # pragma: no cover - model_download is stdlib-only
        return ""
    try:
        return model_download.models_dir("diarization")
    except KeyError:
        # No registry entry yet; fall back to the same search order by hand.
        pass
    root = model_download.find_repo_root()
    if root:
        candidate = os.path.join(root, "models", "diarization")
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(model_download.get_data_dir(), "models", "diarization")


def model_paths() -> tuple[str, str]:
    """``(segmentation, embedding)`` absolute graph paths."""
    directory = models_dir()
    return (
        os.path.join(directory, SEGMENTATION_FILENAME),
        os.path.join(directory, EMBEDDING_FILENAME),
    )


# ---------------------------------------------------------------------------
# Lazy dependency and engine cache
# ---------------------------------------------------------------------------

_sherpa: Any = None

#: Cache of constructed engines, keyed by the full config. Building an engine
#: loads both ONNX graphs, so :func:`warm` and a following :func:`diarize` with
#: the same settings must not pay twice.
_engines: dict[tuple, Any] = {}


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
    """Drop the cached engines and the cached module. For tests."""
    global _sherpa
    _sherpa = None
    _engines.clear()


# ---------------------------------------------------------------------------
# Contract: available() / warm() / diarize()
# ---------------------------------------------------------------------------

def available() -> bool:
    """Cheap readiness probe: dependency importable and both graphs present.

    Deliberately does **not** import ``sherpa_onnx`` or load the ONNX graphs -
    it is called before every run, and on a machine where diarization was never
    enabled it must cost two ``stat`` calls at most.
    """
    if not _dependency_available():
        return False
    segmentation, embedding = model_paths()
    return os.path.isfile(segmentation) and os.path.isfile(embedding)


def _build_config(
    sherpa: Any,
    segmentation: str,
    embedding: str,
    num_speakers: int | None,
    threshold: float,
    min_duration_on: float,
    min_duration_off: float,
) -> Any:
    """Assemble the library config, mapping ``None`` to auto-clustering.

    ``window_shift_ratio`` appears in the plan's sketch of this constructor but
    does not exist in sherpa-onnx 1.12.25 (the pyannote config takes ``model``
    only), so it is intentionally not passed.
    """
    return sherpa.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=segmentation
            ),
        ),
        embedding=sherpa.SpeakerEmbeddingExtractorConfig(model=embedding),
        clustering=sherpa.FastClusteringConfig(
            num_clusters=(
                AUTO_NUM_CLUSTERS if num_speakers is None else int(num_speakers)
            ),
            threshold=float(threshold),
        ),
        min_duration_on=float(min_duration_on),
        min_duration_off=float(min_duration_off),
    )


def _engine_key(
    segmentation: str,
    embedding: str,
    num_speakers: int | None,
    threshold: float,
    min_duration_on: float,
    min_duration_off: float,
) -> tuple:
    return (
        segmentation,
        embedding,
        AUTO_NUM_CLUSTERS if num_speakers is None else int(num_speakers),
        float(threshold),
        float(min_duration_on),
        float(min_duration_off),
    )


def _get_engine(
    segmentation: str,
    embedding: str,
    num_speakers: int | None,
    threshold: float,
    min_duration_on: float,
    min_duration_off: float,
) -> Any:
    """Construct (once) and cache an ``OfflineSpeakerDiarization`` engine."""
    key = _engine_key(
        segmentation, embedding, num_speakers, threshold,
        min_duration_on, min_duration_off,
    )
    engine = _engines.get(key)
    if engine is not None:
        return engine
    sherpa = _get_sherpa()
    config = _build_config(
        sherpa, segmentation, embedding, num_speakers, threshold,
        min_duration_on, min_duration_off,
    )
    if not config.validate():
        raise RuntimeError(
            "sherpa-onnx rejected the diarization config; "
            "check that both model files exist"
        )
    engine = sherpa.OfflineSpeakerDiarization(config)
    _engines[key] = engine
    return engine


def warm() -> None:
    """Preload the default engine so the first real meeting is not the slow one.

    Best-effort by contract: it does nothing when the backend is unavailable and
    lets the caller (:func:`voice_transcriber.diarize.warm`) absorb any failure.
    """
    if not available():
        return
    segmentation, embedding = model_paths()
    _get_engine(
        segmentation, embedding, None,
        DEFAULT_THRESHOLD, MIN_DURATION_ON_S, MIN_DURATION_OFF_S,
    )


def _prepare_audio(audio: Any, sample_rate: int, target_rate: int) -> Any:
    """Return mono ``float32`` samples resampled to ``target_rate``.

    The engine's own ``sample_rate`` is authoritative; dictation audio is
    already 16 kHz but meeting capture may not be, so the rate is read from the
    engine rather than hardcoded.
    """
    import numpy as np

    samples = np.asarray(audio, dtype=np.float32)
    if samples.ndim > 1:
        samples = samples.reshape(samples.shape[0], -1).mean(axis=1)
    if not sample_rate or int(sample_rate) <= 0:
        raise ValueError(f"invalid sample rate {sample_rate!r}")
    if int(sample_rate) == int(target_rate):
        return np.ascontiguousarray(samples, dtype=np.float32)

    from math import gcd

    from scipy.signal import resample_poly

    divisor = gcd(int(target_rate), int(sample_rate))
    resampled = resample_poly(
        samples,
        int(target_rate) // divisor,
        int(sample_rate) // divisor,
    )
    return np.ascontiguousarray(resampled, dtype=np.float32)


def _progress_adapter(
    progress: Callable[[int, int], None] | None
) -> Callable[[int, int], int] | None:
    """Hand the caller's callback to the library, adapting its return type.

    ``OfflineSpeakerDiarization.process`` expects the callback to return an
    ``int`` (0 to continue); the backend contract's callback returns ``None``.
    This is an adapter for that one difference, not a second progress mechanism -
    every call the user sees comes from the library.
    """
    if progress is None:
        return None

    def report(processed: int, total: int) -> int:
        progress(int(processed), int(total))
        return 0

    return report


def diarize(
    audio: Any,
    sample_rate: int,
    *,
    num_speakers: int | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    min_duration_on: float = MIN_DURATION_ON_S,
    min_duration_off: float = MIN_DURATION_OFF_S,
    progress: Callable[[int, int], None] | None = None,
) -> list[Turn]:
    """Label who spoke when, returning ``(start, end, speaker)`` turns.

    ``num_speakers=None`` means "infer the count" and maps to
    ``num_clusters=-1``. The thresholds are forwarded to the library, which owns
    merging; this function never merges turns itself.
    """
    segmentation, embedding = model_paths()
    engine = _get_engine(
        segmentation, embedding, num_speakers, threshold,
        min_duration_on, min_duration_off,
    )
    samples = _prepare_audio(audio, sample_rate, int(engine.sample_rate))
    result = engine.process(samples, callback=_progress_adapter(progress))

    # The library returns an unordered result; sorting is cheap and makes the
    # backend's own output deterministic before the guardrail re-sorts it.
    sort = getattr(result, "sort_by_start_time", None)
    if callable(sort):
        result = sort()

    turns: list[Turn] = []
    for item in result:
        turns.append(Turn(
            start=float(item.start),
            end=float(item.end),
            speaker=int(item.speaker),
            overlap=bool(getattr(item, "overlap", False)),
        ))
    return turns
